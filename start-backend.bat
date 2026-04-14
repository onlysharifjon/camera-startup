@echo off
title Camera Attendance System
color 0A
echo.
echo  ================================================
echo   Camera Attendance System  v2.0  (FastAPI)
echo  ================================================
echo.
cd /d "%~dp0"

set PYTHON=C:\Users\awday\Desktop\camera-agent\.venv\Scripts\python.exe

echo  Admin panel : http://localhost:8080
echo  API docs    : http://localhost:8080/docs
echo.
%PYTHON% -m uvicorn backend.main:app --host 0.0.0.0 --port 8080 --reload
pause
