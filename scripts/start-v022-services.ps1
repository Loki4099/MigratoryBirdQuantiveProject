param(
    [double]$PollSeconds = 1.0,
    [int]$HealthTimeoutSeconds = 120,
    [switch]$SkipFrontendBuild,
    [switch]$EnableProductWorker
)

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $PSScriptRoot "run-v022-service.ps1"
$runtimeDirectory = if ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA "style-rotation\v022-services"
} else {
    Join-Path $workspace ".codex_work\v022-services"
}
$stateFile = Join-Path $runtimeDirectory "services.json"
$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source

if ($PollSeconds -le 0) { throw "PollSeconds must be positive" }
if ($HealthTimeoutSeconds -lt 10) { throw "HealthTimeoutSeconds must be at least 10" }

$pythonCandidates = @(
    @(
        $env:STYLE_ROTATION_PYTHON,
        (Join-Path $env:USERPROFILE ".cache\style-rotation\venv\Scripts\python.exe"),
        (Join-Path $workspace ".venv\Scripts\python.exe")
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
)
if ($pythonCandidates.Count -eq 0) { throw "No v0.22 Python runtime was found" }
$python = $pythonCandidates[0]

if (-not $SkipFrontendBuild) {
    $pnpmCandidates = @(
        @(
            $env:STYLE_ROTATION_PNPM,
            (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd")
        ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
    )
    if ($pnpmCandidates.Count -eq 0) { throw "No frontend pnpm runtime was found" }
    & $pnpmCandidates[0] --dir (Join-Path $workspace "frontend") build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
}

& $python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw "Database migration failed" }
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null

function Test-RecordedProcess {
    param([object]$Record)
    if ($null -eq $Record -or $null -eq $Record.pid -or $null -eq $Record.started_at) { return $false }
    $process = Get-Process -Id ([int]$Record.pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $false }
    try {
        $expected = [DateTimeOffset]::Parse([string]$Record.started_at).UtcDateTime
        return [Math]::Abs(($process.StartTime.ToUniversalTime() - $expected).TotalSeconds) -le 30
    } catch { return $false }
}

$records = @{}
if (Test-Path -LiteralPath $stateFile) {
    try {
        $saved = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
        @($saved.processes) | ForEach-Object {
            if (Test-RecordedProcess $_) { $records[[string]$_.name] = $_ }
        }
    } catch { $records = @{} }
}

if (-not $EnableProductWorker -and $records.ContainsKey("product-worker")) {
    throw "A Product worker is already running; stop v0.22 services before starting the frozen build profile"
}

$requiredServices = @("api", "suite-worker", "asset-export-worker", "gc-worker")
if ($EnableProductWorker) { $requiredServices += "product-worker" }

foreach ($name in $requiredServices) {
    if (-not $records.ContainsKey($name)) {
        $arguments = @(
            "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-File", ('"{0}"' -f $runner),
            "-Service", $name, "-PollSeconds", [string]$PollSeconds
        )
        $process = Start-Process -FilePath $powershell -ArgumentList $arguments `
            -WorkingDirectory $workspace -WindowStyle Hidden -PassThru
        $records[$name] = [PSCustomObject]@{
            name = $name
            pid = [int]$process.Id
            started_at = (Get-Date).ToUniversalTime().ToString("o")
        }
    }
}

[PSCustomObject]@{
    updated_at = (Get-Date).ToUniversalTime().ToString("o")
    processes = @($records.Values | Sort-Object name)
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath "$stateFile.tmp" -Encoding UTF8
Move-Item -LiteralPath "$stateFile.tmp" -Destination $stateFile -Force

$deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
do {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v2/health" -TimeoutSec 5
        $readiness = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v2/workspace/graph-suite-runtime/readiness" -TimeoutSec 5
        $releaseControl = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v2/release-control" -TimeoutSec 5
        $servicesAlive = @(
            $requiredServices | Where-Object {
                $records.ContainsKey($_) -and (Test-RecordedProcess $records[$_])
            }
        ).Count -eq $requiredServices.Count
        if (
            $health.quality.state -eq "ok" -and
            $readiness.ready -eq $true -and
            $releaseControl.v022_explicit_creation_allowed -eq $true -and
            $servicesAlive
        ) { break }
    } catch { }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)

if ((Get-Date) -ge $deadline) { throw "A required v0.22 API or worker service did not become ready" }
Write-Output "v0.22 local services are ready at http://127.0.0.1:8000"
