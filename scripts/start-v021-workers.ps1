param(
    [double]$PollSeconds = 1.0
)

$ErrorActionPreference = "Stop"
& "$PSScriptRoot\start-v021-services.ps1" `
    -PollSeconds $PollSeconds `
    -OnlyWorkers
