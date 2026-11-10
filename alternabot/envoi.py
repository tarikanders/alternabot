"""Envoi des mails spontanés en file par SMTP Gmail (mot de passe d'application), avec plafond par boîte."""
import os
import random
import smtplib
import time
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from . import base, config, emails
from .cli import commande

SMTP_HOTE = "smtp.gmail.com"
SMTP_PORT = 587
TIMEOUT_S = 30
CODES_PASSAGERS = (421, 450, 451, 452)  # refus temporaires de Gmail : la boîte attend demain, pas la candidature
MARQUES_QUOTA = ("sending limit", "sending quota", "5.4.5", "daily user")


class _CompteIndisponible(Exception):
    """La boîte ne peut plus envoyer pour ce lancement ; la candidature reste en file."""


def _comptes(cfg: dict, reel: bool) -> list[dict]:
    """Boîtes de gmail.comptes avec leur mot de passe d'application (exigé seulement pour un vrai envoi)."""
    comptes = []
    for c in config.obtenir(cfg, "gmail.comptes", []) or []:
        if not isinstance(c, dict) or not c.get("email"):
            continue
        variable = c.get("variable_mdp") or ""
        mdp = "".join(os.environ.get(variable, "").split()) if variable else ""
        if reel and not mdp:
            raise config.ErreurConfig(
                f"{variable or 'variable_mdp'} est vide ou absente de profil/.env : "
                f"mot de passe d'application de {c['email']} (voir https://myaccount.google.com/apppasswords)")
        comptes.append({"email": c["email"], "mdp": mdp})
    if not comptes:
        raise config.ErreurConfig("gmail.comptes : aucun compte renseigné")
    return comptes


def _chemin_cv(cfg: dict) -> Path:
    chemin = Path(cfg.get("_profil") or config.dossier_profil()) / str(config.obtenir(cfg, "candidat.cv", "CV.pdf"))
    if not chemin.is_file():
        raise config.ErreurConfig(f"CV introuvable : {chemin}")
    return chemin


def construire_message(cfg: dict, expediteur: str, destinataire: str, objet: str, corps: str) -> EmailMessage:
    """Mail texte UTF-8 de « Prénom Nom <boîte> », réponses vers la même boîte, CV en pièce jointe."""
    nom = f"{config.obtenir(cfg, 'candidat.prenom', '')} {config.obtenir(cfg, 'candidat.nom', '')}".strip()
    cv = _chemin_cv(cfg)
    message = EmailMessage()
    message["From"] = formataddr((nom, expediteur))
    message["Reply-To"] = expediteur
    message["To"] = destinataire
    message["Subject"] = objet
    message.set_content(corps)
    message.add_attachment(cv.read_bytes(), maintype="application", subtype="pdf", filename=cv.name)
    return message


def _ouvrir(compte: dict) -> smtplib.SMTP:
    """Connexion authentifiée ; _CompteIndisponible si la boîte refuse le mot de passe ou ne répond pas."""
    smtp = None
    try:
        smtp = smtplib.SMTP(SMTP_HOTE, SMTP_PORT, timeout=TIMEOUT_S)
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(compte["email"], compte["mdp"])
        return smtp
    except smtplib.SMTPAuthenticationError as e:
        _fermer(smtp)
        raise _CompteIndisponible("authentification refusée (mot de passe d'application invalide ?)") from e
    except (smtplib.SMTPException, OSError) as e:
        _fermer(smtp)
        raise _CompteIndisponible(f"connexion impossible : {e}") from e


def _fermer(smtp) -> None:
    try:
        if smtp is not None:
            smtp.quit()
    except Exception:
        pass


def _texte_erreur(e: smtplib.SMTPResponseException) -> str:
    brut = e.smtp_error
    return brut.decode(errors="replace") if isinstance(brut, bytes) else str(brut)


def _expedier(smtp, message: EmailMessage, compte: str, destinataire: str) -> str | None:
    """None si le mail est parti, sinon le message d'erreur du destinataire. _CompteIndisponible si c'est la boîte."""
    try:
        smtp.send_message(message, from_addr=compte, to_addrs=[destinataire])
        return None
    except smtplib.SMTPAuthenticationError as e:
        raise _CompteIndisponible("authentification refusée") from e
    except smtplib.SMTPRecipientsRefused as e:
        return "; ".join(f"{code} {texte.decode(errors='replace') if isinstance(texte, bytes) else texte}"
                         for code, texte in e.recipients.values()) or str(e)
    except smtplib.SMTPResponseException as e:
        texte = _texte_erreur(e)
        if e.smtp_code in CODES_PASSAGERS or any(m in texte.lower() for m in MARQUES_QUOTA):
            raise _CompteIndisponible(f"refus de Gmail ({e.smtp_code} {texte[:120]})") from e
        return f"{e.smtp_code} {texte}"[:300]
    except (smtplib.SMTPServerDisconnected, OSError) as e:
        raise _CompteIndisponible(f"connexion perdue : {e}") from e
    except smtplib.SMTPException as e:
        return str(e)[:300]


def _corps(cfg: dict, candidature: dict) -> str | None:
    chemin = candidature.get("corps_chemin")
    fichier = Path(cfg.get("_profil") or config.dossier_profil()) / chemin if chemin else None
    return fichier.read_text(encoding="utf-8") if fichier and fichier.is_file() else None


def envoyer(cfg: dict, limite: int | None = None, essai: bool = False) -> dict:
    """Envoie les mails en file, dans la limite du plafond par boîte et par jour.

    Chaque mail part de la boîte qui a le plus de marge, puis on attend un délai aléatoire. En essai, aucune
    connexion SMTP : le message est construit et résumé, les statuts restent inchangés.
    Rend {"envoyees", "essais", "echecs", "restants", "motif"} (motif : None, aucune_candidature, limite,
    plafond_atteint ou comptes_indisponibles).
    """
    comptes = _comptes(cfg, reel=not essai)
    plafond = int(config.obtenir(cfg, "gmail.plafond_jour_par_compte", 15))
    delai_min = float(config.obtenir(cfg, "gmail.delai_min_s", 45))
    delai_max = max(delai_min, float(config.obtenir(cfg, "gmail.delai_max_s", 120)))
    marge = {c["email"]: max(0, plafond - base.envoyes_aujourdhui(c["email"])) for c in comptes}
    actifs = list(comptes)
    connexions: dict[str, smtplib.SMTP] = {}
    bilan = {"envoyees": 0, "essais": 0, "echecs": 0, "restants": 0, "motif": None}
    file = base.candidatures_en_file("email")
    if not file:
        bilan["motif"] = "aucune_candidature"
    cv = _chemin_cv(cfg) if file else None

    def echec(candidature, raison):
        bilan["echecs"] += 1
        print(f"[{candidature['id']}] {candidature['entreprise_nom']} : échec ({raison})")
        if not essai:
            base.maj_statut(candidature["id"], "echec", erreur=raison)

    try:
        for rang, c in enumerate(file):
            if limite is not None and bilan["envoyees"] + bilan["essais"] >= limite:
                bilan["motif"] = "limite"
                break
            destinataire = c.get("email_utilise")
            if not destinataire:
                echec(c, "adresse absente")
                continue
            if base.deja_contactee(c["entreprise_id"], destinataire):
                echec(c, "déjà contactée")
                continue
            if not emails.domaine_recoit(base.domaine_de(destinataire)):
                echec(c, "domaine sans serveur mail")
                continue
            corps = _corps(cfg, c)
            if corps is None:
                echec(c, "corps du mail introuvable")
                continue
            envoye = False
            while not envoye:
                disponibles = [a for a in actifs if marge[a["email"]] > 0]
                if not disponibles:
                    bilan["motif"] = "plafond_atteint" if actifs else "comptes_indisponibles"
                    break
                compte = max(disponibles, key=lambda a: marge[a["email"]])
                message = construire_message(cfg, compte["email"], destinataire, c.get("objet") or "", corps)
                if essai:
                    print(f"[{c['id']}] {c['entreprise_nom']} (essai, rien n'est envoyé)\n"
                          f"    de : {message['From']}\n    à : {destinataire}\n    objet : {c.get('objet')}\n"
                          f"    corps : {len(corps)} caractères, pièce jointe : {cv.name}")
                    bilan["essais"] += 1
                    marge[compte["email"]] -= 1
                    envoye = True
                    continue
                try:
                    if compte["email"] not in connexions:
                        connexions[compte["email"]] = _ouvrir(compte)
                    erreur = _expedier(connexions[compte["email"]], message, compte["email"], destinataire)
                except _CompteIndisponible as e:
                    print(f"Boîte {compte['email']} retirée pour ce lancement : {e}")
                    _fermer(connexions.pop(compte["email"], None))
                    actifs.remove(compte)
                    continue
                if erreur:
                    echec(c, erreur)
                    break
                base.maj_statut(c["id"], "envoyee", expediteur=compte["email"])
                marge[compte["email"]] -= 1
                bilan["envoyees"] += 1
                print(f"[{c['id']}] {c['entreprise_nom']} : envoyée depuis {compte['email']}")
                envoye = True
                encore_de_la_marge = any(marge[a["email"]] > 0 for a in actifs)
                if rang < len(file) - 1 and encore_de_la_marge and (limite is None or bilan["envoyees"] < limite):
                    time.sleep(random.uniform(delai_min, delai_max))
            if bilan["motif"] in ("plafond_atteint", "comptes_indisponibles"):
                break
    finally:
        for smtp in connexions.values():
            _fermer(smtp)
    bilan["restants"] = len(base.candidatures_en_file("email"))
    return bilan


def _arguments(parser) -> None:
    parser.add_argument("--limite", type=int, default=None, help="nombre maximal de mails sur ce lancement")
    parser.add_argument("--essai", action="store_true", help="construit et résume les mails sans rien envoyer")


@commande("envoyer", "Envoie les mails spontanés en file (plafond par boîte et par jour)", _arguments)
def _envoyer(args) -> int:
    cfg = config.charger()
    bilan = envoyer(cfg, args.limite, args.essai)
    print(f"Envoyés : {bilan['envoyees']}, essais : {bilan['essais']}, échecs : {bilan['echecs']}, "
          f"restants en file : {bilan['restants']}")
    messages = {"aucune_candidature": "Aucun mail en file : lance d'abord rediger.",
                "plafond_atteint": "Plafond du jour atteint sur toutes les boîtes.",
                "comptes_indisponibles": "Aucune boîte ne peut envoyer : vérifie les mots de passe d'application."}
    if bilan["motif"] in messages:
        print(messages[bilan["motif"]])
    return 1 if bilan["motif"] == "comptes_indisponibles" or (
        bilan["echecs"] and not (bilan["envoyees"] or bilan["essais"])) else 0
