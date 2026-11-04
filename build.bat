@echo off
chcp 65001 >nul
echo ========================================
echo  网易云音乐下载器 - 打包（PyInstaller）
echo ========================================
echo.

python -c "import PyInstaller" 2>nul
if errorlevel 1 (
    echo [错误] 未安装 PyInstaller，请先运行: pip install pyinstaller
    pause
    exit /b 1
)

echo [1/3] 定位内置 ffmpeg...
for /f "delims=" %%i in ('python -c "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"') do set FFMPEG=%%i
if not exist "%FFMPEG%" (
    echo [错误] 找不到 ffmpeg，请先运行: pip install imageio-ffmpeg
    pause
    exit /b 1
)
echo     %FFMPEG%

echo [2/3] 开始打包...
python -m PyInstaller --noconfirm --onefile --windowed ^
  --name "网易云下载器" ^
  --icon "gui/assets/app.ico" ^
  --add-data "gui/assets;gui/assets" ^
  --add-binary "%FFMPEG%;imageio_ffmpeg/binaries" ^
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

echo.
echo [3/3] 完成
for %%I in ("pack\网易云下载器.exe") do echo 输出: pack\网易云下载器.exe  %%~zI 字节
echo.
echo 发布前请实测：启动、内置 ffmpeg、下载并转一次 mp3
pause
