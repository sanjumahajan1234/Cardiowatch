# CardioWatch

A Flask web application that monitors blood pressure risk for a panel of patients
using FHIR R4 data. Built for CS 6440 Health Informatics, Georgia Tech, Spring 2026.

## What it does

- Connects to the HAPI FHIR R4 public demo server and pulls Patient, Observation,
  Condition, and MedicationRequest resources
- Classifies each patient's blood pressure using the 2017 AHA/ACC guidelines
  (Normal, Elevated, Stage 1, Stage 2, Hypertensive Crisis)
- Shows a dashboard with all patients sorted by risk level
- Runs a background thread that checks for new readings every 5 minutes and sends
  email alerts for any Stage 2 or Crisis readings
- Falls back to built-in synthetic demo data if the FHIR server is unavailable

## Setup

Python 3.11 or later is required.

Install dependencies:

    pip install flask requests fhir.resources

Configure environment variables (recommended):

    CARDIOWATCH_SECRET_KEY=change-me
    CARDIOWATCH_PASSWORD=cardiowatch2026
    CARDIOWATCH_ALERT_EMAIL=you@example.com
    CARDIOWATCH_SMTP_PASSWORD=your-app-password

Notes:
- If SMTP variables are not set, alert emails are safely skipped.
- The app attempts live FHIR loading first, then falls back to synthetic demo data.

## Running the app

### Option 1: All-in-one launcher (recommended)

- Linux/macOS: `./start.sh` or `python start.py`
- Windows: `start.bat` or `python start.py`

### Option 2: Manual

    python app.py

Then open a browser and go to:

    http://127.0.0.1:5000


## Pages

- /              Patient risk dashboard
- /patient/<id>  Individual patient with BP trend chart
- /analytics     Population risk distribution
- /fhir-status   Live connection status to HAPI FHIR server
- /privacy       Data handling disclosure

All dashboard and API pages require login via `/login`.

## File structure

    app.py
    start.sh
    start.bat
    start.py
    Karan.md
    templates/
        base.html
        dashboard.html
        patient_detail.html
        analytics.html
        fhir_status.html
        privacy.html
    static/
        css/
            style.css
        js/
            app.js

## Team

Group 83, CS 6440 Spring 2026, Georgia Institute of Technology

- Sanjay Mahajan
- Karan Shrivastava
- Aasish S. Virjala
