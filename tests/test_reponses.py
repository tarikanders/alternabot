"""Tests de reponses.py (aucun réseau : le serveur IMAP est simulé et note chaque commande reçue)."""
from email.message import EmailMessage

import pytest

from alternabot import base, cli, config, reponses


@pytest.fixture
def cfg(profil):
    return config.charger()


def mail(de: str, sujet: str, texte: str) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Date"] = de, "camille.martin@example.com", sujet, "Mon, 9 Nov 2026 10:00:00 +0100"
    m.set_content(texte)
    return m.as_bytes()


class FauxImap:
    """IMAP4_SSL simulé : rend `boite` et garde la trace des commandes (pour vérifier la lecture seule)."""
    boite: list[bytes] = []
    commandes: list[tuple] = []

    def __init__(self, hote, timeout=None):
        FauxImap.commandes.append(("connexion", hote, timeout))

    def login(self, utilisateur, mdp):
        FauxImap.commandes.append(("login", utilisateur, mdp))

    def select(self, dossier, readonly=False):
        FauxImap.commandes.append(("select", dossier, readonly))
        return "OK", [str(len(self.boite)).encode()]

    def search(self, charset, critere):
        FauxImap.commandes.append(("search", critere))
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.boite)))]

    def fetch(self, ids, partie):
        FauxImap.commandes.append(("fetch", ids, partie))
        return "OK", [(f"{i} (BODY[] {{1}}".encode(), self.boite[int(i) - 1]) for i in ids.split(",")]

    def store(self, *a):
        FauxImap.commandes.append(("store",) + a)

    def logout(self):
        FauxImap.commandes.append(("logout",))


@pytest.fixture
def imap(monkeypatch):
    FauxImap.boite, FauxImap.commandes = [], []
    monkeypatch.setattr(reponses.imaplib, "IMAP4_SSL", FauxImap)
    return FauxImap


def envoyee(nom: str, adresse: str) -> int:
    entreprise = base.ajouter_entreprise(nom, "maps", source_id=f"maps:{nom}")
    candidature = base.creer_candidature(entreprise, "email", email_utilise=adresse)
    base.maj_statut(candidature, "envoyee", expediteur="camille.martin@example.com")
    return candidature


def statut(candidature_id: int) -> str:
    with base.connexion() as conn:
        return conn.execute("SELECT statut FROM candidatures WHERE id = ?", (candidature_id,)).fetchone()[0]


# ---- classement ----------------------------------------------------------------------------------

@pytest.mark.parametrize("texte,classe", [
    ("Delivery Status Notification (Failure)", "rebond"),
    ("Bonjour, nous serions ravis de vous rencontrer la semaine prochaine.", "entretien"),
    ("Pourriez-vous nous donner vos disponibilités pour un échange ?", "entretien"),
    ("Malheureusement nous ne recrutons pas d'alternant cette année.", "refus"),
    ("Je suis actuellement absent jusqu'au 15 novembre.", "automatique"),
    ("Merci, nous souhaitons donner suite à votre candidature.", "a_lire"),
    ("Merci pour votre message.", "a_lire"),
])
def test_classer_mots(texte, classe):
    assert reponses.classer_mots(texte) == classe


def test_corps_coupe_la_citation_de_notre_lettre():
    from email import message_from_bytes
    m = message_from_bytes(mail("rh@acme.example", "Re: Candidature",
                                "Merci, nous revenons vers vous.\n\nLe 9 nov. 2026, Camille a écrit :\n"
                                "> Je suis disponible pour un entretien."))
    assert "entretien" not in reponses.corps(m)
    assert reponses.classer_mots(reponses.corps(m)) == "a_lire"


def test_ia_departage_seulement_si_activee(monkeypatch, cfg):
    monkeypatch.setattr(reponses.ia, "rediger", lambda s, c, cfg_: "entretien")
    assert reponses.classer("Re: candidature", "Merci pour votre message.", cfg) == "a_lire"
    cfg["reponses"] = {"ia": True}
    assert reponses.classer("Re: candidature", "Merci pour votre message.", cfg) == "entretien"
    monkeypatch.setattr(reponses.ia, "rediger", lambda s, c, cfg_: "n'importe quoi")
    assert reponses.classer("Re: candidature", "Merci pour votre message.", cfg) == "a_lire"


# ---- lecture des boîtes --------------------------------------------------------------------------

def test_rebond_marque_la_candidature(imap, cfg):
    c = envoyee("Acme", "contact@acme.example")
    imap.boite = [mail("Mail Delivery Subsystem <mailer-daemon@googlemail.com>", "Delivery Status Notification (Failure)",
                       "Your message wasn't delivered to contact@acme.example because the address couldn't be found.")]
    groupes = reponses.suivre(cfg)
    assert [r["entreprise"] for r in groupes["rebond"]] == ["Acme"]
    assert statut(c) == "rebond"


def test_entretien_par_une_autre_adresse_du_meme_domaine(imap, cfg):
    c = envoyee("Acme", "contact@acme.example")
    imap.boite = [mail("Julie RH <julie@acme.example>", "Re: Candidature alternance",
                       "Bonjour Camille, nous serions ravis de vous rencontrer.")]
    reponses.suivre(cfg)
    assert statut(c) == "entretien"


def test_expediteur_jamais_contacte_ignore(imap, cfg):
    c = envoyee("Acme", "contact@acme.example")
    imap.boite = [mail("lettre@journal.example", "Votre lettre d'information", "Un entretien exclusif avec...")]
    assert reponses.suivre(cfg) == {}
    assert statut(c) == "envoyee"


def test_boite_gratuite_ne_rattache_pas_par_domaine(imap, cfg):
    c = envoyee("Petit Garage", "garage.dupont@gmail.com")
    imap.boite = [mail("ami@gmail.com", "Salut", "On se voit pour un rendez-vous ?")]
    assert reponses.suivre(cfg) == {}
    assert statut(c) == "envoyee"


def test_un_statut_ne_recule_jamais(imap, cfg):
    c = envoyee("Acme", "contact@acme.example")
    imap.boite = [mail("rh@acme.example", "Re: candidature", "Pouvez-vous nous donner vos disponibilités ?"),
                  mail("rh@acme.example", "Re: candidature", "Merci pour votre message.")]
    reponses.suivre(cfg)
    assert statut(c) == "entretien"
    imap.boite = [mail("rh@acme.example", "Re: candidature", "Merci pour votre message.")]  # a_lire, rang 1
    reponses.suivre(cfg)
    assert statut(c) == "entretien"


def test_lecture_seule(imap, cfg):
    envoyee("Acme", "contact@acme.example")
    imap.boite = [mail("rh@acme.example", "Re: candidature", "Merci.")]
    reponses.suivre(cfg)
    noms = [c[0] for c in imap.commandes]
    assert ("select", "INBOX", True) in imap.commandes
    assert all(c[2] == "(BODY.PEEK[])" for c in imap.commandes if c[0] == "fetch")
    assert "store" not in noms and noms[-1] == "logout"
    assert ("login", "camille.martin@example.com", "mdp-test") in imap.commandes


def test_sans_mot_de_passe_erreur_claire(imap, cfg, monkeypatch):
    monkeypatch.delenv("GMAIL_MDP_1", raising=False)
    with pytest.raises(config.ErreurConfig, match="mot de passe d'application"):
        reponses.suivre(cfg)


def test_boite_illisible_n_arrete_pas_le_suivi(monkeypatch, cfg):
    class Refus(FauxImap):
        def login(self, u, m):
            raise reponses.imaplib.IMAP4.error("AUTHENTICATIONFAILED")

    monkeypatch.setattr(reponses.imaplib, "IMAP4_SSL", Refus)
    assert reponses.suivre(cfg) == {}


# ---- rapport et commandes ------------------------------------------------------------------------

def test_commande_reponses_et_statut(imap, profil, capsys):
    envoyee("Acme", "contact@acme.example")
    envoyee("Beta", "contact@beta.example")
    imap.boite = [mail("rh@acme.example", "Re: candidature", "Nous serions ravis de vous rencontrer."),
                  mail("rh@beta.example", "Re: candidature", "Malheureusement, pas de poste.")]
    assert cli.main(["reponses", "--jours", "10"]) == 0
    sortie = capsys.readouterr().out
    assert "2 réponses" in sortie and "1 entretien" in sortie and "1 refus" in sortie
    rapport = (profil / "reponses.md").read_text(encoding="utf-8")
    assert "## entretien (1)" in rapport and "**Acme**" in rapport
    assert cli.main(["statut"]) == 0
    sortie = capsys.readouterr().out
    assert "Réponses : 2 sur 2 candidatures parties (100 %)" in sortie
    assert "entretien proposé : Acme" in sortie
