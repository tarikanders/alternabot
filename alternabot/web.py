"""Téléchargement de pages borné en temps total et en taille.

Le `timeout` de requests ne couvre que l'attente entre deux octets : un site qui répond au compte-gouttes, ou
une résolution DNS qui traîne, peut bloquer des dizaines de minutes. Ici, la requête tourne dans un fil à part
et le fil principal ne l'attend jamais plus de `delai_s` secondes.
"""
import threading
import time

import requests

ENTETES = {"User-Agent": "Mozilla/5.0 (compatible; alternabot)"}
DELAI_TOTAL_S = 15
MAX_OCTETS = 2_000_000
MORCEAU = 65_536
TIMEOUT_REQUESTS = (5, 8)  # connexion, lecture entre deux octets


class Injoignable(Exception):
    """Le site ne répond pas (erreur réseau ou délai total dépassé) : inutile d'essayer ses autres pages."""


def _lire(url: str, fin: float, max_octets: int) -> str | None:
    reponse = requests.get(url, headers=ENTETES, timeout=TIMEOUT_REQUESTS, allow_redirects=True, stream=True)
    try:
        if not reponse.ok:
            return None
        corps = bytearray()
        for morceau in reponse.iter_content(MORCEAU):
            if time.monotonic() > fin:
                raise Injoignable(f"{url} : délai total dépassé")
            corps += morceau
            if len(corps) >= max_octets:
                break
        encodage = reponse.encoding or getattr(reponse, "apparent_encoding", None) or "utf-8"
        return bytes(corps).decode(encodage, errors="replace")
    finally:
        fermer = getattr(reponse, "close", None)
        if fermer:
            fermer()


def telecharger(url: str, delai_s: float = DELAI_TOTAL_S, max_octets: int = MAX_OCTETS) -> str | None:
    """Texte de la page, None sur une erreur HTTP (404...), `Injoignable` si le site ne répond pas à temps."""
    fin = time.monotonic() + delai_s
    resultat: dict = {}

    def travail():
        try:
            resultat["texte"] = _lire(url, fin, max_octets)
        except (requests.RequestException, Injoignable) as e:
            resultat["erreur"] = e

    fil = threading.Thread(target=travail, daemon=True)  # un fil bloqué ne retient pas la fin du programme
    fil.start()
    fil.join(delai_s)
    if fil.is_alive():
        raise Injoignable(f"{url} : pas de réponse en {delai_s:g} s")
    if "erreur" in resultat:
        raise Injoignable(str(resultat["erreur"])) from resultat["erreur"]
    return resultat.get("texte")
