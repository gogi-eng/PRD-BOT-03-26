@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=C:\Users\v.dubovik\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python
"%PY%" -m pip install -r "%~dp0requirements.txt" -q
start "" "%PY%" "%~dp0start_agent.py" %*
