# Gmail : mot de passe d'application

Le bot envoie par SMTP et lit les réponses par IMAP avec un **mot de passe d'application** : un mot de passe de
16 lettres que Google crée pour un seul outil. Ton vrai mot de passe ne sort jamais.

## Créer le mot de passe

1. Active la validation en deux étapes : https://myaccount.google.com/signinoptions/twosv
2. Ouvre https://myaccount.google.com/apppasswords
3. Donne un nom (par exemple « alternabot »), puis **Créer**.
4. Copie les 16 lettres dans `profil/.env` :

```
GMAIL_MDP_1=abcdabcdabcdabcd
```

Les espaces sont ignorés. Si la page « Mots de passe des applications » n'existe pas, c'est que la validation en
deux étapes n'est pas active, ou que le compte est géré par une école ou une entreprise qui l'interdit : utilise
un compte Gmail personnel.

## Déclarer le compte

Dans `profil/config.yaml` :

```yaml
gmail:
  comptes:
    - email: camille.martin@gmail.com
      variable_mdp: GMAIL_MDP_1
```

## Plusieurs boîtes

Tu peux ajouter un deuxième compte (`GMAIL_MDP_2`) : les envois alternent entre les boîtes, chacune avec son
plafond (`plafond_jour_par_compte`). Les réponses sont lues dans toutes les boîtes déclarées.

## Lecture des réponses

`reponses` ouvre la boîte de réception en lecture seule : aucun message n'est marqué comme lu, déplacé ou
supprimé. Seuls les messages qui viennent d'une adresse (ou d'un domaine d'entreprise) à laquelle le bot a écrit
sont classés.

## Si Gmail refuse

- `Username and Password not accepted` : mot de passe d'application faux ou révoqué, recrée-le.
- `Daily user sending limit exceeded` : Gmail a bloqué les envois pour la journée. Le bot s'arrête et reprend le
  lendemain ; baisse ton plafond.
