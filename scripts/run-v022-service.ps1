param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("api", "suite-worker", "asset-export-worker", "product-worker", "gc-worker")]
    [string]$Service,
    [double]$PollSeconds = 1.0
)

$ErrorActionPreference = "Stop"
$env:PYTHONHOME = $null
$env:STYLE_ROTATION_ENVIRONMENT = "local"
$env:STYLE_ROTATION_V022_LOCAL_DEVELOPMENT_ENABLED = "true"
$workspace = Split-Path -Parent $PSScriptRoot
$runtimeDirectory = if ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA "style-rotation\v022-services"
} else {
    Join-Path $workspace ".codex_work\v022-services"
}
$env:STYLE_ROTATION_V022_WORKER_HEARTBEAT_PATH = Join-Path $runtimeDirectory "suite-worker.json"
$stdoutLog = Join-Path $runtimeDirectory ("{0}.stdout.log" -f $Service)
$stderrLog = Join-Path $runtimeDirectory ("{0}.stderr.log" -f $Service)
$exitRecord = Join-Path $runtimeDirectory ("{0}.exit.json" -f $Service)

if ($PollSeconds -le 0) { throw "PollSeconds must be positive" }

$pythonCandidates = @(
    @(
        $env:STYLE_ROTATION_PYTHON,
        (Join-Path $env:USERPROFILE ".cache\style-rotation\venv\Scripts\python.exe"),
        (Join-Path $workspace ".venv\Scripts\python.exe")
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
)
if ($pythonCandidates.Count -eq 0) { throw "No v0.22 Python runtime was found" }
$python = $pythonCandidates[0]

New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
$arguments = switch ($Service) {
    "api" {
        @("-m", "uvicorn", "style_rotation.api.app:app", "--host", "127.0.0.1", "--port", "8000")
    }
    "suite-worker" {
        @(
        "-m", "style_rotation.cli.v022_suite_worker",
        "--worker-id", "v022-suite-worker-local",
        "--forever", "--poll-seconds", [string]$PollSeconds
        )
    }
    "asset-export-worker" {
        @(
        "-m", "style_rotation.cli.v022_asset_data_export_worker",
        "--worker-id", "v022-asset-data-export-worker-local",
        "--forever", "--poll-seconds", [string]$PollSeconds
        )
    }
    "product-worker" {
        @(
        "-m", "style_rotation.cli.v022_product_worker",
        "--worker-id", "v022-product-worker-local",
        "--forever", "--poll-seconds", [string]$PollSeconds
        )
    }
    "gc-worker" {
        @(
        "-m", "style_rotation.cli.v022_research_round_gc",
        "--forever", "--poll-seconds", [string][Math]::Max($PollSeconds, 5.0)
        )
    }
}

$startedAt = (Get-Date).ToUniversalTime().ToString("o")
try {
    $process = Start-Process `
        -FilePath $python `
        -ArgumentList $arguments `
        -WorkingDirectory $workspace `
        -NoNewWindow `
        -PassThru `
        -Wait `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog
    $exitCode = $process.ExitCode
} catch {
    $_ | Out-String | Add-Content -LiteralPath $stderrLog -Encoding UTF8
    $exitCode = 1
}

[PSCustomObject]@{
    service = $Service
    started_at = $startedAt
    exited_at = (Get-Date).ToUniversalTime().ToString("o")
    exit_code = $exitCode
} | ConvertTo-Json | Set-Content -LiteralPath $exitRecord -Encoding UTF8
exit $exitCode
