# La Bonne Alternance

[La Bonne Alternance](https://labonnealternance.apprentissage.beta.gouv.fr) est le service public qui recense les
offres d'alternance et les entreprises qui recrutent des alternants. Le bot y cherche des entreprises par l'API, puis
postule par la **candidature simplifiée** du site (le formulaire avec CV et message), comme tu le ferais à la main.

## Le jeton de l'API

1. Crée un compte gratuit sur https://api.apprentissage.beta.gouv.fr
2. Génère un jeton d'accès dans ton espace.
3. Mets-le dans `profil/.env` :

```
LBA_API_TOKEN=...
```

## Ce qu'il cherche

Les **codes ROME** de `recherche.codes_rome` (les métiers visés), autour de `recherche.latitude` et
`recherche.longitude`, dans `recherche.rayon_km` (arrondi à 10, 30, 60 ou 100 km). Trouve tes codes sur
https://candidat.francetravail.fr/metierscope (par exemple M1805 pour le développement informatique).

Seules les fiches qui ont le bouton de candidature simplifiée sont retenues.

## Ce qu'il envoie

Le formulaire est rempli avec tes coordonnées, ta formation, ton CV et un message court rédigé pour l'entreprise.
Essaie toujours d'abord :

```bash
python -m alternabot lba --essai --limite 2
```

Les captures des formulaires remplis (non envoyés) sont dans `profil/captures/`.

## Règles du site

La Bonne Alternance sert aux candidatures, et seulement à elles. Le bot ne s'en sert que pour postuler, à un rythme
humain (`lba.delai_s` secondes entre deux candidatures) et dans un plafond bas (`lba.plafond_jour`). Si le site
change, les sélecteurs du formulaire peuvent casser : `lba --essai --visible` montre où ça bloque.
