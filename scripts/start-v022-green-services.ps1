param(
    [double]$PollSeconds = 1.0,
    [int]$HealthTimeoutSeconds = 120,
    [switch]$SkipFrontendBuild,
    [switch]$EnableProductWorker
)

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$starter = Join-Path $PSScriptRoot "start-v022-services.ps1"
$greenDatabaseUrl = "postgresql+psycopg://style_rotation:style_rotation@127.0.0.1:55433/style_rotation_green"
$greenPayloadDirectory = if ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA "MigratoryBird\green_v022_payloads"
} else {
    throw "LOCALAPPDATA is required for the isolated Green payload directory"
}

$container = & docker ps `
    --filter "name=^/migratorybird-green-postgres-green-1$" `
    --filter "health=healthy" `
    --format "{{.Names}}"
if ($LASTEXITCODE -ne 0 -or $container -ne "migratorybird-green-postgres-green-1") {
    throw "The isolated Green PostgreSQL container is not healthy"
}

New-Item -ItemType Directory -Path $greenPayloadDirectory -Force | Out-Null
$resolvedPayload = (Resolve-Path -LiteralPath $greenPayloadDirectory).Path
if ($resolvedPayload -like "$workspace*") {
    throw "The Green payload directory must remain outside the OneDrive workspace"
}

$env:STYLE_ROTATION_DATABASE_URL = $greenDatabaseUrl
$env:STYLE_ROTATION_V022_PAYLOAD_DIRECTORY = $resolvedPayload
$env:STYLE_ROTATION_ENVIRONMENT = "local"
$env:STYLE_ROTATION_V022_LOCAL_DEVELOPMENT_ENABLED = "true"

$arguments = @{
    PollSeconds = $PollSeconds
    HealthTimeoutSeconds = $HealthTimeoutSeconds
    SkipFrontendBuild = $SkipFrontendBuild
    EnableProductWorker = $EnableProductWorker
}
& $starter @arguments
