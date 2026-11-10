"""Adresses mail des entreprises : lues sur leur site, sinon devinées, puis contrôlées par le DNS (MX)."""
import csv
import re
import time
from datetime import datetime
from pathlib import Path

import dns.exception
import dns.resolver
from bs4 import BeautifulSoup

from . import base, config, web
from .cli import commande

PAGES_CONTACT = ("", "/contact", "/nous-contacter", "/recrutement", "/carrieres", "/jobs", "/mentions-legales")
DELAI_REQUETE_S = 0.5
DELAI_DNS_S = 5
FICHIER_A_LA_MAIN = "a_contacter_a_la_main.csv"
# Du plus utile au moins utile : une adresse RH vaut mieux qu'une boîte générale.
MOTS_PRIORITAIRES = ("rh", "recrutement", "emploi", "jobs", "talent", "contact", "info", "direction")
# Boîtes devinées, de la plus sûre d'exister à la moins sûre : mieux vaut contact@ que rh@, qui rebondit souvent.
BOITES_DEVINEES = ("contact", "rh", "recrutement", "info")

_ADRESSE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+")
_STRICTE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}$")
_FICHIER = re.compile(r"\.(png|jpe?g|gif|webp|svg|bmp|ico|pdf|css|js|json|xml|webmanifest)$", re.I)
_AROBASE = re.compile(r"\s*[\[({]\s*(?:at|arobase)\s*[\])}]\s*", re.I)
_POINT = re.compile(r"\s*[\[({]\s*(?:dot|point)\s*[\])}]\s*", re.I)
_UTILISATEURS_BIDON = {"noreply", "no-reply", "donotreply", "do-not-reply", "mailer-daemon", "postmaster",
                       "webmaster", "support", "admin", "abuse"}
_DOMAINES_BIDON = {"example.com", "example.org", "example.net", "email.com", "email.fr", "test.com", "domain.com",
                   "domaine.fr", "domaine.com", "yourcompany.com", "company.com", "votresite.fr", "monsite.fr",
                   "sentry.io", "sentry.wixpress.com", "wixpress.com", "schema.org", "w3.org"}
# Sites de plateformes : une page Facebook n'est pas le site de l'entreprise, on n'y devine aucune adresse.
_PLATEFORMES = {"facebook.com", "instagram.com", "linkedin.com", "twitter.com", "x.com", "youtube.com",
                "tiktok.com", "google.com", "wix.com", "wordpress.com", "linktr.ee", "pagesjaunes.fr",
                "doctolib.fr", "fr-fr.facebook.com"}
_cache_mx: dict[str, bool] = {}


def _nettoyer(adresse: str) -> str:
    return (adresse or "").strip().strip(".,;:<>()[]{}\"'").lower()


def adresse_valide(adresse: str) -> bool:
    """Vrai pour une adresse plausible : ni image (logo@2x.png), ni domaine bidon, ni noreply."""
    adresse = _nettoyer(adresse)
    if not _STRICTE.match(adresse) or _FICHIER.search(adresse):
        return False
    utilisateur, domaine = adresse.rsplit("@", 1)
    return (len(utilisateur) >= 2 and utilisateur not in _UTILISATEURS_BIDON
            and domaine not in _DOMAINES_BIDON and domaine not in _PLATEFORMES)


def _extraire(html: str) -> list[str]:
    """Adresses des liens mailto: et du texte visible (adresses écrites « nom [at] site.fr » comprises)."""
    soupe = BeautifulSoup(html, "html.parser")
    trouvees = []
    for lien in soupe.find_all("a", href=True):
        if lien["href"].lower().startswith("mailto:"):
            trouvees.append(re.split(r"[?&]", lien["href"][7:])[0])
    texte = _POINT.sub(".", _AROBASE.sub("@", soupe.get_text(" ")))
    trouvees += _ADRESSE.findall(texte)
    return [a for a in map(_nettoyer, trouvees) if adresse_valide(a)]


def _telecharger(url: str) -> str | None:
    return web.telecharger(url)


def adresses_du_site(site: str | None) -> list[str]:
    """Adresses valides trouvées sur l'accueil et les pages contact, recrutement et mentions légales."""
    if not site or not site.strip():
        return []
    racine = base.racine_site(site)
    if not racine:
        return []
    adresses: list[str] = []
    for chemin in PAGES_CONTACT:
        try:
            html = _telecharger(racine + chemin)
        except web.Injoignable:
            break  # le site ne répond pas : ses autres pages non plus
        if html:
            for adresse in _extraire(html):
                if adresse not in adresses:
                    adresses.append(adresse)
        time.sleep(DELAI_REQUETE_S)
    return adresses


def adresses_par_motif(domaine: str) -> list[str]:
    """Adresses courantes d'un domaine (contact@, rh@, recrutement@, info@), de la plus sûre à la moins sûre."""
    return [f"{boite}@{domaine}" for boite in BOITES_DEVINEES]


def domaine_recoit(domaine: str | None) -> bool:
    """Vrai si le domaine peut recevoir du courrier : un MX (hors MX nul), sinon une adresse A. Résultat mis en cache."""
    domaine = (domaine or "").strip().lower()
    if not domaine:
        return False
    if domaine in _cache_mx:
        return _cache_mx[domaine]
    try:
        reponse = dns.resolver.resolve(domaine, "MX", lifetime=DELAI_DNS_S)
        resultat = any(str(r.exchange).rstrip(".") for r in reponse)
    except dns.resolver.NoAnswer:
        try:
            dns.resolver.resolve(domaine, "A", lifetime=DELAI_DNS_S)
            resultat = True
        except dns.resolver.NoAnswer:
            resultat = False
        except dns.exception.DNSException:
            return False  # panne passagère : pas de verdict mémorisé
    except dns.resolver.NXDOMAIN:
        resultat = False
    except dns.exception.DNSException:
        return False
    _cache_mx[domaine] = resultat
    return resultat


def score(adresse: str) -> int:
    """Rang de l'adresse selon sa boîte (rh, recrutement, emploi, jobs, talent, contact, info, direction) : plus petit = mieux."""
    morceaux = re.split(r"[._+-]", adresse.split("@")[0].lower())
    for rang, mot in enumerate(MOTS_PRIORITAIRES):
        if any(m == mot or (len(mot) > 3 and mot in m) for m in morceaux):
            return rang
    return len(MOTS_PRIORITAIRES)


def trouver(entreprise: dict) -> tuple[str | None, str, str]:
    """(adresse, méthode, confiance) de la meilleure adresse d'une entreprise, ou (None, "", "").

    Méthode « site » : lue sur le site (confiance haute si elle porte le domaine du site, moyenne si c'est une boîte grand public).
    Méthode « motif » : devinée sur le domaine (confiance basse). Dans les deux cas le domaine doit recevoir du courrier.
    """
    site = entreprise.get("site")
    domaine = entreprise.get("domaine") or base.domaine_de(site)
    # Une adresse d'un autre domaine (hébergeur, agence web des mentions légales) n'est pas celle de l'entreprise,
    # sauf une boîte grand public (gmail.com...) qu'une petite structure utilise vraiment.
    lues = [a for a in adresses_du_site(site)
            if (base.domaine_de(a) == domaine or base.domaine_de(a) in base.DOMAINES_GRATUITS)
            and domaine_recoit(base.domaine_de(a))]
    if lues:
        meilleure = min(lues, key=lambda a: (base.domaine_de(a) != domaine, score(a)))
        return meilleure, "site", "haute" if base.domaine_de(meilleure) == domaine else "moyenne"
    if domaine and domaine not in _PLATEFORMES and domaine not in base.DOMAINES_GRATUITS and domaine_recoit(domaine):
        return adresses_par_motif(domaine)[0], "motif", "basse"
    return None, "", ""


def _a_traiter(limite: int | None) -> list[dict]:
    with base.connexion() as conn:
        lignes = conn.execute(
            """SELECT * FROM entreprises e
               WHERE COALESCE(e.site, '') != '' AND e.emails_cherches_le IS NULL
                 AND NOT EXISTS (SELECT 1 FROM emails m WHERE m.entreprise_id = e.id)
               ORDER BY e.id LIMIT ?""", (-1 if limite is None else limite,)).fetchall()
    return [dict(l) for l in lignes]


def _marquer_cherchee(entreprise_id: int) -> None:
    """Note la recherche faite : sans résultat, l'entreprise ne revient pas en tête de file au lancement suivant."""
    with base.connexion() as conn:
        conn.execute("UPDATE entreprises SET emails_cherches_le = ? WHERE id = ?",
                     (datetime.now().isoformat(timespec="seconds"), entreprise_id))


def _noter_a_la_main(cfg: dict, entreprises: list[dict]) -> None:
    """Ajoute au CSV les entreprises à joindre autrement (sans doublon avec ce qu'il contient déjà)."""
    chemin = Path(cfg.get("_profil") or config.dossier_profil()) / FICHIER_A_LA_MAIN
    lignes: list[list[str]] = []
    if chemin.is_file():
        with chemin.open(encoding="utf-8", newline="") as f:
            lignes = list(csv.reader(f))[1:]
    vus = {(l[0], l[1]) for l in lignes if len(l) >= 2}
    for e in entreprises:
        cle = (e["nom"], e.get("site") or "")
        if cle not in vus:
            vus.add(cle)
            lignes.append([e["nom"], e.get("site") or "", e.get("telephone") or "", e.get("adresse") or ""])
    with chemin.open("w", encoding="utf-8", newline="") as f:
        ecrivain = csv.writer(f)
        ecrivain.writerow(["nom", "site", "telephone", "adresse"])
        ecrivain.writerows(lignes)


def trouver_tout(cfg: dict, limite: int | None = None) -> dict:
    """Cherche l'adresse de chaque entreprise avec site et sans adresse en base, et l'enregistre.

    Les entreprises sans résultat vont dans profil/a_contacter_a_la_main.csv.
    Rend {"traitees", "trouvees", "a_la_main"}.
    """
    bilan = {"traitees": 0, "trouvees": 0, "a_la_main": 0}
    sans_resultat = []
    for entreprise in _a_traiter(limite):
        bilan["traitees"] += 1
        adresse, methode, confiance = trouver(entreprise)
        _marquer_cherchee(entreprise["id"])
        if adresse:
            base.ajouter_email(entreprise["id"], adresse, methode=methode, confiance=confiance, verifie=True)
            bilan["trouvees"] += 1
            print(f"{entreprise['nom']} : {adresse} ({methode}, confiance {confiance})")
        else:
            sans_resultat.append(entreprise)
            print(f"{entreprise['nom']} : aucune adresse")
    bilan["a_la_main"] = len(sans_resultat)
    if sans_resultat:
        _noter_a_la_main(cfg, sans_resultat)
    return bilan


def _arguments(parser) -> None:
    parser.add_argument("--limite", type=int, default=None, help="nombre maximal d'entreprises à traiter")


@commande("emails", "Trouve l'adresse mail des entreprises enregistrées (site, sinon motif, puis contrôle MX)", _arguments)
def _emails(args) -> int:
    cfg = config.charger()
    bilan = trouver_tout(cfg, args.limite)
    print(f"{bilan['trouvees']} adresses trouvées sur {bilan['traitees']} entreprises ; "
          f"{bilan['a_la_main']} à contacter à la main ({FICHIER_A_LA_MAIN} dans profil/).")
    return 0
