# Phase 11 — Notifications

## Objectif

Systeme de notifications multi-canaux : Telegram, Discord et Email.

---

## Architecture

```
src/arty_trading/infrastructure/notifications/
├── __init__.py      # Exports publics
├── base.py          # Interface BaseNotifier (ABC)
├── telegram.py      # Notificateur Telegram
├── discord.py       # Notificateur Discord
├── email.py         # Notificateur Email (SMTP)
└── manager.py       # Gestionnaire centralise
```

---

## Canaux supportes

### Telegram
- API : Telegram Bot API
- Configuration : `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
- Fonctions : Messages formates avec emojis

### Discord
- API : Discord Webhook
- Configuration : `DISCORD_WEBHOOK_URL`
- Fonctions : Embeds colores selon le niveau

### Email
- API : SMTP
- Configuration : `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `NOTIFICATION_EMAIL`
- Fonctions : Emails texte UTF-8

---

## Configuration (.env)

```env
# Telegram
TELEGRAM_BOT_TOKEN=123456:ABC-DEF
TELEGRAM_CHAT_ID=0348896324

# Discord
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Email
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=arty@gmail.com
SMTP_PASSWORD=app_password
NOTIFICATION_EMAIL=trader@gmail.com
```

---

## Types de notifications

1. **Signaux** - Notification quand un signal est genere
2. **Trades** - Notification quand un trade est ouvert/ferme
3. **Alertes de risque** - Notification quand une alerte de risque est declenchee
4. **Personnalisee** - Notification libre via l'API

---

## Endpoints API

| Methode | Route | Description |
|---------|-------|-------------|
| GET | `/notifications/status` | Statut des notificateurs |
| POST | `/notifications/send` | Envoyer une notification |
| POST | `/notifications/signal` | Envoyer une notification de signal |
| POST | `/notifications/test` | Tester tous les canaux |

---

## Tests

20 tests couvrent le module (`tests/test_notifications.py`).

---

## Statut

✅ **Termine**
