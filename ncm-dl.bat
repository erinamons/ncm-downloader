@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem 命令行入口：不启动图形界面，直接搜索 / 下载 / 解析地址。
rem 解释器逐个验证依赖，缺了自动安装——和 run.bat 一个思路，但只要求命令行依赖。
rem 用法：ncm-dl.bat search 关键词 -n 5 --json
rem       ncm-dl.bat dl 26093064 --json
rem       ncm-dl.bat --help

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

echo [提示] 找到 Python 但缺少运行依赖，正在安装（首次需要几分钟）… 1>&2
echo        解释器：!ANY! 1>&2
"!ANY!" -m pip install --disable-pip-version-check -r requirements.txt 1>&2
"!ANY!" -c "import requests, mutagen, typer, rich" >nul 2>nul
if errorlevel 1 goto :fail
set "PY=!ANY!"
goto :run

:try
if defined PY goto :eof
if not exist "%~1" goto :eof
if not defined ANY set "ANY=%~1"
"%~1" -c "import requests, mutagen, typer, rich" >nul 2>nul
if errorlevel 1 goto :eof
set "PY=%~1"
goto :eof

:nopython
echo [错误] 找不到可用的 Python 解释器。 1>&2
exit /b 2

:fail
echo [错误] 依赖安装失败，请手动执行： 1>&2
echo     "!ANY!" -m pip install -r requirements.txt 1>&2
exit /b 2

:run
"!PY!" -m ncm.cli %*
exit /b %errorlevel%
