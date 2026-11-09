param([string]$RepositoryName = 'rl-dataset-review')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw 'GitHub CLI (gh) is required. Install it from https://cli.github.com/ and run this script again.'
}
# Trust only this explicitly selected project for this command, without global Git changes.
$env:GIT_CONFIG_COUNT = '1'
$env:GIT_CONFIG_KEY_0 = 'safe.directory'
$env:GIT_CONFIG_VALUE_0 = $PSScriptRoot
& gh auth status
if ($LASTEXITCODE -ne 0) {
    & gh auth login --hostname github.com --git-protocol https --web
    if ($LASTEXITCODE -ne 0) { throw 'GitHub sign-in was not completed. No repository was published.' }
}
$remotes = & git remote
if ($remotes -contains 'origin') {
    $existingRemote = & git remote get-url origin
    throw "An origin remote already exists: $existingRemote. No changes made; use git push to publish further commits."
}
& gh auth setup-git --hostname github.com
if ($LASTEXITCODE -ne 0) { throw 'Could not configure GitHub authentication for Git.' }
& gh repo create $RepositoryName --private --source . --remote origin --push
if ($LASTEXITCODE -ne 0) { throw 'Publishing did not complete. Check the GitHub CLI message above.' }
