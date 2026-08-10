param(
    [double]$PollSeconds = 1.0,
    [int]$HealthTimeoutSeconds = 120,
    [switch]$OnlyWorkers
)

$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$runner = Join-Path $PSScriptRoot "run-v021-service.ps1"
$runtimeDirectory = Join-Path $workspace ".codex_work\v021-services"
$stateFile = Join-Path $runtimeDirectory "services.json"
$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source

if ($PollSeconds -le 0) {
    throw "PollSeconds must be positive"
}
if ($HealthTimeoutSeconds -lt 10) {
    throw "HealthTimeoutSeconds must be at least 10"
}
if (-not (Test-Path -LiteralPath $runner)) {
    throw "Service runner was not found: $runner"
}

New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null

function Test-RecordedProcess {
    param([object]$Record)

    if ($null -eq $Record -or $null -eq $Record.pid -or $null -eq $Record.started_at) {
        return $false
    }
    $process = Get-Process -Id ([int]$Record.pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $false
    }
    try {
        $recordedStart = [DateTimeOffset]::Parse([string]$Record.started_at).UtcDateTime
        $actualStart = $process.StartTime.ToUniversalTime()
        return [Math]::Abs(($actualStart - $recordedStart).TotalSeconds) -le 30
    } catch {
        return $false
    }
}

function Start-DetachedService {
    param([string]$Name)

    # A hidden Start-Process wrapper survives the launching PowerShell command
    # while remaining observable by its exact PID. WMI-created processes are
    # reclaimed by the local tool host and must not be used for these services.
    $arguments = @(
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden",
        "-File", ('"{0}"' -f $runner),
        "-Service", $Name,
        "-PollSeconds", [string]$PollSeconds
    )
    $created = Start-Process `
        -FilePath $powershell `
        -ArgumentList $arguments `
        -WorkingDirectory $workspace `
        -WindowStyle Hidden `
        -PassThru
    if ($null -eq $created -or $created.Id -le 0) {
        throw "Failed to create hidden $Name service wrapper"
    }
    return [PSCustomObject]@{
        name = $Name
        pid = [int]$created.Id
        started_at = (Get-Date).ToUniversalTime().ToString("o")
    }
}

$recordByName = @{}
if (Test-Path -LiteralPath $stateFile) {
    try {
        $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
        @($state.processes) | ForEach-Object {
            if (Test-RecordedProcess -Record $_) {
                $recordByName[[string]$_.name] = $_
            }
        }
    } catch {
        # A stale or partially written file is not service truth.
        $recordByName = @{}
    }
}

$serviceNames = if ($OnlyWorkers) {
    @("experiment", "monitoring", "signal-export")
} else {
    @("api", "experiment", "monitoring", "signal-export")
}

foreach ($name in $serviceNames) {
    if (-not $recordByName.ContainsKey($name)) {
        $recordByName[$name] = Start-DetachedService -Name $name
    }
}

$statePayload = [PSCustomObject]@{
    updated_at = (Get-Date).ToUniversalTime().ToString("o")
    launcher = "start_process_hidden"
    processes = @($recordByName.Values | Sort-Object name)
}
$temporaryState = "$stateFile.tmp"
$statePayload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temporaryState -Encoding UTF8
Move-Item -LiteralPath $temporaryState -Destination $stateFile -Force

if (-not $OnlyWorkers) {
    $deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
    $lastError = "API health endpoint did not answer"
    do {
        $apiRecord = $recordByName["api"]
        if (-not (Test-RecordedProcess -Record $apiRecord)) {
            $stderrLog = Join-Path $runtimeDirectory "api.stderr.log"
            $tail = if (Test-Path -LiteralPath $stderrLog) {
                (Get-Content -LiteralPath $stderrLog -Tail 30) -join [Environment]::NewLine
            } else {
                "No API stderr log was produced."
            }
            throw "Detached API exited before becoming healthy.`n$tail"
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing `
                -Uri "http://127.0.0.1:8000/api/v2/health" -TimeoutSec 5
            if ($response.StatusCode -eq 200) {
                $lastError = $null
                break
            }
            $lastError = "API health returned HTTP $($response.StatusCode)"
        } catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)

    if ($null -ne $lastError) {
        throw "Detached API did not become healthy within $HealthTimeoutSeconds seconds: $lastError"
    }
}

$deadServices = @(
    $serviceNames | Where-Object { -not (Test-RecordedProcess -Record $recordByName[$_]) }
)
if ($deadServices.Count -gt 0) {
    throw "Services exited during startup: $($deadServices -join ', ')"
}

Write-Output "v0.21 detached local services are healthy."
$serviceNames | ForEach-Object {
    $record = $recordByName[$_]
    Write-Output ("{0}: PID {1}" -f $_, $record.pid)
}
