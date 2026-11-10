"""Ligne de commande : python -m alternabot <commande>.

Chaque module de commandes s'enregistre avec le décorateur `commande` ; pour ajouter un module,
mettre son nom dans MODULES_COMMANDES (il est importé au lancement).
"""
import argparse
import importlib
import os
import subprocess
import sys
from pathlib import Path

from . import base, config
from .ia import ErreurIA

COMMANDES: dict[str, dict] = {}
MODULES_COMMANDES: tuple[str, ...] = ("lba_postuler", "maps", "emails", "redaction", "envoi", "reponses", "google_offres", "routine")


def commande(nom: str, aide: str, arguments=None):
    """Enregistre une sous-commande. `arguments(parser)` déclare ses options ; la fonction reçoit les args."""
    def enregistrer(fonction):
        COMMANDES[nom] = {"aide": aide, "arguments": arguments, "executer": fonction}
        return fonction
    return enregistrer


def _problemes_claude(cfg: dict) -> list[str]:
    if config.obtenir(cfg, "ia.moteur", "claude-cli") != "claude-cli":
        return []
    try:
        res = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ["claude est introuvable : installe Claude Code ou passe ia.moteur: api"]
    return [] if res.returncode == 0 else ["`claude --version` échoue : relance Claude Code et connecte-toi"]


def _dossier_navigateurs() -> Path:
    """Cache des navigateurs Playwright (variable PLAYWRIGHT_BROWSERS_PATH, sinon le défaut du système)."""
    autre = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if autre and autre != "0":
        return Path(autre).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    if sys.platform.startswith("win"):
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def _problemes_chromium() -> list[str]:
    """Playwright importable et un Chromium téléchargé, sans lancer de navigateur."""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return ["Playwright est absent : pip install -r requirements.txt"]
    if any(_dossier_navigateurs().glob("chromium*")):
        return []
    return ["Chromium n'est pas installé : python -m playwright install chromium"]


@commande("verifier", "Vérifie le profil, la configuration et les outils")
def _verifier(args) -> int:
    points = []
    dossier = config.dossier_profil()
    points.append(("Dossier profil/ présent", [] if dossier.is_dir() else [config.MESSAGE_PROFIL_ABSENT], True))
    cfg = None
    if dossier.is_dir():
        try:
            cfg = config.charger()
            points.append(("config.yaml valide", [], True))
        except config.ErreurConfig as e:
            points.append(("config.yaml valide", [str(e)], True))
    if cfg is not None:
        points += config.verifier_par_point(cfg)
        points.append(("Moteur IA joignable", _problemes_claude(cfg), True))
        points.append(("Playwright Chromium installé", _problemes_chromium(), True))
    bloque = False
    for libelle, problemes, bloquant in points:
        symbole = "✓" if not problemes else ("✗" if bloquant else "⚠")
        print(f"{symbole} {libelle}")
        for p in problemes:
            print(f"    {p}")
        bloque = bloque or (bool(problemes) and bloquant)
    print("\nPrêt." if not bloque else "\nÀ corriger avant de continuer.")
    return 1 if bloque else 0


@commande("statut", "Affiche les compteurs de la base")
def _statut(args) -> int:
    config.charger()
    s = base.stats()
    print(f"Entreprises : {s['entreprises']} (dont {s['entreprises_avec_email']} avec une adresse)")
    print(f"Candidatures : {s['candidatures']}")
    for statut, n in s["par_statut"].items():
        if n:
            print(f"  {statut} : {n}")
    print(f"Aujourd'hui : {s['mails_aujourdhui']} mails, {s['lba_aujourdhui']} candidatures LBA")
    p = s["par_statut"]
    partis = sum(p[x] for x in ("envoyee", "postee_lba", "reponse", "entretien", "refus", "rebond"))
    repondu = p["reponse"] + p["entretien"] + p["refus"]
    if partis:
        print(f"Réponses : {repondu} sur {partis} candidatures parties ({100 * repondu / partis:.0f} %), "
              f"{p['rebond']} rebonds")
    for nom in s["entretiens"]:
        print(f"  entretien proposé : {nom}")
    return 0


def main(argv: list[str] | None = None) -> int:
    for module in MODULES_COMMANDES:
        importlib.import_module(f"{__package__}.{module}")
    parser = argparse.ArgumentParser(prog="alternabot", description="Candidatures en alternance")
    sous = parser.add_subparsers(dest="commande")
    for nom, c in COMMANDES.items():
        sp = sous.add_parser(nom, help=c["aide"], description=c["aide"])
        if c["arguments"]:
            c["arguments"](sp)
    args = parser.parse_args(argv)
    if not args.commande:
        parser.print_help()
        return 2
    try:
        return COMMANDES[args.commande]["executer"](args) or 0
    except (config.ErreurConfig, ErreurIA) as e:
        print(f"Erreur : {e}", file=sys.stderr)
        return 1
