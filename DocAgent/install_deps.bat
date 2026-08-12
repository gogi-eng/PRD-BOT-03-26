@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=C:\Users\v.dubovik\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python

echo Установка пакетов для Агента Дубовика...
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r "%~dp0requirements.txt"
echo.
echo Готово. Для .doc и .rtf нужен установленный Microsoft Word.
echo Запустите start_agent.bat
pause
