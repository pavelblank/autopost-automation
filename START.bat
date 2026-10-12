@echo off
REM AutoPost Automation 3.0 - created by Mr Blank (github.com/pavelblank/autopost-automation). Keep this notice.
cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo First run the setup steps in INSTALL.md
  pause
  exit /b 1
)
venv\Scripts\python.exe scriptsgent_guard.py --boot
