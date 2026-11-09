@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem 依次找可用的解释器：项目 venv → WorkBuddy 自带 → PATH 里的 python
rem 依赖可能在环境更新后丢失，所以逐个验证能不能 import PyQt5
set "PY="
set "ANY="

if exist ".venv\Scripts\python.exe" call :try "%CD%\.venv\Scripts\python.exe"

for /f "delims=" %%D in ('dir /b /ad /o-d "%USERPROFILE%\.workbuddy\binaries\python\versions" 2^>nul') do (
    call :try "%USERPROFILE%\.workbuddy\binaries\python\versions\%%D\python.exe"
)

for /f "delims=" %%P in ('where python 2^>nul') do (
    echo %%~P | findstr /i "WindowsApps" >nul || call :try "%%P"
)

if defined PY goto :run
if not defined ANY goto :nopython

echo [提示] 找到 Python 但缺少运行依赖，正在安装（首次需要几分钟）…
echo        解释器：!ANY!
"!ANY!" -m pip install --disable-pip-version-check -r requirements.txt
"!ANY!" -c "import PyQt5" >nul 2>nul
if errorlevel 1 goto :fail
set "PY=!ANY!"
goto :run

:try
if defined PY goto :eof
if not exist "%~1" goto :eof
if not defined ANY set "ANY=%~1"
"%~1" -c "import PyQt5" >nul 2>nul
if errorlevel 1 goto :eof
set "PY=%~1"
goto :eof

:nopython
echo [错误] 找不到 Python 解释器。
echo 请安装 Python 3.10 或更高版本，安装时勾选 Add to PATH。
pause
exit /b 1

:fail
echo [错误] 依赖安装失败，请手动执行：
echo     "!ANY!" -m pip install -r requirements.txt
pause
exit /b 1

:run
if "%~1"=="--check" (
    echo [PY]=!PY!
    exit /b 0
)
"!PY!" run_gui.py
if errorlevel 1 pause
