"""Tests de routine.py (aucun vrai sous-processus, launchctl ni crontab : subprocess.run est simulé)."""
import plistlib
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from alternabot import cli, config, routine


class Faux:
    """subprocess.run simulé : enregistre les appels, réponses selon la commande."""

    def __init__(self, reponses=None, delais=(), crontab=""):
        self.appels = []
        self.reponses = reponses or {}  # mot de la commande -> code de retour
        self.delais = set(delais)
        self.crontab = crontab
        self.ecrite = None

    def __call__(self, commande, **kw):
        self.appels.append((list(commande), kw))
        if commande[0] == "crontab":
            if commande[1] == "-l":
                return SimpleNamespace(returncode=0 if self.crontab else 1, stdout=self.crontab, stderr="")
            self.ecrite = kw["input"]
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if commande[0] == "launchctl":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        nom = commande[3]
        if nom in self.delais:
            raise subprocess.TimeoutExpired(commande, kw["timeout"], output=b"debut de sortie")
        code = self.reponses.get(nom, 0)
        return SimpleNamespace(returncode=code, stdout=f"sortie {nom}\n", stderr="erreur\n" if code else "")

    def etapes(self):
        return [c[3] for c, _ in self.appels if c[:3] == [sys.executable, "-m", "alternabot"]]

    def commande(self, nom):
        return next(c for c, _ in self.appels if c[3:4] == [nom])


@pytest.fixture
def faux(monkeypatch):
    f = Faux()
    monkeypatch.setattr(routine.subprocess, "run", f)
    return f


@pytest.fixture
def cfg(profil):
    return config.charger()


def regler(profil, **sections):
    chemin = profil / "config.yaml"
    donnees = yaml.safe_load(chemin.read_text(encoding="utf-8"))
    for section, valeurs in sections.items():
        donnees.setdefault(section, {}).update(valeurs)
    chemin.write_text(yaml.safe_dump(donnees, allow_unicode=True), encoding="utf-8")
    return config.charger()


def avec_cle(profil, monkeypatch):
    monkeypatch.setenv("SERPAPI_KEY", "cle-serp")
    (profil / ".env").write_text((profil / ".env").read_text() + "SERPAPI_KEY=cle-serp\n", encoding="utf-8")
    return config.charger()


def test_commande_enregistree():
    assert "routine" in cli.MODULES_COMMANDES
    assert "routine" in cli.COMMANDES


def test_ordre_et_arguments_des_etapes(cfg, profil, monkeypatch, faux):
    cfg = avec_cle(profil, monkeypatch)
    assert routine.lancer(cfg) == 0
    assert faux.etapes() == ["envoyer", "lba", "offres", "emails", "rediger", "reponses"]
    assert faux.commande("lba")[4:] == ["--limite", "10"]
    assert faux.commande("offres")[4:] == ["--limite", "10"]
    assert faux.commande("emails")[4:] == ["--limite", "30"]
    assert faux.commande("rediger")[4:] == ["--limite", "15"]
    assert faux.commande("reponses")[4:] == ["--jours", "14"]
    assert faux.commande("envoyer")[4:] == []
    for _, kw in faux.appels:
        assert kw["capture_output"] and kw["text"]
    delais = {c[3]: kw["timeout"] for c, kw in faux.appels}
    assert delais == {"envoyer": 3600, "lba": 3600, "offres": 300, "emails": 1800, "rediger": 1800, "reponses": 600}


def test_limites_et_delais_de_la_config(profil, faux):
    cfg = regler(profil, lba={"plafond_jour": 4},
                 routine={"emails_par_jour": 7, "mails_par_jour": 3, "delais": {"reponses": 99}})
    routine.lancer(cfg)
    assert faux.commande("lba")[4:] == ["--limite", "4"]
    assert faux.commande("emails")[4:] == ["--limite", "7"]
    assert faux.commande("rediger")[4:] == ["--limite", "3"]
    assert [kw["timeout"] for c, kw in faux.appels if c[3] == "reponses"] == [99]


def test_essai_passe_essai_a_envoyer_et_lba(cfg, faux):
    routine.lancer(cfg, essai=True)
    assert "--essai" in faux.commande("envoyer")
    assert "--essai" in faux.commande("lba")
    for nom in ("emails", "rediger", "reponses"):
        assert "--essai" not in faux.commande(nom)


def test_sans_essai_pas_de_drapeau(cfg, faux):
    routine.lancer(cfg)
    assert "--essai" not in faux.commande("envoyer")
    assert "--essai" not in faux.commande("lba")


def test_offres_sautee_sans_cle(cfg, faux, capsys):
    assert routine.lancer(cfg) == 0
    assert "offres" not in faux.etapes()
    assert "offres : sautée : pas de clé SerpAPI" in capsys.readouterr().out


def test_etape_trop_longue_tuee_et_suite_lancee(cfg, faux, capsys):
    faux.delais = {"lba"}
    assert routine.lancer(cfg) == 1
    assert faux.etapes() == ["envoyer", "lba", "emails", "rediger", "reponses"]
    sortie = capsys.readouterr().out
    assert "lba : délai dépassé (3600 s)" in sortie
    assert "emails : ok" in sortie


def test_echec_n_arrete_pas_la_suite(cfg, faux, capsys):
    faux.reponses = {"envoyer": 3}
    assert routine.lancer(cfg) == 1
    assert faux.etapes() == ["envoyer", "lba", "emails", "rediger", "reponses"]
    sortie = capsys.readouterr().out
    assert "envoyer : échec (code 3)" in sortie
    assert "erreur" in sortie


def test_echec_garde_les_20_dernieres_lignes(cfg, monkeypatch, capsys):
    def run(commande, **kw):
        return SimpleNamespace(returncode=1, stdout="\n".join(f"ligne {i}" for i in range(50)), stderr="")
    monkeypatch.setattr(routine.subprocess, "run", run)
    routine.lancer(cfg)
    sortie = capsys.readouterr().out
    assert "ligne 49" in sortie and "ligne 30" in sortie
    assert "ligne 29" not in sortie


def test_lancement_impossible_note_echec(cfg, monkeypatch):
    def run(commande, **kw):
        raise FileNotFoundError("python")
    monkeypatch.setattr(routine.subprocess, "run", run)
    assert routine.lancer(cfg) == 1


def test_journal_du_jour_ecrit_en_ajout(cfg, profil, faux):
    faux.reponses = {"emails": 2}
    routine.lancer(cfg)
    routine.lancer(cfg)
    journal = profil / "journaux" / f"{datetime.now():%Y-%m-%d}.log"
    texte = journal.read_text(encoding="utf-8")
    assert texte.count("=== Routine du") == 2
    assert "envoyer : " + " ".join([sys.executable, "-m", "alternabot", "envoyer"]) in texte
    assert "résultat : échec (code 2)" in texte
    assert "sortie envoyer" in texte
    assert "--- Résumé ---" in texte
    assert "offres : sautée : pas de clé SerpAPI" in texte


def test_environnement_herite(cfg, faux, monkeypatch):
    routine.lancer(cfg)
    assert all("env" not in kw for _, kw in faux.appels)


def test_executer_delai(monkeypatch):
    def run(commande, **kw):
        raise subprocess.TimeoutExpired(commande, kw["timeout"], output=b"partiel", stderr=None)
    monkeypatch.setattr(routine.subprocess, "run", run)
    r = routine._executer(["x"], 5)
    assert r == {"statut": "delai", "code": None, "sortie": "partiel"}


# ---------- Installation ----------

@pytest.fixture
def maison(tmp_path, monkeypatch):
    home = tmp_path / "maison"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def test_heure_valide_et_invalide(profil):
    assert routine.lire_heure({"routine": {"heure": "07:05"}}) == (7, 5)
    assert routine.lire_heure({}) == (9, 0)
    for mauvaise in ("9h", "25:00", "09:61", "tard"):
        with pytest.raises(config.ErreurConfig):
            routine.lire_heure({"routine": {"heure": mauvaise}})


def test_installer_mac_ecrit_le_plist(profil, maison, monkeypatch, faux):
    monkeypatch.setattr(routine, "_systeme", lambda: "mac")
    claude = maison / "bin-claude" / "claude"
    monkeypatch.setattr(routine.shutil, "which", lambda nom: str(claude))
    cfg = regler(profil, routine={"heure": "07:30"})
    assert routine.installer(cfg) == 0
    chemin = maison / "Library" / "LaunchAgents" / "io.github.alternabot.routine.plist"
    donnees = plistlib.loads(chemin.read_bytes())
    assert donnees["Label"] == "io.github.alternabot.routine"
    assert donnees["ProgramArguments"] == [sys.executable, "-m", "alternabot", "routine", "lancer"]
    assert donnees["WorkingDirectory"] == str(config.racine())
    assert donnees["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}
    journal = str(profil.resolve() / "journaux" / "launchd.log")
    assert donnees["StandardOutPath"] == donnees["StandardErrorPath"] == journal
    env = donnees["EnvironmentVariables"]
    assert env["ALTERNABOT_PROFIL"] == str(profil.resolve())
    chemins = env["PATH"].split(":")
    assert chemins[0] == str(Path(sys.executable).parent)
    assert str(claude.parent) in chemins
    assert "/opt/homebrew/bin" in chemins and "/usr/bin" in chemins
    assert len(chemins) == len(set(chemins))
    launchctl = [c for c, _ in faux.appels if c[0] == "launchctl"]
    assert [c[1] for c in launchctl] == ["bootout", "bootstrap"]
    assert launchctl[1][3] == str(chemin)
    assert launchctl[1][2].startswith("gui/")


def test_installer_mac_sans_claude(profil, maison, monkeypatch, faux):
    monkeypatch.setattr(routine, "_systeme", lambda: "mac")
    monkeypatch.setattr(routine.shutil, "which", lambda nom: None)
    routine.installer(config.charger())
    chemin = maison / "Library" / "LaunchAgents" / "io.github.alternabot.routine.plist"
    assert plistlib.loads(chemin.read_bytes())["EnvironmentVariables"]["PATH"].endswith(routine.CHEMINS_FIXES)


def test_installer_mac_echec_bootstrap(profil, maison, monkeypatch, capsys):
    monkeypatch.setattr(routine, "_systeme", lambda: "mac")

    def run(commande, **kw):
        return SimpleNamespace(returncode=5 if commande[1] == "bootstrap" else 0, stdout="", stderr="refus")
    monkeypatch.setattr(routine.subprocess, "run", run)
    assert routine.installer(config.charger()) == 1
    assert "refus" in capsys.readouterr().err


def test_installer_mac_heure_invalide(profil, maison, monkeypatch, faux):
    monkeypatch.setattr(routine, "_systeme", lambda: "mac")
    cfg = regler(profil, routine={"heure": "midi"})
    with pytest.raises(config.ErreurConfig):
        routine.installer(cfg)
    assert not (maison / "Library").exists()
    assert faux.appels == []


def test_avertissement_dossier_protege(tmp_path):
    home = tmp_path
    assert "Documents" in routine._avertissement_dossier(home / "Documents" / "x" / "alternabot", home)
    assert "Desktop" in routine._avertissement_dossier(home / "Desktop" / "alternabot", home)
    assert "Downloads" in routine._avertissement_dossier(home / "Downloads" / "a", home)
    assert routine._avertissement_dossier(home / "alternabot", home) is None
    assert routine._avertissement_dossier(home / "Developer" / "alternabot", home) is None


def test_installer_linux_propose_sans_ecrire(profil, monkeypatch, faux, capsys):
    monkeypatch.setattr(routine, "_systeme", lambda: "linux")
    cfg = regler(profil, routine={"heure": "08:15"})
    assert routine.installer(cfg) == 0
    sortie = capsys.readouterr().out
    ligne = routine._ligne_cron(sys.executable, config.racine(), profil.resolve(), 8, 15)
    assert ligne in sortie
    assert ligne.startswith("15 8 * * * cd ")
    assert f"ALTERNABOT_PROFIL={profil.resolve()}" in ligne
    assert f"{sys.executable} -m alternabot routine lancer >> " in ligne
    assert ligne.endswith("cron.log 2>&1")
    assert faux.ecrite is None
    assert not any(c[0] == "crontab" for c, _ in faux.appels)


def test_installer_linux_ecrit_avec_oui_et_remplace_l_ancienne(profil, monkeypatch, faux):
    monkeypatch.setattr(routine, "_systeme", lambda: "linux")
    faux.crontab = ("0 1 * * * sauvegarde\n"
                    "0 6 * * * cd /ancien && /ancien/python -m alternabot routine lancer >> /tmp/x 2>&1\n")
    assert routine.installer(config.charger(), oui=True) == 0
    lignes = faux.ecrite.splitlines()
    assert lignes[0] == "0 1 * * * sauvegarde"
    assert sum("alternabot routine lancer" in l for l in lignes) == 1
    assert "/ancien" not in faux.ecrite
    assert lignes[-1].startswith("0 9 * * * cd ")
    assert faux.ecrite.endswith("\n")


def test_installer_linux_crontab_vide(profil, monkeypatch, faux):
    monkeypatch.setattr(routine, "_systeme", lambda: "linux")
    routine.installer(config.charger(), oui=True)
    assert len(faux.ecrite.splitlines()) == 1


def test_installer_windows(profil, monkeypatch, faux, capsys):
    monkeypatch.setattr(routine, "_systeme", lambda: "autre")
    assert routine.installer(config.charger()) == 1
    assert "Planificateur de tâches" in capsys.readouterr().err
    assert faux.appels == []


def test_retirer_mac(maison, monkeypatch, faux):
    monkeypatch.setattr(routine, "_systeme", lambda: "mac")
    chemin = maison / "Library" / "LaunchAgents" / "io.github.alternabot.routine.plist"
    chemin.parent.mkdir(parents=True)
    chemin.write_bytes(b"x")
    assert routine.retirer() == 0
    assert not chemin.exists()
    assert faux.appels[0][0][:2] == ["launchctl", "bootout"]
    assert routine.retirer() == 0  # déjà retiré : sans erreur


def test_retirer_linux(monkeypatch, faux, capsys):
    monkeypatch.setattr(routine, "_systeme", lambda: "linux")
    faux.crontab = "0 1 * * * sauvegarde\n0 9 * * * cd /a && python -m alternabot routine lancer\n"
    assert routine.retirer() == 0
    assert faux.ecrite is None
    assert "--oui" in capsys.readouterr().out
    assert routine.retirer(oui=True) == 0
    assert faux.ecrite == "0 1 * * * sauvegarde\n"


def test_retirer_linux_rien_a_retirer(monkeypatch, faux):
    monkeypatch.setattr(routine, "_systeme", lambda: "linux")
    faux.crontab = "0 1 * * * sauvegarde\n"
    assert routine.retirer(oui=True) == 0
    assert faux.ecrite is None


def test_cli_routine_lancer(profil, monkeypatch, faux, capsys):
    assert cli.main(["routine", "lancer", "--essai"]) == 0
    assert "--essai" in faux.commande("envoyer")


def test_cli_heure_invalide_rend_1(profil, maison, monkeypatch, faux, capsys):
    regler(profil, routine={"heure": "bof"})
    monkeypatch.setattr(routine, "_systeme", lambda: "linux")
    assert cli.main(["routine", "installer"]) == 1
    assert "HH:MM" in capsys.readouterr().err
