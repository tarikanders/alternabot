"""Google Maps : recherche d'entreprises autour d'une ville, avec un budget en dollars qui ne se dépasse pas."""
import time

import googlemaps
from googlemaps.exceptions import ApiError

from . import base, config
from .cli import commande

MESSAGE_CLE = ("Clé Google Maps manquante : crée-en une (Places API et Geocoding API activées), voir "
               "https://developers.google.com/maps/documentation/places/web-service/get-api-key "
               "puis ajoute GOOGLE_MAPS_API_KEY dans profil/.env")
# Tarifs de liste en dollars par appel, volontairement majorés : le plafond sous-estime le nombre d'appels
# permis, jamais l'inverse (la facture réelle reste inférieure ou égale à l'estimation).
PRIX_USD = {"recherche": 0.032, "details": 0.025, "geocode": 0.005, "trouver": 0.017}
RAYON_MAX_KM = 50  # limite de la recherche à proximité
DELAI_PAGE_S = 2.0  # Google active le jeton de page suivante après un délai variable
ESSAIS_PAGE = 4
DELAI_ENTRE_SECTEURS_S = 0.5
CHAMPS_DETAILS = ["name", "formatted_address", "website", "formatted_phone_number"]


class BudgetEpuise(RuntimeError):
    """Un appel de plus ferait dépasser le budget."""


class _Budget:
    """Dépense estimée, vérifiée AVANT chaque appel payant."""

    def __init__(self, plafond_usd: float):
        self.plafond = float(plafond_usd)
        self.depense = 0.0

    def facturer(self, nature: str) -> None:
        cout = PRIX_USD[nature]
        if self.depense + cout > self.plafond:
            raise BudgetEpuise(f"budget de {self.plafond:.2f} $ atteint ({self.depense:.2f} $ estimés)")
        self.depense += cout


def _client(cle: str):
    return googlemaps.Client(key=cle)


def _coordonnees(client, budget: _Budget, cfg: dict, ville: str | None) -> tuple[float, float, str]:
    """(latitude, longitude, nom de la ville) : celles du profil, sinon la ville demandée est géocodée."""
    ville_profil = config.obtenir(cfg, "recherche.ville", "")
    lat = config.obtenir(cfg, "recherche.latitude")
    lng = config.obtenir(cfg, "recherche.longitude")
    memes_coordonnees = isinstance(lat, (int, float)) and isinstance(lng, (int, float))
    if memes_coordonnees and (not ville or ville.strip().lower() == str(ville_profil).strip().lower()):
        return float(lat), float(lng), str(ville_profil)
    nom = (ville or ville_profil or "").strip()
    if not nom:
        raise config.ErreurConfig("Aucune ville : renseigne recherche.ville ou passe --ville")
    budget.facturer("geocode")
    resultats = client.geocode(nom)
    if not resultats:
        raise config.ErreurConfig(f"Google Maps ne trouve pas la ville {nom!r}")
    lieu = resultats[0]["geometry"]["location"]
    return float(lieu["lat"]), float(lieu["lng"]), nom


def _page_suivante(client, budget: _Budget, jeton: str) -> dict | None:
    """Page suivante des résultats, en attendant que le jeton devienne valide (None si jamais valide)."""
    delai = DELAI_PAGE_S
    for _ in range(ESSAIS_PAGE):
        time.sleep(delai)
        budget.facturer("recherche")
        try:
            return client.places_nearby(page_token=jeton)
        except ApiError as e:
            if e.status != "INVALID_REQUEST":
                raise
            delai *= 1.8
    return None


def _resultats(client, budget: _Budget, lat: float, lng: float, requete: str, rayon_m: int):
    """Lieux trouvés pour une requête, page après page (les pages ne sont payées que si on les lit)."""
    budget.facturer("recherche")
    page = client.places_nearby(location=(lat, lng), radius=rayon_m, keyword=requete, language="fr")
    while page:
        yield from page.get("results", [])
        jeton = page.get("next_page_token")
        page = _page_suivante(client, budget, jeton) if jeton else None


def _detail(client, budget: _Budget, place_id: str) -> dict:
    budget.facturer("details")
    return client.place(place_id, fields=CHAMPS_DETAILS, language="fr").get("result", {})


def _deja_en_base() -> set[str]:
    with base.connexion() as conn:
        return {l[0] for l in conn.execute("SELECT source_id FROM entreprises WHERE source = 'maps'")}


def chercher_entreprises(cfg: dict, ville: str | None = None, secteurs: list[str] | None = None,
                         rayon_km: float | None = None, budget_usd: float | None = None) -> list[int]:
    """Cherche des entreprises sur Google Maps pour chaque secteur et les enregistre en base.

    Les lieux déjà en base ne sont pas repayés. Les entreprises sans site sont gardées. La recherche
    s'arrête dès que le budget estimé est atteint. Rend les identifiants des entreprises enregistrées.
    """
    cle = config.obtenir(cfg, "maps.cle", "")
    if not cle:
        raise config.ErreurConfig(MESSAGE_CLE)
    requetes = [s.strip() for s in (secteurs or config.obtenir(cfg, "recherche.secteurs_maps", []) or []) if s.strip()]
    if not requetes:
        raise config.ErreurConfig("Aucun secteur : renseigne recherche.secteurs_maps ou passe --secteurs")
    plafond = config.obtenir(cfg, "maps.budget_usd", 5) if budget_usd is None else budget_usd
    rayon = min(rayon_km or config.obtenir(cfg, "recherche.rayon_km", 30), RAYON_MAX_KM)
    budget = _Budget(plafond)
    client = _client(cle)
    ids: list[int] = []
    try:
        lat, lng, nom_ville = _coordonnees(client, budget, cfg, ville)
        connus = _deja_en_base()
        for requete in requetes:
            print(f"Recherche : {requete} ({nom_ville}, {rayon:g} km)")
            try:
                for lieu in _resultats(client, budget, lat, lng, requete, int(rayon * 1000)):
                    place_id = lieu.get("place_id")
                    source_id = f"maps:{place_id}"
                    if not place_id or source_id in connus:
                        continue
                    connus.add(source_id)
                    detail = _detail(client, budget, place_id)
                    ids.append(base.ajouter_entreprise(
                        detail.get("name") or lieu.get("name") or "Entreprise", "maps", source_id=source_id,
                        adresse=detail.get("formatted_address") or lieu.get("vicinity"), ville=nom_ville,
                        site=detail.get("website"), telephone=detail.get("formatted_phone_number"),
                        secteur=requete))
            except ApiError as e:
                if e.status == "REQUEST_DENIED":
                    raise config.ErreurConfig(
                        "Google Maps refuse la clé : active Places API et Geocoding API et vérifie la facturation") from e
                print(f"  Secteur {requete!r} abandonné ({e}), on passe au suivant")
            time.sleep(DELAI_ENTRE_SECTEURS_S)
    except BudgetEpuise as e:
        print(f"Arrêt : {e}")
    print(f"Dépense estimée : {budget.depense:.2f} $ sur {budget.plafond:.2f} $")
    return ids


def _arguments(parser) -> None:
    parser.add_argument("--ville", default=None, help="ville de recherche (défaut : recherche.ville)")
    parser.add_argument("--secteurs", default=None, help="requêtes séparées par des virgules (défaut : recherche.secteurs_maps)")
    parser.add_argument("--rayon", type=float, default=None, help="rayon en km (50 au plus)")
    parser.add_argument("--budget", type=float, default=None, help="budget maximal en dollars (défaut : maps.budget_usd)")


@commande("entreprises", "Cherche des entreprises sur Google Maps autour d'une ville", _arguments)
def _entreprises(args) -> int:
    cfg = config.charger()
    secteurs = [s for s in args.secteurs.split(",")] if args.secteurs else None
    ids = chercher_entreprises(cfg, ville=args.ville, secteurs=secteurs, rayon_km=args.rayon, budget_usd=args.budget)
    sans_site = 0
    if ids:
        with base.connexion() as conn:
            marques = ",".join("?" * len(ids))
            sans_site = conn.execute(
                f"SELECT COUNT(*) FROM entreprises WHERE id IN ({marques}) AND COALESCE(site, '') = ''", ids).fetchone()[0]
    print(f"{len(ids)} entreprises enregistrées, dont {sans_site} sans site (pas d'adresse mail à chercher).")
    return 0
