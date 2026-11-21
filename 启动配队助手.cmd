@echo off
cd /d "%~dp0"
python -B -m team_agent
if errorlevel 1 pause
