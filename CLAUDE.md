# Alternabot : guide d'installation pour Claude Code

Tu aides un étudiant à installer et à lancer Alternabot, un bot qui envoie des candidatures en alternance en son
nom. Il n'est peut-être pas développeur : explique simplement, une étape à la fois, et fais toi-même tout ce qui
peut se faire dans le terminal. Quand il dit « installe », déroule les étapes ci-dessous dans l'ordre.

## Règles

- **Tout ce qui est personnel va dans `profil/`**, ignoré par git. N'écris jamais une donnée de l'étudiant (nom,
  adresse, téléphone, clé, mot de passe) ailleurs, ni dans un commit.
- **Ne lis ni n'affiche jamais une clé ou un mot de passe.** Pour remplir `profil/.env`, ouvre-lui le fichier dans
  son éditeur (`open profil/.env` sur macOS, `xdg-open profil/.env` sur Linux) et laisse-le coller la valeur
  lui-même. Pour vérifier, lance `verifier`, jamais `cat profil/.env`. Ne demande pas qu'il colle une clé dans la conversation.
- **N'invente rien sur lui.** `profil/profil.md` se remplit avec ses réponses, pas avec des suppositions.
- **Aucun vrai envoi sans son accord explicite.** Avant le premier `envoyer` ou `lba` sans `--essai`, montre-lui
  `apercu` (ou les captures de `lba --essai`) et demande-lui de confirmer.
- **N'augmente pas les plafonds** de `config.yaml` de toi-même. S'il le demande, rappelle-lui
  [docs/LIMITES.md](docs/LIMITES.md).
- Pas de tiret cadratin dans ce que tu écris pour lui.

## Étapes de l'installation

### 1. Environnement Python

```bash
python3 --version            # 3.11 ou plus récent ; sinon, aide-le à l'installer
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
cp -r profil.exemple profil  # seulement si profil/ n'existe pas encore
```

### 2. Le profil (`profil/profil.md`)

Pose-lui les questions du gabarit, section par section, en quelques messages courts : qui il est, sa formation, ce
qu'il cherche (poste, durée, date de début, rythme, zone), ses compétences (ce qu'il maîtrise, ce qu'il apprend),
deux à quatre projets avec un résultat concret, ce qui le motive, le ton voulu, et ce que l'IA ne doit jamais
inventer. Écris ses réponses à la première personne, sans enjoliver, puis montre-lui le fichier et corrige avec lui.

### 3. Le CV

Demande-lui le chemin de son CV en PDF et copie-le dans `profil/CV.pdf`.

### 4. La configuration (`profil/config.yaml`)

Remplis avec lui : `candidat` (prénom, nom, email, téléphone, LinkedIn, formation, école, rythme, durée, début),
`recherche` (ville, latitude et longitude de la ville, rayon, codes ROME des métiers visés : aide-le à les trouver
sur https://candidat.francetravail.fr/metierscope, secteurs Google Maps, mots-clés des offres) et `gmail.comptes`
(son adresse Gmail). Laisse les plafonds par défaut.

### 5. Les clés (`profil/.env`)

Une à la fois, en suivant le guide de chacune :
1. Gmail : mot de passe d'application, [docs/GMAIL.md](docs/GMAIL.md) (obligatoire).
2. La Bonne Alternance : jeton de l'API, [docs/LBA.md](docs/LBA.md).
3. Google Maps : clé avec l'API Places, [docs/GOOGLE-MAPS.md](docs/GOOGLE-MAPS.md).
4. SerpAPI (optionnel) : [docs/OFFRES-GOOGLE.md](docs/OFFRES-GOOGLE.md).

S'il n'a pas Claude Code connecté pour la rédaction (`claude --version` échoue), propose `ia.moteur: api` et une
clé `ANTHROPIC_API_KEY`.

### 6. Vérification

```bash
.venv/bin/python -m alternabot verifier
```

Corrige chaque ligne en ✗ avec lui, relance, jusqu'à « Prêt. ».

### 7. Premier essai, sans rien envoyer

```bash
.venv/bin/python -m alternabot lba --essai --limite 2
.venv/bin/python -m alternabot entreprises --budget 1
.venv/bin/python -m alternabot emails --limite 10
.venv/bin/python -m alternabot rediger --limite 2
.venv/bin/python -m alternabot apercu
```

Montre-lui les captures (`profil/captures/`) et les deux mails. S'il veut changer le ton ou le contenu, c'est
`profil/profil.md` qu'il faut retoucher, puis relancer `rediger`.

### 8. Premiers vrais envois, puis la routine

Seulement avec son accord : `envoyer --limite 1` vers une première entreprise, puis `lba --limite 1`. Ensuite,
propose `routine lancer --essai`, puis `routine installer` pour que le bot tourne chaque jour
([docs/ROUTINE.md](docs/ROUTINE.md)).

## Après l'installation

- `statut` et `reponses` disent où il en est. Les entretiens proposés apparaissent dans `statut`.
- Une erreur : lis le journal du jour dans `profil/journaux/`, et `verifier` en premier réflexe.
- Le code est dans `alternabot/`, les tests dans `tests/` (`.venv/bin/python -m pytest -q`).
