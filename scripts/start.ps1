# Starts the API, the Telegram bot and the web app without Docker, each in its own window.
# Run scripts\setup.ps1 once first. Usage: powershell -ExecutionPolicy Bypass -File scripts\start.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "No .venv found. Run scripts\setup.ps1 first." }
if (-not (Test-Path web\dist\index.html)) { throw "Web app not built. Run scripts\setup.ps1 first." }

# PostgreSQL normally runs as a Windows service. If it isn't up (or the service won't start),
# run it under this account with pg_ctl instead.
$pgService = Get-CimInstance Win32_Service -Filter "Name LIKE 'postgresql%'" | Select-Object -First 1
if ($pgService -and $pgService.PathName -match '^"(.+\\bin)\\pg_ctl\.exe".* -D "([^"]+)"') {
    $pgBin = $Matches[1]; $pgData = $Matches[2]
    & "$pgBin\pg_isready.exe" -h localhost -q
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Starting PostgreSQL..."
        & "$pgBin\pg_ctl.exe" start -D $pgData -l (Join-Path $env:TEMP "postgresql.log") -w -t 60 | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Could not start PostgreSQL. See $env:TEMP\postgresql.log" }
    }
}

# Same as the api container: apply migrations before starting
& $python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw "Migrations failed. Is PostgreSQL running?" }

function Start-Window([string]$title, [string]$command, [string]$dir = $root) {
    Start-Process powershell -WorkingDirectory $dir -ArgumentList @(
        "-NoExit", "-Command", "`$Host.UI.RawUI.WindowTitle = '$title'; $command")
}

Start-Window "dispatch api" "& '$python' -m uvicorn app.main:app --host 127.0.0.1 --port 8100"
Start-Window "dispatch bot" "& '$python' -m app.bot"
Start-Window "dispatch web" "npm.cmd run preview" (Join-Path $root "web")

Write-Host "Started api (http://127.0.0.1:8100), bot and web (http://localhost:8090)."
Write-Host "Close their windows to stop them."
