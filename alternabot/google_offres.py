"""Offres publiées sur Google (SerpAPI, moteur google_jobs) : l'entreprise qui recrute et son offre sont gardées en base.

Une seule requête SerpAPI par lancement (le plan gratuit en donne 100 par mois). Le mail spontané rédigé
ensuite pour ces entreprises cite le titre de l'offre (`redaction.mail_spontane`).
"""
import hashlib

import requests

from googlemaps.exceptions import ApiError, TransportError

from . import base, config, maps, redaction
from .cli import commande

ENDPOINT = "https://serpapi.com/search.json"
DELAI_API_S = 30
MESSAGE_CLE = ("Clé SerpAPI manquante : crée un compte gratuit (100 recherches par mois) sur https://serpapi.com "
               "puis ajoute SERPAPI_KEY dans profil/.env")


# Sites d'emploi et logiciels de recrutement : le lien d'une offre vers eux ne donne pas le site de l'entreprise.
SITES_EMPLOI = ("indeed.", "linkedin.", "welcometothejungle.", "hellowork.", "jobteaser.", "monster.", "glassdoor.",
                "francetravail.", "pole-emploi.", "apec.", "jooble.", "talent.com", "meteojob.", "regionsjob.",
                "cadremploi.", "jobijoba.", "labonnealternance.", "ziprecruiter.", "google.", "lever.co",
                "workable.", "teamtailor.", "smartrecruiters.", "recruitee.", "welcomekit.", "jobs.", "emploi.",
                "alternance.", "studentjob.", "iquesta.", "letudiant.", "1jeune1solution.", "optioncarriere.")


class ErreurSerpApi(RuntimeError):
    """SerpAPI n'a pas répondu comme prévu."""


def requete_par_defaut(cfg: dict) -> str:
    """« alternance <mots-clés des offres> <ville> », d'après la configuration."""
    mots = [str(m).strip() for m in (config.obtenir(cfg, "recherche.mots_cles_offres", []) or []) if str(m).strip()]
    ville = str(config.obtenir(cfg, "recherche.ville", "")).strip()
    return " ".join(["alternance"] + mots + ([ville] if ville else []))


def _texte(valeur) -> str | None:
    valeur = str(valeur).strip() if valeur is not None else ""
    return valeur or None


def _normaliser(brute: dict) -> dict | None:
    """Aplatit une offre google_jobs ; None sans nom d'entreprise ou sans titre."""
    entreprise = _texte(brute.get("company_name"))
    titre = _texte(brute.get("title"))
    if not entreprise or not titre:
        return None
    options = [o for o in (brute.get("apply_options") or []) if isinstance(o, dict) and o.get("link")]
    lien = _texte(options[0]["link"]) if options else _texte(brute.get("share_link"))
    lieu = _texte(brute.get("location"))
    identifiant = _texte(brute.get("job_id")) or hashlib.sha1(
        "|".join((redaction._plier(entreprise), redaction._plier(titre), redaction._plier(lieu or ""))).encode()).hexdigest()[:24]
    return {"entreprise": entreprise, "titre": titre, "lien": lien, "lieu": lieu, "source_id": f"google:{identifiant}"}


def chercher_offres(cfg: dict, requete: str | None = None) -> list[dict]:
    """Une requête SerpAPI google_jobs. Rend les offres normalisées (entreprise, titre, lien, lieu, source_id)."""
    cle = str(config.obtenir(cfg, "google_offres.cle", "")).strip()
    if not cle:
        raise config.ErreurConfig(MESSAGE_CLE)
    requete = (requete or "").strip() or requete_par_defaut(cfg)
    try:
        reponse = requests.get(ENDPOINT, params={"engine": "google_jobs", "q": requete, "hl": "fr", "gl": "fr",
                                                 "api_key": cle}, timeout=DELAI_API_S)
    except requests.RequestException as e:
        raise ErreurSerpApi(f"SerpAPI injoignable : {e}") from e
    if reponse.status_code in (401, 403):
        raise ErreurSerpApi("SerpAPI refuse la clé : vérifie SERPAPI_KEY dans profil/.env")
    if reponse.status_code == 429:
        raise ErreurSerpApi("SerpAPI : quota de recherches atteint pour ce mois")
    try:
        donnees = reponse.json()
    except ValueError:
        donnees = None
    if not isinstance(donnees, dict):
        raise ErreurSerpApi(f"SerpAPI : réponse illisible (HTTP {reponse.status_code})")
    erreur = donnees.get("error")
    if erreur and "hasn't returned any results" not in str(erreur):
        raise ErreurSerpApi(f"SerpAPI : {erreur}")
    offres: dict[str, dict] = {}
    for brute in donnees.get("jobs_results") or []:
        offre = _normaliser(brute) if isinstance(brute, dict) else None
        if offre and offre["source_id"] not in offres:
            offres[offre["source_id"]] = offre
    return list(offres.values())


def _entreprise_connue(nom: str) -> int | None:
    """Id de l'entreprise de la base (toute source) qui porte ce nom, à la forme juridique près."""
    cible = redaction._nom_distinctif(redaction.nom_usuel(nom))
    if not cible:
        return None
    with base.connexion() as conn:
        for ligne in conn.execute("SELECT id, nom FROM entreprises ORDER BY id"):
            if redaction._nom_distinctif(redaction.nom_usuel(ligne["nom"])) == cible:
                return ligne["id"]
    return None


def enregistrer_offres(offres: list[dict], limite: int | None = None) -> dict:
    """Garde en base les entreprises qui recrutent et leur offre.

    Sont sautées : l'offre déjà en base, l'entreprise déjà contactée. Une entreprise déjà en base (Maps, LBA)
    reçoit l'offre sans doublon. S'arrête à `limite` offres gardées. Rend {"gardees": [ids d'entreprises],
    "deja_en_base": n, "deja_contactees": n}.
    """
    bilan = {"gardees": [], "deja_en_base": 0, "deja_contactees": 0}
    gardees = 0
    for offre in offres:
        if limite is not None and gardees >= limite:
            break
        with base.connexion() as conn:
            if conn.execute("SELECT 1 FROM offres WHERE source_id = ?", (offre["source_id"],)).fetchone():
                bilan["deja_en_base"] += 1
                continue
        identifiant = _entreprise_connue(offre["entreprise"])
        if identifiant is not None and base.deja_contactee(identifiant):
            bilan["deja_contactees"] += 1
            continue
        if identifiant is None:
            identifiant = base.ajouter_entreprise(
                offre["entreprise"], "google", source_id="google:" + redaction._plier(offre["entreprise"]),
                ville=offre.get("lieu"), secteur="offre Google")
        base.ajouter_offre(identifiant, offre["titre"], lien=offre.get("lien"), lieu=offre.get("lieu"),
                           source_id=offre["source_id"])
        if identifiant not in bilan["gardees"]:
            bilan["gardees"].append(identifiant)
        gardees += 1
    return bilan


def _site_par_le_lien(lien: str | None) -> str | None:
    """Racine du lien de l'offre, sauf s'il mène à un site d'emploi ou à un logiciel de recrutement."""
    domaine = base.domaine_de(lien) or ""
    if not domaine or any(domaine.startswith(s) or f".{s}" in f".{domaine}" for s in SITES_EMPLOI):
        return None
    return base.racine_site(lien)


def completer_sites(cfg: dict, entreprises: list[int]) -> int:
    """Donne un site aux entreprises venues d'une offre : Google Maps (nom et lieu), sinon le lien de l'offre.

    Sans site, `emails` ne trouve aucune adresse. Maps est facturé (environ 0,04 $ par entreprise, dans le
    budget `maps.budget_usd`) et seulement si la clé existe. Rend le nombre de sites trouvés.
    """
    with base.connexion() as conn:
        a_completer = [dict(l) for l in conn.execute(
            f"SELECT id, nom, ville FROM entreprises WHERE id IN ({','.join('?' * len(entreprises))}) "
            "AND COALESCE(site, '') = ''", entreprises)] if entreprises else []
    cle = str(config.obtenir(cfg, "maps.cle", "")).strip()
    client = maps._client(cle) if cle and a_completer else None
    budget = maps._Budget(config.obtenir(cfg, "maps.budget_usd", 5) or 5)
    trouves = 0
    for e in a_completer:
        site = None
        if client:
            try:
                budget.facturer("trouver")
                lieux = client.find_place(f"{e['nom']} {e['ville'] or ''}".strip(), "textquery",
                                          fields=["place_id", "name"], language="fr").get("candidates", [])
                if lieux:
                    site = maps._detail(client, budget, lieux[0]["place_id"]).get("website")
            except maps.BudgetEpuise as erreur:
                print(f"Google Maps : {erreur}, les sites restants viennent du lien de l'offre")
                client = None
            except (ApiError, TransportError) as erreur:
                print(f"{e['nom']} : Google Maps indisponible ({erreur})")
        site = site or _site_par_le_lien((base.offre_de(e["id"]) or {}).get("lien"))
        if site:
            with base.connexion() as conn:
                conn.execute("UPDATE entreprises SET site = ?, domaine = ? WHERE id = ?",
                             (site, base.domaine_de(site), e["id"]))
            trouves += 1
    return trouves


def trouver_offres(cfg: dict, requete: str | None = None, limite: int | None = None) -> dict:
    """Cherche (une requête SerpAPI) puis enregistre. Rend le bilan de `enregistrer_offres` plus `trouvees`."""
    requete = (requete or "").strip() or requete_par_defaut(cfg)
    print(f"Recherche Google Jobs : {requete}")
    offres = chercher_offres(cfg, requete)
    bilan = enregistrer_offres(offres, limite)
    bilan["trouvees"] = len(offres)
    bilan["sites"] = completer_sites(cfg, bilan["gardees"])
    return bilan


def _arguments(parser) -> None:
    parser.add_argument("--requete", default=None,
                        help="recherche Google Jobs (défaut : alternance + recherche.mots_cles_offres + ville)")
    parser.add_argument("--limite", type=int, default=None, help="nombre maximal d'offres à garder")


@commande("offres", "Cherche les offres d'alternance publiées sur Google (SerpAPI) et garde les entreprises", _arguments)
def _offres(args) -> int:
    cfg = config.charger()
    try:
        bilan = trouver_offres(cfg, args.requete, args.limite)
    except ErreurSerpApi as e:
        print(f"Erreur : {e}")
        return 1
    print(f"{bilan['trouvees']} offres trouvées : {len(bilan['gardees'])} entreprises gardées, "
          f"{bilan['deja_en_base']} offres déjà en base, {bilan['deja_contactees']} entreprises déjà contactées sautées.")
    if bilan["gardees"]:
        with base.connexion() as conn:
            marques = ",".join("?" * len(bilan["gardees"]))
            sans_site = conn.execute(f"SELECT COUNT(*) FROM entreprises WHERE id IN ({marques}) "
                                     "AND COALESCE(site, '') = ''", bilan["gardees"]).fetchone()[0]
        if sans_site:
            print(f"{sans_site} sans site connu : `emails` ne leur trouvera pas d'adresse, ajoute le site à la main.")
    return 0
