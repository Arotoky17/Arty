# Phase 12 — Qualité des Order Blocks (Grade A/B/C/D) + confirmation M5

## Objectif

Passer d'un bot qui trade **tous** les Order Blocks détectés à un bot qui ne
trade que les OB **de haute qualité** (Grade A/B) **confirmés sur M5**, afin de
maximiser les pips par trade sur XAUUSD.

Règle de conception : la fonctionnalité est **inactive par défaut**
(`OB_QUALITY_ENABLED=false`). Avec la configuration par défaut, le comportement
des phases 1 à 11 est strictement identique (aucun test existant ne change).

## 1. Notation d'un Order Block — `modules/smc/ob_quality.py`

Chaque OB reçoit un score **0-100** : somme pondérée et normalisée de sept
composantes. Toutes les distances sont exprimées en **multiples d'ATR** (jamais
en distance fixe), comme le reste du moteur SMC.

| Composante | Mesure | Poids défaut | Extrêmes |
|---|---|---|---|
| `displacement` | corps du déplacement (bougies suivant l'OB) / ATR | 25 | 1.0 à 2.4 ATR → 0.5 → 1.0 |
| `zone_height` | hauteur de la zone / ATR | 10 | ≤ 1 ATR = 1.0 ; 3 ATR = 0.5 ; > 3 ATR = 0 |
| `mitigation` | nombre de retours déjà effectués | 10 | 0 → 1.0 ; 1 → 0.6 ; 2 → 0.2 ; ≥ 3 → 0 |
| `freshness` | âge de la zone / `OB_MAX_ZONE_AGE_BARS` | 10 | linéaire de 1.0 à 0 |
| `trend` | alignement avec la tendance maître H1 | 15 | aligné = 1.0 ; neutre = 0.4 ; opposé = 0 |
| `confluence` | sweep (0.45) + CHoCH/MSS (0.35) + FVG superposé (0.20) | 20 | 0 à 1 |
| `premium_discount` | discount pour BUY / premium pour SELL (ou OTE) | 10 | correct = 1.0 ; sinon 0 (inconnu = 0.5) |

Confluences : seules les détections **de même direction** et **d'index ≤ index
de la zone** (fenêtre `OB_CONFLUENCE_LOOKBACK_BARS`) sont prises en compte —
garantie anti look-ahead, valable en backtest comme en live.

Conversion en grade :

| Grade | Score | Interprétation |
|---|---|---|
| **A** | ≥ `OB_GRADE_A_THRESHOLD` (85) | Setup premium |
| **B** | ≥ `OB_GRADE_B_THRESHOLD` (70) | Setup valide — tradé |
| **C** | ≥ `OB_GRADE_C_THRESHOLD` (50) | Setup faible — ignoré |
| **D** | < 50 | Setup rejeté |

Une détection sans bornes de zone exploitables retourne un Grade D
(`missing_zone_bounds`, score 0) : elle ne peut jamais être tradée.

## 2. Confirmation M5 — `modules/smc/m5_confirmation.py`

Avant tout signal issu d'un OB, la zone doit être **retestée** puis **rejetée**
en clôture M5 :

1. **Retest** — au moins une bougie M5 de la fenêtre
   (`OB_M5_LOOKBACK_BARS`) chevauche `[zone_bottom, zone_top]`. Sinon
   `no_retest`.
2. **Rejet** — la dernière bougie M5 clôturée doit être directionnelle, avec un
   ratio de rejet `(close - bottom) / hauteur` (miroir pour SELL) :
   - ≥ 1.0 → `strong_rejection`
   - ≥ `OB_M5_MIN_REJECTION_RATIO` (0.5) → `rejection`
   - sinon → `weak_rejection` (refusé)
   - bougie de sens opposé → `no_rejection` (refusé)
3. **Displacement** (optionnel, `OB_M5_REQUIRE_DISPLACEMENT`) — le corps de la
   bougie de rejet doit valoir ≥ `OB_M5_DISPLACEMENT_ATR_MULT` × ATR
   (sinon `no_displacement`, refusé).

Seuls `rejection` et `strong_rejection` confirment la zone. La fonction est pure
(aucune I/O) et n'utilise que les bougies fournies.

## 3. Points d'intégration

```
SMC (H4/H1/M5)
   │
   ├─ application/setup_service.py   ← PARTAGÉ live + backtest MTF
   │     1. note chaque OB (Grade A/B/C/D)
   │     2. refuse la création du setup si grade < OB_MIN_GRADE
   │     3. stocke ob_grade / ob_quality_score / ob_quality_components
   │     4. rafraîchit ob_m5_confirmed à chaque cycle (setups OB actifs)
   │
   ├─ modules/signals/generator.py
   │     filtre qualité OB appliqué aux setups prêts (étape 0) :
   │       - grade < OB_MIN_GRADE          → rejet `grade_below_min`
   │       - M5 non confirmé (si requis)   → rejet `m5_confirmation_missing`
   │
   └─ api/main.py + scripts/run_backtest_mtf.py
         injection de `settings.ob_quality` dans le SignalGenerator
```

Métadonnées de setup ajoutées (Phase 12) :

| Clé | Contenu |
|---|---|
| `ob_grade` | Grade A/B/C/D |
| `ob_quality_score` | Score 0-100 |
| `ob_quality_components` | Contribution en points de chaque composante |
| `ob_quality_reasons` | Détails lisibles (displacement, confluences, pénalités) |
| `ob_m5_confirmed` | `True` si la zone est confirmée M5 |
| `ob_m5_confirmation_type` | `strong_rejection`, `rejection`, `weak_rejection`, … |

Les setups **FVG** ne sont pas notés : le filtre ne s'applique qu'aux OB
(`ob_grade` absent → `evaluate_setup_ob_gate` autorise).

## 4. Activation

```env
OB_QUALITY_ENABLED=true
OB_MIN_GRADE=B                 # A = le plus strict, D = le plus permissif
OB_REQUIRE_M5_CONFIRMATION=true
```

Observation : les logs `[OB REJECTED]`, `[SETUP REJECTED]` (qualité OB) et le
résumé `OB QUALITY | <symbole> | ob_notés=… | rejetés(grade<…)` permettent de
mesurer l'impact du filtre.

## 5. Garanties

- Aucune modification de `ALLOW_LIVE_TRADING` (reste `false` par défaut) ni du
  blocage automatique des comptes réels.
- Aucune phase 1-11 modifiée : les nouveaux paramètres sont optionnels et les
  chemins de code sont inertes lorsque la fonctionnalité est désactivée.
- Aucun look-ahead : les confluences et la confirmation M5 n'utilisent que des
  bougies clôturées antérieures ou égales à l'instant courant.
- Tests : `tests/test_ob_quality.py`, `tests/test_m5_confirmation.py`,
  `tests/test_ob_quality_gate.py`.
