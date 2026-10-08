@echo off
chcp 65001 >nul
rem ============================================================
rem  视频分发工具 VideoDistributor - 一键打包
rem
rem  前置条件：Python 3.9+ 已加入 PATH
rem      pip install -r requirements.txt
rem
rem  产物：dist\VideoDistributor.exe
rem  ffmpeg.exe 故意【不】打包进 exe：
rem    1) 它有好几十 MB，打进去会让 exe 臃肿、启动变慢
rem    2) 它有独立许可证（LGPL/GPL），塞进单文件反而让许可声明含混
rem  打包完请自行把 ffmpeg.exe 放到 exe 旁边。
rem ============================================================

cd /d "%~dp0.."

echo.
echo [1/3] 检查 Python...
python --version >nul 2>&1
if errorlevel 1 (
  echo   找不到 python。请先安装 Python 3.9+ 并勾选 "Add to PATH"。
  pause
  exit /b 1
)

echo [2/3] 检查 tkinterdnd2（拖拽功能）...
python -c "import tkinterdnd2" >nul 2>&1
if errorlevel 1 (
  echo   没装 tkinterdnd2，拖拽功能会不可用（程序仍能跑，只是只能用按钮选文件）。
  echo   要装的话执行： pip install tkinterdnd2
  echo.
)

echo [3/3] 清理旧构建并打包（约 1-2 分钟）...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

python -m PyInstaller --noconfirm VideoDistributor.spec
if errorlevel 1 (
  echo.
  echo   打包失败，看上面的报错信息。
  pause
  exit /b 1
)

echo.
echo ============================================================
echo  完成：dist\VideoDistributor.exe
echo.
echo  接下来：
echo    1) 把 ffmpeg.exe 放到 VideoDistributor.exe 旁边
echo    2) 建一个 音乐库 文件夹，放你有权使用的音乐
echo    3) 建一个 命名库 文件夹，放 TXT（每行一个命名）
echo  然后双击 VideoDistributor.exe 即可。
echo ============================================================
pause