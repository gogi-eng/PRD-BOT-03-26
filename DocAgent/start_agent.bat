@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=C:\Users\v.dubovik\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python

REM Убить только старые окна этого агента (DocAgent\start_agent.py), не весь Python на ПК.
REM Крестик окна оформления агента не гасит процесс — иначе снова стартует старая сборка в памяти.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -and ($_.CommandLine -match 'DocAgent\\start_agent\.py') } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
timeout /t 1 /nobreak >nul

"%PY%" -m pip install -r "%~dp0requirements.txt" -q
start "" "%PY%" "%~dp0start_agent.py" %*
