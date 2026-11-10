# La routine quotidienne

Une fois installée, la routine lance chaque jour, à l'heure `routine.heure` de `config.yaml` :

1. `envoyer` : les mails rédigés la veille ;
2. `lba` : les candidatures La Bonne Alternance du jour ;
3. `offres` : seulement si une clé SerpAPI est renseignée ;
4. `emails` : les adresses des nouvelles entreprises ;
5. `rediger` : les mails du lendemain, mis en file ;
6. `reponses` : le classement des réponses des 14 derniers jours.

Chaque étape a un délai maximal. Une étape qui échoue ou qui dépasse son délai est notée, et les suivantes
tournent quand même. Le journal du jour est dans `profil/journaux/AAAA-MM-JJ.log`.

La routine ne cherche pas de nouvelles entreprises sur Google Maps (c'est payant) : lance `entreprises` toi-même
quand la file se vide.

## Essayer

```bash
python -m alternabot routine lancer --essai
```

Les étapes tournent, mais `envoyer` et `lba` ne font qu'un essai.

## Installer

```bash
python -m alternabot routine installer
```

- **macOS** : une tâche launchd (`~/Library/LaunchAgents/io.github.alternabot.routine.plist`). Le Mac doit être
  allumé à l'heure dite ; s'il dormait, la tâche tourne au réveil.
  Si le dépôt est dans `Documents`, `Bureau` ou `Téléchargements`, macOS empêche launchd d'y lire : déplace le
  dépôt (par exemple dans `~/alternabot`) avant d'installer.
- **Linux** : la commande affiche la ligne crontab, et l'ajoute avec `routine installer --oui`.
- **Windows** : pas d'installation automatique. Crée une tâche dans le Planificateur de tâches qui lance
  `python -m alternabot routine lancer` depuis le dossier du dépôt.

## Retirer

```bash
python -m alternabot routine retirer
```
