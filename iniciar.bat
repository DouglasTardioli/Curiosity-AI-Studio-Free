@echo off
setlocal EnableExtensions
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Execute setup.bat primeiro.
    pause
    exit /b 1
)
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Ollama"
".venv\Scripts\python.exe" -m studio
if errorlevel 1 (
    echo O aplicativo terminou com erro.
    pause
    exit /b 1
)
