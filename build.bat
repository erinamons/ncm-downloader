@echo off
chcp 65001 >nul
echo ========================================
echo  网易云音乐下载器 - 打包（文件夹版）
echo ========================================
echo.

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [错误] 未安装 PyInstaller，请先运行: pip install pyinstaller
    pause
    exit /b 1
)

echo [1/4] 定位 ffmpeg...
for /f "delims=" %%i in ('python -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"') do set FFMPEG=%%i
if not exist "%FFMPEG%" (
    echo [错误] 找不到 ffmpeg，请先运行: pip install imageio-ffmpeg
    pause
    exit /b 1
)
echo     %FFMPEG%

echo [2/4] 打包主程序（文件夹模式）...
python -m PyInstaller --noconfirm --onedir --windowed ^
  --name "网易云下载器" ^
  --icon "gui/assets/app.ico" ^
  --add-data "gui/assets;gui/assets" ^
  --hidden-import PyQt5.QtMultimedia ^
  --hidden-import PyQt5.QtMultimediaWidgets ^
  --distpath pack ^
  --workpath pack_build ^
  run_gui.py

if errorlevel 1 (
    echo [错误] 打包失败
    pause
    exit /b 1
)

echo [3/4] 摆放 ffmpeg 与说明...
if not exist "pack\网易云下载器\ffmpeg\binaries" mkdir "pack\网易云下载器\ffmpeg\binaries"
copy /y "%FFMPEG%" "pack\网易云下载器\ffmpeg\binaries\" >nul
if errorlevel 1 (
    echo [错误] 复制 ffmpeg 失败
    pause
    exit /b 1
)
if exist "docs\使用说明.txt" copy /y "docs\使用说明.txt" "pack\网易云下载器\" >nul

echo [4/4] 完成
echo.
echo 输出目录: pack\网易云下载器\
echo   网易云下载器.exe
echo   ffmpeg\binaries\      （转码用，可删）
echo   _internal\            （依赖库，勿动）
echo   使用说明.txt
echo.
echo 分发时整个文件夹压成 zip 即可；换新版直接覆盖文件夹，用户数据不受影响。
pause
