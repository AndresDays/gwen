$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$launcher = Join-Path $projectRoot "start-gwen-desktop.ps1"
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Gwen.lnk"
$powershellPath = Join-Path $PSHOME "powershell.exe"
$edgeCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} "Microsoft\Edge\Application\msedge.exe"),
    (Join-Path $env:ProgramFiles "Microsoft\Edge\Application\msedge.exe")
)
$edgePath = $edgeCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $powershellPath
$shortcut.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcher`""
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = "Gwen — asistente personal privada"
if ($edgePath) { $shortcut.IconLocation = "$edgePath,0" }
$shortcut.Save()
Write-Host "Gwen instalada en el escritorio."
