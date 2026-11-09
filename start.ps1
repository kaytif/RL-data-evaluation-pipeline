param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8502
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
# The local demo deliberately ignores leftover PostgreSQL settings.
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
& "$PSScriptRoot\.venv\Scripts\python.exe" -m streamlit run "$PSScriptRoot\app.py" --server.address 127.0.0.1 --server.port $Port --browser.gatherUsageStats false
