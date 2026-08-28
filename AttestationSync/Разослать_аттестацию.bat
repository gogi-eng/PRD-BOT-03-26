@echo off
chcp 65001 >nul
title Рассылка аттестации ПрБ МКТС
cd /d "%~dp0"
set PY=
if exist "%LocalAppData%\Programs\Python\Python311\pythonw.exe" set PY=%LocalAppData%\Programs\Python\Python311\pythonw.exe
if exist "%LocalAppData%\Programs\Python\Python311\python.exe" if "%PY%"=="" set PY=%LocalAppData%\Programs\Python\Python311\python.exe
if exist "C:\Users\v.dubovik\AppData\Local\Programs\Python\Python311\pythonw.exe" set PY=C:\Users\v.dubovik\AppData\Local\Programs\Python\Python311\pythonw.exe
if "%PY%"=="" set PY=pythonw
"%PY%" "%~dp0sync_attestaciya.py"
if errorlevel 1 (
  echo.
  echo Если окно не появилось — запустите sync_attestaciya.py через Python.
  pause
)
