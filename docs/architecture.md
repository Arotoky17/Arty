# Architecture Arty Trading Platform

## Vue d'ensemble

La plateforme suit une **Clean Architecture** en couches concentriques, où les dépendances pointent toujours vers l'intérieur (vers le domaine).

```mermaid
graph TB
    subgraph Presentation
        API[FastAPI + WebSocket]
        CLI[CLI]
        Dashboard[Dashboard Web]
    end

    subgraph Application
        UC[Use Cases]
        ORCH[Orchestrators]
    end

    subgraph Domain
        ENT[Entities]
        ENUM[Enums]
        PORT[Interfaces / Ports]
    end

    subgraph Infrastructure
        MT5[MT5 Connector]
        DB[PostgreSQL]
        CACHE[Cache]
        NOTIF[Notifications]
        AI[OpenAI]
    end

    subgraph Modules
        SMC[SMC Engine]
        STRAT[Strategies]
        SIG[Signals]
        RISK[Risk Manager]
        EXEC[Order Executor]
        BT[Backtesting]
    end

    API --> UC
    CLI --> UC
    Dashboard --> API
    UC --> PORT
    ORCH --> PORT
    MT5 -.->|implements| PORT
    DB -.->|implements| PORT
    SMC --> ENT
    STRAT --> PORT
    SIG --> STRAT
    RISK --> PORT
    EXEC --> PORT
    BT --> ENT
```

## Diagramme de composants

```mermaid
C4Component
    title Composants - Phase 1 (Fondations)

    Container_Boundary(platform, "Arty Trading Platform") {
        Component(config, "Configuration", "Pydantic Settings", "Paramètres centralisés")
        Component(logging, "Logging", "Python logging", "Journalisation multi-catégories")
        Component(core, "Core Domain", "Pydantic Models", "Entités et interfaces")
        Component(utils, "Utils", "Python", "Sessions ICT, calculs pips")
        Component(api, "API", "FastAPI", "Endpoints REST")
    }

    Container_Ext(mt5, "MetaTrader 5", "Terminal", "Exécution et données")
    Container_Ext(pg, "PostgreSQL", "Database", "Persistance")
    Container_Ext(openai, "OpenAI", "API", "Assistant IA")

    Rel(api, config, "Lit")
    Rel(api, logging, "Utilise")
    Rel(api, core, "Expose")
```

## Diagramme de classes (Core Domain)

```mermaid
classDiagram
    class Candle {
        +str symbol
        +TimeFrame timeframe
        +datetime time
        +Decimal open
        +Decimal high
        +Decimal low
        +Decimal close
        +int volume
        +Decimal body_size
        +bool is_bullish
    }

    class Signal {
        +UUID id
        +str symbol
        +SignalType signal_type
        +Direction direction
        +Decimal entry_price
        +Decimal stop_loss
        +Decimal take_profit
        +float confidence
        +str strategy_name
        +float risk_reward_ratio
    }

    class Trade {
        +UUID id
        +str symbol
        +Direction direction
        +Decimal volume
        +int ticket
        +bool is_open
    }

    class TradingAccount {
        +int login
        +Decimal balance
        +Decimal equity
        +TradingMode mode
        +bool is_demo
    }

    class IMT5Connector {
        <<interface>>
        +connect() bool
        +disconnect()
        +get_account_info() TradingAccount
    }

    class IStrategy {
        <<interface>>
        +name str
        +analyze(candles, smc_data) Signal
    }

    Signal --> Direction
    Signal --> SignalType
    Trade --> Direction
    Candle --> TimeFrame
```

## Diagramme de séquence - Démarrage API

```mermaid
sequenceDiagram
    participant User
    participant CLI
    participant FastAPI
    participant Settings
    participant Logger

    User->>CLI: arty-trading serve
    CLI->>Settings: get_settings()
    Settings-->>CLI: Settings (demo mode)
    CLI->>FastAPI: uvicorn.run()
    FastAPI->>Settings: get_settings()
    FastAPI->>Logger: setup_logging()
    Logger-->>FastAPI: OK
    FastAPI-->>User: API ready :8000
```

## Diagramme de séquence - Flux de trading (cible)

```mermaid
sequenceDiagram
    participant Bot as Trading Bot
    participant MD as Market Data
    participant SMC as SMC Engine
    participant STRAT as Strategy
    participant SIG as Signal Generator
    participant RISK as Risk Manager
    participant EXEC as Order Executor
    participant MT5 as MetaTrader 5

    Bot->>MD: get_latest_candles()
    MD-->>Bot: candles[]
    Bot->>SMC: detect(candles)
    SMC-->>Bot: smc_concepts[]
    Bot->>STRAT: analyze(candles, smc)
    STRAT-->>Bot: signal
    Bot->>RISK: validate_signal(signal)
    RISK-->>Bot: approved + volume
    Bot->>EXEC: open_order(signal, volume)
    EXEC->>MT5: order_send()
    MT5-->>EXEC: ticket
    EXEC-->>Bot: Trade
```

## Modules et responsabilités

| Module | Package | Responsabilité |
|--------|---------|----------------|
| Configuration | `config/` | Settings Pydantic, .env |
| Core | `core/` | Entités, enums, interfaces |
| Logging | `logging/` | Logs console/fichier |
| Utils | `utils/` | Sessions, pips, helpers |
| Infrastructure | `infrastructure/` | MT5, DB, cache (Phase 2+) |
| Modules | `modules/` | SMC, stratégies, signaux (Phase 4+) |
| Application | `application/` | Use cases (Phase 2+) |
| API | `api/` | FastAPI REST/WebSocket |

## Principes de design

1. **Indépendance des modules** : chaque détecteur SMC, stratégie et service est activable/désactivable
2. **Sécurité par défaut** : mode DEMO, `ALLOW_LIVE_TRADING=false`
3. **Ports & Adapters** : interfaces dans `core/`, implémentations dans `infrastructure/`
4. **Configuration externalisée** : tout paramètre via `.env`
5. **Testabilité** : injection de dépendances via interfaces ABC
