"""Tests de ia.py (aucun appel réseau, claude et l'API sont simulés)."""
import subprocess
from types import SimpleNamespace

import pytest

from alternabot import ia

CADRATIN = chr(0x2014)
DEMI = chr(0x2013)


def faux_run(sortie="Bonjour", code=0, stderr=""):
    appels = []

    def run(commande, **kw):
        appels.append((commande, kw))
        return SimpleNamespace(returncode=code, stdout=sortie, stderr=stderr)
    run.appels = appels
    return run


def test_sans_tirets_incise_et_puce():
    texte = f"Je postule {CADRATIN} avec envie {DEMI} chez vous.\n{CADRATIN} une puce\n{DEMI}Autre"
    assert ia.sans_tirets(texte) == "Je postule, avec envie, chez vous.\nune puce\nAutre"


def test_sans_tirets_garde_le_trait_d_union():
    assert ia.sans_tirets("Bas-Rhin, c'est-à-dire e-commerce") == "Bas-Rhin, c'est-à-dire e-commerce"


def test_sans_tirets_fin_de_phrase():
    assert ia.sans_tirets(f"Merci {CADRATIN}.") == "Merci."


def test_claude_cli_appelle_claude_p_sans_tirets(monkeypatch):
    run = faux_run(f"Bonjour {CADRATIN} Camille\n")
    monkeypatch.setattr(ia.subprocess, "run", run)
    texte = ia.rediger("Tu rédiges.", "Écris un mail.", {"ia": {"moteur": "claude-cli"}})
    assert texte == "Bonjour, Camille"
    commande, kw = run.appels[0]
    assert commande[:2] == ["claude", "-p"]
    assert "Tu rédiges." in commande
    assert kw["input"] == "Écris un mail."
    assert kw["timeout"] == 180


def test_claude_cli_est_le_moteur_par_defaut(monkeypatch):
    run = faux_run()
    monkeypatch.setattr(ia.subprocess, "run", run)
    assert ia.rediger("s", "c", {}) == "Bonjour"
    assert run.appels[0][0][0] == "claude"


def test_claude_introuvable(monkeypatch):
    def run(*a, **kw):
        raise FileNotFoundError("claude")
    monkeypatch.setattr(ia.subprocess, "run", run)
    with pytest.raises(ia.ErreurIA, match="installe Claude Code ou passe ia.moteur: api"):
        ia.rediger("s", "c", {})


def test_claude_delai_depasse(monkeypatch):
    def run(*a, **kw):
        raise subprocess.TimeoutExpired("claude", 180)
    monkeypatch.setattr(ia.subprocess, "run", run)
    with pytest.raises(ia.ErreurIA, match="180"):
        ia.rediger("s", "c", {})


def test_claude_code_de_sortie_non_nul(monkeypatch):
    monkeypatch.setattr(ia.subprocess, "run", faux_run("", 1, "pas connecté"))
    with pytest.raises(ia.ErreurIA, match="pas connecté"):
        ia.rediger("s", "c", {})


def test_reponse_vide(monkeypatch):
    monkeypatch.setattr(ia.subprocess, "run", faux_run("  \n"))
    with pytest.raises(ia.ErreurIA, match="vide"):
        ia.rediger("s", "c", {})


def test_api_sans_cle_nomme_la_variable():
    with pytest.raises(ia.ErreurIA, match="ANTHROPIC_API_KEY"):
        ia.rediger("s", "c", {"ia": {"moteur": "api"}})


def test_api_utilise_le_modele_configure(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-test")
    appels = []

    class FauxMessages:
        def create(self, **kw):
            appels.append(kw)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=f"Salut {CADRATIN} toi")])

    monkeypatch.setattr(ia, "_client", lambda cle: SimpleNamespace(messages=FauxMessages()))
    assert ia.rediger("sys", "cons", {"ia": {"moteur": "api", "modele": "mon-modele"}}) == "Salut, toi"
    assert appels[0]["model"] == "mon-modele" and appels[0]["system"] == "sys"
    assert appels[0]["messages"] == [{"role": "user", "content": "cons"}]


def test_api_modele_par_defaut(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle-test")
    appels = []
    monkeypatch.setattr(ia, "_client", lambda cle: SimpleNamespace(messages=SimpleNamespace(
        create=lambda **kw: appels.append(kw) or SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")]))))
    ia.rediger("s", "c", {"ia": {"moteur": "api"}})
    assert appels[0]["model"] == "claude-sonnet-5-5"


def test_moteur_inconnu():
    with pytest.raises(ia.ErreurIA, match="moteur"):
        ia.rediger("s", "c", {"ia": {"moteur": "autre"}})
