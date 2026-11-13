# Eklenti kaynagini ve filament verisini repoya kopyalar, degisiklik varsa
# commit + push eder. Zamanlanmis gorev 10 dakikada bir calistirir;
# degisiklik yoksa hemen cikar.
$ErrorActionPreference = 'Stop'
$repo    = Split-Path -Parent $MyInvocation.MyCommand.Path
$source  = Join-Path $env:APPDATA 'OrcaSlicer\orca_plugins\filament_takibi\filament_takibi.py'
$dataDir = Join-Path $env:APPDATA 'OrcaSlicer\orca_plugins\plugin_data\filament_takibi'

if (-not (Test-Path $source)) { exit 1 }

$target = Join-Path $repo 'orca-plugin\filament_takibi.py'
New-Item -ItemType Directory -Force (Split-Path $target) | Out-Null
Copy-Item $source $target -Force

# Veri dosyalari. settings.json token icerdigi icin token'siz kopyalanir.
$dataRepo = Join-Path $repo 'orca-plugin\veri'
New-Item -ItemType Directory -Force $dataRepo | Out-Null
foreach ($name in 'spools.json', 'pending.json') {
    $path = Join-Path $dataDir $name
    if (Test-Path $path) { Copy-Item $path (Join-Path $dataRepo $name) -Force }
}
$settingsPath = Join-Path $dataDir 'settings.json'
if (Test-Path $settingsPath) {
    $settings = Get-Content $settingsPath -Raw | ConvertFrom-Json
    $settings.github.token = ''
    $settings | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $dataRepo 'settings.json') -Encoding UTF8
}

Set-Location $repo
# Yarıda kalmış rebase sonraki yedekleri sessizce engellemesin.
if (Test-Path (Join-Path $repo '.git\rebase-merge')) { git rebase --abort }

git add -A
git diff --cached --quiet
if ($LASTEXITCODE -eq 0) { exit 0 }

git commit -q -m ("Eklenti yedegi " + (Get-Date -Format 'yyyy-MM-dd HH:mm'))
# plugin data.json'i GitHub API ile ayri commit'liyor: uzak dal one gecebilir.
git pull --rebase --autostash -q
git push -q
