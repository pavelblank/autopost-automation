@echo off
REM AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
title AutoPost Automation health check
cd /d "%~dp0"
venv\Scripts\python.exe scripts\doctor.py
pause
