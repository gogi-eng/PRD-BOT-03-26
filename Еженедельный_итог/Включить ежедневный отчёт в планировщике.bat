@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo Включаю ДВЕ задачи Windows (время компьютера = Москва):
echo   16:30 — отчёт за сегодня на правку
echo   08:30 — если вчерашнего файла нет (ПК был выключен в 16:30), отчёт за ВЧЕРА
echo Если файл за вчера уже есть — утром ничего не придёт (без повторного Word).
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_daily_task.ps1"
echo.
pause
