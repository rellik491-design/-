<#
.SYNOPSIS
    Регистрация Telegram Companion AI в Планировщике заданий Windows (24/7 автозапуск).
.DESCRIPTION
    Создает задание в Windows Task Scheduler, которое автоматически запускает
    бота в скрытом фоновом режиме при старте системы/входе пользователя
    и перезапускает его при любых сбоях.
#>

$TaskName = "TelegramCompanionAI_24x7"
$ScriptDir = Split-Path -Parent $PSScriptRoot
$VbsPath = Join-Path $ScriptDir "run_hidden.vbs"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Настройка автозапуска Telegram Companion AI 24/7 (Windows)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Путь к проекту: $ScriptDir"
Write-Host "Запускаемый скрипт: $VbsPath"
Write-Host ""

if (-not (Test-Path $VbsPath)) {
    Write-Error "Файл $VbsPath не найден!"
    exit 1
}

# 1. Проверяем, существует ли уже задание
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "Существующее задание '$TaskName' обнаружено. Пересоздаем..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# 2. Создаем действие (запуск скрытого vbs)
$Action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$VbsPath`"" -WorkingDirectory $ScriptDir

# 3. Триггер: при входе любого пользователя в систему
$Trigger = New-ScheduledTaskTrigger -AtLogOn

# 4. Параметры надежности: перезапуск при сбое, работа от батареи, не завершать по таймауту
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0)

# 5. Регистрация задания
try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $Action `
        -Trigger $Trigger `
        -Settings $Settings `
        -Description "Непрерывный запуск Telegram Companion AI (24/7 Watchdog)" | Out-Null

    Write-Host " Задание '$TaskName' успешно зарегистрировано в Планировщике Windows!" -ForegroundColor Green
    Write-Host "Бот будет автоматически запускаться при старте системы и работать в фоне без окон." -ForegroundColor Green
    Write-Host ""
    Write-Host "Чтобы запустить бота прямо сейчас через задачу:" -ForegroundColor Cyan
    Write-Host "  Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor White
    Write-Host ""
    Write-Host "Чтобы удалить автозапуск:" -ForegroundColor Cyan
    Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false" -ForegroundColor White
} catch {
    Write-Error "Ошибка при регистрации задания: $_"
}
