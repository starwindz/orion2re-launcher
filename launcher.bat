@echo off
setlocal
cd /d "%~dp0"
title Orion2Re Launcher

:: Detect available Python command
where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set PY_CMD=py -3
    set PYW_CMD=pyw -3
) else (
    set PY_CMD=python
    set PYW_CMD=pythonw
)

:: Check if customtkinter is installed; install if missing
%PY_CMD% -c "import customtkinter" >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [Orion2Re Launcher] Installing required dependency: customtkinter...
    %PY_CMD% -m pip install -r requirements.txt
)

:: Launch without console window
start "" %PYW_CMD% main.py
exit /b
