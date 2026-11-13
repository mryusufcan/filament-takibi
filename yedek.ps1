# Eklenti kaynagini repoya kopyalar, degisiklik varsa commit + push eder.
# Zamanlanmis gorev 10 dakikada bir calistirir; degisiklik yoksa hemen cikar.
$ErrorActionPreference = 'Stop'
$repo   = Split-Path -Parent $MyInvocation.MyCommand.Path
$source = Join-Path $env:APPDATA 'OrcaSlicer\orca_plugins\filament_takibi\filament_takibi.py'

if (-not (Test-Path $source)) { exit 1 }

$target = Join-Path $repo 'orca-plugin\filament_takibi.py'
New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
Copy-Item $source $target -Force

Set-Location $repo
git add -A
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) { exit 0 }

git commit -q -m ("Eklenti yedegi " + (Get-Date -Format 'yyyy-MM-dd HH:mm'))
# plugin data.json'i GitHub API ile ayri commit'liyor: uzak dal one gecebilir.
git pull --rebase --autostash -q
git push -q
