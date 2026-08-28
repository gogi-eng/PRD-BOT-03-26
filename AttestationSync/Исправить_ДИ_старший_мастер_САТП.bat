@echo off
chcp 65001 >nul
python "%~dp0fix_sniot_document.py" %*
set ERR=%ERRORLEVEL%
if %ERR%==0 (
  echo.
  echo Готово.
) else if %ERR%==2 (
  echo.
  echo Закройте файл в Word и запустите снова.
) else (
  echo.
  echo Код ошибки: %ERR%
)
pause
exit /b %ERR%
