@echo off
chcp 65001 >nul
title Telegram Companion AI - 24/7 Server Runner
echo ======================================================================
echo    👑 TELEGRAM COMPANION AI — СИСТЕМА НЕПРЕРЫВНОЙ РАБОТЫ (24/7)
echo ======================================================================
echo  Этот скрипт непрерывно поддерживает работу бота.
echo  При любых сбоях сети, вылетах или перезагрузках процесс
echo  будет автоматически перезапущен.
echo.
echo  Для ручной остановки нажмите Ctrl + C в этом окне.
echo ======================================================================
echo.

set "PYTHONPATH=src"
cd /d "%~dp0"

:restart_loop
echo [%DATE% %TIME%] 🚀 Запуск ядра Telegram-бота...
uv run python main.py
set EXITCODE=%ERRORLEVEL%

echo.
echo [%DATE% %TIME%] ⚠️ Процесс завершился с кодом возврата: %EXITCODE%
if %EXITCODE% EQU 0 (
    echo [%DATE% %TIME%] 🛑 Штатное завершение работы пользователем.
    goto finish
)

echo [%DATE% %TIME%] 🔄 Автоперезапуск через 3 секунды...
timeout /t 3 /nobreak >nul
echo.
goto restart_loop

:finish
echo.
echo Сервер бота остановлен.
pause
