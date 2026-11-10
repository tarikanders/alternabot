"""Chargement du profil (profil/config.yaml, profil/.env) et vérification de la configuration."""
import os
import re
from pathlib import Path

import yaml

MESSAGE_PROFIL_ABSENT = "Copie profil.exemple/ vers profil/ (cp -r profil.exemple profil) puis remplis-le."
MOTEURS_IA = ("claude-cli", "api")
# Variables référencées par ${...} dont l'absence ne gêne pas.
VARIABLES_OPTIONNELLES = {"SERPAPI_KEY"}
_VARIABLE = re.compile(r"\$\{(\w+)\}")


class ErreurConfig(RuntimeError):
    """Profil ou configuration inutilisable."""


def racine() -> Path:
    """Dossier du projet (celui qui contient alternabot/ et profil.exemple/)."""
    return Path(__file__).resolve().parent.parent


def dossier_profil() -> Path:
    """profil/ à la racine, ou le dossier désigné par ALTERNABOT_PROFIL."""
    autre = os.environ.get("ALTERNABOT_PROFIL")
    return Path(autre).expanduser() if autre else racine() / "profil"


def charger_env(chemin: Path) -> None:
    """Lit un fichier KEY=VALUE dans l'environnement, sans écraser les variables déjà posées."""
    if not chemin.is_file():
        return
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, valeur = ligne.partition("=")
        valeur = valeur.strip()
        if len(valeur) >= 2 and valeur[0] == valeur[-1] and valeur[0] in "\"'":
            valeur = valeur[1:-1]
        os.environ.setdefault(cle.strip(), valeur)


def _remplacer(valeur, chemin: str, absentes: dict):
    """Remplace ${VAR} par l'environnement (vide si absente) dans toute la structure."""
    if isinstance(valeur, str):
        def sub(m):
            contenu = os.environ.get(m.group(1), "")
            if not contenu:
                absentes[m.group(1)] = chemin
            return contenu
        return _VARIABLE.sub(sub, valeur)
    if isinstance(valeur, dict):
        return {k: _remplacer(v, f"{chemin}.{k}" if chemin else str(k), absentes) for k, v in valeur.items()}
    if isinstance(valeur, list):
        return [_remplacer(v, chemin, absentes) for v in valeur]
    return valeur


def charger() -> dict:
    """Charge profil/.env puis profil/config.yaml. Ajoute `_profil` (dossier) et `_absentes` (variable -> clé)."""
    dossier = dossier_profil()
    if not dossier.is_dir():
        raise ErreurConfig(MESSAGE_PROFIL_ABSENT)
    fichier = dossier / "config.yaml"
    if not fichier.is_file():
        raise ErreurConfig(f"{fichier} est absent. {MESSAGE_PROFIL_ABSENT}")
    charger_env(dossier / ".env")
    try:
        brut = yaml.safe_load(fichier.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ErreurConfig(f"config.yaml illisible : {e}") from e
    if not isinstance(brut, dict):
        raise ErreurConfig("config.yaml doit contenir des sections (candidat, recherche, ...).")
    absentes: dict = {}
    cfg = _remplacer(brut, "", absentes)
    cfg["_profil"] = str(dossier)
    cfg["_absentes"] = absentes
    return cfg


def obtenir(cfg: dict, chemin: str, defaut=None):
    """Valeur d'une clé pointée (« ia.moteur »), ou le défaut si absente ou vide."""
    courant = cfg
    for cle in chemin.split("."):
        if not isinstance(courant, dict) or cle not in courant:
            return defaut
        courant = courant[cle]
    return defaut if courant in (None, "") else courant


def _profil_de(cfg: dict) -> Path:
    return Path(cfg.get("_profil") or dossier_profil())


def _p_candidat(cfg):
    problemes = [f"candidat.{c} est vide" for c in ("prenom", "nom", "email", "telephone")
                 if not str(obtenir(cfg, f"candidat.{c}", "")).strip()]
    email = str(obtenir(cfg, "candidat.email", ""))
    if email and "@" not in email:
        problemes.append("candidat.email n'est pas une adresse valide")
    return problemes


def _p_cv(cfg):
    nom = obtenir(cfg, "candidat.cv", "CV.pdf")
    return [] if (_profil_de(cfg) / nom).is_file() else [f"CV absent : place {nom} dans profil/"]


def _p_profil_md(cfg):
    fichier = _profil_de(cfg) / "profil.md"
    if not fichier.is_file():
        return ["profil/profil.md est absent"]
    exemple = racine() / "profil.exemple" / "profil.md"
    if exemple.is_file() and fichier.read_text(encoding="utf-8").strip() == exemple.read_text(encoding="utf-8").strip():
        return ["profil/profil.md est encore identique à l'exemple : remplis-le"]
    return []


def _p_recherche(cfg):
    problemes = []
    if not obtenir(cfg, "recherche.ville"):
        problemes.append("recherche.ville est vide")
    for cle in ("latitude", "longitude", "rayon_km"):
        if not isinstance(obtenir(cfg, f"recherche.{cle}"), (int, float)):
            problemes.append(f"recherche.{cle} doit être un nombre")
    return problemes


def _p_ia(cfg):
    moteur = obtenir(cfg, "ia.moteur", "claude-cli")
    if moteur not in MOTEURS_IA:
        return [f"ia.moteur vaut {moteur!r} : choisis {' ou '.join(MOTEURS_IA)}"]
    if moteur == "api" and not os.environ.get("ANTHROPIC_API_KEY"):
        return ["ia.moteur est api mais ANTHROPIC_API_KEY est vide dans profil/.env"]
    return []


def _p_variables(cfg):
    return [f"{var} est vide ou absente de profil/.env (utilisée par {cle})"
            for var, cle in sorted(cfg.get("_absentes", {}).items())
            if var not in VARIABLES_OPTIONNELLES]


def _p_gmail(cfg):
    comptes = [c for c in (obtenir(cfg, "gmail.comptes", []) or []) if isinstance(c, dict) and c.get("email")]
    if not comptes:
        return ["gmail.comptes : aucun compte renseigné"]
    problemes = []
    for c in comptes:
        variable = c.get("variable_mdp") or ""
        if not variable:
            problemes.append(f"gmail.comptes : variable_mdp manquante pour {c['email']}")
        elif not os.environ.get(variable):
            problemes.append(f"{variable} est vide ou absente de profil/.env (mot de passe d'application de {c['email']})")
    return problemes


def verifier_par_point(cfg: dict) -> list[tuple[str, list[str], bool]]:
    """Points de contrôle de la configuration : (libellé, problèmes, bloquant)."""
    return [
        ("Informations du candidat", _p_candidat(cfg), True),
        ("CV présent", _p_cv(cfg), True),
        ("profil.md rempli", _p_profil_md(cfg), True),
        ("Zone de recherche", _p_recherche(cfg), True),
        ("Moteur IA réglé", _p_ia(cfg), True),
        ("Clés du .env (La Bonne Alternance, Google Maps)", _p_variables(cfg), False),
        ("Compte Gmail avec mot de passe d'application (mails spontanés, réponses)", _p_gmail(cfg), False),
    ]


def verifier_config(cfg: dict) -> list[str]:
    """Liste à plat des problèmes de configuration (vide si tout est en ordre)."""
    return [p for _, problemes, _ in verifier_par_point(cfg) for p in problemes]
