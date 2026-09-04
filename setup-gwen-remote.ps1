$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$tailscale = Get-Command tailscale -ErrorAction SilentlyContinue
if (-not $tailscale) {
    throw "Tailscale no está instalado. Instálalo en esta PC y en el iPhone, inicia sesión en ambos y vuelve a ejecutar este script."
}
$status = & $tailscale.FullName status --json | ConvertFrom-Json
$dnsName = ([string]$status.Self.DNSName).TrimEnd('.')
if ($status.BackendState -ne "Running" -or -not $dnsName.EndsWith(".ts.net")) {
    throw "Esta PC todavía no está conectada correctamente a Tailscale."
}
$password = Read-Host "Crea la contraseña privada de Gwen (mínimo 12 caracteres)" -AsSecureString
$pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($password)
try { $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
if ($plain.Length -lt 12) { throw "La contraseña debe tener al menos 12 caracteres." }
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$payload = $plain | & $python -c "import secrets,sys; from gwen.security import password_hash; p=sys.stdin.read().rstrip(chr(10)+chr(13)); print(password_hash(p)); print(secrets.token_urlsafe(48))"
$plain = $null
if ($LASTEXITCODE -ne 0 -or $payload.Count -ne 2) { throw "No se pudo crear la configuración segura." }
$config = [ordered]@{
    web_remote_enabled = $true
    web_password_hash = $payload[0]
    web_session_secret = $payload[1]
    web_public_origin = "https://$dnsName"
}
$configPath = Join-Path $projectRoot ".gwen-web-auth.json"
$config | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding utf8
& $tailscale.FullName serve --bg 8765
if ($LASTEXITCODE -ne 0) { throw "Tailscale Serve no pudo activarse." }
& (Join-Path $projectRoot "stop-gwen-web.ps1")
& (Join-Path $projectRoot "start-gwen-web.ps1")
Write-Host "Acceso privado habilitado en https://$dnsName"
Write-Host "Instala Tailscale en el iPhone, entra a la misma cuenta y abre esa URL."