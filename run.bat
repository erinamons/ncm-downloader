@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem 依赖都装在 WorkBuddy 自带的解释器里，优先用它
set "PY="
set "ROOT=%USERPROFILE%\.workbuddy\binaries\python\versions"
if exist "!ROOT!" (
    for /f "delims=" %%D in ('dir /b /ad /o-n "!ROOT!" 2^>nul') do (
        if not defined PY if exist "!ROOT!\%%D\python.exe" set "PY=!ROOT!\%%D\python.exe"
    )
)

if not defined PY (
    where python >nul 2>nul && set "PY=python"
)

if not defined PY (
    echo [错误] 找不到 Python 解释器
    echo 请安装 Python 3.10 或更高版本，并在安装时勾选 Add to PATH
    pause
    exit /b 1
)

if "%~1"=="--check" (
    echo [PY]=!PY!
    exit /b 0
)

"!PY!" run_gui.py
if errorlevel 1 pause
