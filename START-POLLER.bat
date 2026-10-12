@echo off
REM AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
cd /d "%~dp0"
start "" /min venv\Scripts\pythonw.exe scripts\poller_supervisor.py
