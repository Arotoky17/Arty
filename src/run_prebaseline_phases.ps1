# ==============================================================================
# Arty - Script de lancement des Phases 2 à 5 (Préparation Baseline)
# ==============================================================================
# Usage :
#   .\run_prebaseline_phases.ps1
#   .\run_prebaseline_phases.ps1 -SkipCalibration
#   .\run_prebaseline_phases.ps1 -OnlyReview
# ==============================================================================

param(
    [switch]$SkipCalibration,   # Saute la phase 3 (calibration spreads)
    [switch]$OnlyReview,        # Lance uniquement la phase 4 (revue visuelle)
    [switch]$SkipAudits         # Saute les audits de la phase 2
)

# --- Configuration ---
$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
if (-not $ProjectRoot) { $ProjectRoot = Get-Location }

Set-Location -LiteralPath $ProjectRoot

# Vérification de l'environnement virtuel
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "Environnement virtuel introuvable. Lance d'abord : python -m venv .venv"
    exit 1
}

# Création des dossiers de sortie
$ReportsDirs = @(
    "reports\data_readiness\raw_audit",
    "reports\data_readiness\spreads_full_dev",
    "reports\data_readiness\spreads_2026",
    "reports\prebaseline\data_audit",
    "reports\prebaseline\detection_review",
    "reports\prebaseline\detection_regression",
    "reports\prebaseline\holdout_market_audit"
)

foreach ($dir in $ReportsDirs) {
    $fullPath = Join-Path $ProjectRoot $dir
    if (-not (Test-Path $fullPath)) {
        New-Item -ItemType Directory -Path $fullPath -Force | Out-Null
    }
}

# Variable pour matplotlib (évite les problèmes de cache)
$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".quality-cache\matplotlib"
if (-not (Test-Path $env:MPLCONFIGDIR)) {
    New-Item -ItemType Directory -Path $env:MPLCONFIGDIR -Force | Out-Null
}

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  $Message" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Invoke-Python {
    param(
        [string]$Script,
        [string[]]$Arguments
    )
    $cmd = @($Script) + $Arguments
    Write-Host "→ $VenvPython $($cmd -join ' ')" -ForegroundColor DarkGray
    & $VenvPython @cmd
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Échec de la commande : $Script"
        exit $LASTEXITCODE
    }
}

# ==============================================================================
# PHASE 2 — Audits des données
# ==============================================================================
if (-not $OnlyReview -and -not $SkipAudits) {
    Write-Step "PHASE 2 — Audits des données"

    # Audit des données brutes
    Invoke-Python "tools/audit_data.py" @(
        "--data", "data/raw",
        "--output", "reports/data_readiness/raw_audit"
    )

    # Audit des régimes mensuels
    Invoke-Python "tools/audit_monthly_regimes.py" @(
        "--data", "data/raw",
        "--allow-holdout-market-audit"
    )
}

# ==============================================================================
# PHASE 3 — Calibration des spreads
# ==============================================================================
if (-not $OnlyReview -and -not $SkipCalibration) {
    Write-Step "PHASE 3 — Calibration des spreads"

    # Calibration complète sur le dev set
    Invoke-Python "tools/calibrate_spreads.py" @(
        "--data", "data/raw",
        "--output", "reports/data_readiness/spreads_full_dev"
    )

    # Calibration pour 2026 (si données 2025 disponibles)
    try {
        Invoke-Python "tools/calibrate_spreads.py" @(
            "--data", "data/raw",
            "--effective-year", "2026",
            "--allow-holdout-quotes",
            "--output", "reports/data_readiness/spreads_2026"
        )
    }
    catch {
        Write-Warning "Calibration 2026 échouée (normal si données 2025 absentes) : $_"
    }
}

# ==============================================================================
# PHASE 4 — Revue visuelle des détections
# ==============================================================================
Write-Step "PHASE 4 — Génération des graphiques de revue visuelle"

Invoke-Python "tools/review_detections.py" @(
    "--output", "reports/prebaseline/detection_review"
)

Write-Host ""
Write-Host "IMPORTANT :" -ForegroundColor Yellow
Write-Host "Ouvre maintenant le fichier suivant et fais la revue manuelle :" -ForegroundColor Yellow
Write-Host "  reports\prebaseline\detection_review\index.md" -ForegroundColor White
Write-Host ""
Write-Host "Objectif : ≥ 16/20 corrects pour Swing, Displacement, Order Blocks et FVG" -ForegroundColor Yellow
Write-Host ""

# ==============================================================================
# PHASE 5 — Contrôles pré-baseline complets
# ==============================================================================
if (-not $OnlyReview) {
    Write-Step "PHASE 5 — Contrôles pré-baseline complets"

    Invoke-Python "tools/audit_data.py" @(
        "--output", "reports/prebaseline/data_audit"
    )

    Invoke-Python "tools/compare_detections.py" @(
        "--output", "reports/prebaseline/detection_regression"
    )

    Invoke-Python "tools/audit_holdout_regimes.py" @(
        "--allow-holdout-market-audit",
        "--output", "reports/prebaseline/holdout_market_audit"
    )

    Invoke-Python "tools/statistical_power.py"
}

# ==============================================================================
# Fin
# ==============================================================================
Write-Step "TERMINÉ"

Write-Host "Prochaines actions manuelles :" -ForegroundColor Green
Write-Host "1. Faire la revue visuelle des graphiques (Phase 4)" -ForegroundColor White
Write-Host "2. Remplir le fichier preregistration.md" -ForegroundColor White
Write-Host "3. Vérifier les rapports dans reports/prebaseline/ et reports/data_readiness/" -ForegroundColor White
Write-Host ""