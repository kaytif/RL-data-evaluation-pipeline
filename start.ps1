param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8502
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
# Respect DATABASE_URL from the shell or the ignored local .env file.
& "$PSScriptRoot\.venv\Scripts\python.exe" "$PSScriptRoot\scripts\start_local.py" --port $Port
