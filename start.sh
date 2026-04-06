#!/bin/bash
# CardioWatch All-in-One Launcher
# Starts backend (Flask) and opens frontend in browser

set -e

echo "=== CardioWatch Launcher ==="
echo "Starting Flask backend..."

# Ensure Python dependencies are installed
if ! python -c "import flask, requests" 2>/dev/null; then
    echo "Installing Python dependencies..."
    pip install flask requests fhir.resources
fi

# Start Flask in background
export FLASK_ENV=development
export FLASK_DEBUG=1
python app.py &
FLASK_PID=$!

# Give Flask a moment to start
sleep 3

# Open frontend in default browser
case "$(uname -s)" in
    Linux*)     xdg-open http://127.0.0.1:5000 ;;
    Darwin*)    open http://127.0.0.1:5000 ;;
    CYGWIN*|MINGW*|MSYS*) start http://127.0.0.1:5000 ;;
    *)          echo "Please open http://127.0.0.1:5000 in your browser" ;;
esac

echo "Backend PID: $FLASK_PID"
echo "Frontend URL: http://127.0.0.1:5000"
echo "Press Ctrl+C to stop the backend"

# Wait for Flask process
wait $FLASK_PID
