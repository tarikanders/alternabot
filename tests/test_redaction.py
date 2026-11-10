"""Tests de redaction.py (aucun réseau : requests et ia.rediger sont simulés)."""
from types import SimpleNamespace

import pytest

from alternabot import base, config, ia, redaction, web
from tests.conftest import page

CADRATIN = chr(0x2014)
DEMI = chr(0x2013)
CORPS = ("Bonjour, je suis étudiante en alternance et votre entreprise m'intéresse beaucoup. " * 8).strip()


@pytest.fixture
def cfg(profil):
    return config.charger()


def reponse(texte, ok=True):
    return page(texte, ok)


def test_lire_site_sans_url():
    assert redaction.lire_site(None) == ""
    assert redaction.lire_site("  ") == ""


def test_lire_site_nettoie_et_borne(monkeypatch):
    vus = []

    def get(url, **kw):
        vus.append((url, kw))
        if url.endswith("/a-propos"):
            return reponse("<html><body><p>" + "x" * 5000 + "</p></body></html>")
        if url.endswith("/about"):
            return reponse("", ok=False)
        return reponse("<html><nav>Menu</nav><script>var a=1</script><body><h1>Acme</h1><p>On fait du  conseil</p>"
                       "<footer>Mentions</footer></body></html>")

    monkeypatch.setattr(web.requests, "get", get)
    texte = redaction.lire_site("acme.example")
    assert texte.startswith("Acme On fait du conseil")
    assert "Menu" not in texte and "var a" not in texte and "Mentions" not in texte
    assert len(texte) <= 4000
    assert vus[0][0] == "https://acme.example"
    assert vus[0][1]["headers"]["User-Agent"] == "Mozilla/5.0 (compatible; alternabot)"
    assert vus[0][1]["timeout"]


def test_lire_site_tolere_les_erreurs_reseau(monkeypatch):
    def get(url, **kw):
        raise web.requests.ConnectionError("hors ligne")

    monkeypatch.setattr(web.requests, "get", get)
    assert redaction.lire_site("https://acme.example") == ""


def test_profil_texte(cfg):
    assert "Camille Martin" in redaction.profil_texte(cfg)


def test_profil_texte_absent(cfg, profil):
    (profil / "profil.md").unlink()
    with pytest.raises(config.ErreurConfig):
        redaction.profil_texte(cfg)


def test_systeme_sans_tiret_et_avec_les_regles():
    assert CADRATIN not in redaction.SYSTEME and DEMI not in redaction.SYSTEME
    assert "vouvoie" in redaction.SYSTEME and "n'inventes rien" in redaction.SYSTEME


def test_message_lba_signe_et_utilise_site_et_offre(monkeypatch, cfg):
    appels = []

    def rediger(systeme, consigne, cfg_):
        appels.append((systeme, consigne))
        return "Bonjour,\n\nMon message pour Acme.\n\nCordialement,"

    monkeypatch.setattr(redaction.ia, "rediger", rediger)
    monkeypatch.setattr(web.requests, "get", lambda url, **kw: reponse("<p>Acme fait du conseil data</p>"))
    texte = redaction.message_lba({"nom": "Acme", "ville": "Lyon", "site": "acme.example"}, cfg,
                                  {"intitule": "Alternant data"})
    assert texte.endswith("Cordialement,\nCamille Martin")
    assert appels[0][0] == redaction.SYSTEME
    consigne = appels[0][1]
    for attendu in ("Acme", "Lyon", "Alternant data", "conseil data", "Camille Martin, étudiante à Lyon"):
        assert attendu in consigne


def test_message_lba_ne_double_pas_la_signature(monkeypatch, cfg):
    monkeypatch.setattr(redaction.ia, "rediger", lambda *a: "Bonjour,\n\nTexte.\n\nCordialement,\nCamille Martin")
    texte = redaction.message_lba({"nom": "Acme"}, cfg)
    assert texte.count("Camille Martin") == 1


def test_relire_texte_correct(cfg):
    assert redaction.relire(CORPS, {"nom": "Acme"}, cfg) == []


@pytest.mark.parametrize("trou", ["[Nom]", "{entreprise}", "XXX", "NOM", "Bonjour [Prénom du contact]"])
def test_relire_trou_de_gabarit(cfg, trou):
    defauts = redaction.relire(CORPS + " " + trou, {"nom": "Acme"}, cfg)
    assert any("trou de gabarit" in d for d in defauts)


def test_relire_longueur(cfg):
    assert any("trop court" in d for d in redaction.relire("Bonjour", {"nom": "Acme"}, cfg))
    assert any("trop long" in d for d in redaction.relire(CORPS * 5, {"nom": "Acme"}, cfg))
    assert redaction.relire("Bonjour", {"nom": "Acme"}, cfg, minimum=3, maximum=20) == []


def test_relire_tiret(cfg):
    for tiret in (CADRATIN, DEMI):
        defauts = redaction.relire(f"{CORPS} {tiret} fin", {"nom": "Acme"}, cfg)
        assert any("tiret" in d for d in defauts)


def test_relire_autre_entreprise(cfg):
    base.ajouter_entreprise("Acme SAS", "lba", source_id="lba:1")
    base.ajouter_entreprise("Zephyr Conseil SARL", "lba", source_id="lba:2")
    texte = CORPS + " Je connais aussi Zephyr Conseil."
    defauts = redaction.relire(texte, {"nom": "Acme"}, cfg)
    assert any("autre entreprise" in d and "zephyr conseil" in d for d in defauts)
    # la cible elle-même n'est pas une autre entreprise
    assert redaction.relire(CORPS + " Acme me plaît.", {"nom": "Acme"}, cfg) == []


def test_relire_ignore_un_nom_qui_est_aussi_la_ville_ou_le_profil(cfg):
    base.ajouter_entreprise("Lyon", "lba", source_id="lba:3")
    assert redaction.relire(CORPS + " Je vis à Lyon.", {"nom": "Acme"}, cfg) == []


def test_rediger_relu_reessaie_puis_accepte(cfg):
    textes = iter([CORPS + " [Nom]", "court", CORPS])
    assert redaction.rediger_relu(lambda: next(textes), {"nom": "Acme"}, cfg) == CORPS


def test_rediger_relu_erreur_claire_apres_les_essais(cfg):
    appels = []

    def fabrique():
        appels.append(1)
        return "court [Nom]"

    with pytest.raises(redaction.ErreurRedaction) as e:
        redaction.rediger_relu(fabrique, {"nom": "Acme"}, cfg, essais=3)
    assert len(appels) == 3
    assert "Acme" in str(e.value) and "trou de gabarit" in str(e.value)
    assert isinstance(e.value, ia.ErreurIA)


# ---- Mail spontané --------------------------------------------------------------------------------

TEXTE_MAIL = "Bonjour,\n\n" + ("Votre travail dans le conseil data m'intéresse beaucoup. " * 20).strip() + "\n\nCordialement,"


def ia_fixe(monkeypatch, texte=TEXTE_MAIL, appels=None):
    """IA simulée : `texte` fixe, ou fonction du numéro d'appel (1, 2, ...)."""
    compteur = []

    def rediger(systeme, consigne, cfg_):
        compteur.append(1)
        if appels is not None:
            appels.append((systeme, consigne))
        return texte(len(compteur)) if callable(texte) else texte

    monkeypatch.setattr(redaction.ia, "rediger", rediger)
    monkeypatch.setattr(web.requests, "get", lambda url, **kw: reponse("<p>Acme fait du conseil data</p>"))


def test_mail_spontane_objet_corps_et_signature(monkeypatch, cfg):
    appels = []
    ia_fixe(monkeypatch, appels=appels)
    cfg["candidat"].update(formation="Master Data et IA", linkedin="https://www.linkedin.com/in/camille-martin")
    objet, corps = redaction.mail_spontane({"nom": "Acme", "ville": "Lyon", "site": "acme.example"}, cfg)
    assert objet == "Candidature alternance Master Data et IA : Camille Martin"
    assert corps.endswith("Cordialement,\nCamille Martin\n+33 6 00 00 00 00\ncamille.martin@example.com\n"
                          "https://www.linkedin.com/in/camille-martin")
    assert redaction.MAIL_MIN <= len(corps) <= redaction.MAIL_MAX
    assert redaction.relire(corps, {"nom": "Acme"}, cfg, minimum=redaction.MAIL_MIN, maximum=redaction.MAIL_MAX) == []
    systeme, consigne = appels[0]
    assert systeme == redaction.SYSTEME
    for attendu in ("Acme", "Lyon", "conseil data", "Camille Martin, étudiante à Lyon", "CV est joint"):
        assert attendu in consigne


def test_mail_spontane_sans_linkedin_ni_formation(monkeypatch, cfg):
    ia_fixe(monkeypatch)
    cfg["candidat"].update(formation="", linkedin="")
    objet, corps = redaction.mail_spontane({"nom": "Acme"}, cfg)
    assert objet == "Candidature alternance : Camille Martin"
    assert corps.endswith("Cordialement,\nCamille Martin\n+33 6 00 00 00 00\ncamille.martin@example.com")


def test_mail_spontane_remplace_la_signature_de_l_ia_et_ajoute_la_formule(monkeypatch, cfg):
    ia_fixe(monkeypatch, TEXTE_MAIL.removesuffix("Cordialement,") + "Bien cordialement,\nCamille M.\nTel : 06")
    _, corps = redaction.mail_spontane({"nom": "Acme"}, cfg)
    assert corps.count("ordialement") == 1 and "Camille M." not in corps and "Tel : 06" not in corps
    ia_fixe(monkeypatch, TEXTE_MAIL.removesuffix("Cordialement,").rstrip())
    _, corps = redaction.mail_spontane({"nom": "Acme"}, cfg)
    assert "\n\nCordialement,\nCamille Martin\n" in corps


def test_mail_spontane_objet_borne(cfg):
    cfg["candidat"]["formation"] = "Master spécialisé en ingénierie des données et intelligence artificielle appliquée"
    objet = redaction._objet(cfg)
    assert len(objet) < 100 and objet.endswith(": Camille Martin") and "appliquée" not in objet


def candidat_a_rediger(nom="Acme", adresse="rh@acme.example", **champs):
    identifiant = base.ajouter_entreprise(nom, "maps", source_id=f"maps:{nom}", site=f"https://{nom.lower()}.example", **champs)
    if adresse:
        base.ajouter_email(identifiant, adresse, methode="site", confiance="haute")
    return identifiant


def candidatures():
    with base.connexion() as conn:
        return [dict(l) for l in conn.execute("SELECT * FROM candidatures ORDER BY id")]


def test_preparer_cree_la_candidature_en_file(monkeypatch, cfg, profil):
    ia_fixe(monkeypatch)
    identifiant = candidat_a_rediger("Acme Conseil")
    assert redaction.preparer(cfg) == 1
    (c,) = candidatures()
    assert (c["entreprise_id"], c["canal"], c["statut"], c["email_utilise"]) == (identifiant, "email", "en_file", "rh@acme.example")
    assert c["objet"].startswith("Candidature alternance") and c["objet"].endswith("Camille Martin")
    assert c["corps_chemin"] == f"lettres/mail_acme_conseil_{identifiant}.txt"
    corps = (profil / c["corps_chemin"]).read_text(encoding="utf-8")
    assert corps.startswith("Bonjour,") and "Cordialement,\nCamille Martin" in corps
    # relancer ne réécrit pas un mail déjà en file
    assert redaction.preparer(cfg) == 0 and len(candidatures()) == 1


def test_preparer_saute_les_entreprises_deja_contactees_sans_adresse_ou_en_echec(monkeypatch, cfg):
    appels = []
    ia_fixe(monkeypatch, appels=appels)
    contactee = candidat_a_rediger("Contactee", "x@deja.example")
    base.maj_statut(base.creer_candidature(contactee, "lba"), "postee_lba")
    candidat_a_rediger("SansAdresse", None)
    echec = candidat_a_rediger("EnEchec", "y@echec.example")
    base.maj_statut(base.creer_candidature(echec, "email", email_utilise="y@echec.example"), "echec", erreur="550")
    autre_adresse = candidat_a_rediger("EchecAutreAdresse", "z@neuf.example")
    base.maj_statut(base.creer_candidature(autre_adresse, "email", email_utilise="vieux@neuf.example"), "echec")
    assert redaction.preparer(cfg) == 1
    assert len(appels) == 1 and "EchecAutreAdresse" in appels[0][1]


def test_preparer_limite(monkeypatch, cfg):
    ia_fixe(monkeypatch)
    for nom in ("A1", "B2", "C3"):
        candidat_a_rediger(nom, f"rh@{nom.lower()}.example")
    assert redaction.preparer(cfg, limite=2) == 2


def test_preparer_texte_refuse_passe_a_la_suivante(monkeypatch, cfg, capsys):
    ia_fixe(monkeypatch, lambda n: "court [Nom]" if n <= 3 else TEXTE_MAIL)
    candidat_a_rediger("Refusee", "rh@refusee.example")
    candidat_a_rediger("Acceptee", "rh@acceptee.example")
    assert redaction.preparer(cfg) == 1
    assert [c["email_utilise"] for c in candidatures()] == ["rh@acceptee.example"]
    assert "Refusee : mail refusé" in capsys.readouterr().out


def test_preparer_s_arrete_quand_l_ia_est_en_panne(monkeypatch, cfg, capsys):
    appels = []

    def panne(systeme, consigne, cfg_):
        appels.append(1)
        raise ia.ErreurIA("claude introuvable")

    monkeypatch.setattr(redaction.ia, "rediger", panne)
    monkeypatch.setattr(web.requests, "get", lambda url, **kw: reponse(""))
    candidat_a_rediger("A1", "rh@a1.example")
    candidat_a_rediger("B2", "rh@b2.example")
    assert redaction.preparer(cfg) == 0
    assert len(appels) == 1 and candidatures() == []
    assert "Moteur IA indisponible" in capsys.readouterr().out


def test_commandes_rediger_et_apercu(monkeypatch, profil, capsys):
    from alternabot import cli
    ia_fixe(monkeypatch)
    candidat_a_rediger("Acme")
    assert cli.main(["rediger", "--limite", "1"]) == 0
    assert "1 mails rédigés" in capsys.readouterr().out
    assert cli.main(["apercu"]) == 0
    sortie = capsys.readouterr().out
    assert "À : rh@acme.example" in sortie and "Objet : Candidature alternance" in sortie and "Bonjour," in sortie
    assert cli.main(["apercu", "--id", "1"]) == 0 and "Candidature 1" in capsys.readouterr().out
    assert cli.main(["apercu", "--id", "99"]) == 1


@pytest.mark.parametrize("brut,usuel", [
    ("shape it (ESN SSII engagée RSE)", "shape it"),
    ("ESN | Synanto Lyon", "Synanto Lyon"),
    ("Dupont & Fils - Agence web", "Dupont & Fils"),
    ("Agence Web Lyon", "Agence Web Lyon"),           # rien de distinctif à garder : nom laissé tel quel
    ("Data Solutions | Conseil informatique", "Data Solutions | Conseil informatique"),
    ("Acme", "Acme"),
])
def test_nom_usuel_retire_les_mots_cles_de_maps(brut, usuel):
    assert redaction.nom_usuel(brut) == usuel


def test_mail_spontane_emploie_le_nom_usuel(monkeypatch, cfg):
    consignes = []
    monkeypatch.setattr(redaction.ia, "rediger", lambda s, c, cfg_: consignes.append(c) or CORPS)
    monkeypatch.setattr(web.requests, "get", lambda url, **kw: reponse("", ok=False))
    redaction.mail_spontane({"nom": "ESN | Synanto Lyon", "ville": "Lyon"}, cfg)
    assert "Synanto Lyon" in consignes[0] and "ESN |" not in consignes[0]
