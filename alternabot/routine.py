"""Routine quotidienne : enchaîne les étapes du bot, chacune avec un délai maximal, et l'installe.

`routine lancer` exécute chaque étape dans un sous-processus : une étape qui échoue ou qui dépasse
son délai est notée, et les suivantes tournent quand même. `routine installer` programme ce lancement
chaque jour (launchd sur macOS, cron sur Linux).
"""
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import config
from .cli import commande

ETIQUETTE = "io.github.alternabot.routine"
ACTIONS = ("lancer", "installer", "retirer")
CHEMINS_FIXES = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
DOSSIERS_PROTEGES = ("Documents", "Desktop", "Downloads")
LIGNES_DE_SORTIE = 20
# Marque qui distingue notre ligne dans la crontab.
MARQUE_CRON = "alternabot routine lancer"
# Délais maximaux par défaut, en secondes (surchargeables par routine.delais.<étape>).
DELAIS = {"envoyer": 3600, "lba": 3600, "offres": 300, "emails": 1800, "rediger": 1800, "reponses": 600}


def _systeme() -> str:
    """'mac', 'linux' ou 'autre'."""
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("linux"):
        return "linux"
    return "autre"


def _entier(cfg: dict, chemin: str, defaut: int) -> int:
    valeur = config.obtenir(cfg, chemin, defaut)
    try:
        return int(valeur)
    except (TypeError, ValueError):
        raise config.ErreurConfig(f"{chemin} doit être un nombre entier (reçu {valeur!r})") from None


def _delai(cfg: dict, nom: str) -> int:
    return _entier(cfg, f"routine.delais.{nom}", DELAIS[nom])


def construire_etapes(cfg: dict, essai: bool = False) -> list[dict]:
    """Étapes dans l'ordre : {nom, commande (liste ou None), delai, saut (raison si sautée)}."""
    base = [sys.executable, "-m", "alternabot"]
    marque_essai = ["--essai"] if essai else []
    plafond_lba = _entier(cfg, "lba.plafond_jour", 10)
    avec_cle = bool(config.obtenir(cfg, "google_offres.cle"))
    return [
        {"nom": "envoyer", "commande": base + ["envoyer"] + marque_essai, "delai": _delai(cfg, "envoyer")},
        {"nom": "lba", "commande": base + ["lba", "--limite", str(plafond_lba)] + marque_essai,
         "delai": _delai(cfg, "lba")},
        {"nom": "offres", "commande": base + ["offres", "--limite", "10"] if avec_cle else None,
         "delai": _delai(cfg, "offres"), "saut": None if avec_cle else "pas de clé SerpAPI"},
        {"nom": "emails", "commande": base + ["emails", "--limite", str(_entier(cfg, "routine.emails_par_jour", 30))],
         "delai": _delai(cfg, "emails")},
        {"nom": "rediger", "commande": base + ["rediger", "--limite", str(_entier(cfg, "routine.mails_par_jour", 15))],
         "delai": _delai(cfg, "rediger")},
        {"nom": "reponses", "commande": base + ["reponses", "--jours", "14"], "delai": _delai(cfg, "reponses")},
    ]


def _texte(valeur) -> str:
    if valeur is None:
        return ""
    return valeur.decode("utf-8", "replace") if isinstance(valeur, bytes) else valeur


def _executer(commande: list[str], delai: int) -> dict:
    """Lance une commande avec un délai maximal. Rend {statut: ok|echec|delai, code, sortie}."""
    try:
        res = subprocess.run(commande, timeout=delai, capture_output=True, text=True)
    except subprocess.TimeoutExpired as e:
        return {"statut": "delai", "code": None, "sortie": _texte(e.stdout) + _texte(e.stderr)}
    except OSError as e:
        return {"statut": "echec", "code": None, "sortie": f"lancement impossible : {e}"}
    sortie = _texte(res.stdout) + _texte(res.stderr)
    return {"statut": "ok" if res.returncode == 0 else "echec", "code": res.returncode, "sortie": sortie}


def _resultat(r: dict, delai: int) -> str:
    if r["statut"] == "ok":
        return "ok"
    if r["statut"] == "delai":
        return f"délai dépassé ({delai} s)"
    return f"échec (code {r['code']})" if r["code"] is not None else "échec"


def _fin_de_sortie(sortie: str) -> str:
    return "\n".join(sortie.rstrip().splitlines()[-LIGNES_DE_SORTIE:])


def _journal(maintenant: datetime) -> Path:
    dossier = config.dossier_profil() / "journaux"
    dossier.mkdir(parents=True, exist_ok=True)
    return dossier / f"{maintenant:%Y-%m-%d}.log"


def lancer(cfg: dict, essai: bool = False) -> int:
    """Exécute les étapes, écrit le journal du jour et le résumé. Rend 0 si aucune n'a échoué."""
    debut = datetime.now()
    journal = _journal(debut)
    resume, echec = [], False
    with journal.open("a", encoding="utf-8") as f:
        f.write(f"=== Routine du {debut:%Y-%m-%d %H:%M:%S}{' (essai)' if essai else ''} ===\n")
        for etape in construire_etapes(cfg, essai):
            heure = datetime.now()
            if etape["commande"] is None:
                f.write(f"\n[{heure:%H:%M:%S}] {etape['nom']} : sautée : {etape['saut']}\n")
                resume.append(f"{etape['nom']} : sautée : {etape['saut']}")
                continue
            t0 = time.monotonic()
            r = _executer(etape["commande"], etape["delai"])
            duree = time.monotonic() - t0
            resultat = _resultat(r, etape["delai"])
            echec = echec or r["statut"] != "ok"
            f.write(f"\n[{heure:%H:%M:%S}] {etape['nom']} : {' '.join(etape['commande'])}\n"
                    f"durée {duree:.0f} s, résultat : {resultat}\n")
            if r["sortie"].strip():
                f.write(r["sortie"].rstrip() + "\n")
            ligne = f"{etape['nom']} : {resultat} ({duree:.0f} s)"
            if r["statut"] != "ok" and r["sortie"].strip():
                ligne += "\n    " + _fin_de_sortie(r["sortie"]).replace("\n", "\n    ")
            resume.append(ligne)
        f.write("\n--- Résumé ---\n" + "\n".join(resume) + "\n\n")
    texte = "\n".join(resume)
    print(texte)
    print(f"Journal : {journal}")
    return 1 if echec else 0


# ---------- Installation ----------

def lire_heure(cfg: dict) -> tuple[int, int]:
    """(heure, minute) tirées de routine.heure « HH:MM »."""
    brut = str(config.obtenir(cfg, "routine.heure", "09:00")).strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", brut)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise config.ErreurConfig(f"routine.heure doit être au format HH:MM (reçu {brut!r}), ex. \"09:00\"")
    return int(m.group(1)), int(m.group(2))


def _chemin_plist() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{ETIQUETTE}.plist"


def _path_launchd(python: str, claude: str | None) -> str:
    """PATH de la tâche : sous launchd il est minimal, et `claude -p` serait introuvable."""
    dossiers = [str(Path(python).parent)]
    if claude:
        dossiers.append(str(Path(claude).parent))
    dossiers += CHEMINS_FIXES.split(":")
    return ":".join(dict.fromkeys(dossiers))


def _plist(python: str, racine: Path, profil: Path, heure: int, minute: int, claude: str | None) -> dict:
    """Contenu du LaunchAgent."""
    journal = str(profil / "journaux" / "launchd.log")
    return {
        "Label": ETIQUETTE,
        "ProgramArguments": [python, "-m", "alternabot", "routine", "lancer"],
        "WorkingDirectory": str(racine),
        "StartCalendarInterval": {"Hour": heure, "Minute": minute},
        "StandardOutPath": journal,
        "StandardErrorPath": journal,
        "EnvironmentVariables": {"ALTERNABOT_PROFIL": str(profil), "PATH": _path_launchd(python, claude)},
    }


def _avertissement_dossier(racine: Path, home: Path) -> str | None:
    """Message si le dépôt est dans un dossier que launchd n'a pas le droit de lire."""
    for nom in DOSSIERS_PROTEGES:
        if racine.is_relative_to(home / nom):
            return (f"Attention : le dépôt est dans ~/{nom}, que launchd n'a pas le droit de lire sans "
                    f"« Accès complet au disque ». Déplace le dépôt (ex. ~/alternabot) avant d'installer.")
    return None


def _ligne_cron(python: str, racine: Path, profil: Path, heure: int, minute: int) -> str:
    """Ligne de crontab de la routine."""
    journal = profil / "journaux" / "cron.log"
    ligne = (f"{minute} {heure} * * * cd {shlex.quote(str(racine))} && ALTERNABOT_PROFIL={shlex.quote(str(profil))} "
             f"{shlex.quote(python)} -m alternabot routine lancer >> {shlex.quote(str(journal))} 2>&1")
    return ligne.replace("%", "\\%")


def _lire_crontab() -> str:
    res = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return res.stdout if res.returncode == 0 else ""


def _ecrire_crontab(texte: str) -> None:
    res = subprocess.run(["crontab", "-"], input=texte, capture_output=True, text=True)
    if res.returncode != 0:
        raise config.ErreurConfig(f"crontab a refusé la nouvelle table : {res.stderr.strip()}")


def _sans_alternabot(crontab: str) -> list[str]:
    return [l for l in crontab.splitlines() if MARQUE_CRON not in l]


def _launchctl(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *arguments], capture_output=True, text=True)


def _installer_mac(cfg: dict) -> int:
    heure, minute = lire_heure(cfg)
    racine, profil = config.racine(), config.dossier_profil().resolve()
    alerte = _avertissement_dossier(racine, Path.home())
    if alerte:
        print(alerte)
    (profil / "journaux").mkdir(parents=True, exist_ok=True)
    chemin = _chemin_plist()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    contenu = _plist(sys.executable, racine, profil, heure, minute, shutil.which("claude"))
    with chemin.open("wb") as f:
        plistlib.dump(contenu, f)
    domaine = f"gui/{os.getuid()}"
    _launchctl("bootout", domaine, str(chemin))  # absente au premier passage : erreur sans importance
    res = _launchctl("bootstrap", domaine, str(chemin))
    if res.returncode != 0:
        print(f"launchctl bootstrap a échoué : {(res.stderr or res.stdout).strip()}", file=sys.stderr)
        return 1
    print(f"Routine installée : tous les jours à {heure:02d}:{minute:02d} ({chemin}).")
    return 0


def _installer_linux(cfg: dict, oui: bool) -> int:
    heure, minute = lire_heure(cfg)
    racine, profil = config.racine(), config.dossier_profil().resolve()
    ligne = _ligne_cron(sys.executable, racine, profil, heure, minute)
    print("Ligne crontab de la routine :\n" + ligne)
    if not oui:
        print("\nRien n'est écrit. Relance avec --oui pour l'ajouter à ta crontab.")
        return 0
    (profil / "journaux").mkdir(parents=True, exist_ok=True)
    lignes = _sans_alternabot(_lire_crontab()) + [ligne]
    _ecrire_crontab("\n".join(lignes) + "\n")
    print("Ligne ajoutée à la crontab.")
    return 0


def installer(cfg: dict, oui: bool = False) -> int:
    systeme = _systeme()
    if systeme == "mac":
        return _installer_mac(cfg)
    if systeme == "linux":
        return _installer_linux(cfg, oui)
    print("Pas d'installation automatique sur ce système : crée une tâche dans le Planificateur de tâches "
          f"qui exécute `{sys.executable} -m alternabot routine lancer` chaque jour.", file=sys.stderr)
    return 1


def retirer(oui: bool = False) -> int:
    systeme = _systeme()
    if systeme == "mac":
        chemin = _chemin_plist()
        _launchctl("bootout", f"gui/{os.getuid()}", str(chemin))
        existait = chemin.exists()
        chemin.unlink(missing_ok=True)
        print("Routine retirée." if existait else "Aucune routine installée.")
        return 0
    if systeme == "linux":
        actuelle = _lire_crontab()
        if not any(MARQUE_CRON in l for l in actuelle.splitlines()):
            print("Aucune ligne alternabot dans la crontab.")
            return 0
        if not oui:
            print("Ligne qui serait retirée de la crontab :")
            print("\n".join(l for l in actuelle.splitlines() if MARQUE_CRON in l))
            print("\nRelance avec --oui pour la retirer.")
            return 0
        reste = _sans_alternabot(actuelle)
        _ecrire_crontab("\n".join(reste) + "\n" if reste else "")
        print("Ligne retirée de la crontab.")
        return 0
    print("Pas de routine automatique sur ce système : supprime la tâche du Planificateur de tâches.", file=sys.stderr)
    return 1


def _arguments(parser) -> None:
    parser.add_argument("action", choices=ACTIONS, help="lancer les étapes, installer ou retirer la tâche quotidienne")
    parser.add_argument("--essai", action="store_true", help="(lancer) envoyer et lba tournent en mode essai")
    parser.add_argument("--oui", action="store_true", help="(installer, retirer) écrit la crontab sous Linux")


@commande("routine", "Routine quotidienne : lancer les étapes, installer ou retirer la tâche programmée", _arguments)
def _routine(args) -> int:
    if args.action == "lancer":
        return lancer(config.charger(), essai=args.essai)
    if args.action == "installer":
        return installer(config.charger(), oui=args.oui)
    return retirer(oui=args.oui)
