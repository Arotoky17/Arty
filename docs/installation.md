# Guide d'installation - Arty Trading Platform

## Prérequis

- Python 3.11+
- MetaTrader 5 installé (Windows)
- Docker & Docker Compose (pour PostgreSQL)
- Compte MT5 Démo

## Installation

### 1. Environnement Python

```powershell
cd C:\Arty
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

### 2. Configuration

```powershell
copy .env.example .env
```

Éditez `.env` avec vos identifiants MT5 Démo :

```
MT5_LOGIN=12345678
MT5_PASSWORD=votre_mot_de_passe
MT5_SERVER=MetaQuotes-Demo
```

### 3. Base de données

```powershell
docker compose up -d postgres
```

### 4. Vérification

```powershell
pytest
arty-trading info
arty-trading serve
```

Ouvrez http://localhost:8000/health

## MetaTrader 5

- Le terminal MT5 doit être installé et lancé
- Utilisez un compte **Démo** uniquement en phase de développement
- `ALLOW_LIVE_TRADING=false` par défaut (ne pas modifier sans validation)

## Dépannage

| Problème | Solution |
|----------|----------|
| `ModuleNotFoundError` | `pip install -e ".[dev]"` |
| MT5 connexion échouée | Vérifier terminal ouvert + identifiants |
| PostgreSQL inaccessible | `docker compose ps` puis relancer |
