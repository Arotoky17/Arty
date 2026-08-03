#Requires -Version 5.1
<#
.SYNOPSIS
    Lance Arty - Bot de trading Forex (SMC/ICT + IA)
.DESCRIPTION
    Installe les dépendances si nécessaire et démarre l'API FastAPI.
.EXAMPLE
    .\run.ps1
    .\run.ps1 -Info
#>
param(
    [switch]$Info,
    [switch]$InstallOnly,
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

Write-Host ""
Write-Host "  ======================================" -ForegroundColor Cyan
Write-Host "       Arty - Trading Bot Forex        " -ForegroundColor Cyan
Write-Host "  ======================================" -ForegroundColor Cyan
Write-Host ""

# Trouver Python
$Python = $null
$PythonCandidates = @(
    "$ProjectRoot\.venv\Scripts\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:ProgramFiles\Python312\python.exe",
    "$env:ProgramFiles\Python311\python.exe"
)

foreach ($candidate in $PythonCandidates) {
    if (Test-Path $candidate) {
        $Python = $candidate
        break
    }
}

if (-not $Python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notlike "*WindowsApps*") {
        $Python = $cmd.Source
    }
}

if (-not $Python) {
    Write-Host "[ERREUR] Python 3.11+ introuvable." -ForegroundColor Red
    Write-Host ""
    Write-Host "Installez Python depuis https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "Cochez 'Add Python to PATH' lors de l'installation." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Puis relancez: .\run.ps1" -ForegroundColor Yellow
    exit 1
}

Write-Host "[OK] Python: $Python" -ForegroundColor Green

# Créer venv si absent
$VenvPython = "$ProjectRoot\.venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "[...] Creation de l'environnement virtuel..." -ForegroundColor Yellow
    & $Python -m venv "$ProjectRoot\.venv"
}

# Installer dépendances
Write-Host "[...] Installation des dependances..." -ForegroundColor Yellow
& $VenvPython -m pip install -q --upgrade pip
& $VenvPython -m pip install -q -e "$ProjectRoot[dev]"

# Copier .env si absent
if (-not (Test-Path "$ProjectRoot\.env")) {
    Copy-Item "$ProjectRoot\.env.example" "$ProjectRoot\.env"
    Write-Host "[OK] Fichier .env cree depuis .env.example" -ForegroundColor Green
}

# Créer dossiers
New-Item -ItemType Directory -Force -Path "$ProjectRoot\logs" | Out-Null
New-Item -ItemType Directory -Force -Path "$ProjectRoot\data" | Out-Null

if ($InstallOnly) {
    Write-Host "[OK] Installation terminee." -ForegroundColor Green
    exit 0
}

if ($Info) {
    & $VenvPython -m arty_trading.cli info
    exit 0
}

Write-Host ""
Write-Host "[OK] Demarrage d'Arty sur http://localhost:$Port" -ForegroundColor Green
Write-Host "     Health check : http://localhost:$Port/health" -ForegroundColor Gray
Write-Host "     Documentation: http://localhost:$Port/docs" -ForegroundColor Gray
Write-Host "     Ctrl+C pour arreter" -ForegroundColor Gray
Write-Host ""

& $VenvPython -m arty_trading.cli serve --port $Port
