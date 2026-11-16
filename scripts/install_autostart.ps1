# Setup autostart shortcut in current user Startup folder
$startupFolder = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startupFolder "TelegramCompanionAI.lnk"
$projectDir = "C:\Users\ildar\Documents\GitHub\-"
$vbsPath = Join-Path $projectDir "run_hidden.vbs"

$wsh = New-Object -ComObject WScript.Shell
$sc = $wsh.CreateShortcut($shortcutPath)
$sc.TargetPath = "wscript.exe"
$sc.Arguments = "`"$vbsPath`""
$sc.WorkingDirectory = $projectDir
$sc.Description = "Telegram Companion AI 24/7 Autostart"
$sc.Save()

Write-Host "Created shortcut: $shortcutPath"
Write-Host "Points to: $vbsPath"
