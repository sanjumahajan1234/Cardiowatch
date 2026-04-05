@echo off
REM CardioWatch All-in-One Launcher (Windows)

echo === CardioWatch Launcher ===
echo Starting Flask backend...

REM Ensure Python dependencies are installed
python -c "import flask, requests" >nul 2>&1
if errorlevel 1 (
    echo Installing Python dependencies...
    pip install flask requests fhir.resources
)

REM Start Flask in background
start /B python app.py

REM Give Flask a moment to start
timeout /t 3 /nobreak >nul

REM Open frontend in default browser
start http://127.0.0.1:5000

echo Backend started in background.
echo Frontend URL: http://127.0.0.1:5000
echo Close this window to stop the backend (or kill the python process).
pause
