"""Base SQLite (profil/alternabot.db) : entreprises, adresses trouvées, candidatures."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import urlparse

from . import config

STATUTS = ("en_file", "envoyee", "echec", "rebond", "reponse", "entretien", "refus", "postee_lba", "essai")
# Statuts qui prouvent qu'une candidature est partie vers l'entreprise.
STATUTS_CONTACTEE = ("envoyee", "postee_lba", "reponse", "entretien", "refus")
STATUTS_ENVOYES = ("envoyee", "postee_lba")
# Boîtes grand public : le domaine ne désigne pas une entreprise.
DOMAINES_GRATUITS = {
    "gmail.com", "googlemail.com", "outlook.com", "outlook.fr", "hotmail.com", "hotmail.fr", "live.com",
    "live.fr", "yahoo.com", "yahoo.fr", "orange.fr", "wanadoo.fr", "free.fr", "sfr.fr", "laposte.net",
    "icloud.com", "me.com", "proton.me", "protonmail.com", "gmx.fr", "gmx.com", "bbox.fr",
}

def _liste_sql(valeurs) -> str:
    return ", ".join(repr(v) for v in valeurs)


_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS entreprises (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    nom        TEXT NOT NULL,
    siret      TEXT,
    adresse    TEXT,
    ville      TEXT,
    site       TEXT,
    domaine    TEXT,
    telephone  TEXT,
    secteur    TEXT,
    source     TEXT NOT NULL CHECK (source IN ('lba', 'maps', 'google')),
    source_id  TEXT UNIQUE,
    cree_le    TEXT NOT NULL,
    emails_cherches_le TEXT
);
CREATE TABLE IF NOT EXISTS emails (
    entreprise_id INTEGER NOT NULL REFERENCES entreprises(id),
    email         TEXT NOT NULL,
    methode       TEXT,
    confiance     TEXT NOT NULL DEFAULT 'moyenne' CHECK (confiance IN ('haute', 'moyenne', 'basse')),
    verifie       INTEGER NOT NULL DEFAULT 0,
    cree_le       TEXT NOT NULL,
    UNIQUE (entreprise_id, email)
);
CREATE TABLE IF NOT EXISTS candidatures (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entreprise_id INTEGER NOT NULL REFERENCES entreprises(id),
    canal         TEXT NOT NULL CHECK (canal IN ('lba', 'email')),
    email_utilise TEXT,
    objet         TEXT,
    corps_chemin  TEXT,
    lettre_chemin TEXT,
    statut        TEXT NOT NULL DEFAULT 'en_file' CHECK (statut IN ({_liste_sql(STATUTS)})),
    expediteur    TEXT,
    envoyee_le    TEXT,
    erreur        TEXT,
    cree_le       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidatures_entreprise ON candidatures(entreprise_id);
CREATE TABLE IF NOT EXISTS offres (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entreprise_id INTEGER NOT NULL REFERENCES entreprises(id),
    titre         TEXT NOT NULL,
    lien          TEXT,
    lieu          TEXT,
    source_id     TEXT UNIQUE,
    cree_le       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_offres_entreprise ON offres(entreprise_id);
"""


def _maintenant() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _aujourdhui() -> str:
    return datetime.now().date().isoformat()


def domaine_de(adresse: str | None) -> str | None:
    """Domaine d'une URL ou d'un email, sans « www. », en minuscules."""
    if not adresse:
        return None
    adresse = adresse.strip().lower()
    if "@" in adresse:
        domaine = adresse.rsplit("@", 1)[1]
    else:
        domaine = urlparse(adresse if "//" in adresse else "//" + adresse).netloc.split(":")[0]
    return domaine.removeprefix("www.") or None


def racine_site(url: str | None) -> str | None:
    """« https://site.fr » d'une URL quelconque (chemin, paramètres de suivi et fragment retirés)."""
    if not url or not url.strip():
        return None
    url = url.strip()
    morceaux = urlparse(url if "//" in url else "https://" + url)
    return f"{morceaux.scheme or 'https'}://{morceaux.netloc}" if morceaux.netloc else None


def page_site(url: str | None) -> str | None:
    """L'URL donnée sans ses paramètres ni son fragment (« ?utm_source=... » retiré)."""
    if not url or not url.strip():
        return None
    url = url.strip()
    morceaux = urlparse(url if "//" in url else "https://" + url)
    return f"{morceaux.scheme or 'https'}://{morceaux.netloc}{morceaux.path}".rstrip("/") if morceaux.netloc else None


def _migrer(conn: sqlite3.Connection) -> None:
    """Ajoute aux bases créées par une version précédente les colonnes apparues depuis."""
    colonnes = {l["name"] for l in conn.execute("PRAGMA table_info(entreprises)")}
    if "emails_cherches_le" not in colonnes:
        conn.execute("ALTER TABLE entreprises ADD COLUMN emails_cherches_le TEXT")


@contextmanager
def connexion():
    """Connexion à la base du profil (tables créées si besoin), validée puis fermée à la sortie."""
    chemin = config.dossier_profil() / "alternabot.db"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(chemin)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.executescript(_SCHEMA)
        _migrer(conn)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init() -> None:
    """Crée la base et ses tables si elles n'existent pas."""
    with connexion():
        pass


def ajouter_entreprise(nom: str, source: str, source_id: str | None = None, siret: str | None = None,
                       adresse: str | None = None, ville: str | None = None, site: str | None = None,
                       telephone: str | None = None, secteur: str | None = None) -> int:
    """Ajoute une entreprise, ou complète celle qui a le même source_id ou le même siret. Rend son id."""
    champs = {"nom": nom, "siret": siret, "adresse": adresse, "ville": ville, "site": site,
              "domaine": domaine_de(site), "telephone": telephone, "secteur": secteur}
    with connexion() as conn:
        existante = None
        if source_id:
            existante = conn.execute("SELECT id FROM entreprises WHERE source_id = ?", (source_id,)).fetchone()
        if not existante and siret:
            existante = conn.execute("SELECT id FROM entreprises WHERE siret = ?", (siret,)).fetchone()
        if existante:
            affectations = ", ".join(f"{c} = COALESCE(:{c}, {c})" for c in champs)
            conn.execute(f"UPDATE entreprises SET {affectations} WHERE id = :id", {**champs, "id": existante["id"]})
            return existante["id"]
        cur = conn.execute(
            """INSERT INTO entreprises (nom, siret, adresse, ville, site, domaine, telephone, secteur,
                                        source, source_id, cree_le)
               VALUES (:nom, :siret, :adresse, :ville, :site, :domaine, :telephone, :secteur,
                       :source, :source_id, :cree_le)""",
            {**champs, "source": source, "source_id": source_id, "cree_le": _maintenant()},
        )
        return cur.lastrowid


def ajouter_email(entreprise_id: int, email: str, methode: str | None = None,
                  confiance: str = "moyenne", verifie: bool = False) -> None:
    """Enregistre une adresse pour une entreprise (mise à jour si elle existe déjà)."""
    with connexion() as conn:
        conn.execute(
            """INSERT INTO emails (entreprise_id, email, methode, confiance, verifie, cree_le)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (entreprise_id, email) DO UPDATE
               SET methode = excluded.methode, confiance = excluded.confiance, verifie = excluded.verifie""",
            (entreprise_id, email.strip().lower(), methode, confiance, int(verifie), _maintenant()),
        )


def meilleur_email(entreprise_id: int) -> dict | None:
    """Adresse la plus fiable d'une entreprise : confiance, puis vérifiée, puis la plus ancienne."""
    with connexion() as conn:
        ligne = conn.execute(
            """SELECT * FROM emails WHERE entreprise_id = ?
               ORDER BY CASE confiance WHEN 'haute' THEN 0 WHEN 'moyenne' THEN 1 ELSE 2 END,
                        verifie DESC, cree_le, rowid
               LIMIT 1""",
            (entreprise_id,),
        ).fetchone()
    return dict(ligne) if ligne else None


def deja_contactee(entreprise_id: int, email: str | None = None) -> bool:
    """Vrai si une candidature envoyée ou postée existe déjà pour cette entreprise, pour le même siret,
    pour le même domaine (site ou adresse, hors boîtes grand public) ou pour la même adresse.
    Un rebond ne compte que pour la même adresse : une autre adresse de l'entreprise reste permise."""
    email = email.strip().lower() if email else None
    domaine_mail = domaine_de(email)
    if domaine_mail in DOMAINES_GRATUITS:
        domaine_mail = None
    with connexion() as conn:
        ent = conn.execute("SELECT siret, domaine FROM entreprises WHERE id = ?", (entreprise_id,)).fetchone()
        siret = ent["siret"] if ent else None
        domaine = ent["domaine"] if ent else None
        ligne = conn.execute(
            f"""SELECT 1 FROM candidatures c JOIN entreprises e ON e.id = c.entreprise_id
                WHERE (c.statut IN ({_liste_sql(STATUTS_CONTACTEE)})
                       OR (c.statut = 'rebond' AND lower(c.email_utilise) = :email))
                  AND (c.entreprise_id = :id
                       OR (:siret IS NOT NULL AND e.siret = :siret)
                       OR (:domaine IS NOT NULL AND e.domaine = :domaine)
                       OR (:domaine_mail IS NOT NULL
                           AND lower(substr(c.email_utilise, instr(c.email_utilise, '@') + 1)) = :domaine_mail)
                       OR (:email IS NOT NULL AND lower(c.email_utilise) = :email))
                LIMIT 1""",
            {"id": entreprise_id, "siret": siret, "domaine": domaine, "domaine_mail": domaine_mail, "email": email},
        ).fetchone()
    return ligne is not None


def ajouter_offre(entreprise_id: int, titre: str, lien: str | None = None, lieu: str | None = None,
                  source_id: str | None = None) -> int:
    """Enregistre une offre d'emploi liée à une entreprise (mise à jour si le source_id existe). Rend son id."""
    with connexion() as conn:
        existante = conn.execute("SELECT id FROM offres WHERE source_id = ?", (source_id,)).fetchone() if source_id else None
        if existante:
            conn.execute("UPDATE offres SET entreprise_id = ?, titre = ?, lien = COALESCE(?, lien), lieu = COALESCE(?, lieu) "
                         "WHERE id = ?", (entreprise_id, titre, lien, lieu, existante["id"]))
            return existante["id"]
        cur = conn.execute(
            "INSERT INTO offres (entreprise_id, titre, lien, lieu, source_id, cree_le) VALUES (?, ?, ?, ?, ?, ?)",
            (entreprise_id, titre, lien, lieu, source_id, _maintenant()))
        return cur.lastrowid


def offre_de(entreprise_id: int) -> dict | None:
    """Offre la plus récente gardée pour une entreprise (titre, lien, lieu), ou None."""
    with connexion() as conn:
        ligne = conn.execute("SELECT * FROM offres WHERE entreprise_id = ? ORDER BY id DESC LIMIT 1",
                             (entreprise_id,)).fetchone()
    return dict(ligne) if ligne else None


def creer_candidature(entreprise_id: int, canal: str, email_utilise: str | None = None,
                      objet: str | None = None, corps_chemin: str | None = None,
                      lettre_chemin: str | None = None, statut: str = "en_file",
                      expediteur: str | None = None) -> int:
    """Crée une candidature (en file par défaut) et rend son id."""
    with connexion() as conn:
        cur = conn.execute(
            """INSERT INTO candidatures (entreprise_id, canal, email_utilise, objet, corps_chemin,
                                         lettre_chemin, statut, expediteur, cree_le)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (entreprise_id, canal, email_utilise, objet, corps_chemin, lettre_chemin, statut,
             expediteur, _maintenant()),
        )
        return cur.lastrowid


def maj_statut(candidature_id: int, statut: str, erreur: str | None = None,
               expediteur: str | None = None) -> None:
    """Change le statut ; date l'envoi quand la candidature est envoyée ou postée."""
    with connexion() as conn:
        conn.execute(
            f"""UPDATE candidatures
                SET statut = :statut, erreur = :erreur, expediteur = COALESCE(:expediteur, expediteur),
                    envoyee_le = CASE WHEN :statut IN ({_liste_sql(STATUTS_ENVOYES)})
                                      THEN :maintenant ELSE envoyee_le END
                WHERE id = :id""",
            {"statut": statut, "erreur": erreur, "expediteur": expediteur,
             "maintenant": _maintenant(), "id": candidature_id},
        )


def candidatures_en_file(canal: str | None = None, limite: int | None = None) -> list[dict]:
    """Candidatures en file, de la plus ancienne à la plus récente, avec le nom de l'entreprise."""
    requete = """SELECT c.*, e.nom AS entreprise_nom FROM candidatures c
                 JOIN entreprises e ON e.id = c.entreprise_id
                 WHERE c.statut = 'en_file' AND (:canal IS NULL OR c.canal = :canal)
                 ORDER BY c.cree_le, c.id LIMIT :limite"""
    with connexion() as conn:
        lignes = conn.execute(requete, {"canal": canal, "limite": -1 if limite is None else limite}).fetchall()
    return [dict(ligne) for ligne in lignes]


def _compter_du_jour(canal: str, expediteur: str | None = None) -> int:
    with connexion() as conn:
        return conn.execute(
            """SELECT COUNT(*) FROM candidatures
               WHERE canal = :canal AND substr(envoyee_le, 1, 10) = :jour
                 AND (:expediteur IS NULL OR expediteur = :expediteur)""",
            {"canal": canal, "jour": _aujourdhui(), "expediteur": expediteur},
        ).fetchone()[0]


def envoyes_aujourdhui(expediteur: str) -> int:
    """Mails envoyés aujourd'hui depuis cette boîte."""
    return _compter_du_jour("email", expediteur)


def postees_lba_aujourdhui() -> int:
    """Candidatures postées aujourd'hui sur La Bonne Alternance."""
    return _compter_du_jour("lba")


def stats() -> dict:
    """Compteurs pour la commande statut."""
    with connexion() as conn:
        par_statut = {s: 0 for s in STATUTS}
        par_statut.update({l["statut"]: l["n"] for l in conn.execute(
            "SELECT statut, COUNT(*) AS n FROM candidatures GROUP BY statut")})
        return {
            "entreprises": conn.execute("SELECT COUNT(*) FROM entreprises").fetchone()[0],
            "entreprises_avec_email": conn.execute("SELECT COUNT(DISTINCT entreprise_id) FROM emails").fetchone()[0],
            "candidatures": sum(par_statut.values()),
            "par_statut": par_statut,
            "mails_aujourdhui": _compter_du_jour("email"),
            "lba_aujourdhui": _compter_du_jour("lba"),
            "entretiens": [l[0] for l in conn.execute(
                """SELECT DISTINCT e.nom FROM candidatures c JOIN entreprises e ON e.id = c.entreprise_id
                   WHERE c.statut = 'entretien' ORDER BY e.nom""")],
        }
