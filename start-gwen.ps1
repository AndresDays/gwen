$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pidPath = Join-Path $projectRoot ".gwen.pid"
$stdoutPath = Join-Path $projectRoot "gwen.out.log"
$stderrPath = Join-Path $projectRoot "gwen.err.log"

if (-not (Test-Path -LiteralPath $pythonPath)) {
    throw "No se encontró .venv. Crea el entorno e instala el proyecto primero."
}

if (Test-Path -LiteralPath $pidPath) {
    $savedPid = (Get-Content -Raw -LiteralPath $pidPath).Trim()
    if ($savedPid -match '^\d+$' -and (Get-Process -Id ([int]$savedPid) -ErrorAction SilentlyContinue)) {
        Write-Host "Gwen ya está activa (PID $savedPid)."
        exit 0
    }
    Remove-Item -LiteralPath $pidPath -Force
}

$process = Start-Process `
    -FilePath $pythonPath `
    -ArgumentList "-m", "gwen" `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -WindowStyle Hidden `
    -PassThru

Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ascii
Write-Host "Gwen inició en segundo plano (PID $($process.Id))."
