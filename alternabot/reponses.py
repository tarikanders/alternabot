"""Suivi des réponses : lit les boîtes Gmail en IMAP, en lecture seule, et classe chaque réponse à une candidature.

Lecture seule : la boîte est ouverte en `readonly` et les messages lus par `BODY.PEEK[]`, donc aucun n'est
marqué comme lu. Seule la base du profil change (statut des candidatures), et seulement vers un état plus avancé.
"""
import email
import imaplib
import re
from collections import defaultdict
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr
from pathlib import Path

from . import base, config, envoi, ia
from .cli import commande

IMAP_HOTE = "imap.gmail.com"
TIMEOUT_S = 30
TAILLE_LOT = 100
CORPS_MAX = 3000
FICHIER_RAPPORT = "reponses.md"

# Classes, dans l'ordre où elles sont testées : un rebond est certain, une réponse automatique n'est pas un refus,
# un refus poli parle souvent d'« échange », donc refus avant entretien.
CLASSES = ("rebond", "automatique", "refus", "entretien", "positif", "a_lire")
MOTS = {
    "rebond": ("delivery status notification", "undelivered mail", "mail delivery failed", "adresse introuvable",
               "address not found", "delivery incomplete", "message not delivered", "n'a pas pu être distribué",
               "wasn't delivered", "returned mail", "non remis"),
    "automatique": ("absence du bureau", "message automatique", "réponse automatique", "reponse automatique",
                    "out of office", "automatic reply", "je suis actuellement absent", "je suis absent",
                    "accusé de réception", "accuse de reception", "nous avons bien reçu", "nous avons bien recu",
                    "ne pas répondre à ce message", "ne pas repondre a ce message"),
    "refus": ("ne recherchons pas", "ne recrutons pas", "pas de poste", "aucun poste", "pas donner suite",
              "donnerons pas suite", "donnons pas suite", "sans suite", "pas été retenue", "pas ete retenue",
              "pas retenu", "ne sommes pas en mesure", "regret", "malheureusement", "pas d'alternance",
              "pas d'alternant", "pas de recrutement", "effectif complet", "équipes sont complètes",
              "ne correspond pas", "pas de besoin", "aucun besoin", "pas de place"),
    "entretien": ("entretien", "rencontrer", "rendez-vous", "visio", "échange téléphonique", "echange telephonique",
                  "vous appeler", "vos disponibilités", "vos disponibilites", "un créneau", "un creneau"),
    "positif": ("intéressé par votre", "interesse par votre", "intéressée par votre", "votre profil nous",
                "votre profil a retenu", "recontacter", "nous reviendrons vers vous", "votre cv", "transmis"),
}
# Statut que prend la candidature pour chaque classe (une réponse automatique ne change rien).
STATUT_DE_CLASSE = {"rebond": "rebond", "refus": "refus", "entretien": "entretien", "positif": "reponse",
                    "a_lire": "reponse"}
# Un statut n'est remplacé que par un statut de rang supérieur : un « merci » après un entretien n'efface rien.
RANG = {"envoyee": 0, "postee_lba": 0, "rebond": 1, "reponse": 1, "refus": 2, "entretien": 3}
EXPEDITEURS_REBOND = ("mailer-daemon", "postmaster")

_ADRESSE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+")
# Début de la citation de notre propre mail : sans cette coupe, notre lettre (« disponible pour un entretien »)
# serait lue comme si l'entreprise l'avait écrite.
_CITATION = re.compile(r"^(le .{0,160}a [ée]crit ?:|on .{0,160}wrote:|-{2,} ?(message d'origine|original message)"
                       r"|de ?: .*@|from ?: .*@)", re.IGNORECASE | re.MULTILINE)

SYSTEME_CLASSEMENT = """Tu classes la réponse d'une entreprise à une candidature spontanée en alternance.
Réponds par un seul mot parmi : entretien, positif, refus, automatique, a_lire.
entretien : l'entreprise propose un entretien, un appel ou demande des disponibilités.
positif : réponse intéressée sans proposition de rendez-vous.
refus : pas de poste, pas de suite.
automatique : accusé de réception ou absence.
a_lire : tout le reste, ou si tu hésites."""


def sujet(message) -> str:
    """Sujet décodé, sur une seule ligne."""
    try:
        texte = str(make_header(decode_header(message.get("Subject", "") or "")))
    except Exception:
        texte = message.get("Subject", "") or ""
    return " ".join(texte.split())


def corps(message) -> str:
    """Première partie texte, sans la citation de notre mail ni les lignes « > », tronquée."""
    brut = ""
    for partie in message.walk():
        if partie.get_content_type() == "text/plain":
            try:
                brut = partie.get_payload(decode=True).decode(partie.get_content_charset() or "utf-8", "replace")
            except Exception:
                brut = ""
            break
    coupe = _CITATION.search(brut)
    if coupe:
        brut = brut[:coupe.start()]
    lignes = [l for l in brut.splitlines() if not l.lstrip().startswith(">")]
    return "\n".join(lignes).strip()[:CORPS_MAX]


def classer_mots(texte: str) -> str:
    """Classe par mots-clés ; « a_lire » si rien ne correspond."""
    texte = (texte or "").lower()
    for classe in CLASSES[:-1]:
        if any(mot in texte for mot in MOTS[classe]):
            return classe
    return "a_lire"


def classer(sujet_: str, corps_: str, cfg: dict) -> str:
    """Mots-clés d'abord ; si rien ne tranche et que `reponses.ia` est vrai, l'IA départage."""
    classe = classer_mots(f"{sujet_}\n{corps_}")
    if classe != "a_lire" or not config.obtenir(cfg, "reponses.ia", False):
        return classe
    try:
        verdict = ia.rediger(SYSTEME_CLASSEMENT, f"Sujet : {sujet_}\n\n{corps_}", cfg)
    except ia.ErreurIA:
        return "a_lire"
    mot = (verdict.strip().split() or ["a_lire"])[0].strip(".,;:").lower()
    return mot if mot in CLASSES and mot != "rebond" else "a_lire"


def candidatures_suivies() -> list[dict]:
    """Mails envoyés (et réponses déjà vues) : adresse, domaine, statut et entreprise."""
    with base.connexion() as conn:
        lignes = conn.execute(
            f"""SELECT c.id, c.email_utilise, c.statut, c.envoyee_le, e.nom AS entreprise_nom
                FROM candidatures c JOIN entreprises e ON e.id = c.entreprise_id
                WHERE c.canal = 'email' AND COALESCE(c.email_utilise, '') != ''
                  AND c.statut IN ({base._liste_sql(RANG)})""").fetchall()
    return [dict(l, email_utilise=l["email_utilise"].lower()) for l in lignes]


def _index(suivies: list[dict]) -> tuple[dict, dict]:
    """({adresse: candidature}, {domaine d'entreprise: candidature}) ; les boîtes gratuites n'indexent pas de domaine."""
    par_adresse, par_domaine = {}, {}
    for c in suivies:
        par_adresse.setdefault(c["email_utilise"], c)
        domaine = base.domaine_de(c["email_utilise"])
        if domaine and domaine not in base.DOMAINES_GRATUITS:
            par_domaine.setdefault(domaine, c)
    return par_adresse, par_domaine


def rattacher(message, par_adresse: dict, par_domaine: dict) -> dict | None:
    """Candidature à laquelle répond ce message : même adresse, même domaine, ou adresse citée dans un rebond."""
    expediteur = parseaddr(message.get("From", ""))[1].lower()
    if expediteur.split("@")[0] in EXPEDITEURS_REBOND:
        texte = corps(message) + "\n" + "".join(
            p.get_payload(decode=True).decode("utf-8", "replace") for p in message.walk()
            if p.get_content_type() in ("text/plain", "message/delivery-status") and p.get_payload(decode=True))
        for adresse in _ADRESSE.findall(texte):
            if adresse.lower() in par_adresse:
                return par_adresse[adresse.lower()]
        return None
    if expediteur in par_adresse:
        return par_adresse[expediteur]
    return par_domaine.get(base.domaine_de(expediteur) or "")


def _comptes_lisibles(cfg: dict) -> list[dict]:
    comptes = [c for c in envoi._comptes(cfg, reel=False) if c["mdp"]]
    if not comptes:
        raise config.ErreurConfig("aucun mot de passe d'application Gmail dans profil/.env (voir docs/GMAIL.md) : "
                                  "la lecture des réponses utilise le même que l'envoi")
    return comptes


def lire_boite(compte: dict, depuis: datetime) -> list:
    """Messages de la boîte de réception reçus depuis la date, sans en marquer aucun comme lu."""
    serveur = imaplib.IMAP4_SSL(IMAP_HOTE, timeout=TIMEOUT_S)
    messages = []
    try:
        serveur.login(compte["email"], compte["mdp"])
        serveur.select("INBOX", readonly=True)
        statut, donnees = serveur.search(None, f'(SINCE "{depuis.strftime("%d-%b-%Y")}")')
        ids = donnees[0].split() if statut == "OK" and donnees and donnees[0] else []
        for i in range(0, len(ids), TAILLE_LOT):
            statut, reponse = serveur.fetch(b",".join(ids[i:i + TAILLE_LOT]).decode(), "(BODY.PEEK[])")
            if statut != "OK":
                continue
            messages += [email.message_from_bytes(item[1]) for item in reponse if isinstance(item, tuple)]
    finally:
        try:
            serveur.logout()
        except Exception:
            pass
    return messages


def suivre(cfg: dict, jours: int = 30) -> dict:
    """Lit les boîtes, classe les réponses, avance les statuts et écrit profil/reponses.md. Rend {classe: [réponses]}."""
    par_adresse, par_domaine = _index(candidatures_suivies())
    depuis = datetime.now() - timedelta(days=jours)
    trouvees: dict[int, dict] = {}
    for compte in _comptes_lisibles(cfg):
        try:
            messages = lire_boite(compte, depuis)
        except (imaplib.IMAP4.error, OSError) as e:
            print(f"{compte['email']} : lecture impossible ({e})")
            continue
        print(f"{compte['email']} : {len(messages)} messages depuis le {depuis:%d/%m}")
        for message in messages:
            candidature = rattacher(message, par_adresse, par_domaine)
            if not candidature:
                continue
            s, c = sujet(message), corps(message)
            est_rebond = parseaddr(message.get("From", ""))[1].lower().split("@")[0] in EXPEDITEURS_REBOND
            classe = "rebond" if est_rebond else classer(s, c, cfg)
            reponse = {"candidature": candidature["id"], "entreprise": candidature["entreprise_nom"],
                       "de": parseaddr(message.get("From", ""))[1].lower(), "sujet": s[:100],
                       "date": message.get("Date", ""), "classe": classe}
            # Plusieurs messages pour la même candidature : on garde le plus parlant.
            garde = trouvees.get(candidature["id"])
            if not garde or _rang_classe(classe) > _rang_classe(garde["classe"]):
                trouvees[candidature["id"]] = reponse
    statuts = {c["id"]: c["statut"] for c in candidatures_suivies()}
    for reponse in trouvees.values():
        nouveau = STATUT_DE_CLASSE.get(reponse["classe"])
        ancien = statuts.get(reponse["candidature"], "envoyee")
        if nouveau and RANG[nouveau] > RANG.get(ancien, 0):
            base.maj_statut(reponse["candidature"], nouveau)
    groupes = defaultdict(list)
    for reponse in trouvees.values():
        groupes[reponse["classe"]].append(reponse)
    _ecrire_rapport(cfg, groupes, jours)
    return dict(groupes)


def _rang_classe(classe: str) -> int:
    return {"automatique": 0, "a_lire": 1, "rebond": 1, "positif": 2, "refus": 2, "entretien": 3}.get(classe, 0)


def _ecrire_rapport(cfg: dict, groupes: dict, jours: int) -> Path:
    chemin = Path(cfg.get("_profil") or config.dossier_profil()) / FICHIER_RAPPORT
    lignes = [f"# Réponses aux candidatures ({jours} derniers jours, lu le {datetime.now():%d/%m/%Y %H:%M})", ""]
    for classe in ("entretien", "positif", "a_lire", "refus", "automatique", "rebond"):
        items = sorted(groupes.get(classe, []), key=lambda r: r["entreprise"] or "")
        lignes += [f"## {classe.replace('_', ' ')} ({len(items)})", ""]
        lignes += [f"- **{r['entreprise']}**, {r['de']}, « {r['sujet']} » ({r['date']})" for r in items]
        lignes.append("")
    chemin.write_text("\n".join(lignes), encoding="utf-8")
    return chemin


def _arguments(parser) -> None:
    parser.add_argument("--jours", type=int, default=30, help="ancienneté maximale des messages lus")


@commande("reponses", "Lit les boîtes Gmail (lecture seule) et classe les réponses aux candidatures", _arguments)
def _reponses(args) -> int:
    cfg = config.charger()
    groupes = suivre(cfg, args.jours)
    total = sum(len(v) for v in groupes.values())
    resume = ", ".join(f"{len(groupes.get(c, []))} {c.replace('_', ' ')}" for c in CLASSES if groupes.get(c))
    print(f"{total} réponses" + (f" : {resume}" if resume else "") + f". Rapport : profil/{FICHIER_RAPPORT}")
    return 0
