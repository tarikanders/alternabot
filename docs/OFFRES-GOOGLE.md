# Offres publiées sur Google (optionnel)

La commande `offres` interroge Google Jobs par [SerpAPI](https://serpapi.com) : les offres d'alternance publiées
sur les sites d'emploi et les sites des entreprises. Pour chaque offre, le bot garde l'entreprise qui recrute et
l'intitulé de l'offre. Le mail spontané rédigé ensuite pour cette entreprise cite l'offre.

Le bot ne remplit pas les formulaires des sites d'emploi : il écrit directement à l'entreprise.

## La clé

1. Crée un compte gratuit sur https://serpapi.com (100 recherches par mois).
2. Copie ta clé (« Your Private API Key ») dans `profil/.env` :

```
SERPAPI_KEY=...
```

## Utilisation

```bash
python -m alternabot offres --requete "alternance data Lyon" --limite 5
python -m alternabot emails
python -m alternabot rediger
```

Sans `--requete`, la recherche est « alternance » suivi de `recherche.mots_cles_offres` et de ta ville. Une
commande `offres` coûte une recherche SerpAPI.

## Le site de l'entreprise

Une offre ne donne pas le site de l'entreprise. Le bot le cherche sur Google Maps (nom et lieu, environ 0,04 $
par entreprise, dans le budget `maps.budget_usd`), sinon il le déduit du lien de l'offre quand ce lien ne mène pas
à un site d'emploi. Une entreprise restée sans site n'aura pas d'adresse : ajoute son site à la main ou
contacte-la autrement.
