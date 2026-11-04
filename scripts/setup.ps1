# One-time setup for running without Docker on Windows. Safe to run again.
# Needs: Python 3.11+, Node.js, PostgreSQL with the PostGIS extension installed.
# Usage: powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
param(
    [string]$PgBin = "",
    [int]$PgPort = 5432,
    [string]$PgSuperuser = "postgres"
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
# Windows PowerShell's -Encoding UTF8 adds a BOM, which Java's XML properties loader can choke on
$utf8NoBom = New-Object Text.UTF8Encoding $false

function Read-DotEnv {
    $vars = @{}
    foreach ($line in Get-Content .env) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') { $vars[$Matches[1]] = $Matches[2].Trim() }
    }
    return $vars
}

function Invoke-Native([string]$exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "$exe failed (exit code $LASTEXITCODE)" }
}

if (-not (Test-Path .env)) {
    Copy-Item .env.example .env
    throw "Created .env from .env.example. Fill in the values, then run this script again."
}
$envVars = Read-DotEnv

# --- PostgreSQL: role, databases, PostGIS ---
if (-not $PgBin) {
    $PgBin = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\psql.exe" -ErrorAction SilentlyContinue |
        Sort-Object { [int]$_.Directory.Parent.Name } | Select-Object -Last 1 | ForEach-Object { $_.DirectoryName }
}
if (-not $PgBin) { throw "PostgreSQL not found. Install it, or pass -PgBin 'C:\path\to\PostgreSQL\bin'." }
$psql = Join-Path $PgBin "psql.exe"

& (Join-Path $PgBin "pg_isready.exe") -h localhost -p $PgPort | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "PostgreSQL is not running on port $PgPort. Start it (services.msc -> postgresql-x64-NN -> Start), then run this script again."
}

$user = $envVars["POSTGRES_USER"]; $password = $envVars["POSTGRES_PASSWORD"]; $db = $envVars["POSTGRES_DB"]
if (-not ($user -and $password -and $db)) { throw "Set POSTGRES_USER, POSTGRES_PASSWORD and POSTGRES_DB in .env" }
$sqlPassword = $password.Replace("'", "''")

$superPassword = Read-Host "Password of the PostgreSQL '$PgSuperuser' user (set when PostgreSQL was installed)" -AsSecureString
$env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($superPassword))
$connect = @("-h", "localhost", "-p", "$PgPort", "-U", $PgSuperuser, "-v", "ON_ERROR_STOP=1", "-q")
try {
    @"
SELECT format('CREATE ROLE %I LOGIN', '$user') WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$user')
\gexec
ALTER ROLE "$user" PASSWORD '$sqlPassword';
SELECT format('CREATE DATABASE %I OWNER %I', '$db', '$user') WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '$db')
\gexec
SELECT format('CREATE DATABASE traccar OWNER %I', '$user') WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'traccar')
\gexec
"@ | & $psql @connect -d postgres
    if ($LASTEXITCODE -ne 0) { throw "Creating the database role/databases failed" }

    & $psql @connect -d $db -c "CREATE EXTENSION IF NOT EXISTS postgis"
    if ($LASTEXITCODE -ne 0) {
        throw "PostGIS is not installed. Open Stack Builder (Start menu -> PostgreSQL -> Application Stack Builder), choose Spatial Extensions -> PostGIS bundle, install it, then run this script again."
    }
} finally {
    Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
}
Write-Host "Database ready: $db and traccar on localhost:$PgPort" -ForegroundColor Green

# --- .env: settings docker compose would otherwise provide ---
$additions = @()
if (-not $envVars.ContainsKey("DATABASE_URL")) {
    $encoded = [Uri]::EscapeDataString($password)
    $additions += "DATABASE_URL=postgresql+asyncpg://${user}:${encoded}@localhost:${PgPort}/${db}"
}
if (-not $envVars.ContainsKey("TRACCAR_URL")) { $additions += "TRACCAR_URL=http://localhost:8082" }
if ($additions) {
    $lines = @("", "# Running without Docker (added by scripts\setup.ps1)") + $additions
    [IO.File]::AppendAllText((Join-Path $root ".env"), ($lines -join "`r`n") + "`r`n", $utf8NoBom)
    Write-Host "Added to .env: $($additions | ForEach-Object { $_.Split('=')[0] })"
}

# --- Traccar config (copy into the Traccar install folder) ---
$traccarXml = @"
<?xml version='1.0' encoding='UTF-8'?>
<!DOCTYPE properties SYSTEM 'http://java.sun.com/dtd/properties.dtd'>
<properties>
    <entry key='database.driver'>org.postgresql.Driver</entry>
    <entry key='database.url'>jdbc:postgresql://localhost:$PgPort/traccar</entry>
    <entry key='database.user'>$([Security.SecurityElement]::Escape($user))</entry>
    <entry key='database.password'>$([Security.SecurityElement]::Escape($password))</entry>
    <!-- Push every position to the API as JSON -->
    <entry key='forward.enable'>true</entry>
    <entry key='forward.type'>json</entry>
    <entry key='forward.url'>$([Security.SecurityElement]::Escape("http://localhost:8100/traccar/positions?token=$($envVars['TRACCAR_FORWARD_TOKEN'])"))</entry>
</properties>
"@
[IO.File]::WriteAllText((Join-Path $root "traccar.xml"), $traccarXml, $utf8NoBom)
Write-Host "Wrote traccar.xml (copy it over C:\Program Files\Traccar\conf\traccar.xml)"

# --- Python ---
if (-not (Test-Path .venv)) { Invoke-Native "python" @("-m", "venv", ".venv") }
Invoke-Native ".venv\Scripts\python.exe" @("-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", "requirements.txt")
Invoke-Native ".venv\Scripts\python.exe" @("-m", "alembic", "upgrade", "head")
Write-Host "Python packages installed, database migrated" -ForegroundColor Green

# --- Web app ---
Push-Location web
try {
    Invoke-Native "npm.cmd" @("ci")
    Invoke-Native "npm.cmd" @("run", "build")
} finally {
    Pop-Location
}
Write-Host "Web app built" -ForegroundColor Green
Write-Host ""
Write-Host "Setup done. Next: start Traccar (see README), then run scripts\start.ps1"
