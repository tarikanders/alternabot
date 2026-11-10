"""Tests de base.py."""
import sqlite3

import pytest

from alternabot import base


@pytest.fixture(autouse=True)
def _profil(profil):
    return profil


def test_init_cree_les_trois_tables(profil):
    base.init()
    noms = {r[0] for r in sqlite3.connect(profil / "alternabot.db").execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"entreprises", "emails", "candidatures"} <= noms


def test_ajouter_entreprise_upsert_par_source_id():
    a = base.ajouter_entreprise("Acme", "maps", source_id="p1", ville="Lyon")
    b = base.ajouter_entreprise("Acme SAS", "maps", source_id="p1", site="https://www.acme.fr/contact")
    assert a == b
    with base.connexion() as conn:
        ligne = conn.execute("SELECT * FROM entreprises WHERE id = ?", (a,)).fetchone()
    assert ligne["nom"] == "Acme SAS" and ligne["ville"] == "Lyon" and ligne["domaine"] == "acme.fr"


def test_ajouter_entreprise_upsert_par_siret():
    a = base.ajouter_entreprise("Acme", "lba", source_id="l1", siret="12345678900011")
    b = base.ajouter_entreprise("Acme", "maps", source_id="p9", siret="12345678900011")
    assert a == b


def test_ajouter_entreprise_source_inconnue():
    with pytest.raises(sqlite3.IntegrityError):
        base.ajouter_entreprise("Acme", "autre", source_id="x")


def test_domaine_de():
    assert base.domaine_de("https://www.Acme.fr:8080/a") == "acme.fr"
    assert base.domaine_de("RH@Acme.fr") == "acme.fr"
    assert base.domaine_de(None) is None


def test_meilleur_email_confiance_puis_verification():
    e = base.ajouter_entreprise("Acme", "maps", source_id="p1")
    assert base.meilleur_email(e) is None
    base.ajouter_email(e, "Contact@acme.fr", "site", "moyenne")
    base.ajouter_email(e, "rh@acme.fr", "site", "haute", verifie=True)
    base.ajouter_email(e, "info@acme.fr", "motif", "basse")
    assert base.meilleur_email(e)["email"] == "rh@acme.fr"
    base.ajouter_email(e, "contact@acme.fr", "site", "haute", verifie=True)
    assert base.meilleur_email(e)["email"] == "contact@acme.fr"  # à égalité, la plus ancienne


def test_ajouter_email_deux_fois_ne_duplique_pas():
    e = base.ajouter_entreprise("Acme", "maps", source_id="p1")
    base.ajouter_email(e, "rh@acme.fr")
    base.ajouter_email(e, "RH@acme.fr", confiance="haute")
    with base.connexion() as conn:
        assert conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0] == 1
    assert base.meilleur_email(e)["confiance"] == "haute"


def _envoyee(entreprise_id, email, statut="envoyee", canal="email"):
    c = base.creer_candidature(entreprise_id, canal, email)
    base.maj_statut(c, statut, expediteur="moi@example.com")
    return c


def test_deja_contactee_meme_entreprise():
    e = base.ajouter_entreprise("Acme", "maps", source_id="p1")
    assert not base.deja_contactee(e)
    _envoyee(e, "rh@acme.fr")
    assert base.deja_contactee(e)


def test_deja_contactee_meme_domaine_autre_adresse():
    a = base.ajouter_entreprise("Acme Lyon", "maps", source_id="p1")
    b = base.ajouter_entreprise("Acme Paris", "maps", source_id="p2")
    _envoyee(a, "rh@acme.fr")
    assert base.deja_contactee(b, "contact@acme.fr")


def test_deja_contactee_meme_domaine_de_site():
    a = base.ajouter_entreprise("Acme Lyon", "maps", source_id="p1", site="https://acme.fr")
    b = base.ajouter_entreprise("Acme Paris", "lba", source_id="l2", site="https://www.acme.fr/agence")
    _envoyee(a, "rh@acme.fr")
    assert base.deja_contactee(b)


def test_deja_contactee_meme_siret():
    a = base.ajouter_entreprise("Acme", "lba", source_id="l1", siret="12345678900011")
    b = base.ajouter_entreprise("Acme", "maps", source_id="p1")
    with base.connexion() as conn:
        conn.execute("UPDATE entreprises SET siret = '12345678900011' WHERE id = ?", (b,))
    _envoyee(a, None, "postee_lba", "lba")
    assert base.deja_contactee(b)


def test_boite_grand_public_ne_bloque_pas_les_autres():
    a = base.ajouter_entreprise("Petit artisan", "maps", source_id="p1")
    b = base.ajouter_entreprise("Autre artisan", "maps", source_id="p2")
    _envoyee(a, "artisan1@gmail.com")
    assert not base.deja_contactee(b, "artisan2@gmail.com")
    assert base.deja_contactee(b, "artisan1@gmail.com")


def test_candidature_en_file_ne_compte_pas():
    e = base.ajouter_entreprise("Acme", "maps", source_id="p1")
    base.creer_candidature(e, "email", "rh@acme.fr")
    assert not base.deja_contactee(e, "rh@acme.fr")


def test_rebond_ne_compte_que_pour_la_meme_adresse():
    e = base.ajouter_entreprise("Acme", "maps", source_id="p1")
    _envoyee(e, "rh@acme.fr", "rebond")
    assert base.deja_contactee(e, "rh@acme.fr")
    assert not base.deja_contactee(e, "contact@acme.fr")


def test_envoyes_aujourdhui_par_expediteur():
    e = base.ajouter_entreprise("Acme", "maps", source_id="p1")
    f = base.ajouter_entreprise("Beta", "maps", source_id="p2")
    c1 = base.creer_candidature(e, "email", "rh@acme.fr")
    c2 = base.creer_candidature(f, "email", "rh@beta.fr")
    base.maj_statut(c1, "envoyee", expediteur="a@example.com")
    base.maj_statut(c2, "envoyee", expediteur="b@example.com")
    assert base.envoyes_aujourdhui("a@example.com") == 1
    assert base.envoyes_aujourdhui("autre@example.com") == 0
    base.maj_statut(c1, "reponse")
    assert base.envoyes_aujourdhui("a@example.com") == 1


def test_postees_lba_aujourdhui():
    e = base.ajouter_entreprise("Acme", "lba", source_id="l1")
    _envoyee(e, None, "postee_lba", "lba")
    assert base.postees_lba_aujourdhui() == 1
    assert base.envoyes_aujourdhui("moi@example.com") == 0


def test_candidatures_en_file_et_maj_statut():
    e = base.ajouter_entreprise("Acme", "maps", source_id="p1")
    c = base.creer_candidature(e, "email", "rh@acme.fr", objet="Alternance")
    f = base.candidatures_en_file()
    assert [x["id"] for x in f] == [c] and f[0]["entreprise_nom"] == "Acme"
    assert base.candidatures_en_file(canal="lba") == []
    base.maj_statut(c, "echec", erreur="SMTP refusé")
    assert base.candidatures_en_file() == []
    with base.connexion() as conn:
        ligne = conn.execute("SELECT * FROM candidatures WHERE id = ?", (c,)).fetchone()
    assert ligne["erreur"] == "SMTP refusé" and ligne["envoyee_le"] is None


def test_statut_invalide_refuse():
    e = base.ajouter_entreprise("Acme", "maps", source_id="p1")
    c = base.creer_candidature(e, "email", "rh@acme.fr")
    with pytest.raises(sqlite3.IntegrityError):
        base.maj_statut(c, "inconnu")


def test_stats():
    e = base.ajouter_entreprise("Acme", "maps", source_id="p1")
    base.ajouter_email(e, "rh@acme.fr")
    _envoyee(e, "rh@acme.fr")
    s = base.stats()
    assert s["entreprises"] == 1 and s["entreprises_avec_email"] == 1
    assert s["candidatures"] == 1 and s["par_statut"]["envoyee"] == 1 and s["mails_aujourdhui"] == 1


def test_racine_et_page_du_site_sans_parametres_de_suivi():
    url = "https://exemple.fr/agence-lyon/?utm_source=google&utm_medium=gbp"
    assert base.racine_site(url) == "https://exemple.fr"
    assert base.page_site(url) == "https://exemple.fr/agence-lyon"
    assert base.racine_site("exemple.fr") == "https://exemple.fr"
    assert base.racine_site("") is None


def test_base_d_une_version_precedente_migree(profil):
    import sqlite3
    ancienne = sqlite3.connect(profil / "alternabot.db")
    ancienne.execute("CREATE TABLE entreprises (id INTEGER PRIMARY KEY AUTOINCREMENT, nom TEXT NOT NULL, siret TEXT,"
                     " adresse TEXT, ville TEXT, site TEXT, domaine TEXT, telephone TEXT, secteur TEXT,"
                     " source TEXT NOT NULL, source_id TEXT UNIQUE, cree_le TEXT NOT NULL)")
    ancienne.commit()
    ancienne.close()
    with base.connexion() as conn:
        assert "emails_cherches_le" in {l["name"] for l in conn.execute("PRAGMA table_info(entreprises)")}
