"""Fixtures : un profil complet et fictif dans un dossier temporaire."""
import os
import shutil
from pathlib import Path

import pytest
import yaml
from types import SimpleNamespace

RACINE = Path(__file__).resolve().parent.parent
VARIABLES = ("ANTHROPIC_API_KEY", "LBA_API_TOKEN", "GOOGLE_MAPS_API_KEY", "SERPAPI_KEY", "GMAIL_MDP_1",
             "ALTERNABOT_PROFIL")


@pytest.fixture(autouse=True)
def environnement_isole():
    """Chaque test part sans clé et retrouve l'environnement d'origine à la fin."""
    avant = dict(os.environ)
    for v in VARIABLES:
        os.environ.pop(v, None)
    yield
    os.environ.clear()
    os.environ.update(avant)


@pytest.fixture
def profil_exemple(tmp_path, monkeypatch):
    """Copie brute de profil.exemple/ (rien n'est rempli)."""
    dossier = tmp_path / "profil"
    shutil.copytree(RACINE / "profil.exemple", dossier)
    monkeypatch.setenv("ALTERNABOT_PROFIL", str(dossier))
    return dossier


@pytest.fixture
def profil(profil_exemple):
    """Profil rempli avec des valeurs fictives : `verifier_config` n'y trouve rien à redire."""
    chemin = profil_exemple / "config.yaml"
    cfg = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    cfg["candidat"].update(prenom="Camille", nom="Martin", email="camille.martin@example.com",
                           telephone="+33 6 00 00 00 00")
    cfg["gmail"]["comptes"][0]["email"] = "camille.martin@example.com"
    chemin.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    (profil_exemple / "CV.pdf").write_bytes(b"%PDF-1.4 faux cv")
    (profil_exemple / "profil.md").write_text("# Mon profil\n\nCamille Martin, étudiante à Lyon.\n", encoding="utf-8")
    (profil_exemple / ".env").write_text(
        "LBA_API_TOKEN=jeton-test\nGOOGLE_MAPS_API_KEY=cle-test\nGMAIL_MDP_1=mdp-test\n", encoding="utf-8")
    return profil_exemple


def page(texte: str, ok: bool = True) -> SimpleNamespace:
    """Réponse requests simulée, lisible en flux comme le fait web.telecharger."""
    donnees = texte.encode("utf-8")
    return SimpleNamespace(ok=ok, text=texte, encoding="utf-8", close=lambda: None,
                           iter_content=lambda taille: (donnees[i:i + taille] for i in range(0, len(donnees), taille)))
