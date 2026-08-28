@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=C:\Users\v.dubovik\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python
"%PY%" -m pip install python-docx Pillow -q
echo.
echo === Еженедельный итог ===
echo Собираю файлы за текущую неделю и готовлю отчёт Word...
echo.
"%PY%" "%~dp0weekly_report.py" %*
echo.
pause
