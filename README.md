# Alternabot

Un bot qui cherche une alternance à ta place, chaque jour, depuis ton ordinateur.

- Il **postule sur La Bonne Alternance** (candidature simplifiée), avec un message rédigé pour chaque entreprise.
- Il **trouve des entreprises autour de ta ville** (Google Maps), cherche leur adresse mail sur leur site, puis leur
  **écrit une candidature spontanée** qui part de ton profil et de ce que l'entreprise dit d'elle-même.
- En option, il lit les **offres d'alternance publiées sur Google** et écrit aux entreprises qui recrutent, en citant l'offre.
- Il **suit les réponses** dans ta boîte Gmail (entretien, refus, réponse automatique, adresse invalide) sans rien y toucher.
- Il **tourne seul** une fois par jour.

Tout reste chez toi : ton profil, ton CV et la base de suivi vivent dans le dossier `profil/`, que git ignore.

> **Utilise-le avec soin.** Ce sont de vrais mails, envoyés en ton nom à de vraies personnes. Les plafonds par défaut
> sont bas exprès (15 mails par jour et par boîte, 10 candidatures La Bonne Alternance par jour). Relis les premiers
> mails avec `apercu` avant tout envoi. Voir [docs/LIMITES.md](docs/LIMITES.md).

## Ce qu'il te faut

| Quoi | Obligatoire | Coût | Guide |
|---|---|---|---|
| Python 3.11 ou plus récent, macOS ou Linux | oui | gratuit | |
| [Claude Code](https://claude.com/claude-code) connecté (abonnement Claude), ou une clé API Anthropic | oui | ton abonnement, ou à l'usage | |
| Un compte Gmail avec la validation en deux étapes et un mot de passe d'application | oui | gratuit | [docs/GMAIL.md](docs/GMAIL.md) |
| Un jeton de l'API La Bonne Alternance | oui pour `lba` | gratuit | [docs/LBA.md](docs/LBA.md) |
| Une clé Google Maps Platform | oui pour `entreprises` | facturée à l'appel, environ 1 $ pour 35 à 40 entreprises, plafond réglable | [docs/GOOGLE-MAPS.md](docs/GOOGLE-MAPS.md) |
| Une clé SerpAPI | non | 100 recherches gratuites par mois | [docs/OFFRES-GOOGLE.md](docs/OFFRES-GOOGLE.md) |

## Installation avec Claude Code (recommandé)

```bash
git clone https://github.com/tarikanders/alternabot.git
cd alternabot
claude
```

Puis dis-lui : **« installe »**. Claude Code suit [CLAUDE.md](CLAUDE.md) : il crée l'environnement Python, te pose
les questions pour remplir ton profil, t'aide à créer tes clés, et s'arrête quand `verifier` est tout vert.

## Installation à la main

```bash
git clone https://github.com/tarikanders/alternabot.git
cd alternabot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp -r profil.exemple profil
```

Ensuite :

1. Remplis `profil/profil.md` : c'est ce que l'IA lit pour écrire tes mails. Sois précis et honnête.
2. Mets ton CV en PDF dans `profil/CV.pdf`.
3. Remplis `profil/config.yaml` : tes coordonnées, ta formation, ta ville et ses coordonnées GPS, tes codes ROME.
4. Remplis `profil/.env` avec tes clés (voir les guides du tableau ci-dessus).
5. Vérifie :

```bash
python -m alternabot verifier
```

Chaque ligne en ✗ dit ce qui manque et comment le corriger. Recommence jusqu'à « Prêt. ».

## Utilisation

Toutes les commandes se lancent depuis le dossier du dépôt, avec l'environnement activé (`source .venv/bin/activate`).

### La Bonne Alternance

```bash
python -m alternabot lba --essai --limite 2   # remplit deux formulaires sans les envoyer, captures dans profil/captures/
python -m alternabot lba                      # postule pour de vrai, dans la limite de lba.plafond_jour
```

### Mails spontanés

```bash
python -m alternabot entreprises --budget 1   # entreprises autour de ta ville (Google Maps), 1 $ au plus
python -m alternabot emails                   # adresse de chaque entreprise, lue sur son site et contrôlée
python -m alternabot rediger --limite 5       # rédige 5 mails et les met en file
python -m alternabot apercu                   # relis-les
python -m alternabot envoyer --essai          # montre ce qui partirait, sans rien envoyer
python -m alternabot envoyer                  # envoie, dans la limite du plafond par boîte
```

Les entreprises sans adresse trouvée vont dans `profil/a_contacter_a_la_main.csv`, avec leur téléphone.

### Offres publiées sur Google (optionnel)

```bash
python -m alternabot offres --requete "alternance data Lyon" --limite 5
```

Les entreprises trouvées passent ensuite par `emails`, `rediger` et `envoyer`, et leur mail cite l'offre.

### Suivi

```bash
python -m alternabot reponses --jours 30      # classe les réponses reçues, rapport dans profil/reponses.md
python -m alternabot statut                   # compteurs, taux de réponse, entretiens proposés
```

### Tous les jours, tout seul

```bash
python -m alternabot routine lancer --essai   # une routine complète, sans aucun envoi
python -m alternabot routine installer        # chaque jour à routine.heure (launchd sur macOS, cron sur Linux)
python -m alternabot routine retirer
```

Voir [docs/ROUTINE.md](docs/ROUTINE.md).

## Comment il écrit

Le prompt de rédaction ne contient aucun parcours type : il lit ton `profil.md` et le texte du site de l'entreprise.
Chaque texte est ensuite relu par le code, et refusé puis réécrit s'il contient un trou de gabarit (« [Nom] »), le nom
d'une autre entreprise, un tiret cadratin, ou s'il est trop court ou trop long. Le CV est la seule pièce jointe :
la lettre est le corps du mail.

Par défaut, la rédaction passe par `claude -p`, donc par ton abonnement Claude Code. Pour passer par l'API, mets
`ia.moteur: api` dans `config.yaml` et `ANTHROPIC_API_KEY` dans `.env`.

## Structure

```
alternabot/          le code (python -m alternabot <commande>)
profil.exemple/      gabarits à copier dans profil/
profil/              TES données, ignorées par git : profil.md, CV.pdf, config.yaml, .env, base SQLite, journaux
docs/                guides des services et limites
tests/               tests (pytest), sans réseau
```

## Tests

```bash
python -m pytest -q
```

## Licence

MIT, voir [LICENSE](LICENSE).
