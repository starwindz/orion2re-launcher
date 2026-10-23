@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
title Building Orion2Re Standalone Launcher

echo =======================================================
echo   Orion2Re Launcher - Standalone EXE Build Script
echo =======================================================
echo.

:: Detect Python executable
where py >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set PY_CMD=py -3
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% equ 0 (
        set PY_CMD=python
    ) else (
        echo [ERROR] Python is required to build the executable, but was not found in PATH.
        echo Please install Python 3.10+ to build launcher.exe.
        pause
        exit /b 1
    )
)

echo [1/4] Checking and installing build dependencies...
%PY_CMD% -m pip install --quiet --upgrade pip
%PY_CMD% -m pip install --quiet pyinstaller customtkinter

echo [2/4] Packaging launcher into standalone executable (launcher.exe)...
%PY_CMD% -m PyInstaller --noconsole --onefile --clean --collect-all customtkinter --name launcher main.py
if %ERRORLEVEL% neq 0 (
    echo [ERROR] PyInstaller build failed!
    pause
    exit /b %ERRORLEVEL%
)

echo [3/4] Copying launcher.exe to root directory...
if exist "dist\launcher.exe" (
    copy /y "dist\launcher.exe" ".\launcher.exe" >nul
) else (
    echo [ERROR] dist\launcher.exe not found!
    pause
    exit /b 1
)

echo [4/4] Cleaning up temporary build artifacts...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "launcher.spec" del /f /q "launcher.spec"

echo.
echo =======================================================
echo   [SUCCESS] launcher.exe has been created successfully!
echo   You can now run launcher.exe directly without Python.
echo =======================================================
echo.
pause

