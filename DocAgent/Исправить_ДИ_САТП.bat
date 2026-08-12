@echo off
chcp 65001 >nul
title СНиОТ — правила оформления
cd /d "%~dp0"
set PY=C:\Users\v.dubovik\AppData\Local\Programs\Python\Python311\python.exe
if not exist "%PY%" set PY=python
"%PY%" "%~dp0fix_satp_di_runner.py" --gui
if errorlevel 1 pause
