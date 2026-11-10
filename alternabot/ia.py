"""Rédaction par l'IA : Claude Code en ligne de commande (défaut) ou API Anthropic."""
import os
import re
import subprocess
import tempfile

from .config import obtenir

MODELE_PAR_DEFAUT = "claude-sonnet-5-5"
DELAI_CLAUDE_S = 180

# Tirets cadratin (U+2014) et demi-cadratin (U+2013), jamais le trait d'union ASCII.
_PUCE = re.compile(r"(?m)^[ \t]*[\u2014\u2013][ \t]*")
_INCISE = re.compile(r"\s*[\u2014\u2013]\s*")


class ErreurIA(RuntimeError):
    """Le moteur de rédaction n'a pas pu répondre."""


def sans_tirets(texte: str) -> str:
    """Retire les tirets cadratin et demi-cadratin (puce supprimée, incise remplacée par une virgule)."""
    texte = _PUCE.sub("", texte)
    texte = _INCISE.sub(", ", texte)
    texte = re.sub(r",\s*([.!?;:])", r"\1", texte)
    return re.sub(r",\s*,", ",", texte)


def _via_claude(systeme: str, consigne: str) -> str:
    """Appelle `claude -p` : prompt système en argument, consigne sur stdin, aucun outil ni fichier."""
    commande = ["claude", "-p", "--output-format", "text", "--system-prompt", systeme,
                "--tools", "", "--no-session-persistence"]
    try:
        # Lancé hors du dépôt pour ne pas y charger le CLAUDE.md d'installation.
        res = subprocess.run(commande, input=consigne, capture_output=True, text=True,
                             timeout=DELAI_CLAUDE_S, cwd=tempfile.gettempdir())
    except FileNotFoundError as e:
        raise ErreurIA("Commande claude introuvable : installe Claude Code ou passe ia.moteur: api") from e
    except subprocess.TimeoutExpired as e:
        raise ErreurIA(f"claude n'a pas répondu en {DELAI_CLAUDE_S} s") from e
    if res.returncode != 0:
        raise ErreurIA(f"claude a échoué (code {res.returncode}) : {(res.stderr or res.stdout).strip()[:300]}")
    return res.stdout


def _client(cle: str):
    import anthropic
    return anthropic.Anthropic(api_key=cle)


def _via_api(systeme: str, consigne: str, cfg: dict) -> str:
    cle = os.environ.get("ANTHROPIC_API_KEY")
    if not cle:
        raise ErreurIA("ANTHROPIC_API_KEY est vide : renseigne-la dans profil/.env ou passe ia.moteur: claude-cli")
    modele = obtenir(cfg, "ia.modele", MODELE_PAR_DEFAUT)
    reponse = _client(cle).messages.create(model=modele, max_tokens=2000, system=systeme,
                                           messages=[{"role": "user", "content": consigne}])
    return "".join(b.text for b in reponse.content if getattr(b, "type", "") == "text")


def rediger(systeme: str, consigne: str, cfg: dict) -> str:
    """Rédige un texte d'après le prompt système et la consigne, sans tirets cadratin."""
    moteur = obtenir(cfg, "ia.moteur", "claude-cli")
    if moteur == "claude-cli":
        texte = _via_claude(systeme, consigne)
    elif moteur == "api":
        texte = _via_api(systeme, consigne, cfg)
    else:
        raise ErreurIA(f"ia.moteur vaut {moteur!r} : choisis claude-cli ou api")
    texte = sans_tirets(texte.strip())
    if not texte:
        raise ErreurIA("Le moteur IA a rendu une réponse vide")
    return texte
