# ==============================================================================
# Arty - Script Phases 2 a 5 (Preparation Baseline)
# ==============================================================================

param(
    [switch]$SkipCalibration,
    [switch]$OnlyReview,
    [switch]$SkipAudits
)

$ErrorActionPreference = "Stop"
$ProjectRoot = if ($PSScriptRoot) { $PSScriptRoot } else { Get-Location }
Set-Location -LiteralPath $ProjectRoot

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "ERREUR : Environnement virtuel introuvable (.venv)" -ForegroundColor Red
    exit 1
}

$dirs = @(
    "reports\data_readiness\raw_audit",
    "reports\data_readiness\spreads_full_dev",
    "reports\data_readiness\spreads_2026",
    "reports\prebaseline\data_audit",
    "reports\prebaseline\detection_review",
    "reports\prebaseline\detection_regression",
    "reports\prebaseline\holdout_market_audit",
    ".quality-cache\matplotlib"
)

foreach ($dir in $dirs) {
    $path = Join-Path $ProjectRoot $dir
    if (-not (Test-Path $path)) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
    }
}

$env:MPLCONFIGDIR = Join-Path $ProjectRoot ".quality-cache\matplotlib"

function Write-Step($msg) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Run-Python($script, $arguments) {
    Write-Host "-> python $script $($arguments -join ' ')" -ForegroundColor DarkGray
    & $VenvPython $script @arguments
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ECHEC : $script" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# PHASE 2
if (-not $OnlyReview -and -not $SkipAudits) {
    Write-Step "PHASE 2 - Audits des donnees"

    Run-Python "tools/audit_data.py" @(
        "--data", "data/raw",
        "--output", "reports/data_readiness/raw_audit"
    )

    Run-Python "tools/audit_monthly_regimes.py" @(
        "--data", "data/raw",
        "--allow-holdout-market-audit"
    )
}

# PHASE 3
if (-not $OnlyReview -and -not $SkipCalibration) {
    Write-Step "PHASE 3 - Calibration des spreads"

    Run-Python "tools/calibrate_spreads.py" @(
        "--data", "data/raw",
        "--output", "reports/data_readiness/spreads_full_dev"
    )

    try {
        Run-Python "tools/calibrate_spreads.py" @(
            "--data", "data/raw",
            "--effective-year", "2026",
            "--allow-holdout-quotes",
            "--output", "reports/data_readiness/spreads_2026"
        )
    }
    catch {
        Write-Host "Calibration 2026 ignoree (donnees 2025 probablement absentes)" -ForegroundColor Yellow
    }
}

# PHASE 4
Write-Step "PHASE 4 - Generation des graphiques de revue"

Run-Python "tools/review_detections.py" @(
    "--output", "reports/prebaseline/detection_review"
)

Write-Host ""
Write-Host "ACTION MANUELLE REQUISE :" -ForegroundColor Yellow
Write-Host "Ouvre ce fichier et fais la revue visuelle :" -ForegroundColor Yellow
Write-Host "  reports\prebaseline\detection_review\index.md" -ForegroundColor White
Write-Host ""
Write-Host "Objectif : >= 16/20 corrects pour Swing / Displacement / OB / FVG" -ForegroundColor Yellow
Write-Host ""

# PHASE 5
if (-not $OnlyReview) {
    Write-Step "PHASE 5 - Controles pre-baseline complets"

    Run-Python "tools/audit_data.py" @(
        "--output", "reports/prebaseline/data_audit"
    )

    Run-Python "tools/compare_detections.py" @(
        "--output", "reports/prebaseline/detection_regression"
    )

    Run-Python "tools/audit_holdout_regimes.py" @(
        "--allow-holdout-market-audit",
        "--output", "reports/prebaseline/holdout_market_audit"
    )

    Run-Python "tools/statistical_power.py"
}

Write-Step "TERMINE AVEC SUCCES"

Write-Host "Prochaines etapes manuelles :" -ForegroundColor Green
Write-Host "1. Faire la revue visuelle des graphiques (Phase 4)" -ForegroundColor White
Write-Host "2. Remplir le fichier preregistration.md" -ForegroundColor White
Write-Host "3. Verifier les rapports dans le dossier reports" -ForegroundColor White
Write-Host ""