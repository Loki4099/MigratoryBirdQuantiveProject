$ErrorActionPreference = "Stop"
$workspace = Split-Path -Parent $PSScriptRoot
$runtimeDirectory = if ($env:LOCALAPPDATA) {
    Join-Path $env:LOCALAPPDATA "style-rotation\v022-services"
} else {
    Join-Path $workspace ".codex_work\v022-services"
}
$stateFile = Join-Path $runtimeDirectory "services.json"

if (-not (Test-Path -LiteralPath $stateFile)) {
    Write-Output "No recorded v0.22 local services."
    exit 0
}

$state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
foreach ($record in @($state.processes)) {
    $process = Get-Process -Id ([int]$record.pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) { continue }
    $expected = if ($record.started_at -is [DateTime]) {
        ([DateTime]$record.started_at).ToUniversalTime()
    } else {
        [DateTimeOffset]::Parse(
            [string]$record.started_at,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::RoundtripKind
        ).UtcDateTime
    }
    if ([Math]::Abs(($process.StartTime.ToUniversalTime() - $expected).TotalSeconds) -gt 30) {
        throw "Refusing to stop PID $($record.pid): recorded start time does not match"
    }
    & taskkill.exe /PID $process.Id /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to stop recorded v0.22 service tree for PID $($process.Id)"
    }
    Write-Output ("Stopped {0} (PID {1})" -f $record.name, $process.Id)
}

Remove-Item -LiteralPath $stateFile
