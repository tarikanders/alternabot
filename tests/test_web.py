"""Tests de web.py : téléchargement borné en temps et en taille (aucun réseau, requests simulé)."""
import threading
import time

import pytest

from alternabot import emails, web
from tests.conftest import page


def test_page_lue(monkeypatch):
    monkeypatch.setattr(web.requests, "get", lambda url, **kw: page("<p>Bonjour</p>"))
    assert web.telecharger("https://acme.example") == "<p>Bonjour</p>"


def test_erreur_http_rend_none(monkeypatch):
    monkeypatch.setattr(web.requests, "get", lambda url, **kw: page("", ok=False))
    assert web.telecharger("https://acme.example") is None


def test_erreur_reseau_leve_injoignable(monkeypatch):
    def get(url, **kw):
        raise web.requests.ConnectionError("hors ligne")

    monkeypatch.setattr(web.requests, "get", get)
    with pytest.raises(web.Injoignable):
        web.telecharger("https://acme.example")


def test_site_qui_ne_repond_pas_coupe_au_delai_total(monkeypatch):
    """Le cas dtek.fr : requests reste bloqué (DNS, octets au compte-gouttes) malgré son timeout."""
    libere = threading.Event()
    monkeypatch.setattr(web.requests, "get", lambda url, **kw: (libere.wait(5), page("trop tard"))[1])
    debut = time.monotonic()
    with pytest.raises(web.Injoignable):
        web.telecharger("https://lent.example", delai_s=0.3)
    assert time.monotonic() - debut < 1.5
    libere.set()


def test_corps_qui_coule_sans_fin_coupe_au_delai(monkeypatch):
    def goutte_a_goutte(taille):
        while True:
            time.sleep(0.05)
            yield b"x"

    reponse = page("")
    reponse.iter_content = goutte_a_goutte
    monkeypatch.setattr(web.requests, "get", lambda url, **kw: reponse)
    debut = time.monotonic()
    with pytest.raises(web.Injoignable):
        web.telecharger("https://lent.example", delai_s=0.3)
    assert time.monotonic() - debut < 1.5


def test_page_trop_lourde_coupee(monkeypatch):
    monkeypatch.setattr(web.requests, "get", lambda url, **kw: page("a" * 5000))
    assert len(web.telecharger("https://acme.example", max_octets=1000)) <= 1000 + web.MORCEAU


def test_requete_en_flux_avec_timeout(monkeypatch):
    vus = []
    monkeypatch.setattr(web.requests, "get", lambda url, **kw: vus.append(kw) or page("ok"))
    web.telecharger("https://acme.example")
    assert vus[0]["stream"] is True and vus[0]["timeout"] and vus[0]["headers"]["User-Agent"]


def test_site_injoignable_abandonne_des_la_premiere_page(monkeypatch):
    demandees = []

    def get(url, **kw):
        demandees.append(url)
        raise web.requests.ConnectTimeout("injoignable")

    monkeypatch.setattr(web.requests, "get", get)
    monkeypatch.setattr(emails.time, "sleep", lambda s: None)
    assert emails.adresses_du_site("https://mort.example") == []
    assert demandees == ["https://mort.example"]
