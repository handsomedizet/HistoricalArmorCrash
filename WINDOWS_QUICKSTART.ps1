param(
    [ValidateSet("doctor", "build", "run", "analyze", "all")]
    [string]$Command = "doctor",
    [string]$Config = "study.toml",
    [string]$StudyDir = "runs",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $Config)) {
    Copy-Item "study.example.toml" $Config
    Write-Host "Created $Config. Set LS_DYNA_EXECUTABLE in .env, then run this command again."
    exit 1
}

$arguments = @("pipeline.py", $Command, "--config", $Config, "--study-dir", $StudyDir)
if ($DryRun) {
    $arguments += "--dry-run"
}

python @arguments
exit $LASTEXITCODE

