"""Tests de envoi.py (aucun réseau : smtplib.SMTP, le DNS et les pauses sont simulés)."""
import smtplib

import pytest
import yaml

from alternabot import base, cli, config, envoi

OBJET = "Candidature alternance Master Data : Camille Martin"
CORPS = "Bonjour,\n\nVoici ma candidature.\n\nCordialement,\nCamille Martin"


class Serveur:
    """Faux serveur SMTP : garde les connexions ouvertes, les mails envoyés et les pannes à simuler."""

    def __init__(self):
        self.connexions = []
        self.erreurs_login = {}   # boîte -> exception levée au login
        self.erreurs_envoi = {}   # destinataire -> exception levée à l'envoi
        self.interdit = False
        self.ordre = []           # (boîte, destinataire) dans l'ordre des envois

    def classe(self):
        serveur = self

        class FauxSMTP:
            def __init__(self, hote, port, timeout=None):
                assert not serveur.interdit, "connexion SMTP interdite en essai"
                self.hote, self.port, self.etapes, self.envoyes, self.ferme = hote, port, [], [], False
                self.utilisateur = self.mdp = None
                serveur.connexions.append(self)

            def ehlo(self):
                self.etapes.append("ehlo")

            def starttls(self):
                self.etapes.append("starttls")

            def login(self, utilisateur, mdp):
                self.etapes.append("login")
                if utilisateur in serveur.erreurs_login:
                    raise serveur.erreurs_login[utilisateur]
                self.utilisateur, self.mdp = utilisateur, mdp

            def send_message(self, message, from_addr, to_addrs):
                if to_addrs[0] in serveur.erreurs_envoi:
                    raise serveur.erreurs_envoi[to_addrs[0]]
                self.envoyes.append((message, from_addr, to_addrs))
                serveur.ordre.append((self.utilisateur, to_addrs[0]))

            def quit(self):
                self.ferme = True

        return FauxSMTP

    def envois(self):
        return self.ordre


@pytest.fixture
def serveur(monkeypatch):
    s = Serveur()
    monkeypatch.setattr(envoi.smtplib, "SMTP", s.classe())
    return s


@pytest.fixture(autouse=True)
def pauses(monkeypatch):
    attentes = []
    monkeypatch.setattr(envoi.time, "sleep", attentes.append)
    monkeypatch.setattr(envoi.emails, "domaine_recoit", lambda domaine: not str(domaine).startswith("mort."))
    return attentes


@pytest.fixture
def deux_comptes(profil):
    chemin = profil / "config.yaml"
    c = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    c["gmail"]["comptes"] = [{"email": "camille.un@example.com", "variable_mdp": "GMAIL_MDP_1"},
                             {"email": "camille.deux@example.com", "variable_mdp": "GMAIL_MDP_2"}]
    chemin.write_text(yaml.safe_dump(c, allow_unicode=True), encoding="utf-8")
    with (profil / ".env").open("a", encoding="utf-8") as f:
        f.write("GMAIL_MDP_2=abcd efgh ijkl mnop\n")
    return profil


@pytest.fixture
def cfg(profil):
    return config.charger()


def en_file(profil, n=1, prefixe="e", domaine=None):
    """Met n candidatures par mail en file (une entreprise et un domaine chacune). Rend leurs identifiants."""
    ids = []
    for i in range(n):
        nom = f"{prefixe}{i}"
        site = f"{domaine or nom + '.example'}"
        entreprise = base.ajouter_entreprise(nom, "maps", source_id=f"maps:{nom}", site=f"https://{site}")
        (profil / "lettres").mkdir(exist_ok=True)
        (profil / "lettres" / f"mail_{nom}.txt").write_text(CORPS, encoding="utf-8")
        ids.append(base.creer_candidature(entreprise, "email", email_utilise=f"contact@{site}", objet=OBJET,
                                          corps_chemin=f"lettres/mail_{nom}.txt"))
    return ids


def statut(identifiant):
    with base.connexion() as conn:
        ligne = conn.execute("SELECT statut, erreur, expediteur FROM candidatures WHERE id = ?", (identifiant,)).fetchone()
    return ligne["statut"], ligne["erreur"], ligne["expediteur"]


# ---- message --------------------------------------------------------------------------------------

def test_message_construit_avec_cv_joint(cfg):
    m = envoi.construire_message(cfg, "camille.un@example.com", "rh@acme.example", OBJET, CORPS)
    assert m["From"] == "Camille Martin <camille.un@example.com>"
    assert m["Reply-To"] == "camille.un@example.com" and m["To"] == "rh@acme.example" and m["Subject"] == OBJET
    pieces = list(m.iter_attachments())
    assert [p.get_filename() for p in pieces] == ["CV.pdf"] and pieces[0].get_content_type() == "application/pdf"
    assert pieces[0].get_payload(decode=True) == b"%PDF-1.4 faux cv"
    assert m.get_body().get_content().strip() == CORPS and m.get_body().get_content_type() == "text/plain"


def test_message_avec_accents(cfg):
    m = envoi.construire_message(cfg, "camille.un@example.com", "rh@acme.example", "Alternance étudiante", "Été à Lyon")
    assert m.get_body().get_content().strip() == "Été à Lyon"
    assert m.as_bytes()  # sérialisable en UTF-8


# ---- mots de passe --------------------------------------------------------------------------------

def test_mot_de_passe_absent_erreur_nommant_la_variable(cfg, profil, serveur):
    en_file(profil)
    (profil / ".env").write_text("GOOGLE_MAPS_API_KEY=cle-test\n", encoding="utf-8")
    import os
    os.environ.pop("GMAIL_MDP_1", None)
    with pytest.raises(config.ErreurConfig) as e:
        envoi.envoyer(config.charger())
    assert "GMAIL_MDP_1" in str(e.value) and "camille.martin@example.com" in str(e.value)
    assert serveur.connexions == []


def test_mot_de_passe_d_application_sans_espaces(deux_comptes, serveur):
    en_file(deux_comptes, 2)
    envoi.envoyer(config.charger())
    mdp = {c.utilisateur: c.mdp for c in serveur.connexions}
    assert mdp == {"camille.un@example.com": "mdp-test", "camille.deux@example.com": "abcdefghijklmnop"}
    assert all(c.etapes == ["ehlo", "starttls", "ehlo", "login"] and c.hote == "smtp.gmail.com" and c.port == 587
               and c.ferme for c in serveur.connexions)


# ---- tourniquet et plafond ------------------------------------------------------------------------

def test_deux_comptes_plafond_15_trente_envois_alternes(deux_comptes, serveur, pauses):
    ids = en_file(deux_comptes, 40)
    bilan = envoi.envoyer(config.charger())
    assert (bilan["envoyees"], bilan["restants"], bilan["motif"]) == (30, 10, "plafond_atteint")
    boites = [u for u, _ in serveur.envois()]
    assert boites[:4] == ["camille.un@example.com", "camille.deux@example.com"] * 2
    assert boites.count("camille.un@example.com") == boites.count("camille.deux@example.com") == 15
    assert [statut(i)[0] for i in ids].count("envoyee") == 30 and [statut(i)[0] for i in ids].count("en_file") == 10
    assert statut(ids[1])[2] == "camille.deux@example.com"
    # un délai aléatoire entre deux envois, dans la fourchette du profil
    assert len(pauses) == 29 and all(45 <= p <= 120 for p in pauses)


def test_envois_deja_faits_aujourd_hui_comptent(deux_comptes, serveur):
    deja = en_file(deux_comptes, 15, prefixe="avant")
    for i in deja:
        base.maj_statut(i, "envoyee", expediteur="camille.un@example.com")
    en_file(deux_comptes, 3, prefixe="nouveau")
    bilan = envoi.envoyer(config.charger())
    assert bilan["envoyees"] == 3
    assert {u for u, _ in serveur.envois()} == {"camille.deux@example.com"}


def test_limite(deux_comptes, serveur):
    en_file(deux_comptes, 5)
    bilan = envoi.envoyer(config.charger(), limite=2)
    assert (bilan["envoyees"], bilan["restants"], bilan["motif"]) == (2, 3, "limite")


def test_file_vide(cfg, serveur):
    bilan = envoi.envoyer(cfg)
    assert bilan["motif"] == "aucune_candidature" and bilan["envoyees"] == 0 and serveur.connexions == []


# ---- doublons et vérifications --------------------------------------------------------------------

def test_candidature_deja_envoyee_au_meme_domaine_sautee(cfg, profil, serveur):
    premiere, seconde = en_file(profil, 1, prefixe="a", domaine="acme.example") + en_file(
        profil, 1, prefixe="b", domaine="acme.example")
    base.maj_statut(premiere, "envoyee", expediteur="camille.martin@example.com")
    bilan = envoi.envoyer(cfg)
    assert bilan["envoyees"] == 0 and bilan["echecs"] == 1
    assert statut(seconde)[:2] == ("echec", "déjà contactée")
    assert serveur.connexions == []


def test_domaine_sans_serveur_mail_en_echec(cfg, profil, serveur):
    (mort,) = en_file(profil, 1, prefixe="x", domaine="mort.example")
    (bon,) = en_file(profil, 1, prefixe="y")
    bilan = envoi.envoyer(cfg)
    assert statut(mort)[:2] == ("echec", "domaine sans serveur mail") and statut(bon)[0] == "envoyee"
    assert (bilan["envoyees"], bilan["echecs"]) == (1, 1)


def test_corps_introuvable_en_echec(cfg, profil, serveur):
    (i,) = en_file(profil)
    (profil / "lettres" / "mail_e0.txt").unlink()
    envoi.envoyer(cfg)
    assert statut(i)[:2] == ("echec", "corps du mail introuvable")


# ---- erreurs SMTP ---------------------------------------------------------------------------------

def test_erreur_destinataire_550_echec_et_suite(cfg, profil, serveur):
    mauvais, bon = en_file(profil, 2)
    serveur.erreurs_envoi["contact@e0.example"] = smtplib.SMTPRecipientsRefused(
        {"contact@e0.example": (550, b"5.1.1 user unknown")})
    bilan = envoi.envoyer(cfg)
    etat = statut(mauvais)
    assert etat[0] == "echec" and "550" in etat[1] and "user unknown" in etat[1]
    assert statut(bon)[0] == "envoyee" and bilan["echecs"] == 1


def test_erreur_553_sur_l_envoi_echec(cfg, profil, serveur):
    (i,) = en_file(profil)
    serveur.erreurs_envoi["contact@e0.example"] = smtplib.SMTPDataError(553, b"adresse invalide")
    envoi.envoyer(cfg)
    assert statut(i)[0] == "echec" and "553" in statut(i)[1]


def test_authentification_refusee_retire_le_compte_et_garde_la_file(deux_comptes, serveur):
    ids = en_file(deux_comptes, 3)
    serveur.erreurs_login["camille.un@example.com"] = smtplib.SMTPAuthenticationError(535, b"refus")
    bilan = envoi.envoyer(config.charger())
    assert bilan["envoyees"] == 3 and bilan["echecs"] == 0
    assert {u for u, _ in serveur.envois()} == {"camille.deux@example.com"}
    assert [statut(i)[0] for i in ids] == ["envoyee"] * 3


def test_tous_les_comptes_refuses_la_file_reste(cfg, profil, serveur):
    ids = en_file(profil, 2)
    serveur.erreurs_login["camille.martin@example.com"] = smtplib.SMTPAuthenticationError(535, b"refus")
    bilan = envoi.envoyer(cfg)
    assert (bilan["envoyees"], bilan["echecs"], bilan["restants"], bilan["motif"]) == (0, 0, 2, "comptes_indisponibles")
    assert [statut(i)[0] for i in ids] == ["en_file", "en_file"]


def test_quota_gmail_retire_la_boite_sans_condamner_la_candidature(cfg, profil, serveur):
    (i,) = en_file(profil)
    serveur.erreurs_envoi["contact@e0.example"] = smtplib.SMTPDataError(
        550, b"5.4.5 Daily user sending limit exceeded")
    bilan = envoi.envoyer(cfg)
    assert statut(i)[0] == "en_file" and bilan["motif"] == "comptes_indisponibles" and bilan["echecs"] == 0


# ---- essai ----------------------------------------------------------------------------------------

def test_essai_ne_se_connecte_pas_et_laisse_les_statuts(cfg, profil, serveur, capsys):
    serveur.interdit = True
    ids = en_file(profil, 2)
    bilan = envoi.envoyer(cfg, essai=True)
    sortie = capsys.readouterr().out
    assert (bilan["essais"], bilan["envoyees"], bilan["restants"]) == (2, 0, 2)
    assert serveur.connexions == []
    assert [statut(i)[0] for i in ids] == ["en_file", "en_file"]
    assert "contact@e0.example" in sortie and OBJET in sortie and "CV.pdf" in sortie


def test_essai_sans_mot_de_passe_ne_bloque_pas(cfg, profil, serveur):
    en_file(profil)
    import os
    os.environ.pop("GMAIL_MDP_1", None)
    (profil / ".env").write_text("", encoding="utf-8")
    assert envoi.envoyer(config.charger(), essai=True)["essais"] == 1


def test_essai_respecte_le_plafond(cfg, profil, serveur):
    en_file(profil, 20)
    bilan = envoi.envoyer(cfg, essai=True)
    assert bilan["essais"] == 15 and bilan["motif"] == "plafond_atteint"


def test_essai_signale_une_candidature_deja_contactee_sans_la_toucher(cfg, profil, serveur):
    a, b = en_file(profil, 1, prefixe="a", domaine="acme.example") + en_file(profil, 1, prefixe="b", domaine="acme.example")
    base.maj_statut(a, "envoyee")
    bilan = envoi.envoyer(cfg, essai=True)
    assert bilan["echecs"] == 1 and statut(b)[0] == "en_file"


# ---- commande -------------------------------------------------------------------------------------

def test_commande_envoyer_essai(profil, serveur, capsys):
    en_file(profil)
    assert cli.main(["envoyer", "--essai", "--limite", "1"]) == 0
    assert "essais : 1" in capsys.readouterr().out


def test_commande_envoyer_sans_mot_de_passe_sort_en_erreur(profil, serveur, capsys):
    en_file(profil)
    (profil / ".env").write_text("", encoding="utf-8")
    import os
    os.environ.pop("GMAIL_MDP_1", None)
    assert cli.main(["envoyer"]) == 1
    assert "GMAIL_MDP_1" in capsys.readouterr().err
