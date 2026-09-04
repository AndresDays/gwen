$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$webStarter = Join-Path $projectRoot "start-gwen-web.ps1"
$edgeCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"),
    (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe")
)
$edgePath = $edgeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $edgePath) {
    throw "Microsoft Edge no está instalado."
}

& $webStarter
$ready = $false
foreach ($attempt in 1..20) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8765/login" -TimeoutSec 1
        if ($response.StatusCode -eq 200) {
            $ready = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 250
    }
}
if (-not $ready) {
    throw "Gwen no pudo iniciar su interfaz local."
}

Start-Process -FilePath $edgePath -ArgumentList "--app=http://127.0.0.1:8765", "--start-maximized"
