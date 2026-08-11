param(
    [double]$PollSeconds = 1.0,
    [int]$HealthTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
& "$PSScriptRoot\start-v021-services.ps1" `
    -PollSeconds $PollSeconds `
    -HealthTimeoutSeconds $HealthTimeoutSeconds
