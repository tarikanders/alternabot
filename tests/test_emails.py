"""Tests de emails.py (aucun réseau : requests et dns.resolver sont simulés)."""
import csv
from types import SimpleNamespace

import dns.resolver
import pytest

from alternabot import base, cli, config, emails, web
from tests.conftest import page

ACCUEIL = """<html><body>
<a href="mailto:contact@acme.example?subject=Bonjour">Nous écrire</a>
<p>RH : rh [at] acme.example</p>
<img src="logo@2x.png"><p>logo@2x.png</p>
<p>info@example.com noreply@acme.example</p>
<a href="mailto:Direction@Acme.Example">Direction</a>
</body></html>"""


@pytest.fixture(autouse=True)
def reseau_neutre(monkeypatch):
    monkeypatch.setattr(emails.time, "sleep", lambda s: None)
    emails._cache_mx.clear()
    yield
    emails._cache_mx.clear()


@pytest.fixture
def cfg(profil):
    return config.charger()


def site_simule(monkeypatch, pages):
    """requests.get rend la page dont l'URL est dans `pages` (chemin -> html), 404 sinon."""
    vus = []

    def get(url, **kw):
        vus.append((url, kw))
        for chemin, html in pages.items():
            if url == "https://acme.example" + chemin:
                return page(html)
        return SimpleNamespace(ok=False, text="")

    monkeypatch.setattr(web.requests, "get", get)
    return vus


def dns_simule(monkeypatch, mx=(), sans_mx=(), inexistants=()):
    """Domaines avec MX, domaines avec seulement une adresse A, domaines inexistants."""
    appels = []

    def resolve(domaine, type_, **kw):
        appels.append((domaine, type_))
        if domaine in inexistants or (domaine not in mx and domaine not in sans_mx):
            raise dns.resolver.NXDOMAIN()
        if type_ == "MX":
            if domaine in mx:
                return [SimpleNamespace(exchange=f"mx.{domaine}.")]
            raise dns.resolver.NoAnswer()
        return [SimpleNamespace(address="192.0.2.1")]

    monkeypatch.setattr(emails.dns.resolver, "resolve", resolve)
    return appels


# ---- lecture du site ------------------------------------------------------------------------------

def test_adresses_du_site_garde_les_vraies_adresses(monkeypatch):
    vus = site_simule(monkeypatch, {"": ACCUEIL})
    adresses = emails.adresses_du_site("acme.example/")
    assert set(adresses) == {"contact@acme.example", "rh@acme.example", "direction@acme.example"}
    # accueil puis les pages de contact, avec un User-Agent et un timeout
    assert [u for u, _ in vus] == ["https://acme.example" + p for p in emails.PAGES_CONTACT]
    assert vus[0][1]["headers"]["User-Agent"] and vus[0][1]["timeout"]


def test_adresses_du_site_lit_les_pages_contact_sans_doublon(monkeypatch):
    site_simule(monkeypatch, {"": ACCUEIL, "/recrutement": "<p>Écrire à recrutement@acme.example ou rh@acme.example</p>"})
    adresses = emails.adresses_du_site("https://acme.example")
    assert adresses.count("rh@acme.example") == 1 and "recrutement@acme.example" in adresses


def test_adresses_du_site_tolere_les_erreurs_reseau(monkeypatch):
    def get(url, **kw):
        raise web.requests.ConnectionError("hors ligne")

    monkeypatch.setattr(web.requests, "get", get)
    assert emails.adresses_du_site("https://acme.example") == []
    assert emails.adresses_du_site(None) == [] and emails.adresses_du_site("  ") == []


@pytest.mark.parametrize("adresse, attendu", [
    ("camille@acme.example", True), ("logo@2x.png", False), ("icone@3x.webp", False), ("noreply@acme.example", False),
    ("no-reply@acme.example", False), ("nom@example.com", False), ("a@acme.example", False),
    ("pas-une-adresse", False), ("x@sentry.io", False), ("contact@facebook.com", False),
])
def test_adresse_valide(adresse, attendu):
    assert emails.adresse_valide(adresse) is attendu


def test_adresses_obfusquees_simples():
    html = "<p>Ecrire a jean [at] acme.example ou marie (arobase) acme.example, ou paul [at] acme [dot] example</p>"
    assert set(emails._extraire(html)) == {"jean@acme.example", "marie@acme.example", "paul@acme.example"}


# ---- motifs, score, DNS ---------------------------------------------------------------------------

def test_adresses_par_motif():
    assert emails.adresses_par_motif("acme.example") == [
        "contact@acme.example", "rh@acme.example", "recrutement@acme.example", "info@acme.example"]


def test_score_rh_avant_contact():
    ordre = ["rh@x.fr", "recrutement@x.fr", "emploi@x.fr", "jobs@x.fr", "talent@x.fr", "contact@x.fr", "info@x.fr",
             "direction@x.fr", "jean.dupont@x.fr"]
    assert sorted(reversed(ordre), key=emails.score) == ordre
    assert emails.score("drh.paris@x.fr") == emails.score("jean.dupont@x.fr")  # « rh » dans « drh » n'est pas un mot
    assert emails.score("rh.paris@x.fr") == emails.score("rh@x.fr")


def test_domaine_recoit_mx_a_nxdomain_et_cache(monkeypatch):
    appels = dns_simule(monkeypatch, mx=["avec-mx.example"], sans_mx=["sans-mx.example"])
    assert emails.domaine_recoit("avec-mx.example") is True
    assert emails.domaine_recoit("sans-mx.example") is True       # repli sur l'enregistrement A
    assert emails.domaine_recoit("inconnu.example") is False      # NXDOMAIN
    assert emails.domaine_recoit("") is False and emails.domaine_recoit(None) is False
    nb = len(appels)
    assert emails.domaine_recoit("avec-mx.example") is True
    assert len(appels) == nb  # cache


def test_domaine_recoit_mx_nul_et_panne_dns(monkeypatch):
    monkeypatch.setattr(emails.dns.resolver, "resolve", lambda d, t, **kw: [SimpleNamespace(exchange=".")])
    assert emails.domaine_recoit("nul.example") is False

    def panne(d, t, **kw):
        raise dns.resolver.NoNameservers()

    monkeypatch.setattr(emails.dns.resolver, "resolve", panne)
    assert emails.domaine_recoit("panne.example") is False
    assert "panne.example" not in emails._cache_mx  # pas de verdict mémorisé sur une panne passagère


# ---- trouver --------------------------------------------------------------------------------------

def test_trouver_prefere_le_site_et_les_adresses_rh(monkeypatch):
    site_simule(monkeypatch, {"": ACCUEIL})
    dns_simule(monkeypatch, mx=["acme.example"])
    assert emails.trouver({"site": "https://acme.example"}) == ("rh@acme.example", "site", "haute")


def test_trouver_ecarte_l_adresse_d_un_domaine_sans_mx(monkeypatch):
    site_simule(monkeypatch, {"": "<p>rh@mort.example contact@acme.example</p>"})
    dns_simule(monkeypatch, mx=["acme.example"], inexistants=["mort.example"])
    adresse, methode, _ = emails.trouver({"site": "https://acme.example"})
    assert (adresse, methode) == ("contact@acme.example", "site")


def test_trouver_ignore_l_adresse_de_l_hebergeur(monkeypatch):
    site_simule(monkeypatch, {"/mentions-legales": "<p>Hébergeur : contact@hebergeur.example</p>"})
    dns_simule(monkeypatch, mx=["acme.example", "hebergeur.example"])
    assert emails.trouver({"site": "https://acme.example"}) == ("contact@acme.example", "motif", "basse")


def test_trouver_boite_grand_public_confiance_moyenne(monkeypatch):
    site_simule(monkeypatch, {"": "<p>boulangerie.acme@gmail.com</p>"})
    dns_simule(monkeypatch, mx=["gmail.com", "acme.example"])
    assert emails.trouver({"site": "https://acme.example"}) == ("boulangerie.acme@gmail.com", "site", "moyenne")


def test_trouver_motif_quand_le_site_ne_donne_rien(monkeypatch):
    site_simule(monkeypatch, {"": "<p>Bienvenue</p>"})
    dns_simule(monkeypatch, mx=["acme.example"])
    assert emails.trouver({"site": "https://acme.example"}) == ("contact@acme.example", "motif", "basse")


def test_trouver_rien_sans_mx(monkeypatch):
    site_simule(monkeypatch, {"": "<p>Bienvenue</p>"})
    dns_simule(monkeypatch, inexistants=["acme.example"])
    assert emails.trouver({"site": "https://acme.example"}) == (None, "", "")


def test_trouver_ne_devine_rien_sur_une_page_de_reseau_social(monkeypatch):
    monkeypatch.setattr(web.requests, "get", lambda url, **kw: SimpleNamespace(ok=False, text=""))
    dns_simule(monkeypatch, mx=["facebook.com", "gmail.com"])
    assert emails.trouver({"site": "https://www.facebook.com/acme"})[0] is None
    assert emails.trouver({"site": "https://gmail.com"})[0] is None


# ---- trouver_tout et commande ---------------------------------------------------------------------

def test_trouver_tout_enregistre_et_note_les_sans_resultat(monkeypatch, cfg, profil):
    avec = base.ajouter_entreprise("Acme", "maps", source_id="maps:1", site="https://acme.example")
    sans_site = base.ajouter_entreprise("Sans Site", "maps", source_id="maps:2")
    mort = base.ajouter_entreprise("Mort", "maps", source_id="maps:3", site="https://mort.example",
                                   telephone="04 00 00 00 00", adresse="2 rue B, Lyon")
    deja = base.ajouter_entreprise("Deja", "maps", source_id="maps:4", site="https://deja.example")
    base.ajouter_email(deja, "x@deja.example")
    site_simule(monkeypatch, {"": ACCUEIL})
    dns_simule(monkeypatch, mx=["acme.example"], inexistants=["mort.example"])
    bilan = emails.trouver_tout(cfg)
    assert bilan == {"traitees": 2, "trouvees": 1, "a_la_main": 1}
    trouve = base.meilleur_email(avec)
    assert (trouve["email"], trouve["methode"], trouve["confiance"], trouve["verifie"]) == ("rh@acme.example", "site", "haute", 1)
    assert base.meilleur_email(sans_site) is None and base.meilleur_email(mort) is None
    with (profil / emails.FICHIER_A_LA_MAIN).open(encoding="utf-8", newline="") as f:
        lignes = list(csv.reader(f))
    assert lignes == [["nom", "site", "telephone", "adresse"],
                      ["Mort", "https://mort.example", "04 00 00 00 00", "2 rue B, Lyon"]]
    # relancer ne duplique pas la ligne
    emails.trouver_tout(cfg)
    with (profil / emails.FICHIER_A_LA_MAIN).open(encoding="utf-8", newline="") as f:
        assert len(list(csv.reader(f))) == 2


def test_trouver_tout_limite(monkeypatch, cfg):
    for i in range(3):
        base.ajouter_entreprise(f"E{i}", "maps", source_id=f"maps:{i}", site=f"https://e{i}.example")
    monkeypatch.setattr(web.requests, "get", lambda url, **kw: SimpleNamespace(ok=False, text=""))
    dns_simule(monkeypatch, mx=["e0.example", "e1.example", "e2.example"])
    assert emails.trouver_tout(cfg, limite=2)["traitees"] == 2


def test_commande_emails(monkeypatch, profil, capsys):
    base.ajouter_entreprise("Acme", "maps", source_id="maps:1", site="https://acme.example")
    site_simule(monkeypatch, {"": ACCUEIL})
    dns_simule(monkeypatch, mx=["acme.example"])
    assert cli.main(["emails", "--limite", "5"]) == 0
    assert "1 adresses trouvées sur 1 entreprises" in capsys.readouterr().out


def test_pages_contact_cherchees_a_la_racine_du_site(monkeypatch):
    demandees = []
    monkeypatch.setattr(emails, "_telecharger", lambda url: demandees.append(url) or None)
    monkeypatch.setattr(emails.time, "sleep", lambda s: None)
    emails.adresses_du_site("https://exemple.fr/agence-lyon/?utm_source=google")
    assert "https://exemple.fr/contact" in demandees
    assert all("utm_source" not in u and "agence-lyon" not in u for u in demandees)


def test_entreprise_sans_resultat_pas_retraitee(monkeypatch, cfg):
    """Une entreprise déjà cherchée sans succès ne bloque pas la file : la suivante passe au lancement d'après."""
    for i in range(2):
        base.ajouter_entreprise(f"E{i}", "maps", source_id=f"maps:{i}", site=f"https://e{i}.example")
    monkeypatch.setattr(web.requests, "get", lambda url, **kw: SimpleNamespace(ok=False, text=""))
    monkeypatch.setattr(emails.time, "sleep", lambda s: None)
    dns_simule(monkeypatch, inexistants=["e0.example", "e1.example"])
    vues = []
    monkeypatch.setattr(emails, "trouver", lambda e: vues.append(e["nom"]) or (None, "", ""))
    emails.trouver_tout(cfg, limite=1)
    emails.trouver_tout(cfg, limite=1)
    emails.trouver_tout(cfg, limite=1)
    assert vues == ["E0", "E1"]
