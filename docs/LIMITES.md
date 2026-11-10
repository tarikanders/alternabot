# Limites et bon usage

## Les plafonds par défaut

| Réglage | Défaut | Pourquoi |
|---|---|---|
| `gmail.plafond_jour_par_compte` | 15 | au-delà, Gmail peut bloquer la boîte, et les mails finissent en indésirables |
| `gmail.delai_min_s` / `delai_max_s` | 45 / 120 s | un rythme humain entre deux envois |
| `lba.plafond_jour` | 10 | La Bonne Alternance est un service public, pas un canal de masse |
| `maps.budget_usd` | 5 $ | Google Maps est facturé à l'appel |

Tu peux les monter, à tes risques. Quinze bons mails par jour valent mieux que cent mails génériques.

## Ce que le bot ne fait jamais

- Écrire deux fois à la même entreprise (même adresse, même domaine ou même SIRET).
- Écrire à une adresse dont le domaine ne reçoit pas de mail (contrôle MX).
- Toucher à ta boîte de réception : la lecture des réponses est en lecture seule.
- Envoyer un texte où il reste un trou de gabarit (« [Nom] ») ou le nom d'une autre entreprise. Il ne
  peut pas repérer une invention sur toi : c'est pour ça que `profil.md` liste ce qu'il ne doit jamais inventer.

## Ce qui reste à ta charge

- **Relire.** Lis les premiers mails avec `apercu`. Si un texte ne te ressemble pas, corrige `profil/profil.md`.
- **Répondre.** Le bot classe les réponses, il n'y répond pas. Un entretien proposé attend ta réponse, vite.
- **Le droit.** Écrire à l'adresse de contact ou de recrutement d'une entreprise pour candidater est un usage
  normal. N'utilise pas le bot pour autre chose que tes candidatures, et ne le pointe pas vers des adresses
  personnelles.
- **Les conditions des services.** Gmail, Google Maps, SerpAPI et La Bonne Alternance ont leurs propres conditions
  d'utilisation : c'est ton compte qui les accepte.
