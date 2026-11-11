# Выкладка ночи 4 на прод одной командой (PowerShell 5). Запускать из любой папки:
#     powershell -ExecutionPolicy Bypass -File D:\itdeti_learn\itdeti\scripts\deploy_night4.ps1
# Продолжить после остановки с шага N:
#     powershell -ExecutionPolicy Bypass -File D:\itdeti_learn\itdeti\scripts\deploy_night4.ps1 -FromStep N
# Что делает каждый шаг — в начале scripts\deploy_night4.py. Секрет прода читается только из
# D:\itdeti_secrets\prod.env и нигде не печатается.
param([int]$FromStep = 1)

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Write-Host "Не найден $python"; exit 2 }
Push-Location $root
try {
    & $python -B scripts\deploy_night4.py --prod --from-step $FromStep
    $code = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $code
