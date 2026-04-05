# CardioWatch Development Log

## 2026-04-05 — Karan Shrivastava

Implemented a core set of README-aligned features to harden security, improve data loading behavior, and ensure completeness:

### 1) Secure configuration via environment variables
- Replaced hardcoded secrets with environment-variable-based configuration
- `CARDIOWATCH_SECRET_KEY`, `CARDIOWATCH_PASSWORD`, `CARDIOWATCH_ALERT_EMAIL`, `CARDIOWATCH_SMTP_PASSWORD`
- Added safe fallbacks and graceful handling when SMTP credentials are missing

### 2) Live FHIR-first loading with fallback
- Modified `load_patients()` to attempt live HAPI FHIR patient/observation fetch first
- Falls back to built-in synthetic demo cohort if FHIR unavailable or empty
- Updated documentation and report labels to reflect actual data source

### 3) Authentication hardening
- Added `@login_required` to dashboard and all API endpoints
- Protected `/api/test-alert`, `/api/classify-bp`, `/api/patient/<id>/latest`, `/api/export-data`
- Added guard for empty patient list in test-alert to avoid crashes

### 4) Report data-source labeling
- Patient reports now correctly indicate whether data is from HAPI FHIR or synthetic fallback

### 5) Missing static asset
- Created `static/js/app.js` (referenced by base.html) with shared toast helper

### 6) README updates
- Documented environment variable setup
- Clarified live-FHIR-first behavior and SMTP-skip handling
- Noted login requirement for dashboard/API pages

### Validation
- Syntax-checked with `python -m py_compile app.py` — passes