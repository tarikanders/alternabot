"""La Bonne Alternance : recherche d'offres et d'entreprises par l'API Alternance, enregistrement en base."""
import json
import re

import requests

from . import base, config

ENDPOINT = "https://api.apprentissage.beta.gouv.fr/api/job/v1/search"
RAYONS = (10, 30, 60, 100)  # rayons acceptés par l'API, en km
MESSAGE_JETON = ("Jeton La Bonne Alternance manquant : crée un compte gratuit sur "
                 "https://api.apprentissage.beta.gouv.fr, puis ajoute LBA_API_TOKEN dans profil/.env")
FICHIER_FICHES = "lba_fiches.json"
DELAI_API_S = 20


class ErreurLBA(RuntimeError):
    """L'API La Bonne Alternance n'a pas répondu comme prévu."""


def arrondir_rayon(rayon_km: float) -> int:
    """Plus petit rayon accepté par l'API qui couvre le rayon demandé (100 au plus)."""
    return next((r for r in RAYONS if rayon_km <= r), RAYONS[-1])


def _dig(donnees, *chemin, defaut=None):
    """Accès tolérant à un champ imbriqué : la réponse de l'API peut varier légèrement."""
    courant = donnees
    for cle in chemin:
        if not isinstance(courant, dict):
            return defaut
        courant = courant.get(cle)
    return courant if courant is not None else defaut


def _ville_de(lieu: dict, adresse: str | None) -> str | None:
    ville = lieu.get("city") or lieu.get("commune")
    if ville:
        return str(ville).strip()
    correspondance = re.search(r"\b\d{5}\s+(.+)$", adresse or "")
    return correspondance.group(1).strip().title() if correspondance else None


def _normaliser(element: dict, type_fiche: str) -> dict | None:
    """Aplatit une offre ou une entreprise ; None sans nom ou sans recipient_id (pas de candidature simplifiée)."""
    lieu_travail = element.get("workplace") or {}
    candidature = element.get("apply") or {}
    recipient_id = candidature.get("recipient_id")
    nom = lieu_travail.get("name") or lieu_travail.get("legal_name") or lieu_travail.get("brand") or ""
    if not recipient_id or not nom.strip():
        return None
    siret = lieu_travail.get("siret")
    identifiant = siret or _dig(element, "identifier", "id") or recipient_id
    lieu = lieu_travail.get("location") or {}
    adresse = lieu.get("address")
    return {
        "nom": nom.strip(),
        "siret": siret,
        "adresse": adresse,
        "ville": _ville_de(lieu, adresse),
        "site": lieu_travail.get("website"),
        "telephone": candidature.get("phone"),
        "url": candidature.get("url"),
        "recipient_id": recipient_id,
        "type": type_fiche,
        "intitule": _dig(element, "offer", "title"),
        "source_id": f"lba:{identifiant}",
    }


def chercher(cfg: dict, romes: list[str] | None = None, latitude: float | None = None,
             longitude: float | None = None, rayon_km: float | None = None) -> list[dict]:
    """Fiches postulables autour du point (par défaut la zone de recherche du profil), offres d'abord."""
    jeton = str(config.obtenir(cfg, "lba.jeton", "")).strip()
    if not jeton:
        raise config.ErreurConfig(MESSAGE_JETON)
    romes = list(romes or config.obtenir(cfg, "recherche.codes_rome", []) or [])
    if not romes:
        raise config.ErreurConfig("recherche.codes_rome est vide : indique au moins un code ROME dans config.yaml")
    latitude = config.obtenir(cfg, "recherche.latitude") if latitude is None else latitude
    longitude = config.obtenir(cfg, "recherche.longitude") if longitude is None else longitude
    rayon = config.obtenir(cfg, "recherche.rayon_km", 30) if rayon_km is None else rayon_km
    if latitude is None or longitude is None:
        raise config.ErreurConfig("recherche.latitude et recherche.longitude sont nécessaires")
    parametres = {"romes": ",".join(romes[:20]), "latitude": latitude, "longitude": longitude,
                  "radius": arrondir_rayon(rayon)}
    entetes = {"Authorization": f"Bearer {jeton}", "Accept": "application/json"}
    try:
        reponse = requests.get(ENDPOINT, params=parametres, headers=entetes, timeout=DELAI_API_S)
    except requests.RequestException as e:
        raise ErreurLBA(f"API La Bonne Alternance injoignable : {e}") from e
    if reponse.status_code in (401, 403):
        raise ErreurLBA("Jeton La Bonne Alternance refusé : vérifie LBA_API_TOKEN dans profil/.env")
    if not reponse.ok:
        raise ErreurLBA(f"API La Bonne Alternance : erreur HTTP {reponse.status_code}")
    donnees = reponse.json()
    brutes = ([(o, "offre") for o in donnees.get("jobs") or donnees.get("offers") or []]
              + [(e, "entreprise") for e in donnees.get("recruiters") or donnees.get("companies") or []])
    fiches: dict[str, dict] = {}
    for element, type_fiche in brutes:
        fiche = _normaliser(element, type_fiche)
        if fiche and fiche["source_id"] not in fiches:  # une offre précède l'entreprise du même siret
            fiches[fiche["source_id"]] = fiche
    return list(fiches.values())


def fiches_enregistrees() -> dict[str, dict]:
    """Fiches mémorisées par `enregistrer`, par source_id (la base ne garde ni l'URL ni le type)."""
    fichier = config.dossier_profil() / FICHIER_FICHES
    if not fichier.is_file():
        return {}
    try:
        return json.loads(fichier.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def enregistrer(fiches: list[dict]) -> list[int]:
    """Ajoute les entreprises à la base (source « lba ») et mémorise leurs fiches. Rend les ids, sans doublon."""
    memoire = fiches_enregistrees()
    ids: list[int] = []
    for fiche in fiches:
        identifiant = base.ajouter_entreprise(
            fiche["nom"], "lba", source_id=fiche["source_id"], siret=fiche.get("siret"),
            adresse=fiche.get("adresse"), ville=fiche.get("ville"), site=fiche.get("site"),
            telephone=fiche.get("telephone"))
        if identifiant not in ids:
            ids.append(identifiant)
        ancienne = memoire.get(fiche["source_id"])
        if not (ancienne and ancienne.get("type") == "offre" and fiche.get("type") != "offre"):
            memoire[fiche["source_id"]] = fiche
    fichier = config.dossier_profil() / FICHIER_FICHES
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_text(json.dumps(memoire, ensure_ascii=False, indent=1), encoding="utf-8")
    return ids
