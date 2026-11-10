"""Candidature simplifiée La Bonne Alternance par Playwright : le vrai formulaire, comme un humain.

Sélecteurs relevés sur le site (ils peuvent changer : lance d'abord `lba --essai`) :
  bouton d'ouverture : [data-testid="postuler-button"] (le premier nœud est la variante mobile, invisible)
  champs : #applicant_last_name #applicant_first_name #applicant_email #applicant_phone #applicant_message
  situation : radio[name=applicant_inscription_formation], le premier choix est « déjà inscrit »
  « déjà inscrit » fait apparaître : #applicant_contract_duration select (durée), #applicant_contract_start
  select (liste de cases + « Enregistrer »), #applicant_formation_description (école, 200 caractères),
  #applicant_rythm_description (rythme, 200 caractères)
  CV : input[type=file] (pdf ou docx, 3 Mo)    envoi : [data-testid="candidature-not-sent"]
"""
import random
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from . import base, config, ia, lba, redaction
from .cli import commande

LBA_HOST = "labonnealternance.apprentissage.beta.gouv.fr"
TAILLE_MAX_CV_MO = 3
LIMITE_CHAMP = 200


def _sync_playwright():
    from playwright.sync_api import sync_playwright
    return sync_playwright()


def telephone_fr(brut: str) -> str:
    """+33 6 00 00 00 00 devient 0600000000 : le champ refuse le format international."""
    chiffres = re.sub(r"[^\d+]", "", brut or "")
    if chiffres.startswith("+33"):
        return "0" + chiffres[3:]
    if chiffres.startswith("33") and len(chiffres) == 11:
        return "0" + chiffres[2:]
    return chiffres


def texte_formation(cfg: dict) -> str:
    """Formation et école réunies, coupées à 200 caractères (limite du formulaire)."""
    morceaux = [str(config.obtenir(cfg, f"candidat.{c}", "")).strip() for c in ("formation", "ecole")]
    return ", ".join(m for m in morceaux if m)[:LIMITE_CHAMP]


def _slug(nom: str) -> str:
    return re.sub(r"[^\w-]", "_", (nom or "").lower())[:40]


def cv_fichier(cfg: dict) -> Path:
    """Chemin du CV, vérifié (présent, 3 Mo au plus)."""
    chemin = Path(cfg.get("_profil") or config.dossier_profil()) / str(config.obtenir(cfg, "candidat.cv", "CV.pdf"))
    if not chemin.is_file():
        raise config.ErreurConfig(f"CV introuvable : {chemin}")
    if chemin.stat().st_size > TAILLE_MAX_CV_MO * 1_048_576:
        raise config.ErreurConfig(f"CV trop lourd : La Bonne Alternance accepte {TAILLE_MAX_CV_MO} Mo au plus")
    return chemin


def fermer_cookies(page) -> None:
    """Accepte ou refuse le bandeau de cookies s'il est affiché."""
    for libelle in ("Tout accepter", "Continuer sans accepter", "Accepter"):
        bouton = page.get_by_role("button", name=libelle, exact=False)
        try:
            if bouton.count() and bouton.first.is_visible():
                bouton.first.click()
                page.wait_for_timeout(300)
                return
        except Exception:
            pass


def texte_modale(page) -> str:
    """Texte de la fenêtre de candidature si elle est encore montée, sinon celui de la page."""
    for selecteur in ("[role=dialog]", "body"):
        zone = page.locator(selecteur)
        try:
            if zone.count():
                return zone.first.inner_text() or ""
        except Exception:
            continue
    return ""


def attendre_issue(page, delai_s: int = 30) -> tuple[str, str]:
    """Attend la fin de l'envoi : (« postee_lba » | « echec », détail).

    Jamais de `text=/.../` dans une liste CSS séparée par des virgules : Playwright lève une erreur et
    une candidature réellement partie serait comptée en échec, donc retentée.
    """
    fin = time.time() + delai_s
    texte = ""
    while time.time() < fin:
        if page.get_by_test_id("candidature-sent").count():
            return "postee_lba", "confirmation affichée"
        texte = texte_modale(page)
        if re.search(r"candidature a bien [ée]t[ée] envoy|candidature envoy[ée]e", texte, re.I):
            return "postee_lba", "confirmation affichée"
        if re.search(r"d[ée]j[àa] (envoy|postul|candidat)", texte, re.I):
            return "echec", "LBA : candidature déjà envoyée à cette entreprise"
        alerte = page.locator('.fr-alert--error, [role="alert"]')
        try:
            if alerte.count():
                message = (alerte.first.inner_text() or "").strip().replace("\n", " ")
                if message:
                    return "echec", message[:200]
        except Exception:
            pass
        page.wait_for_timeout(500)
    return "echec", "aucune issue détectée, fin de la fenêtre : " + texte.strip()[-160:]


def capturer(page, chemin: Path) -> None:
    """Capture la fenêtre de candidature (la preuve de ce qui part), sinon la page entière."""
    chemin.parent.mkdir(parents=True, exist_ok=True)
    fenetre = page.locator("[role=dialog]")
    try:
        if fenetre.count() and fenetre.first.is_visible():
            fenetre.first.screenshot(path=str(chemin))
            return
    except Exception:
        pass
    page.screenshot(path=str(chemin), full_page=True)


def remplir_details(page, cfg: dict) -> None:
    """Remplit durée, début, formation et rythme (visibles après « déjà inscrit »). Chaque champ est
    optionnel : un select renommé ne doit pas faire échouer toute la candidature."""
    duree = str(config.obtenir(cfg, "candidat.duree_contrat", "")).strip()
    if duree:
        try:
            page.select_option("#applicant_contract_duration select", label=duree)
        except Exception:
            pass
    brut = config.obtenir(cfg, "candidat.debut_contrat", "")
    morceaux = brut if isinstance(brut, list) else str(brut).split(",")
    debuts = [str(d).strip() for d in morceaux if str(d).strip()]
    if debuts:
        try:
            page.locator("#applicant_contract_start select").click()
            page.wait_for_selector("[role=listbox]", timeout=8_000)
            liste = page.locator("[role=listbox]").first
            for debut in debuts:
                libelle = liste.get_by_text(debut[:1].upper() + debut[1:], exact=True)
                if not libelle.count():
                    continue
                # Un formulaire restauré peut avoir déjà coché la case : un clic la décocherait.
                id_case = libelle.first.get_attribute("for")
                deja = False
                if id_case:
                    case = liste.locator(f'input[id="{id_case}"]')
                    deja = bool(case.count()) and case.first.is_checked()
                if not deja:
                    libelle.first.click()  # le label : la case est masquée
            enregistrer = liste.get_by_role("button", name="Enregistrer")
            (enregistrer.first if enregistrer.count() else liste).click()
            page.wait_for_timeout(400)
        except Exception:
            pass
    for champ, valeur in (("#applicant_formation_description", texte_formation(cfg)),
                          ("#applicant_rythm_description",
                           str(config.obtenir(cfg, "candidat.rythme", "")).strip()[:LIMITE_CHAMP])):
        if valeur:
            try:
                page.fill(champ, valeur)
            except Exception:
                pass


def ouvrir_et_remplir(page, fiche: dict, message: str, cv: Path, cfg: dict) -> None:
    """Ouvre la fiche, la fenêtre de candidature et remplit tout, CV compris. Lève si un élément manque."""
    candidat = lambda cle: str(config.obtenir(cfg, f"candidat.{cle}", ""))  # noqa: E731
    page.goto(fiche["url"], wait_until="networkidle", timeout=60_000)
    # LBA réinjecte le formulaire depuis le localStorage : sans purge, la cible suivante s'ouvre déjà remplie.
    try:
        page.evaluate("() => { localStorage.clear(); sessionStorage.clear(); }")
        page.reload(wait_until="networkidle", timeout=60_000)
    except Exception:
        pass
    fermer_cookies(page)
    bouton = page.locator('[data-testid="postuler-button"]').locator("visible=true")
    if not bouton.count():
        raise LookupError("pas de bouton de candidature simplifiée sur la fiche")
    bouton.first.scroll_into_view_if_needed()
    bouton.first.click()
    page.wait_for_selector("#applicant_email", timeout=20_000)

    page.fill("#applicant_last_name", candidat("nom"))
    page.fill("#applicant_first_name", candidat("prenom"))
    page.fill("#applicant_email", candidat("email"))
    page.fill("#applicant_phone", telephone_fr(candidat("telephone")))
    page.fill("#applicant_message", message)

    # Radios du design système de l'État : l'input est masqué, on clique le label puis on vérifie.
    inscrit = bool(candidat("ecole").strip() or candidat("formation").strip())
    radios = page.locator('input[name="applicant_inscription_formation"]')
    if radios.count() >= 2:
        radio = radios.nth(0 if inscrit else 1)
        page.locator(f'label[for="{radio.get_attribute("id")}"]').first.click()
        if not radio.is_checked():
            radio.check(force=True)
        page.wait_for_timeout(700)  # le temps de monter les champs conditionnels
        if inscrit:
            remplir_details(page, cfg)

    zone_cv = page.locator('input[type="file"]')
    if zone_cv.count():
        zone_cv.first.set_input_files(str(cv))
    else:
        page.wait_for_selector('input[type="file"]', state="attached", timeout=20_000)
        page.locator('input[type="file"]').first.set_input_files(str(cv))
    page.wait_for_timeout(800)


def postuler_une(page, fiche: dict, message: str, cv: Path, cfg: dict, essai: bool,
                 captures: Path) -> tuple[str, str]:
    """Remplit (2 tentatives, avant tout envoi) puis envoie sauf en essai. Rend (statut, détail)."""
    slug = _slug(fiche["nom"])
    for tentative in (1, 2):
        try:
            ouvrir_et_remplir(page, fiche, message, cv, cfg)
            break
        except LookupError as e:
            return "echec", str(e)
        except Exception as e:
            capturer(page, captures / f"{slug}_tentative{tentative}.png")
            if tentative == 2:
                return "echec", f"remplissage impossible : {type(e).__name__}: {e}"[:200]
            page.wait_for_timeout(3_000)
    capturer(page, captures / f"{slug}{'_essai' if essai else ''}.png")
    if essai:
        return "essai", "formulaire rempli, non envoyé"
    page.locator('[data-testid="candidature-not-sent"]').first.click()
    statut, detail = attendre_issue(page)
    capturer(page, captures / f"{slug}_{'ok' if statut == 'postee_lba' else 'echec'}.png")
    return statut, detail


def _cibles(limite: int) -> list[tuple[int, dict]]:
    """(id d'entreprise, fiche) des fiches LBA en base, jamais contactées, sans échec passé, sur le domaine LBA.
    Les offres passent avant les entreprises susceptibles de recruter."""
    memoire = lba.fiches_enregistrees()
    with base.connexion() as conn:
        lignes = conn.execute("SELECT id, source_id FROM entreprises WHERE source = 'lba' ORDER BY id").fetchall()
        en_echec = {l[0] for l in conn.execute("SELECT entreprise_id FROM candidatures WHERE statut = 'echec'")}
    cibles = []
    for ligne in lignes:
        fiche = memoire.get(ligne["source_id"])
        if not fiche or not fiche.get("recipient_id") or urlparse(fiche.get("url") or "").netloc != LBA_HOST:
            continue
        if ligne["id"] in en_echec or base.deja_contactee(ligne["id"]):
            continue
        cibles.append((ligne["id"], fiche))
    cibles.sort(key=lambda c: c[1].get("type") != "offre")
    return cibles[:limite]


def postuler(cfg: dict, limite: int | None, essai: bool, visible: bool) -> dict:
    """Postule aux fiches LBA de la base, dans la limite du plafond du jour (ignoré en essai : rien ne part).

    Rend {"cibles", "postees", "essais", "echecs", "reste_plafond", "motif"}. Sans place ni cible,
    le navigateur n'est pas lancé.
    """
    plafond = int(config.obtenir(cfg, "lba.plafond_jour", 10))
    reste = max(0, plafond - base.postees_lba_aujourdhui())
    bilan = {"cibles": 0, "postees": 0, "essais": 0, "echecs": 0, "reste_plafond": reste, "motif": None}
    place = plafond if essai else reste
    nombre = place if limite is None else min(limite, place)
    if nombre <= 0:
        bilan["motif"] = "plafond_atteint" if not essai else "limite_nulle"
        return bilan
    cibles = _cibles(nombre)
    bilan["cibles"] = len(cibles)
    if not cibles:
        bilan["motif"] = "aucune_fiche"
        return bilan

    cv = cv_fichier(cfg)
    profil = Path(cfg.get("_profil") or config.dossier_profil())
    captures, lettres = profil / "captures", profil / "lettres"
    lettres.mkdir(parents=True, exist_ok=True)
    delai = int(config.obtenir(cfg, "lba.delai_s", 60))
    with _sync_playwright() as p:
        navigateur = p.chromium.launch(headless=not visible)
        try:
            page = navigateur.new_context(locale="fr-FR", viewport={"width": 1280, "height": 900}).new_page()
            for rang, (entreprise_id, fiche) in enumerate(cibles, 1):
                entreprise = {"nom": fiche["nom"], "ville": fiche.get("ville"), "site": fiche.get("site")}
                offre = {"intitule": fiche["intitule"]} if fiche.get("intitule") else None
                chemin_lettre = None
                try:
                    message = redaction.rediger_relu(
                        lambda: redaction.message_lba(entreprise, cfg, offre), entreprise, cfg)
                except redaction.ErreurRedaction as e:
                    # Texte refusé : la fiche n'y est pour rien, elle reste candidate demain.
                    bilan["echecs"] += 1
                    print(f"[{rang}/{len(cibles)}] {fiche['nom']} : message refusé, réessayé au prochain lancement ({e})")
                    continue
                except ia.ErreurIA as e:
                    # Moteur IA en panne : inutile d'ouvrir les fiches suivantes.
                    bilan["echecs"] += 1
                    bilan["motif"] = "ia_indisponible"
                    print(f"Moteur IA indisponible, arrêt : {e}")
                    break
                try:
                    fichier = lettres / f"lba_{_slug(fiche['nom'])}.txt"
                    fichier.write_text(message, encoding="utf-8")
                    chemin_lettre = str(fichier.relative_to(profil))
                    statut, detail = postuler_une(page, fiche, message, cv, cfg, essai, captures)
                except Exception as e:
                    statut, detail = "echec", f"{type(e).__name__}: {e}"[:200]
                bilan[{"postee_lba": "postees", "essai": "essais"}.get(statut, "echecs")] += 1
                identifiant = base.creer_candidature(
                    entreprise_id, "lba", objet=fiche.get("intitule") or "Candidature spontanée LBA",
                    corps_chemin=chemin_lettre, statut="en_file")
                base.maj_statut(identifiant, statut, erreur=None if statut in ("postee_lba", "essai") else detail)
                print(f"[{rang}/{len(cibles)}] {fiche['nom']} : {statut} ({detail})")
                if rang < len(cibles) and not essai:
                    time.sleep(delai + random.randint(0, delai // 2))
        finally:
            navigateur.close()
    return bilan


def _arguments(parser) -> None:
    parser.add_argument("--chercher-seulement", action="store_true", help="cherche et enregistre, sans postuler")
    parser.add_argument("--essai", action="store_true", help="remplit et capture le formulaire sans l'envoyer")
    parser.add_argument("--limite", type=int, default=None, help="nombre maximal de candidatures sur ce lancement")
    parser.add_argument("--visible", action="store_true", help="affiche le navigateur")
    parser.add_argument("--rayon", type=int, default=None, help="rayon de recherche en km (arrondi à 10, 30, 60 ou 100)")


@commande("lba", "Cherche sur La Bonne Alternance puis postule (candidature simplifiée)", _arguments)
def _lba(args) -> int:
    cfg = config.charger()
    try:
        fiches = lba.chercher(cfg, rayon_km=args.rayon)
    except lba.ErreurLBA as e:
        print(f"Erreur : {e}")
        return 1
    ids = lba.enregistrer(fiches)
    print(f"{len(fiches)} fiches postulables trouvées ({len(ids)} entreprises en base).")
    if args.chercher_seulement:
        return 0
    bilan = postuler(cfg, args.limite, args.essai, args.visible)
    print(f"Postées : {bilan['postees']}, essais : {bilan['essais']}, échecs : {bilan['echecs']} "
          f"(plafond restant aujourd'hui : {bilan['reste_plafond']})")
    if bilan["motif"] == "plafond_atteint":
        print("Plafond du jour atteint : aucune candidature lancée.")
    elif bilan["motif"] == "aucune_fiche":
        print("Aucune fiche à postuler : élargis la zone ou les codes ROME.")
    return 1 if bilan["echecs"] and not (bilan["postees"] or bilan["essais"]) else 0
