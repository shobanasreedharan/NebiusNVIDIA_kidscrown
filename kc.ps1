<#
KidsCrown local helper. Put this file in the repo root and run from there:

  .\kc.ps1 setup      # venv + pip install + npm install + .env check
  .\kc.ps1 seed       # create/refresh demo family (demo@kidscrown.local)
  .\kc.ps1 backend    # FastAPI on http://localhost:8000
  .\kc.ps1 frontend   # Vite on http://localhost:5173
  .\kc.ps1 dev        # backend + frontend, each in its own window
  .\kc.ps1 test       # backend tests + offline evals + deterministic evals
  .\kc.ps1 evals      # eval dry-run (no remote calls)

If scripts are blocked:  Set-ExecutionPolicy -Scope Process Bypass
#>
param(
    [Parameter(Position = 0)]
    [ValidateSet("setup", "seed", "backend", "frontend", "dev", "test", "evals", "help")]
    [string]$Command = "help"
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
Set-Location $Root

function Require-Venv {
    if (-not (Test-Path $Py)) {
        throw "Virtualenv not found. Run: .\kc.ps1 setup"
    }
}

function Check-Env {
    $envFile = Join-Path $Root ".env"
    if (-not (Test-Path $envFile)) {
        Write-Warning ".env not found at repo root. Create it with:"
        Write-Host @"
NEBIUS_API_KEY=...
YOUTUBE_API_KEY=...
TAVILY_API_KEY=...
JWT_SECRET=replace_with_a_32_plus_character_secret
CORS_ORIGINS=http://localhost:5173
"@
        return
    }
    $lines = Get-Content $envFile
    foreach ($key in "NEBIUS_API_KEY", "YOUTUBE_API_KEY", "TAVILY_API_KEY", "JWT_SECRET", "CORS_ORIGINS") {
        if (-not ($lines | Where-Object { $_ -match "^\s*$key\s*=\s*\S+" })) {
            Write-Warning "$key is missing or empty in .env"
        }
    }
    $jwt = $lines | Where-Object { $_ -match "^\s*JWT_SECRET\s*=" } | Select-Object -First 1
    if ($jwt -and (($jwt -split "=", 2)[1].Trim().Length -lt 32)) {
        Write-Warning "JWT_SECRET should be 32+ characters"
    }
}

switch ($Command) {
    "setup" {
        if (-not (Test-Path $Py)) { py -3 -m venv .venv }
        & $Py -m pip install --upgrade pip
        & $Py -m pip install -r requirements.txt
        Push-Location (Join-Path $Root "frontend")
        npm install
        Pop-Location
        Check-Env
        Write-Host "Setup complete." -ForegroundColor Green
    }
    "seed" {
        Require-Venv
        & $Py scripts\seed_demo_family.py
        Write-Host "Demo login: demo@kidscrown.local / DemoPass123!"
    }
    "backend" {
        Require-Venv
        Check-Env
        & $Py -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
    }
    "frontend" {
        Push-Location (Join-Path $Root "frontend")
        npm run dev
        Pop-Location
    }
    "dev" {
        Require-Venv
        Check-Env
        Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root'; & '$Py' -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000"
        Start-Process powershell -ArgumentList "-NoExit", "-Command", "Set-Location '$Root\frontend'; npm run dev"
        Write-Host "Backend: http://localhost:8000   Frontend: http://localhost:5173"
    }
    "test" {
        Require-Venv
        & $Py -m pytest tests -q
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $Py -m pytest evals -q
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $Py -m pytest evals/test_deterministic.py -q
        exit $LASTEXITCODE
    }
    "evals" {
        Require-Venv
        & $Py evals/run_evals.py --dry-run
    }
    default {
        Get-Help $MyInvocation.MyCommand.Path -Detailed | Out-Host
    }
}
