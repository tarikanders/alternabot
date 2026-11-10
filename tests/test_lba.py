"""Tests de lba.py et lba_postuler.py (aucun réseau, aucun navigateur : requests, Playwright et l'IA sont simulés)."""
import contextlib
from types import SimpleNamespace

import pytest
import yaml

from alternabot import base, cli, config, lba, lba_postuler

URL_FICHE = f"https://{lba_postuler.LBA_HOST}/recherche-emploi/fiche-{{}}"


@pytest.fixture
def cfg(profil):
    return config.charger()


def element(nom, siret, recipient_id="rec-1", ident=None, intitule=None, ville_adresse="1 rue A 69003 LYON"):
    return {
        "identifier": {"id": ident or f"id-{nom}"},
        "workplace": {"name": nom, "siret": siret, "website": f"https://{nom.lower()}.example",
                      "location": {"address": ville_adresse}},
        "apply": {"url": URL_FICHE.format(nom.lower()), "phone": "0400000000", "recipient_id": recipient_id},
        "offer": {"title": intitule},
    }


def faux_get(donnees, statut=200, appels=None):
    def get(url, **kw):
        if appels is not None:
            appels.append((url, kw))
        return SimpleNamespace(status_code=statut, ok=statut < 400, json=lambda: donnees)
    return get


# ---- lba.py -------------------------------------------------------------------------------------

def test_chercher_sans_jeton_erreur_claire(profil):
    chemin = profil / "config.yaml"
    c = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    c["lba"]["jeton"] = ""
    chemin.write_text(yaml.safe_dump(c), encoding="utf-8")
    with pytest.raises(config.ErreurConfig) as e:
        lba.chercher(config.charger())
    assert "api.apprentissage.beta.gouv.fr" in str(e.value) and "LBA_API_TOKEN" in str(e.value)


def test_chercher_ecarte_recipient_id_nul_et_normalise(monkeypatch, cfg):
    donnees = {"jobs": [element("Acme", "11111111100011", intitule="Alternant data"),
                        element("SansFormulaire", "22222222200022", recipient_id=None)],
               "recruiters": [element("Beta", "33333333300033")]}
    appels = []
    monkeypatch.setattr(lba.requests, "get", faux_get(donnees, appels=appels))
    fiches = lba.chercher(cfg)
    assert [f["nom"] for f in fiches] == ["Acme", "Beta"]
    acme = fiches[0]
    assert acme == {"nom": "Acme", "siret": "11111111100011", "adresse": "1 rue A 69003 LYON", "ville": "Lyon",
                    "site": "https://acme.example", "telephone": "0400000000", "url": URL_FICHE.format("acme"),
                    "recipient_id": "rec-1", "type": "offre", "intitule": "Alternant data",
                    "source_id": "lba:11111111100011"}
    assert fiches[1]["type"] == "entreprise"
    url, kw = appels[0]
    assert url == lba.ENDPOINT
    assert kw["headers"]["Authorization"] == "Bearer jeton-test"
    assert kw["params"]["romes"] == "M1805,M1403" and kw["params"]["radius"] == 30


def test_chercher_dedoublonne_par_siret_en_gardant_l_offre(monkeypatch, cfg):
    donnees = {"jobs": [element("Acme", "11111111100011", intitule="Alternant")],
               "recruiters": [element("Acme", "11111111100011")]}
    monkeypatch.setattr(lba.requests, "get", faux_get(donnees))
    fiches = lba.chercher(cfg)
    assert len(fiches) == 1 and fiches[0]["type"] == "offre"


@pytest.mark.parametrize("demande, attendu", [(5, 10), (10, 10), (11, 30), (45, 60), (61, 100), (500, 100)])
def test_rayon_arrondi_au_superieur(demande, attendu):
    assert lba.arrondir_rayon(demande) == attendu


def test_chercher_arrondit_le_rayon_et_prend_les_parametres(monkeypatch, cfg):
    appels = []
    monkeypatch.setattr(lba.requests, "get", faux_get({}, appels=appels))
    lba.chercher(cfg, romes=["M1805"], latitude=1.5, longitude=2.5, rayon_km=45)
    params = appels[0][1]["params"]
    assert (params["romes"], params["latitude"], params["longitude"], params["radius"]) == ("M1805", 1.5, 2.5, 60)


def test_chercher_jeton_refuse(monkeypatch, cfg):
    monkeypatch.setattr(lba.requests, "get", faux_get({}, statut=401))
    with pytest.raises(lba.ErreurLBA) as e:
        lba.chercher(cfg)
    assert "LBA_API_TOKEN" in str(e.value)


def test_enregistrer_stable_et_sans_doublon(monkeypatch, cfg):
    donnees = {"jobs": [element("Acme", "11111111100011", intitule="Alternant")],
               "recruiters": [element("Beta", "33333333300033")]}
    monkeypatch.setattr(lba.requests, "get", faux_get(donnees))
    ids = lba.enregistrer(lba.chercher(cfg))
    assert len(ids) == 2
    assert lba.enregistrer(lba.chercher(cfg)) == ids  # relancer ne crée rien de plus
    assert base.stats()["entreprises"] == 2
    assert lba.fiches_enregistrees()["lba:11111111100011"]["intitule"] == "Alternant"


def test_enregistrer_garde_l_offre_contre_l_entreprise(cfg):
    offre = {"nom": "Acme", "siret": "1", "source_id": "lba:1", "type": "offre", "intitule": "X", "url": "u"}
    entreprise = dict(offre, type="entreprise", intitule=None)
    lba.enregistrer([offre])
    lba.enregistrer([entreprise])
    assert lba.fiches_enregistrees()["lba:1"]["type"] == "offre"


# ---- lba_postuler.py : fonctions pures ------------------------------------------------------------

def test_telephone_fr():
    assert lba_postuler.telephone_fr("+33 6 00 00 00 00") == "0600000000"
    assert lba_postuler.telephone_fr("33600000000") == "0600000000"
    assert lba_postuler.telephone_fr("06 00 00 00 00") == "0600000000"


def test_formation_coupee_a_200(cfg):
    cfg["candidat"]["formation"] = "F" * 250
    cfg["candidat"]["ecole"] = "Université"
    assert len(lba_postuler.texte_formation(cfg)) == 200
    cfg["candidat"]["formation"] = "Master Data"
    assert lba_postuler.texte_formation(cfg) == "Master Data, Université"


def test_cv_trop_lourd_ou_absent(cfg, profil):
    assert lba_postuler.cv_fichier(cfg).name == "CV.pdf"
    (profil / "CV.pdf").write_bytes(b"0" * (3 * 1_048_576 + 1))
    with pytest.raises(config.ErreurConfig):
        lba_postuler.cv_fichier(cfg)
    (profil / "CV.pdf").unlink()
    with pytest.raises(config.ErreurConfig):
        lba_postuler.cv_fichier(cfg)


# ---- lba_postuler.py : faux Playwright -----------------------------------------------------------

class FauxLocateur:
    def __init__(self, page, selecteur):
        self.page, self.selecteur = page, selecteur

    @property
    def first(self):
        return self

    def locator(self, selecteur):
        return FauxLocateur(self.page, f"{self.selecteur} >> {selecteur}")

    def nth(self, i):
        return self

    def count(self):
        return 2

    def click(self, **kw):
        self.page.clics.append(self.selecteur)

    def is_visible(self):
        return True

    def is_checked(self):
        return True

    def get_attribute(self, nom):
        return "radio-0"

    def inner_text(self):
        return ""

    def scroll_into_view_if_needed(self):
        pass

    def set_input_files(self, chemin):
        self.page.fichiers.append(chemin)

    def screenshot(self, path):
        open(path, "wb").write(b"png")

    def get_by_text(self, *a, **kw):
        return self

    def get_by_role(self, *a, **kw):
        return self


class FaussePage:
    def __init__(self, confirme=True):
        self.champs, self.clics, self.fichiers, self.urls = {}, [], [], []
        self.confirme = confirme

    def goto(self, url, **kw):
        self.urls.append(url)

    def evaluate(self, *a):
        pass

    def reload(self, **kw):
        pass

    def wait_for_selector(self, *a, **kw):
        pass

    def wait_for_timeout(self, *a):
        pass

    def fill(self, selecteur, valeur):
        self.champs[selecteur] = valeur

    def select_option(self, selecteur, **kw):
        self.champs[selecteur] = kw.get("label")

    def locator(self, selecteur):
        return FauxLocateur(self, selecteur)

    def get_by_role(self, *a, **kw):
        return SimpleNamespace(count=lambda: 0)

    def get_by_test_id(self, identifiant):
        return SimpleNamespace(count=lambda: 1 if (self.confirme and identifiant == "candidature-sent") else 0)

    def screenshot(self, path, **kw):
        open(path, "wb").write(b"png")


class FauxNavigateur:
    def __init__(self, page, lancements):
        self.page, self.lancements = page, lancements

    def new_context(self, **kw):
        return SimpleNamespace(new_page=lambda: self.page)

    def close(self):
        self.lancements.append("ferme")


def installer_faux_playwright(monkeypatch, page):
    lancements = []

    @contextlib.contextmanager
    def faux():
        yield SimpleNamespace(chromium=SimpleNamespace(
            launch=lambda **kw: lancements.append(("lance", kw)) or FauxNavigateur(page, lancements)))

    monkeypatch.setattr(lba_postuler, "_sync_playwright", faux)
    monkeypatch.setattr(lba_postuler.time, "sleep", lambda s: lancements.append(("pause", s)))
    return lancements


def remplir_base(cfg, noms=("Acme", "Beta")):
    fiches = [{"nom": n, "siret": f"{i}" * 14, "source_id": f"lba:{i}", "type": "offre", "intitule": "Alternant",
               "url": URL_FICHE.format(n.lower()), "recipient_id": "rec", "ville": "Lyon", "site": None}
              for i, n in enumerate(noms, 1)]
    return lba.enregistrer(fiches)


@pytest.fixture
def messages(monkeypatch):
    monkeypatch.setattr(lba_postuler.redaction, "message_lba",
                        lambda ent, cfg, offre: ("Bonjour, candidature pour " + ent["nom"] + ". ") * 15
                        + "\nCordialement,\nCamille Martin")


def test_plafond_atteint_n_ouvre_pas_le_navigateur(monkeypatch, cfg, messages):
    ids = remplir_base(cfg)
    for _ in range(10):
        base.maj_statut(base.creer_candidature(ids[0], "lba"), "postee_lba")
    lancements = installer_faux_playwright(monkeypatch, FaussePage())
    monkeypatch.setattr(lba_postuler, "_sync_playwright", lambda: pytest.fail("navigateur lancé"))
    bilan = lba_postuler.postuler(cfg, None, False, False)
    assert bilan["motif"] == "plafond_atteint" and bilan["postees"] == 0
    assert lancements == []


def test_entreprise_deja_contactee_non_reproposee(monkeypatch, cfg, messages):
    ids = remplir_base(cfg)
    base.maj_statut(base.creer_candidature(ids[0], "email", email_utilise="rh@acme.example"), "envoyee")
    cibles = lba_postuler._cibles(10)
    assert [c[0] for c in cibles] == [ids[1]]


def test_cibles_ecarte_hors_domaine_lba_et_echecs(cfg):
    ids = remplir_base(cfg, ("Acme", "Beta", "Gamma"))
    memoire = lba.fiches_enregistrees()
    memoire["lba:2"]["url"] = "https://ats-externe.example/offre"
    (config.dossier_profil() / lba.FICHIER_FICHES).write_text(__import__("json").dumps(memoire), encoding="utf-8")
    base.maj_statut(base.creer_candidature(ids[0], "lba"), "echec", erreur="x")
    assert [c[0] for c in lba_postuler._cibles(10)] == [ids[2]]


def test_essai_remplit_capture_et_n_envoie_pas(monkeypatch, cfg, profil, messages):
    ids = remplir_base(cfg)
    cfg["candidat"].update(formation="Master Data " + "x" * 300, ecole="Université", rythme="2 semaines",
                           duree_contrat="24 mois", debut_contrat="septembre 2027")
    page = FaussePage()
    lancements = installer_faux_playwright(monkeypatch, page)
    bilan = lba_postuler.postuler(cfg, 2, True, False)
    assert bilan["essais"] == 2 and bilan["postees"] == 0 and bilan["echecs"] == 0
    assert '[data-testid="candidature-not-sent"]' not in page.clics
    assert page.champs["#applicant_email"] == "camille.martin@example.com"
    assert page.champs["#applicant_phone"] == "0600000000"
    assert len(page.champs["#applicant_formation_description"]) == 200
    assert page.champs["#applicant_rythm_description"] == "2 semaines"
    assert page.champs["#applicant_contract_duration select"] == "24 mois"
    assert page.fichiers and page.fichiers[0].endswith("CV.pdf")
    assert sorted(p.name for p in (profil / "captures").iterdir()) == ["acme_essai.png", "beta_essai.png"]
    assert (profil / "lettres" / "lba_acme.txt").read_text(encoding="utf-8").endswith("Camille Martin")
    assert ("lance", {"headless": True}) in lancements and "ferme" in lancements
    assert not any(l[0] == "pause" for l in lancements if isinstance(l, tuple))
    stats = base.stats()
    assert stats["par_statut"]["essai"] == 2 and stats["lba_aujourdhui"] == 0
    assert not base.deja_contactee(ids[0])  # un essai ne bloque pas la vraie candidature


def test_envoi_reel_poste_note_et_respecte_la_limite(monkeypatch, cfg, messages):
    remplir_base(cfg, ("Acme", "Beta", "Gamma"))
    page = FaussePage()
    lancements = installer_faux_playwright(monkeypatch, page)
    bilan = lba_postuler.postuler(cfg, 2, False, True)
    assert bilan["postees"] == 2 and bilan["cibles"] == 2
    assert '[data-testid="candidature-not-sent"]' in page.clics
    assert base.stats()["lba_aujourdhui"] == 2
    assert ("lance", {"headless": False}) in lancements
    assert [l for l in lancements if isinstance(l, tuple) and l[0] == "pause"]  # une pause entre deux
    assert len([l for l in lancements if isinstance(l, tuple) and l[0] == "pause"]) == 1


def test_echec_message_refuse_par_la_relecture(monkeypatch, cfg):
    remplir_base(cfg, ("Acme",))
    monkeypatch.setattr(lba_postuler.redaction, "message_lba", lambda ent, cfg, offre: "Bonjour [Nom]")
    installer_faux_playwright(monkeypatch, FaussePage())
    bilan = lba_postuler.postuler(cfg, None, False, False)
    assert bilan["echecs"] == 1 and bilan["postees"] == 0
    with base.connexion() as conn:
        assert conn.execute("SELECT COUNT(*) FROM candidatures").fetchone()[0] == 0
    # La fiche n'est pas condamnée : elle reste candidate au prochain lancement.
    assert len(lba_postuler._cibles(10)) == 1


def test_ia_en_panne_arrete_sans_condamner_les_fiches(monkeypatch, cfg):
    remplir_base(cfg, ("Acme", "Globex"))
    appels = []

    def panne(ent, cfg, offre):
        appels.append(ent["nom"])
        raise lba_postuler.ia.ErreurIA("claude introuvable")
    monkeypatch.setattr(lba_postuler.redaction, "message_lba", panne)
    installer_faux_playwright(monkeypatch, FaussePage())
    bilan = lba_postuler.postuler(cfg, None, False, False)
    assert bilan["motif"] == "ia_indisponible" and len(appels) == 1
    assert len(lba_postuler._cibles(10)) == 2


def test_aucune_fiche_n_ouvre_pas_le_navigateur(monkeypatch, cfg):
    monkeypatch.setattr(lba_postuler, "_sync_playwright", lambda: pytest.fail("navigateur lancé"))
    assert lba_postuler.postuler(cfg, None, False, False)["motif"] == "aucune_fiche"


# ---- cli ------------------------------------------------------------------------------------------

def test_commande_lba_chercher_seulement(monkeypatch, cfg, capsys):
    donnees = {"jobs": [element("Acme", "11111111100011")]}
    monkeypatch.setattr(lba.requests, "get", faux_get(donnees))
    monkeypatch.setattr(lba_postuler, "_sync_playwright", lambda: pytest.fail("navigateur lancé"))
    assert cli.main(["lba", "--chercher-seulement"]) == 0
    assert "1 fiches" in capsys.readouterr().out
    assert base.stats()["entreprises"] == 1


def test_commande_lba_sans_jeton(profil, capsys):
    c = yaml.safe_load((profil / "config.yaml").read_text(encoding="utf-8"))
    c["lba"]["jeton"] = ""
    (profil / "config.yaml").write_text(yaml.safe_dump(c), encoding="utf-8")
    assert cli.main(["lba"]) == 1
    assert "LBA_API_TOKEN" in capsys.readouterr().err
