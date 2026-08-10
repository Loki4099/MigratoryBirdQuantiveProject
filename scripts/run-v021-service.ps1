param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("api", "experiment", "monitoring", "signal-export")]
    [string]$Service,
    [double]$PollSeconds = 1.0
)

$ErrorActionPreference = "Stop"
$env:PYTHONHOME = $null
$workspace = Split-Path -Parent $PSScriptRoot
$python = Join-Path $workspace ".venv\Scripts\python.exe"
$runtimeDirectory = Join-Path $workspace ".codex_work\v021-services"
$stdoutLog = Join-Path $runtimeDirectory ("{0}.stdout.log" -f $Service)
$stderrLog = Join-Path $runtimeDirectory ("{0}.stderr.log" -f $Service)
$exitRecord = Join-Path $runtimeDirectory ("{0}.exit.json" -f $Service)

if ($PollSeconds -le 0) {
    throw "PollSeconds must be positive"
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "Workspace Python runtime was not found: $python"
}

New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
Set-Location -LiteralPath $workspace

$commandArguments = switch ($Service) {
    "api" {
        @(
            "-m", "uvicorn", "style_rotation.api.app:app",
            "--host", "127.0.0.1", "--port", "8000"
        )
    }
    "experiment" {
        @(
            "-m", "style_rotation.cli.main", "experiment", "run-v021-worker",
            "--worker-id", "v021-experiment-service", "--forever",
            "--poll-seconds", "$PollSeconds"
        )
    }
    "monitoring" {
        @(
            "-m", "style_rotation.cli.main", "experiment", "run-v021-monitoring-worker",
            "--worker-id", "v021-monitoring-service", "--forever",
            "--poll-seconds", "$PollSeconds"
        )
    }
    "signal-export" {
        @(
            "-m", "style_rotation.cli.main", "experiment", "run-signal-export-worker",
            "--worker-id", "v021-signal-export-service", "--forever",
            "--poll-seconds", "$PollSeconds"
        )
    }
}

# The wrapper itself is a hidden Start-Process instance that outlives the
# launching PowerShell command. Keeping Python attached to this wrapper gives
# us one exact, observable service lifetime and durable stdout/stderr logs.
$startedAt = (Get-Date).ToUniversalTime().ToString("o")
try {
    # Start-Process provides raw, non-blocking log redirection. Direct
    # PowerShell stream redirection would turn Uvicorn's normal stderr logging
    # into a terminating NativeCommandError under ErrorActionPreference=Stop.
    $process = Start-Process `
        -FilePath $python `
        -ArgumentList $commandArguments `
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
