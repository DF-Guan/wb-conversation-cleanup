@echo off
chcp 65001 >nul 2>&1
title WorkBuddy Conversation Cleanup
setlocal

set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=py"
if not exist "%PY%" set "PY=python"

set PYTHONIOENCODING=utf-8

"%PY%" "%~dp0scripts\cleanup.py"

if errorlevel 1 (
  echo.
  echo [Run failed] Python not found or script error.
  echo Please make sure Python is installed.
)

echo.
pause
