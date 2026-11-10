"""Tests de config.py et de la commande verifier."""
import os

import pytest

from alternabot import cli, config


def test_sans_profil_erreur_claire(tmp_path, monkeypatch):
    monkeypatch.setenv("ALTERNABOT_PROFIL", str(tmp_path / "absent"))
    with pytest.raises(config.ErreurConfig, match=r"cp -r profil\.exemple profil"):
        config.charger()


def test_profil_par_defaut_a_la_racine(monkeypatch):
    monkeypatch.delenv("ALTERNABOT_PROFIL", raising=False)
    assert config.dossier_profil() == config.racine() / "profil"


def test_exemple_brut_liste_les_champs_vides(profil_exemple):
    problemes = config.verifier_config(config.charger())
    for attendu in ("candidat.prenom", "candidat.nom", "candidat.email", "candidat.telephone", "CV absent",
                    "encore identique", "gmail.comptes"):
        assert any(attendu in p for p in problemes), attendu


def test_profil_rempli_sans_probleme(profil):
    assert config.verifier_config(config.charger()) == []


def test_champ_obligatoire_vide_signale(profil):
    chemin = profil / "config.yaml"
    chemin.write_text(chemin.read_text(encoding="utf-8").replace("prenom: Camille", "prenom: ''"), encoding="utf-8")
    assert config.verifier_config(config.charger()) == ["candidat.prenom est vide"]


def test_variable_absente_vide_et_signalee(profil):
    (profil / ".env").write_text("GOOGLE_MAPS_API_KEY=cle-test\nGMAIL_MDP_1=mdp-test\n", encoding="utf-8")
    cfg = config.charger()
    assert cfg["lba"]["jeton"] == ""
    assert cfg["_absentes"]["LBA_API_TOKEN"] == "lba.jeton"
    assert any("LBA_API_TOKEN" in p for p in config.verifier_config(cfg))


def test_variable_optionnelle_non_signalee(profil):
    cfg = config.charger()
    assert cfg["google_offres"]["cle"] == ""
    assert not any("SERPAPI_KEY" in p for p in config.verifier_config(cfg))


def test_variable_remplacee_dans_la_config(profil):
    assert config.charger()["lba"]["jeton"] == "jeton-test"


def test_env_n_ecrase_pas_l_environnement(profil, monkeypatch):
    monkeypatch.setenv("LBA_API_TOKEN", "depuis-l-environnement")
    assert config.charger()["lba"]["jeton"] == "depuis-l-environnement"


def test_env_guillemets_et_commentaires(tmp_path):
    fichier = tmp_path / ".env"
    fichier.write_text('# commentaire\nA_TEST_1="avec espace"\nB_TEST_1=simple\nsans_egal\n', encoding="utf-8")
    config.charger_env(fichier)
    assert os.environ["A_TEST_1"] == "avec espace"
    assert os.environ["B_TEST_1"] == "simple"


def test_moteur_api_sans_cle(profil):
    chemin = profil / "config.yaml"
    chemin.write_text(chemin.read_text(encoding="utf-8").replace("moteur: claude-cli", "moteur: api"), encoding="utf-8")
    assert any("ANTHROPIC_API_KEY" in p for p in config.verifier_config(config.charger()))


def test_moteur_inconnu(profil):
    chemin = profil / "config.yaml"
    chemin.write_text(chemin.read_text(encoding="utf-8").replace("moteur: claude-cli", "moteur: autre"), encoding="utf-8")
    assert any("ia.moteur" in p for p in config.verifier_config(config.charger()))


def test_mot_de_passe_gmail_absent_nomme_la_variable(profil):
    (profil / ".env").write_text("LBA_API_TOKEN=a\nGOOGLE_MAPS_API_KEY=b\n", encoding="utf-8")
    assert any("GMAIL_MDP_1" in p for p in config.verifier_config(config.charger()))


def test_obtenir_valeurs_par_defaut():
    assert config.obtenir({"a": {"b": ""}}, "a.b", "x") == "x"
    assert config.obtenir({}, "a.b", "x") == "x"
    assert config.obtenir({"a": {"b": 3}}, "a.b") == 3


@pytest.fixture
def outils_ok(monkeypatch):
    monkeypatch.setattr(cli, "_problemes_claude", lambda cfg: [])
    monkeypatch.setattr(cli, "_problemes_chromium", lambda: [])


def test_verifier_sans_profil_code_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ALTERNABOT_PROFIL", str(tmp_path / "absent"))
    assert cli.main(["verifier"]) == 1
    sortie = capsys.readouterr().out
    assert "✗ Dossier profil/ présent" in sortie
    assert "profil.exemple" in sortie


def test_verifier_champ_vide_code_1(profil, outils_ok, capsys):
    chemin = profil / "config.yaml"
    chemin.write_text(chemin.read_text(encoding="utf-8").replace("email: camille.martin@example.com", "email: ''", 1),
                      encoding="utf-8")
    assert cli.main(["verifier"]) == 1
    assert "candidat.email est vide" in capsys.readouterr().out


def test_verifier_tout_vert_code_0(profil, outils_ok, capsys):
    assert cli.main(["verifier"]) == 0
    sortie = capsys.readouterr().out
    assert "✗" not in sortie and "Prêt." in sortie


def test_verifier_cle_manquante_n_est_pas_bloquante(profil, outils_ok, capsys):
    (profil / ".env").write_text("GMAIL_MDP_1=mdp-test\n", encoding="utf-8")
    assert cli.main(["verifier"]) == 0
    assert "⚠" in capsys.readouterr().out


def test_statut_sur_base_vide(profil, capsys):
    assert cli.main(["statut"]) == 0
    assert "Entreprises : 0" in capsys.readouterr().out


def test_statut_sans_profil_erreur_propre(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ALTERNABOT_PROFIL", str(tmp_path / "absent"))
    assert cli.main(["statut"]) == 1
    assert "profil.exemple" in capsys.readouterr().err


def test_commande_enregistree_par_un_module(profil, capsys):
    @cli.commande("bonjour", "Test", arguments=lambda p: p.add_argument("--nom", default="monde"))
    def _bonjour(args):
        print(f"bonjour {args.nom}")
        return 0
    try:
        assert cli.main(["bonjour", "--nom", "Camille"]) == 0
        assert "bonjour Camille" in capsys.readouterr().out
    finally:
        del cli.COMMANDES["bonjour"]
