@echo off
setlocal EnableExtensions
cd /d "%~dp0"

py -3.12 --version >nul 2>&1
if errorlevel 1 (
    where winget >nul 2>&1 || goto :no_python
    winget install --exact --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    if errorlevel 1 goto :no_python
    call :refresh_path
)
py -3.12 --version >nul 2>&1 || goto :no_python
py -3.12 -m venv .venv || goto :failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :failed
if not exist .env copy ".env.example" ".env" >nul
if not exist "vozes" mkdir "vozes"
".venv\Scripts\python.exe" -m piper.download_voices --download-dir vozes pt_BR-faber-medium || goto :failed

where ffmpeg >nul 2>&1
if errorlevel 1 (
    where winget >nul 2>&1 || goto :no_ffmpeg
    winget install --exact --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    if errorlevel 1 goto :no_ffmpeg
    call :refresh_path
)
where ffmpeg >nul 2>&1 || goto :no_ffmpeg
where ffprobe >nul 2>&1 || goto :no_ffmpeg
ffmpeg -hide_banner -filters 2>nul | findstr /L /C:" ass " >nul || goto :no_ffmpeg

if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Ollama"
where ollama >nul 2>&1
if errorlevel 1 (
    where winget >nul 2>&1 || goto :no_ollama
    winget install --exact --id Ollama.Ollama --accept-package-agreements --accept-source-agreements
    if errorlevel 1 goto :no_ollama
    call :refresh_path
)
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Ollama"
where ollama >nul 2>&1 || goto :no_ollama
ollama list >nul 2>&1
if errorlevel 1 (
    start "Ollama" /min ollama serve
    powershell -NoProfile -Command "for ($i=0; $i -lt 20; $i++) { try { Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 1 | Out-Null; exit 0 } catch { Start-Sleep -Seconds 1 } }; exit 1"
    if errorlevel 1 goto :no_ollama
)
ollama pull qwen2.5:3b || goto :failed
echo Instalacao concluida. Execute iniciar.bat.
pause
exit /b 0

:refresh_path
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "[Environment]::ExpandEnvironmentVariables(([Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User']))"`) do set "PATH=%%P;%PATH%"
exit /b 0

:no_python
echo Instale Python 3.12 com o Python Launcher e execute o setup novamente.
goto :failed
:no_ffmpeg
echo Instale FFmpeg com libass e FFprobe, adicione os executaveis ao PATH e execute o setup novamente.
goto :failed
:no_ollama
echo Instale e abra o Ollama para Windows e execute o setup novamente.
goto :failed
:failed
echo A instalacao ficou incompleta. Confira a mensagem acima.
pause
exit /b 1
