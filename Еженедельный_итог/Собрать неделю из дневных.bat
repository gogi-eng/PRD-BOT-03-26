@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=C:\Users\v.dubovik\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python
echo.
echo === Неделя из уже правленных дневных отчётов ===
echo Беру файлы из «принятые» (если есть) или из «на_правку».
echo Повторно НЕ оформляю и спеллер не запускаю.
echo.
"%PY%" "%~dp0weekly_report.py" --from-daily %*
echo.
pause
