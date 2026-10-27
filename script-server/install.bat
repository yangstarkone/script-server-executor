@echo off
rem ==========================================================================
rem  One-click installer for the script-server executor (Windows).
rem  Run this file inside the package folder; it re-launches itself elevated.
rem  It registers the scheduled task + watchdog and starts the service.
rem  Prerequisite: Python 3.10+ in PATH, and .env prepared (copy .env.example).
rem
rem  usage: install.bat            interactive (pauses at the end)
rem         install.bat -nopause   for scripts / automation
rem  Keep this file ASCII-only.
rem ==========================================================================
setlocal EnableExtensions
cd /d "%~dp0"
title Script-Server installer
set "NOPAUSE="
if /i "%~1"=="-nopause" set "NOPAUSE=1"

net session >nul 2>&1
if errorlevel 1 (
    echo [INFO] requesting administrator privileges ...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -ArgumentList '-nopause'"
    exit /b
)

if not exist "%~dp0.env" (
    echo [ERROR] .env not found in %~dp0
    echo         Copy .env.example to .env and fill WF_SCRIPT_TOKEN / WF_SCRIPTS_DIR first.
    if not defined NOPAUSE pause
    exit /b 1
)

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH. Install Python 3.10+ and tick "Add to PATH".
    if not defined NOPAUSE pause
    exit /b 1
)

echo [1/2] registering scheduled task + watchdog and starting ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\cutover-to-uvicorn-task.ps1"
if errorlevel 1 (
    echo [ERROR] install failed - see logs\cutover.log and logs\script-server-error.log
    if not defined NOPAUSE pause
    exit /b 1
)

echo [2/2] status ...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy\ops-http.ps1" status
echo.
echo Done.
if not defined NOPAUSE pause
exit /b 0
