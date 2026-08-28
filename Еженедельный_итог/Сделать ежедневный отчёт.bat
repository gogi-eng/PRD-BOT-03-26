@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=C:\Users\v.dubovik\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python
"%PY%" -m pip install python-docx Pillow -q
echo.
echo === Ежедневный отчёт на правку ===
echo Собираю работу за сегодня и сохраняю Word в папку «Ежедневные отчёты\на_правку».
echo.
"%PY%" "%~dp0weekly_report.py" --daily %*
echo.
echo Файл: Рабочий стол \ Ежедневные отчёты \ на_правку
echo Поправьте в Word и сохраните. Недельный итог заберёт его как есть.
echo.
pause
