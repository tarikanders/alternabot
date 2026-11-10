"""Rédaction des candidatures : lecture du site de l'entreprise, prompt, relecture par le code.

Deux textes : le message court du formulaire La Bonne Alternance, et le mail spontané (lettre dans le
corps du mail, CV seul en pièce jointe) avec sa mise en file. Ils partagent SYSTEME, `relire` et `rediger_relu`.
"""
import re
import unicodedata
from pathlib import Path

from bs4 import BeautifulSoup

from . import base, config, ia, web
from .cli import commande

PAGES_SITE = ("", "/a-propos", "/qui-sommes-nous", "/about")
TAILLE_SITE = 4000
TAILLE_PAGE = 2500

# Bornes de longueur (en caractères) du message du formulaire La Bonne Alternance.
LBA_MIN = 400
LBA_MAX = 1500
# Bornes de longueur du mail spontané (signature comprise).
MAIL_MIN = 900
MAIL_MAX = 1800

SYSTEME = """Tu rédiges des candidatures spontanées en alternance, en français, pour un étudiant.
Le texte doit sembler écrit à la main par le candidat : sobre, authentique, précis, jamais générique.

Règles :
- Tu vouvoies le destinataire.
- Tu t'appuies uniquement sur le profil fourni. Tu n'inventes rien qui n'y figure pas : ni chiffre, ni
  expérience, ni diplôme, ni outil, ni référence, ni client. En cas de doute, tu ne l'écris pas.
- Tu ne parles que de l'entreprise visée. Tu n'affirmes sur elle que ce que montre l'extrait de son site
  ou l'offre fournis. Sans extrait, tu restes général sur elle et précis sur le candidat.
- Tu ne laisses aucun champ à remplir : ni crochets, ni accolades, ni « XXX ».
- Pas de formule creuse (« passionné », « dynamique et motivé », « je suis convaincu que »), pas de flatterie.
- Aucun tiret cadratin ni demi-cadratin, sous aucune forme (ni incise, ni puce). Utilise une virgule, un
  point, deux-points ou des parenthèses. Les traits d'union des mots composés restent permis.
- Tu écris le texte seul, sans titre, sans commentaire, sans guillemets autour."""

_TROU = re.compile(
    r"\[[^\]\n]{2,40}\]|\{[^}\n]{2,40}\}|\bX{3,}\b|\bNOM\b|\bPR[EÉ]NOM\b|\bENTREPRISE\b"
    r"|(?i:nom de l'entreprise|nom du contact|à compléter|a completer|lorem ipsum)")
_TIRET = re.compile("[\\u2014\\u2013]")
_FORMES_JURIDIQUES = {"sas", "sasu", "sarl", "eurl", "sa", "sci", "snc", "scop", "ste", "societe"}


class ErreurRedaction(ia.ErreurIA):
    """Aucun texte acceptable après les essais autorisés."""


def _nettoyer(html: str) -> str:
    soupe = BeautifulSoup(html, "html.parser")
    for balise in soupe(["script", "style", "noscript", "nav", "footer", "header"]):
        balise.decompose()
    return " ".join(soupe.get_text(" ").split())[:TAILLE_PAGE]


def lire_site(url: str | None) -> str:
    """Texte nettoyé de l'accueil et des pages à propos du site (4000 caractères au plus, vide sans site)."""
    if not url or not url.strip():
        return ""
    racine = base.racine_site(url)
    if not racine:
        return ""
    # La page donnée (souvent celle de l'agence locale) d'abord, puis l'accueil et les pages à propos.
    adresses = list(dict.fromkeys([base.page_site(url)] + [racine + chemin for chemin in PAGES_SITE]))
    textes: list[str] = []
    for adresse in adresses:
        try:
            html = web.telecharger(adresse)
        except web.Injoignable:
            break  # le site ne répond pas : ses autres pages non plus
        if html:
            texte = _nettoyer(html)
            if texte and texte not in textes:  # un site à page unique répond pareil partout
                textes.append(texte)
    return " ".join(textes)[:TAILLE_SITE]


def profil_texte(cfg: dict) -> str:
    """Contenu de profil/profil.md."""
    fichier = Path(cfg.get("_profil") or config.dossier_profil()) / "profil.md"
    if not fichier.is_file():
        raise config.ErreurConfig("profil/profil.md est absent : " + config.MESSAGE_PROFIL_ABSENT)
    return fichier.read_text(encoding="utf-8").strip()


def _signature(cfg: dict) -> str:
    return f"{config.obtenir(cfg, 'candidat.prenom', '')} {config.obtenir(cfg, 'candidat.nom', '')}".strip()


def _signer(texte: str, signature: str) -> str:
    """S'assure que le texte finit par la signature (avec « Cordialement, » si la formule manque)."""
    texte = texte.rstrip()
    fin = "\n".join(texte.splitlines()[-4:])
    if signature in fin:
        return texte
    if "cordialement" not in fin.lower():
        texte += "\n\nCordialement,"
    return f"{texte}\n{signature}"


def message_lba(entreprise: dict, cfg: dict, offre: dict | None = None) -> str:
    """Message court (champ « message » du formulaire La Bonne Alternance), signé prénom nom."""
    nom = nom_usuel(entreprise.get("nom", "")) or "l'entreprise"
    signature = _signature(cfg)
    lignes = [
        "Rédige le message de candidature à l'alternance qui sera collé dans le champ « message » du "
        "formulaire de candidature simplifiée de La Bonne Alternance.",
        f"Entreprise visée : {nom}" + (f" ({entreprise['ville']})" if entreprise.get("ville") else ""),
    ]
    if offre and offre.get("intitule"):
        lignes.append(f"Offre à laquelle il répond : {offre['intitule']}")
    site = lire_site(entreprise.get("site"))
    lignes.append("Extrait du site de l'entreprise :\n" + (site or "(aucun site connu)"))
    lignes.append("Profil du candidat (seule source de vérité) :\n" + profil_texte(cfg))
    lignes.append(
        "Contraintes : entre 600 et 1200 caractères, quelques courts paragraphes. Commence par « Bonjour, », "
        f"nomme {nom} une fois, montre en deux ou trois phrases le lien entre le profil et l'entreprise ou "
        "l'offre, dis la durée et la date de début si le profil les donne, mentionne que le CV est joint. "
        f"Termine par « Cordialement, » puis la signature « {signature} ».")
    texte = ia.rediger(SYSTEME, "\n\n".join(lignes), cfg)
    return _signer(texte, signature)


def _plier(texte: str) -> str:
    """Minuscules sans accents, ponctuation en espaces : pour comparer des noms."""
    texte = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", texte).split())


def _nom_distinctif(nom: str) -> str:
    mots = [m for m in _plier(nom).split() if m not in _FORMES_JURIDIQUES]
    return " ".join(mots)


# Mots qui décrivent un métier sans nommer personne : « ESN », « Agence web »… ajoutés par les fiches Google Maps.
_MOTS_DESCRIPTIFS = {
    "esn", "ssii", "agence", "cabinet", "societe", "entreprise", "web", "digital", "digitale", "numerique",
    "informatique", "conseil", "consulting", "developpement", "logiciel", "logiciels", "services", "service",
    "solutions", "it", "rse", "engagee", "engage", "expert", "experts", "comptable", "marketing", "communication",
    "seo", "creation", "site", "sites", "internet", "data", "de", "du", "des", "et", "en", "la", "le", "les",
}
_SEPARATEURS = re.compile(r"\s+[|\-\u2013\u2014\u00b7:]\s+")


def nom_usuel(nom: str) -> str:
    """Nom à écrire dans le mail : sans les parenthèses ni les segments purement descriptifs des fiches Maps.

    « shape it (ESN SSII engagée RSE) » donne « shape it », « ESN | Synanto Lyon » donne « Synanto Lyon ».
    Si rien de distinctif ne reste, le nom est rendu tel quel.
    """
    nom = (nom or "").strip()
    sans_parentheses = " ".join(re.sub(r"\([^)]*\)", " ", nom).split())
    segments = [s.strip() for s in _SEPARATEURS.split(sans_parentheses) if s.strip()]
    distinctifs = [s for s in segments if not set(_plier(s).split()) <= _MOTS_DESCRIPTIFS]
    if not distinctifs:
        return nom
    return distinctifs[0]


def _autres_noms(entreprise: dict, cfg: dict) -> list[str]:
    """Noms (pliés) des autres entreprises de la base, sans ceux qui désignent aussi la cible,
    la ville de recherche, la ville de l'entreprise ou un mot du profil du candidat."""
    cible = _nom_distinctif(entreprise.get("nom", ""))
    neutres = {_plier(config.obtenir(cfg, "recherche.ville", "")), _plier(entreprise.get("ville") or "")}
    try:
        profil = " " + _plier(profil_texte(cfg)) + " "
    except config.ErreurConfig:
        profil = ""
    with base.connexion() as conn:
        noms = [ligne[0] for ligne in conn.execute("SELECT DISTINCT nom FROM entreprises")]
    resultat = []
    for nom in noms:
        autre = _nom_distinctif(nom)
        if len(autre) < 4 or autre in neutres or autre == cible:
            continue
        if cible and (autre in cible or cible in autre):
            continue
        if f" {autre} " in profil:
            continue
        resultat.append(autre)
    return resultat


def relire(texte: str, entreprise: dict, cfg: dict, minimum: int = LBA_MIN, maximum: int = LBA_MAX) -> list[str]:
    """Défauts trouvés par le code : trous de gabarit, longueur, autre entreprise citée, tiret cadratin."""
    defauts = []
    texte = texte or ""
    for trou in sorted({m.group(0).strip() for m in _TROU.finditer(texte)}):
        defauts.append(f"trou de gabarit : {trou}")
    if len(texte) < minimum:
        defauts.append(f"trop court ({len(texte)} caractères, minimum {minimum})")
    elif len(texte) > maximum:
        defauts.append(f"trop long ({len(texte)} caractères, maximum {maximum})")
    pli = " " + _plier(texte) + " "
    for autre in _autres_noms(entreprise, cfg):
        if f" {autre} " in pli:
            defauts.append(f"cite une autre entreprise : {autre}")
    if _TIRET.search(texte):
        defauts.append("tiret cadratin ou demi-cadratin")
    return defauts


def rediger_relu(fabrique, entreprise: dict, cfg: dict, essais: int = 3, **bornes) -> str:
    """Appelle `fabrique()` jusqu'à obtenir un texte sans défaut (`bornes` : minimum, maximum pour `relire`)."""
    defauts: list[str] = []
    for _ in range(max(1, essais)):
        texte = fabrique()
        defauts = relire(texte, entreprise, cfg, **bornes)
        if not defauts:
            return texte
    raise ErreurRedaction(f"Texte refusé après {max(1, essais)} essais pour {entreprise.get('nom', '?')} : "
                          + " ; ".join(defauts))


# ---- Mail spontané -------------------------------------------------------------------------------

def _objet(cfg: dict) -> str:
    """Objet court et précis : « Candidature alternance <formation> : Prénom Nom »."""
    domaine = " ".join(str(config.obtenir(cfg, "candidat.formation", "")).split())
    if len(domaine) > 60:
        domaine = domaine[:60].rsplit(" ", 1)[0]
    return f"Candidature alternance {domaine} : {_signature(cfg)}" if domaine else f"Candidature alternance : {_signature(cfg)}"


def _bloc_signature(cfg: dict) -> str:
    """Prénom nom, téléphone, email et linkedin (ce qui existe), un par ligne."""
    lignes = [_signature(cfg)] + [str(config.obtenir(cfg, f"candidat.{c}", "")).strip()
                                  for c in ("telephone", "email", "linkedin")]
    return "\n".join(l for l in lignes if l)


def _clore(texte: str, cfg: dict) -> str:
    """Remplace tout ce qui suit « Cordialement » (ou ajoute la formule) par la signature complète."""
    texte = texte.rstrip()
    formule = re.search(r"(?im)^\s*(?:bien\s+|très\s+)?cordialement\b", texte)
    if formule:
        texte = texte[:formule.start()].rstrip()
    return f"{texte}\n\nCordialement,\n{_bloc_signature(cfg)}"


def mail_spontane(entreprise: dict, cfg: dict, offre: dict | None = None) -> tuple[str, str]:
    """(objet, corps) d'un mail de candidature spontanée, d'après le site de l'entreprise et profil.md.

    Le corps (900 à 1800 caractères) est la lettre elle-même ; le CV est la seule pièce jointe.
    Avec une `offre` (titre, lien), le mail la cite : l'entreprise recrute, la candidature y répond.
    """
    nom = nom_usuel(entreprise.get("nom", "")) or "l'entreprise"
    lignes = [
        "Rédige le corps d'un mail de candidature spontanée à l'alternance, envoyé à l'entreprise ci-dessous. "
        "Ce mail tient lieu de lettre de motivation : il n'y a pas de lettre en pièce jointe.",
        f"Entreprise visée : {nom}" + (f" ({entreprise['ville']})" if entreprise.get("ville") else ""),
    ]
    if offre and offre.get("titre"):
        lignes.append(f"Offre publiée par l'entreprise, à laquelle ce mail répond : {offre['titre']}"
                      + (f" ({offre['lieu']})" if offre.get("lieu") else "")
                      + (f"\nLien de l'offre : {offre['lien']}" if offre.get("lien") else "")
                      + "\nCite l'intitulé de l'offre une fois, dans le premier paragraphe, sans inventer son contenu.")
    lignes += [
        "Extrait du site de l'entreprise :\n" + (lire_site(entreprise.get("site")) or "(aucun site connu)"),
        "Profil du candidat (seule source de vérité) :\n" + profil_texte(cfg),
        "Contraintes : entre 1000 et 1500 caractères, trois ou quatre courts paragraphes. Commence par "
        f"« Bonjour, », nomme {nom} une fois, montre le lien précis entre le profil et ce que fait l'entreprise, "
        "dis la durée, la date de début et le rythme si le profil les donne, propose un échange, mentionne que "
        "le CV est joint. Termine par « Cordialement, » et rien après : la signature est ajoutée ensuite.",
    ]
    corps = _clore(ia.rediger(SYSTEME, "\n\n".join(lignes), cfg), cfg)
    return _objet(cfg), corps


def _a_rediger() -> list[dict]:
    """Entreprises avec une adresse et sans candidature par mail en file."""
    with base.connexion() as conn:
        lignes = conn.execute(
            """SELECT * FROM entreprises e
               WHERE EXISTS (SELECT 1 FROM emails m WHERE m.entreprise_id = e.id)
                 AND NOT EXISTS (SELECT 1 FROM candidatures c WHERE c.entreprise_id = e.id
                                 AND c.canal = 'email' AND c.statut = 'en_file')
               ORDER BY e.id""").fetchall()
    return [dict(l) for l in lignes]


def _en_echec_pour(entreprise_id: int, email: str) -> bool:
    """Un envoi vers cette adresse a déjà échoué : inutile de réécrire le mail pour la même adresse."""
    with base.connexion() as conn:
        return conn.execute(
            "SELECT 1 FROM candidatures WHERE entreprise_id = ? AND canal = 'email' AND statut = 'echec' "
            "AND lower(email_utilise) = ? LIMIT 1", (entreprise_id, email.lower())).fetchone() is not None


def preparer(cfg: dict, limite: int | None = None) -> int:
    """Rédige un mail pour chaque entreprise à contacter et le met en file. Rend le nombre de mails préparés.

    Un texte refusé après les essais : on passe à l'entreprise suivante. Moteur IA en panne : on s'arrête.
    """
    profil = Path(cfg.get("_profil") or config.dossier_profil())
    dossier = profil / "lettres"
    prepares = 0
    for entreprise in _a_rediger():
        if limite is not None and prepares >= limite:
            break
        adresse = (base.meilleur_email(entreprise["id"]) or {}).get("email")
        if not adresse or base.deja_contactee(entreprise["id"], adresse) or _en_echec_pour(entreprise["id"], adresse):
            continue
        try:
            offre = base.offre_de(entreprise["id"])
            corps = rediger_relu(lambda: mail_spontane(entreprise, cfg, offre)[1], entreprise, cfg,
                                 minimum=MAIL_MIN, maximum=MAIL_MAX)
        except ErreurRedaction as e:
            print(f"{entreprise['nom']} : mail refusé, réessayé au prochain lancement ({e})")
            continue
        except ia.ErreurIA as e:
            print(f"Moteur IA indisponible, arrêt : {e}")
            break
        dossier.mkdir(parents=True, exist_ok=True)
        fichier = dossier / f"mail_{_plier(entreprise['nom']).replace(' ', '_')[:40] or 'entreprise'}_{entreprise['id']}.txt"
        fichier.write_text(corps, encoding="utf-8")
        base.creer_candidature(entreprise["id"], "email", email_utilise=adresse, objet=_objet(cfg),
                               corps_chemin=str(fichier.relative_to(profil)))
        prepares += 1
        print(f"{entreprise['nom']} : mail prêt pour {adresse}")
    return prepares


def _candidatures_a_montrer(identifiant: int | None) -> list[dict]:
    """La candidature demandée, sinon les trois prochaines mails en file."""
    if identifiant is None:
        return base.candidatures_en_file("email", 3)
    with base.connexion() as conn:
        lignes = conn.execute(
            """SELECT c.*, e.nom AS entreprise_nom FROM candidatures c JOIN entreprises e ON e.id = c.entreprise_id
               WHERE c.id = ?""", (identifiant,)).fetchall()
    return [dict(l) for l in lignes]


def _arguments_rediger(parser) -> None:
    parser.add_argument("--limite", type=int, default=None, help="nombre maximal de mails à rédiger")


@commande("rediger", "Rédige les mails spontanés des entreprises dont l'adresse est connue", _arguments_rediger)
def _rediger(args) -> int:
    cfg = config.charger()
    n = preparer(cfg, args.limite)
    print(f"{n} mails rédigés et mis en file (python -m alternabot apercu pour les relire).")
    return 0


def _arguments_apercu(parser) -> None:
    parser.add_argument("--id", type=int, default=None, help="numéro de la candidature (défaut : les 3 prochaines en file)")


@commande("apercu", "Affiche destinataire, objet et corps des prochains mails en file", _arguments_apercu)
def _apercu(args) -> int:
    cfg = config.charger()
    profil = Path(cfg.get("_profil") or config.dossier_profil())
    candidatures = _candidatures_a_montrer(args.id)
    if not candidatures:
        print("Aucun mail à montrer.")
        return 1 if args.id is not None else 0
    for c in candidatures:
        chemin = profil / (c.get("corps_chemin") or "")
        corps = chemin.read_text(encoding="utf-8") if c.get("corps_chemin") and chemin.is_file() else "(corps introuvable)"
        print(f"--- Candidature {c['id']} ({c['entreprise_nom']}, {c['statut']})")
        print(f"À : {c['email_utilise']}\nObjet : {c['objet']}\n\n{corps}\n")
    return 0
