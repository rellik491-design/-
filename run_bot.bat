@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Telegram Companion AI - Setup & 24/7 Runner

cd /d "%~dp0"
set "PYTHONPATH=src"
set "PYTHONUTF8=1"

echo ======================================================================
echo    TELEGRAM COMPANION AI — АВТОУСТАНОВКА И ЗАПУСК 24/7
echo ======================================================================
echo.

REM ----------------------------------------------------------------------
REM 1. Проверка и подготовка директорий
REM ----------------------------------------------------------------------
if not exist "storage\photos" mkdir "storage\photos"
if not exist "assets\voice_pack" mkdir "assets\voice_pack"

REM ----------------------------------------------------------------------
REM 2. Поиск / автоустановка uv (менеджер Python и зависимостей)
REM ----------------------------------------------------------------------
set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
set "USE_UV=0"

where uv >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    set "USE_UV=1"
) else (
    echo [1/4] Менеджер пакетов uv не найден. Пытаюсь установить автоматически...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%USERPROFILE%\.cargo\bin;%PATH%"
    where uv >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        set "USE_UV=1"
        echo [OK] uv успешно установлен.
    ) else (
        echo [WARN] Не удалось установить uv автоматически. Переход на стандартный Python + pip.
    )
)

REM ----------------------------------------------------------------------
REM 3. Установка Python и всех зависимостей проекта
REM ----------------------------------------------------------------------
echo [2/4] Установка / проверка зависимостей...
if "%USE_UV%"=="1" (
    uv sync --frozen
    if %ERRORLEVEL% NEQ 0 (
        echo [INFO] Пробуем uv sync без --frozen...
        uv sync
        if %ERRORLEVEL% NEQ 0 (
            echo [ERROR] Ошибка установки зависимостей через uv!
            pause
            exit /b 1
        )
    )
    set "RUN_CMD=uv run python main.py"
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Python не найден в системе! Установите Python 3.14+ или uv: https://docs.astral.sh/uv/
        pause
        exit /b 1
    )
    if not exist ".venv\Scripts\python.exe" (
        echo [INFO] Создаю виртуальное окружение .venv...
        python -m venv .venv
        if %ERRORLEVEL% NEQ 0 (
            echo [ERROR] Не удалось создать .venv!
            pause
            exit /b 1
        )
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
    ".venv\Scripts\pip.exe" install -r requirements.txt
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Ошибка установки зависимостей из requirements.txt!
        pause
        exit /b 1
    )
    set "RUN_CMD=.venv\Scripts\python.exe main.py"
)
echo [OK] Все зависимости готовы.
echo.

REM ----------------------------------------------------------------------
REM 4. Проверка конфигурации .env
REM ----------------------------------------------------------------------
echo [3/4] Проверка файла конфигурации .env...
if not exist ".env" (
    if exist ".env.example" (
        copy /Y ".env.example" ".env" >nul
        echo.
        echo ==================================================================
        echo  СОЗДАН ФАЙЛ .env ИЗ ШАБЛОНА .env.example!
        echo  Заполните обязательные поля:
        echo    1. ADMIN_BOT_TOKEN     - токен бота из @BotFather
        echo    2. ADMIN_TELEGRAM_ID   - ваш числовой Telegram ID
        echo    3. GEMINI_API_KEY      - ключ Google Gemini API
        echo       (или GEMINI_API_KEYS=ключ1,ключ2,ключ3 для авто-ротации)
        echo.
        echo  Сейчас откроется Блокнот. Впишите ключи, сохраните (Ctrl+S),
        echo  закройте Блокнот и нажмите любую клавишу для запуска бота.
        echo ==================================================================
        start "" notepad.exe ".env"
        pause
    ) else (
        echo [ERROR] Не найден ни .env, ни .env.example!
        pause
        exit /b 1
    )
)

findstr /C:"123456789:ABCDEF_your_bot_token_here" ".env" >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    echo.
    echo [WARN] В файле .env всё ещё указан шаблонный ADMIN_BOT_TOKEN!
    echo Откройте .env, впишите реальный токен бота и сохраните файл.
    start "" notepad.exe ".env"
    pause
)

REM ----------------------------------------------------------------------
REM 5. Запуск бота в режиме 24/7 с автоперезапуском при сбоях
REM ----------------------------------------------------------------------
echo [4/4] Запуск сервера бота (для остановки нажмите Ctrl + C)...
echo.

:restart_loop
echo [%DATE% %TIME%] Запуск Telegram Companion AI...
%RUN_CMD%
set "EXITCODE=%ERRORLEVEL%"

echo.
echo [%DATE% %TIME%] Процесс завершился с кодом: %EXITCODE%
if "%EXITCODE%"=="0" (
    echo [%DATE% %TIME%] Штатное завершение работы.
    goto finish
)

echo [%DATE% %TIME%] Автоперезапуск через 3 секунды...
timeout /t 3 /nobreak >nul
echo.
goto restart_loop

:finish
echo.
echo Сервер бота остановлен.
pause
