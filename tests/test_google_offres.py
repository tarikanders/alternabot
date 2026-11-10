"""Tests de google_offres.py (aucun réseau : requests.get est remplacé)."""
from types import SimpleNamespace

import pytest

from alternabot import base, cli, config, google_offres, ia, redaction, web
from alternabot.google_offres import ErreurSerpApi

CADRATIN = chr(0x2014)
DEMI = chr(0x2013)


def offre_brute(entreprise="Acme SAS", titre="Alternance développeur data", lieu="Lyon, France", job_id="j1", lien="https://emploi.example/1"):
    return {"title": titre, "company_name": entreprise, "location": lieu, "job_id": job_id, "via": "via Indeed",
            "share_link": "https://www.google.com/search?q=x",
            "apply_options": [{"title": "Indeed", "link": lien}, {"title": "Autre", "link": "https://autre.example"}]}


def reponse(donnees, statut=200):
    return SimpleNamespace(status_code=statut, ok=statut < 400, json=lambda: donnees)


@pytest.fixture
def cfg(profil):
    (profil / ".env").write_text((profil / ".env").read_text(encoding="utf-8") + "SERPAPI_KEY=serp-test\n", encoding="utf-8")
    return config.charger()


class FauxMaps:
    """Client Google Maps simulé : `sites` associe un nom d'entreprise cherché à son site."""

    def __init__(self, sites=None):
        self.sites, self.appels = sites or {}, []

    def find_place(self, texte, type_, fields=None, language=None):
        self.appels.append(texte)
        for nom, site in self.sites.items():
            if texte.startswith(nom):
                return {"candidates": [{"place_id": nom, "name": nom}]}
        return {"candidates": []}

    def place(self, place_id, fields=None, language=None):
        return {"result": {"website": self.sites[place_id]}}


@pytest.fixture(autouse=True)
def maps_simule(monkeypatch):
    client = FauxMaps()
    monkeypatch.setattr(google_offres.maps, "_client", lambda cle: client)
    return client


def brancher(monkeypatch, donnees, statut=200):
    appels = []

    def get(url, **kw):
        appels.append((url, kw))
        return reponse(donnees, statut)

    monkeypatch.setattr(google_offres.requests, "get", get)
    return appels


def test_sans_cle_erreur_nommant_serpapi_key(profil, monkeypatch):
    appels = brancher(monkeypatch, {})
    with pytest.raises(config.ErreurConfig) as e:
        google_offres.chercher_offres(config.charger())
    assert "SERPAPI_KEY" in str(e.value) and appels == []


def test_requete_par_defaut_depuis_la_config(cfg):
    assert google_offres.requete_par_defaut(cfg) == "alternance data développeur Lyon"


def test_offres_normalisees_et_une_seule_requete(monkeypatch, cfg):
    appels = brancher(monkeypatch, {"jobs_results": [
        offre_brute(), offre_brute(job_id="j1"), {"title": "Sans entreprise"}, offre_brute("Beta", "Alternant BI", None, "j2")]})
    offres = google_offres.chercher_offres(cfg)
    assert offres == [
        {"entreprise": "Acme SAS", "titre": "Alternance développeur data", "lien": "https://emploi.example/1",
         "lieu": "Lyon, France", "source_id": "google:j1"},
        {"entreprise": "Beta", "titre": "Alternant BI", "lien": "https://emploi.example/1", "lieu": None,
         "source_id": "google:j2"}]
    assert len(appels) == 1
    url, kw = appels[0]
    assert url == "https://serpapi.com/search.json" and kw["timeout"]
    assert kw["params"] == {"engine": "google_jobs", "q": "alternance data développeur Lyon", "hl": "fr", "gl": "fr",
                            "api_key": "serp-test"}


def test_requete_donnee_et_lien_de_repli(monkeypatch, cfg):
    brute = offre_brute()
    brute.pop("apply_options")
    brute.pop("job_id")
    appels = brancher(monkeypatch, {"jobs_results": [brute]})
    (offre,) = google_offres.chercher_offres(cfg, "alternance data Strasbourg")
    assert appels[0][1]["params"]["q"] == "alternance data Strasbourg"
    assert offre["lien"] == "https://www.google.com/search?q=x" and offre["source_id"].startswith("google:")


def test_aucun_resultat_n_est_pas_une_erreur(monkeypatch, cfg):
    brancher(monkeypatch, {"error": "Google hasn't returned any results for this query."})
    assert google_offres.chercher_offres(cfg) == []


@pytest.mark.parametrize("statut,mot", [(401, "SERPAPI_KEY"), (429, "quota"), (500, "HTTP 500")])
def test_erreurs_http(monkeypatch, cfg, statut, mot):
    brancher(monkeypatch, None, statut)
    with pytest.raises(ErreurSerpApi) as e:
        google_offres.chercher_offres(cfg)
    assert mot in str(e.value)


def test_erreur_serpapi_dans_la_reponse(monkeypatch, cfg):
    brancher(monkeypatch, {"error": "Invalid API key."})
    with pytest.raises(ErreurSerpApi) as e:
        google_offres.chercher_offres(cfg)
    assert "Invalid API key" in str(e.value)


def test_reseau_coupe(monkeypatch, cfg):
    def get(url, **kw):
        raise google_offres.requests.ConnectionError("hors ligne")

    monkeypatch.setattr(google_offres.requests, "get", get)
    with pytest.raises(ErreurSerpApi):
        google_offres.chercher_offres(cfg)


def test_enregistre_entreprise_google_et_offre(monkeypatch, cfg):
    brancher(monkeypatch, {"jobs_results": [offre_brute(), offre_brute("Acme SAS", "Alternant BI", job_id="j9")]})
    bilan = google_offres.trouver_offres(cfg)
    assert bilan["trouvees"] == 2 and len(bilan["gardees"]) == 1
    with base.connexion() as conn:
        (e,) = conn.execute("SELECT * FROM entreprises").fetchall()
        offres = conn.execute("SELECT * FROM offres ORDER BY id").fetchall()
    assert (e["nom"], e["source"], e["site"]) == ("Acme SAS", "google", None)
    assert [(o["entreprise_id"], o["titre"], o["lieu"], o["lien"]) for o in offres] == [
        (e["id"], "Alternance développeur data", "Lyon, France", "https://emploi.example/1"),
        (e["id"], "Alternant BI", "Lyon, France", "https://emploi.example/1")]
    assert base.offre_de(e["id"])["titre"] == "Alternant BI"


def test_relancer_ne_double_rien(monkeypatch, cfg):
    brancher(monkeypatch, {"jobs_results": [offre_brute()]})
    google_offres.trouver_offres(cfg)
    bilan = google_offres.trouver_offres(cfg)
    assert bilan["gardees"] == [] and bilan["deja_en_base"] == 1
    with base.connexion() as conn:
        assert conn.execute("SELECT COUNT(*) FROM offres").fetchone()[0] == 1


def test_entreprise_deja_contactee_sautee(monkeypatch, cfg):
    contactee = base.ajouter_entreprise("Acme", "maps", source_id="maps:1", site="https://acme.example")
    base.maj_statut(base.creer_candidature(contactee, "email", email_utilise="rh@acme.example"), "envoyee")
    brancher(monkeypatch, {"jobs_results": [offre_brute("Acme SAS"), offre_brute("Beta", job_id="j2")]})
    bilan = google_offres.trouver_offres(cfg)
    assert bilan["deja_contactees"] == 1 and len(bilan["gardees"]) == 1
    assert base.offre_de(contactee) is None
    assert base.stats()["entreprises"] == 2


def test_entreprise_connue_non_contactee_recoit_l_offre_sans_doublon(monkeypatch, cfg):
    connue = base.ajouter_entreprise("Acme", "maps", source_id="maps:1", site="https://acme.example")
    brancher(monkeypatch, {"jobs_results": [offre_brute("ACME SAS")]})
    bilan = google_offres.trouver_offres(cfg)
    assert bilan["gardees"] == [connue] and base.stats()["entreprises"] == 1
    assert base.offre_de(connue)["titre"] == "Alternance développeur data"


def test_limite(monkeypatch, cfg):
    brancher(monkeypatch, {"jobs_results": [offre_brute(f"Ent{n}x", job_id=f"j{n}") for n in range(5)]})
    assert len(google_offres.trouver_offres(cfg, limite=2)["gardees"]) == 2


# ---- Le mail cite l'offre --------------------------------------------------------------------------

TEXTE_MAIL = "Bonjour,\n\n" + ("Votre travail dans le conseil data m'intéresse beaucoup. " * 20).strip() + "\n\nCordialement,"


def ia_espion(monkeypatch):
    consignes = []
    monkeypatch.setattr(redaction.ia, "rediger", lambda s, c, cfg_: consignes.append(c) or TEXTE_MAIL)
    monkeypatch.setattr(web.requests, "get", lambda url, **kw: SimpleNamespace(
        ok=False, text="", encoding="utf-8", close=lambda: None, iter_content=lambda t: iter(())))
    return consignes


def test_mail_spontane_cite_le_titre_de_l_offre(monkeypatch, cfg):
    consignes = ia_espion(monkeypatch)
    redaction.mail_spontane({"nom": "Acme"}, cfg, {"titre": "Alternance développeur data", "lieu": "Lyon",
                                                  "lien": "https://emploi.example/1"})
    assert "Alternance développeur data" in consignes[0] and "https://emploi.example/1" in consignes[0]


def test_mail_prepare_pour_une_entreprise_google_cite_l_offre(monkeypatch, cfg):
    consignes = ia_espion(monkeypatch)
    brancher(monkeypatch, {"jobs_results": [offre_brute("Acme Conseil")]})
    (identifiant,) = google_offres.trouver_offres(cfg)["gardees"]
    base.ajouter_email(identifiant, "rh@acme-conseil.example", methode="site", confiance="haute")
    assert redaction.preparer(cfg) == 1
    assert "Alternance développeur data" in consignes[0]


def test_commande_offres(monkeypatch, profil, capsys):
    (profil / ".env").write_text((profil / ".env").read_text(encoding="utf-8") + "SERPAPI_KEY=serp-test\n", encoding="utf-8")
    appels = brancher(monkeypatch, {"jobs_results": [offre_brute(), offre_brute("Beta", job_id="j2")]})
    assert cli.main(["offres", "--requete", "alternance data Strasbourg", "--limite", "1"]) == 0
    sortie = capsys.readouterr().out
    assert "2 offres trouvées : 1 entreprises gardées" in sortie
    assert appels[0][1]["params"]["q"] == "alternance data Strasbourg"


def test_commande_offres_sans_cle(monkeypatch, profil, capsys):
    brancher(monkeypatch, {})
    assert cli.main(["offres"]) == 1
    assert "SERPAPI_KEY" in capsys.readouterr().err


def test_aucun_tiret_dans_les_textes_du_module():
    source = open(google_offres.__file__, encoding="utf-8").read()
    assert CADRATIN not in source and DEMI not in source


def test_site_trouve_par_google_maps(monkeypatch, cfg, maps_simule):
    maps_simule.sites = {"Acme SAS": "https://www.acme-conseil.example/"}
    brancher(monkeypatch, {"jobs_results": [offre_brute(lien="https://fr.indeed.com/viewjob?jk=1")]})
    bilan = google_offres.trouver_offres(cfg)
    assert bilan["sites"] == 1
    with base.connexion() as conn:
        site, domaine = conn.execute("SELECT site, domaine FROM entreprises WHERE id = ?", (bilan["gardees"][0],)).fetchone()
    assert (site, domaine) == ("https://www.acme-conseil.example/", "acme-conseil.example")
    assert maps_simule.appels == ["Acme SAS Lyon, France"]


def test_site_par_le_lien_de_l_offre_sauf_site_d_emploi(monkeypatch, cfg):
    brancher(monkeypatch, {"jobs_results": [
        offre_brute("Acme", job_id="j1", lien="https://carrieres.acme.example/offres/42?src=google"),
        offre_brute("Beta", job_id="j2", lien="https://fr.indeed.com/viewjob?jk=2"),
        offre_brute("Gamma", job_id="j3", lien="https://gamma.teamtailor.com/jobs/1"),
        offre_brute("Delta", job_id="j4", lien="https://www.welcometothejungle.com/fr/companies/delta")]})
    bilan = google_offres.trouver_offres(cfg)
    with base.connexion() as conn:
        sites = dict(conn.execute("SELECT nom, site FROM entreprises").fetchall())
    assert sites == {"Acme": "https://carrieres.acme.example", "Beta": None, "Gamma": None, "Delta": None}
    assert bilan["sites"] == 1


def test_sans_cle_maps_aucun_appel_maps(monkeypatch, cfg, maps_simule):
    cfg["maps"]["cle"] = ""
    brancher(monkeypatch, {"jobs_results": [offre_brute(lien="https://fr.indeed.com/viewjob?jk=1")]})
    assert google_offres.trouver_offres(cfg)["sites"] == 0
    assert maps_simule.appels == []


def test_budget_maps_respecte(monkeypatch, cfg, maps_simule):
    cfg["maps"]["budget_usd"] = 0.05  # une recherche et un détail : 0,042 $
    maps_simule.sites = {"A": "https://a.example", "B": "https://b.example"}
    brancher(monkeypatch, {"jobs_results": [offre_brute("A", job_id="1", lien="https://fr.indeed.com/1"),
                                            offre_brute("B", job_id="2", lien="https://fr.indeed.com/2")]})
    assert google_offres.trouver_offres(cfg)["sites"] == 1
    assert maps_simule.appels == ["A Lyon, France"]
