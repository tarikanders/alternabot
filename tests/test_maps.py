"""Tests de maps.py (aucun réseau : googlemaps.Client est remplacé par un faux client)."""
import pytest
import yaml
from googlemaps.exceptions import ApiError

from alternabot import base, config, maps


class FauxClient:
    """Rend des pages de lieux par requête et des détails par place_id, et note chaque appel."""

    def __init__(self, pages=None, details=None, erreurs=None):
        self.pages = pages or {}      # mot-clé -> liste de pages (dict {"results": [...]})
        self.details = details or {}
        self.erreurs = erreurs or {}  # mot-clé -> exception levée à la recherche
        self.appels = []
        self.jetons = {}

    def geocode(self, ville):
        self.appels.append(("geocode", ville))
        return [{"geometry": {"location": {"lat": 48.0, "lng": 7.0}}}] if ville != "Nullepart" else []

    def places_nearby(self, **kw):
        if "page_token" in kw:
            self.appels.append(("page", kw["page_token"]))
            return self.jetons[kw["page_token"]]
        mot = kw["keyword"]
        self.appels.append(("recherche", mot, kw["location"], kw["radius"]))
        if mot in self.erreurs:
            raise self.erreurs[mot]
        pages = self.pages.get(mot, [{"results": []}])
        for i, page in enumerate(pages[:-1]):
            page["next_page_token"] = f"{mot}-{i + 1}"
            self.jetons[f"{mot}-{i + 1}"] = pages[i + 1]
        return pages[0]

    def place(self, place_id, **kw):
        self.appels.append(("detail", place_id))
        return {"result": self.details.get(place_id, {})}

    def compte(self, nature):
        return sum(1 for a in self.appels if a[0] == nature)


@pytest.fixture(autouse=True)
def sans_attente(monkeypatch):
    monkeypatch.setattr(maps.time, "sleep", lambda s: None)


@pytest.fixture
def cfg(profil):
    return config.charger()


def brancher(monkeypatch, client):
    monkeypatch.setattr(maps, "_client", lambda cle: client)
    return client


def lieu(pid, nom="Acme"):
    return {"place_id": pid, "name": nom}


def test_sans_cle_erreur_claire_avec_le_lien_de_la_doc(profil):
    chemin = profil / "config.yaml"
    c = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    c["maps"]["cle"] = ""
    chemin.write_text(yaml.safe_dump(c), encoding="utf-8")
    with pytest.raises(config.ErreurConfig) as e:
        maps.chercher_entreprises(config.charger())
    assert "developers.google.com" in str(e.value) and "GOOGLE_MAPS_API_KEY" in str(e.value)


def test_enregistre_avec_et_sans_site(monkeypatch, cfg):
    client = brancher(monkeypatch, FauxClient(
        pages={"agence web": [{"results": [lieu("p1", "Acme"), lieu("p2", "Sans Site")]}]},
        details={"p1": {"name": "Acme SAS", "formatted_address": "1 rue A, Lyon", "website": "https://www.acme.example/",
                        "formatted_phone_number": "04 00 00 00 00"},
                 "p2": {"name": "Sans Site"}}))
    ids = maps.chercher_entreprises(cfg, secteurs=["agence web"])
    assert len(ids) == 2
    with base.connexion() as conn:
        acme, sans = conn.execute("SELECT * FROM entreprises ORDER BY id").fetchall()
    assert (acme["nom"], acme["source"], acme["source_id"], acme["secteur"]) == ("Acme SAS", "maps", "maps:p1", "agence web")
    assert (acme["domaine"], acme["telephone"], acme["adresse"], acme["ville"]) == (
        "acme.example", "04 00 00 00 00", "1 rue A, Lyon", "Lyon")
    assert sans["site"] is None and sans["nom"] == "Sans Site"
    # la ville et les coordonnées du profil servent : aucun géocodage
    assert client.compte("geocode") == 0
    assert client.appels[0][2:] == ((45.764, 4.8357), 30000)


def test_place_deja_en_base_n_est_pas_repayee(monkeypatch, cfg):
    base.ajouter_entreprise("Acme", "maps", source_id="maps:p1")
    client = brancher(monkeypatch, FauxClient(pages={"agence web": [{"results": [lieu("p1"), lieu("p2")]}]},
                                              details={"p2": {"name": "Beta"}}))
    ids = maps.chercher_entreprises(cfg, secteurs=["agence web"])
    assert len(ids) == 1
    assert [a for a in client.appels if a[0] == "detail"] == [("detail", "p2")]


def test_une_ville_differente_est_geocodee(monkeypatch, cfg):
    client = brancher(monkeypatch, FauxClient(pages={"esn": [{"results": []}]}))
    maps.chercher_entreprises(cfg, ville="Nantes", secteurs=["esn"], rayon_km=10)
    assert ("geocode", "Nantes") in client.appels
    assert client.appels[1][2:] == ((48.0, 7.0), 10000)


def test_coordonnees_absentes_geocode_la_ville_du_profil(monkeypatch, profil):
    chemin = profil / "config.yaml"
    c = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    c["recherche"].pop("latitude")
    chemin.write_text(yaml.safe_dump(c), encoding="utf-8")
    client = brancher(monkeypatch, FauxClient(pages={"esn": [{"results": []}]}))
    maps.chercher_entreprises(config.charger(), secteurs=["esn"])
    assert ("geocode", "Lyon") in client.appels


def test_ville_introuvable(monkeypatch, cfg):
    brancher(monkeypatch, FauxClient())
    with pytest.raises(config.ErreurConfig) as e:
        maps.chercher_entreprises(cfg, ville="Nullepart", secteurs=["esn"])
    assert "Nullepart" in str(e.value)


def test_pages_suivantes_lues(monkeypatch, cfg):
    client = brancher(monkeypatch, FauxClient(
        pages={"esn": [{"results": [lieu("p1")]}, {"results": [lieu("p2")]}]},
        details={"p1": {"name": "A"}, "p2": {"name": "B"}}))
    assert len(maps.chercher_entreprises(cfg, secteurs=["esn"])) == 2
    assert client.compte("page") == 1


def test_budget_stoppe_avant_le_depassement_et_garde_l_acquis(monkeypatch, cfg, capsys):
    # recherche 0,032 + détail 0,025 = 0,057 : un seul lieu tient dans 0,06 $
    client = brancher(monkeypatch, FauxClient(
        pages={"esn": [{"results": [lieu("p1"), lieu("p2"), lieu("p3")]}]},
        details={"p1": {"name": "A"}, "p2": {"name": "B"}, "p3": {"name": "C"}}))
    ids = maps.chercher_entreprises(cfg, secteurs=["esn", "autre"], budget_usd=0.06)
    assert len(ids) == 1 and base.stats()["entreprises"] == 1
    assert client.compte("detail") == 1
    assert client.compte("recherche") == 1  # le second secteur n'est même pas lancé
    assert "budget" in capsys.readouterr().out


def test_budget_du_profil_par_defaut(monkeypatch, cfg, capsys):
    brancher(monkeypatch, FauxClient(pages={"esn": [{"results": []}]}))
    maps.chercher_entreprises(cfg, secteurs=["esn"])
    assert "sur 5.00 $" in capsys.readouterr().out


def test_erreur_api_sur_un_secteur_passe_au_suivant(monkeypatch, cfg):
    client = brancher(monkeypatch, FauxClient(
        pages={"bon": [{"results": [lieu("p1")]}]}, details={"p1": {"name": "A"}},
        erreurs={"mauvais": ApiError("UNKNOWN_ERROR")}))
    assert len(maps.chercher_entreprises(cfg, secteurs=["mauvais", "bon"])) == 1
    assert client.compte("recherche") == 2


def test_cle_refusee_est_fatale(monkeypatch, cfg):
    brancher(monkeypatch, FauxClient(erreurs={"esn": ApiError("REQUEST_DENIED")}))
    with pytest.raises(config.ErreurConfig):
        maps.chercher_entreprises(cfg, secteurs=["esn"])


def test_jeton_de_page_invalide_est_reessaye_puis_abandonne(monkeypatch, cfg):
    client = brancher(monkeypatch, FauxClient(pages={"esn": [{"results": [lieu("p1")]}, {"results": []}]},
                                              details={"p1": {"name": "A"}}))
    appel = client.places_nearby

    def nearby(**kw):
        if "page_token" in kw:
            client.appels.append(("page", kw["page_token"]))
            raise ApiError("INVALID_REQUEST")
        return appel(**kw)

    client.places_nearby = nearby
    assert len(maps.chercher_entreprises(cfg, secteurs=["esn"])) == 1
    assert client.compte("page") == maps.ESSAIS_PAGE


def test_commande_entreprises_compte_les_sans_site(monkeypatch, profil, capsys):
    from alternabot import cli
    brancher(monkeypatch, FauxClient(pages={"esn": [{"results": [lieu("p1"), lieu("p2")]}]},
                                     details={"p1": {"name": "A", "website": "https://a.example"}, "p2": {"name": "B"}}))
    assert cli.main(["entreprises", "--secteurs", "esn", "--budget", "1"]) == 0
    assert "2 entreprises enregistrées, dont 1 sans site" in capsys.readouterr().out
