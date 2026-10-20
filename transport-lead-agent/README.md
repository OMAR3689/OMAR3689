# AI Transport Lead Agent 🚚🤖

Agent IA de prospection pour détecter des entreprises qui recherchent un prestataire de transport/logistique.

## Ce que l'agent fait

1. Lance des recherches web ciblées sur les besoins transport/logistique.
2. Détecte les signaux d'intention: `appel d'offres`, `recherche transporteur`, `prestataire transport`, `sous-traitant transport`, `freight tender`, `carrier needed`, `transportation provider`, etc.
3. Extrait les informations publiques disponibles: entreprise, pays, ville, besoin, source, URL, email/téléphone lorsqu'ils sont publiquement visibles.
4. Déduplique les prospects.
5. Attribue un score de priorité de 0 à 100.
6. Génère `data/leads.json` et `data/leads.csv`.
7. Peut être exécuté automatiquement chaque matin via GitHub Actions.

## Cible initiale

- Maroc, France, Belgique, Allemagne, Espagne, Italie.
- Transport routier, messagerie, distribution, flotte, affrètement, dernier kilomètre et sous-traitance.
- Priorité aux demandes publiées récemment et aux entreprises exprimant explicitement un besoin de prestataire.

## Configuration

Créer le secret GitHub `TAVILY_API_KEY` dans Settings → Secrets and variables → Actions.

Le workflow utilise ensuite l'API Tavily pour la recherche web. Les résultats restent des leads à qualifier: l'agent ne prétend pas qu'une entreprise est cliente tant qu'un besoin réel n'est pas vérifié.

## Lancer

```bash
pip install -r requirements.txt
export TAVILY_API_KEY="..."
python transport-lead-agent/agent.py
```
