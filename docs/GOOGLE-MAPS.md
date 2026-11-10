# Google Maps : trouver des entreprises

La commande `entreprises` cherche, autour de ta ville, les lieux qui répondent à chaque requête de
`recherche.secteurs_maps` (par exemple « agence web », « cabinet de conseil data »), puis lit leur fiche : nom,
adresse, téléphone et **site web**. C'est le site qui permet ensuite à `emails` de trouver une adresse.

## Créer la clé

1. Ouvre https://console.cloud.google.com et crée un projet.
2. Active la facturation (Google demande une carte, même pour l'usage gratuit).
3. Active **Places API** et **Geocoding API** (menu « API et services », « Bibliothèque »).
4. Crée une clé (« Identifiants », « Créer des identifiants », « Clé API ») et restreins-la à ces deux API.
5. Mets-la dans `profil/.env` :

```
GOOGLE_MAPS_API_KEY=...
```

Guide officiel : https://developers.google.com/maps/documentation/places/web-service/get-api-key

## Le coût

Chaque recherche et chaque fiche sont facturées. Le bot estime la dépense **avant** chaque appel et s'arrête au
budget : `maps.budget_usd` dans la config, ou `--budget` sur la ligne de commande. Ordre de grandeur mesuré :
environ 1 $ pour 35 à 40 entreprises. Une entreprise déjà en base n'est jamais repayée.

Pour ne jamais être surpris, fixe aussi un plafond de dépense dans la console Google Cloud (« Facturation »,
« Budgets et alertes »).
