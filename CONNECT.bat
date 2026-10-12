@echo off
REM AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
title Connect your accounts
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo Run INSTALL.bat first.
  pause
  exit /b 1
)
venv\Scripts\python.exe scripts\connect.py %*
pause
