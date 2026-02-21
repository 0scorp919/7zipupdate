@echo off
:: UA: Встановлюємо кодування UTF-8 для коректного відображення кирилиці
chcp 65001 >nul
setlocal

:: ============================================================
:: 7-ZIP EXTRA MANAGER — Портативний лаунчер
:: UA: Цей файл є частиною проекту devops/7zipupdate.
::     Розміщується у devops/7zipupdate/ разом з менеджером.
::     Не потребує хардкодованих шляхів — auto-detect від %~dp0.
::
:: Структура капсули (очікувана):
::   CAPSULE_ROOT\
::     apps\python\current\python\python.exe
::     devops\7zipupdate\7zip_manager.py   ← цей файл поруч
::     tags\                               ← системний PATH (Win+R → 7zip)
::
:: Запуск:
::   Напряму:  devops\7zipupdate\7zip_launcher.bat
::   Або через tags\7zip.bat (системний PATH)
:: ============================================================

:: --- 1. ПЕРЕВІРКА ПРАВ АДМІНІСТРАТОРА ---
:: UA: Перевіряємо, чи є права адміна. Якщо ні — перезапускаємо з UAC.
NET SESSION >nul 2>&1
if %errorLevel% neq 0 (
    echo [INFO] Запит прав адміністратора...
    powershell -Command "Start-Process '%~f0' -Verb RunAs"
    exit /b
)

:: --- 2. AUTO-DETECT CAPSULE ROOT ---
:: UA: %~dp0 = папка де лежить цей .bat файл (devops\7zipupdate\)
::     Два рівні вгору = корінь капсули (CAPSULE_ROOT)
::     Структура: CAPSULE_ROOT\devops\7zipupdate\7zip_launcher.bat
set "LAUNCHER_DIR=%~dp0"
:: UA: Прибираємо trailing backslash
if "%LAUNCHER_DIR:~-1%"=="\" set "LAUNCHER_DIR=%LAUNCHER_DIR:~0,-1%"

:: UA: Піднімаємось два рівні: 7zipupdate\ → devops\ → CAPSULE_ROOT\
for %%A in ("%LAUNCHER_DIR%\..") do set "DEVOPS_DIR=%%~fA"
for %%A in ("%DEVOPS_DIR%\..") do set "CAPSULE_ROOT=%%~fA"

set "PYTHON_EXE=%CAPSULE_ROOT%\apps\python\current\python\python.exe"
set "SCRIPT_FILE=%CAPSULE_ROOT%\devops\7zipupdate\7zip_manager.py"

:: --- 3. ВІЗУАЛІЗАЦІЯ ---
echo ========================================================
echo   7-ZIP EXTRA MAINTENANCE (Admin Mode)
echo   (c) Oleksii Rovnianskyi System
echo   Root: %CAPSULE_ROOT%
echo ========================================================

:: --- 4. ПЕРЕВІРКИ БЕЗПЕКИ ---
if not exist "%PYTHON_EXE%" (
    echo [CRITICAL ERROR] Python not found at:
    echo   %PYTHON_EXE%
    echo.
    echo [HINT] Переконайся що WinPython встановлено у:
    echo   %CAPSULE_ROOT%\apps\python\current\python\
    echo   Запусти: Win+R -^> python
    pause
    exit /b 1
)

if not exist "%SCRIPT_FILE%" (
    echo [CRITICAL ERROR] Script not found at:
    echo   %SCRIPT_FILE%
    pause
    exit /b 1
)

:: --- 5. ЗАПУСК СКРИПТА ---
echo [INFO] Запуск Python скрипта...
"%PYTHON_EXE%" "%SCRIPT_FILE%" %*

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Скрипт завершився з помилкою %ERRORLEVEL%.
    echo Перевір лог-файл у: %CAPSULE_ROOT%\logs\7ziplog\
    pause
) else (
    echo.
    echo [OK] Успішно завершено.
)

endlocal
exit /b 0
