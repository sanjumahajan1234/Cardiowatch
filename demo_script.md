# CardioWatch Demo Script (5 minutes)

## Setup (1 minute)
"Let me start the CardioWatch application and show you what we've built so far."

1. Run `python start.py` or `start.bat`
2. Show login page (password: cardiowatch2026)
3. Briefly explain: "This is a Flask web app that monitors blood pressure risk using FHIR data"

## Live Demo Flow (3 minutes)

### 1. Dashboard Overview (45 seconds)
- Point out patient cards sorted by risk level
- Show risk badges (Normal, Elevated, Stage 1, Stage 2, Crisis)
- Mention live FHIR vs synthetic data indicator
- Click FHIR Status to show live connection

### 2. Patient Details (45 seconds)
- Click on a high-risk patient
- Show BP trend chart
- Point out medications and conditions
- Explain how we classify using AHA/ACC guidelines

### 3. Population Analytics (45 seconds)
- Navigate to Analytics page
- Show risk distribution pie chart
- Point out summary statistics
- Show worsening trend patients if any

### 4. Alert System (45 seconds)
- On dashboard, click "Send Alert" for a Stage 2/Crisis patient
- Show toast notification
- Explain: "In production, this sends email alerts to clinicians"

## Technical Highlights (30 seconds)
- "We're using HAPI FHIR public server for live patient data"
- "Background thread checks for new readings every 5 minutes"
- "All routes are login-protected for HIPAA compliance"
- "Environment variables secure sensitive configuration"

## Current Status & Next Steps (30 seconds)
- "Core features implemented: FHIR integration, BP classification, dashboard, analytics"
- "Authentication and alert system are functional"
- "Next: Add more visualizations, improve UI, add unit tests"
- "The project demonstrates real-world health informatics workflow"

## Close
"Questions about the implementation or architecture?"
