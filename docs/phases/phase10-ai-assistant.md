# Phase 10 — IA Assistant

## Objectif

Creer un assistant IA capable d'expliquer les signaux, analyser les trades, resumer les performances, repondre aux questions et suggerer des ameliorations.

L'IA ne peut **JAMAIS** ouvrir une position seule.

---

## Architecture

```
src/arty_trading/modules/ai/
├── __init__.py            # Exports publics
├── base.py               # Interface AIProvider (ABC)
├── openai_provider.py    # Provider OpenAI (GPT-4, GPT-4o)
├── anthropic_provider.py # Provider Anthropic (Claude 3.5)
└── assistant.py          # Assistant IA principal
```

---

## Providers supportes

### OpenAI
- Modeles : GPT-4o, GPT-4o-mini, GPT-4 Turbo
- Variable d'env : `OPENAI_API_KEY`
- Package requis : `openai`

### Anthropic
- Modeles : Claude 3.5 Sonnet, Claude 3 Opus
- Variable d'env : `ANTHROPIC_API_KEY`
- Package requis : `anthropic`

---

## Configuration (.env)

```env
# Provider IA (openai ou anthropic)
AI_PROVIDER=openai

# OpenAI
OPENAI_API_KEY=sk-...
AI_MODEL=gpt-4o-mini

# Anthropic (alternative)
ANTHROPIC_API_KEY=sk-ant-...
AI_ANTHROPIC_MODEL=claude-3-5-sonnet-20241022

# Parametres generaux
AI_MAX_TOKENS=2048
AI_TEMPERATURE=0.7
AI_SYSTEM_PROMPT=
```

---

## Fonctionnalites

### 1. Chat general
L'utilisateur peut poser n'importe quelle question sur le trading, les strategies, les concepts SMC.

### 2. Explication de signaux
L'IA analyse un signal genere et explique :
- Pourquoi le signal a ete genere
- La coherence des concepts SMC
- La qualite du ratio risque/rendement
- Le niveau de confiance
- Les risques eventuels

### 3. Analyse de trades
L'IA examine un trade ferme et fournit :
- Ce qui s'est bien/mal passe
- Respect des regles de la strategie
- Suggestions d'amelioration
- Note de qualite d'execution

### 4. Resume de performances
L'IA genere un rapport de performance en langage naturel :
- Resume global
- Points forts
- Points faibles
- Recommandations

### 5. Suggestions d'amelioration
L'IA analyse la configuration et suggere :
- Ameliorations prioritaires
- Ajustements de parametres
- Strategies a activer/desactiver

---

## Endpoints API

| Methode | Route | Description |
|---------|-------|-------------|
| GET | `/ai/status` | Statut de l'assistant IA |
| POST | `/ai/chat` | Chat general |
| POST | `/ai/explain/signal` | Expliquer un signal |
| POST | `/ai/analyze/trade` | Analyser un trade |
| POST | `/ai/performance-summary` | Resume des performances |
| POST | `/ai/suggestions` | Suggestions d'amelioration |
| POST | `/ai/clear-history` | Vider l'historique |

---

## Securite

- L'IA ne peut **JAMAIS** ouvrir, fermer ou modifier une position
- L'IA ne fait que des suggestions et des analyses
- Le prompt systeme rappelle les regles de securite
- L'IA rappelle toujours les regles de gestion du risque

---

## Tests

20 tests couvrent le module IA (`tests/test_ai.py`) :

- **TestAIMessage** (2 tests) : Creation de messages
- **TestAIResponse** (2 tests) : Creation de reponses
- **TestOpenAIProvider** (4 tests) : Provider OpenAI
- **TestAnthropicProvider** (4 tests) : Provider Anthropic
- **TestAIAssistant** (8 tests) : Assistant principal
- **TestAIRoutes** (4 tests) : Routes API

---

## Statut

✅ **Termine** — 20 tests passent.
