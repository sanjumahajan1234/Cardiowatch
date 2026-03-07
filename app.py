from flask import Flask, render_template, request, redirect, url_for, jsonify, flash
from datetime import datetime, timedelta
import random
import smtplib
from email.mime.text import MIMEText
import threading
import time
import requests as http_requests
import json

app = Flask(__name__)
app.secret_key = 'cardiowatch-secret-key'

# ============================================================================
# AHA/ACC Blood Pressure Classification Engine
# Mirrors WasteNot's classify_food_item() rule-based approach
# Reference: Whelton et al., JACC 2018
# ============================================================================

BP_CATEGORIES = {
    'normal':    {'label': 'Normal',            'color': '#16a34a', 'badge': 'green',  'priority': 0},
    'elevated':  {'label': 'Elevated',          'color': '#eab308', 'badge': 'yellow', 'priority': 1},
    'stage1':    {'label': 'Stage 1 Hypertension', 'color': '#f97316', 'badge': 'orange','priority': 2},
    'stage2':    {'label': 'Stage 2 Hypertension', 'color': '#dc2626', 'badge': 'red',  'priority': 3},
    'crisis':    {'label': 'Hypertensive Crisis',  'color': '#7c3aed', 'badge': 'purple','priority': 4},
}

def classify_bp(systolic, diastolic):
    """
    Rule-based AHA/ACC BP classifier.
    Mirrors WasteNot's classify_food_item() pattern - not everything needs ML.
    Returns category key from BP_CATEGORIES.
    """
    if systolic is None or diastolic is None:
        return 'normal'

    if systolic > 180 or diastolic > 120:
        return 'crisis'
    elif systolic >= 140 or diastolic >= 90:
        return 'stage2'
    elif systolic >= 130 or diastolic >= 80:
        return 'stage1'
    elif 120 <= systolic <= 129 and diastolic < 80:
        return 'elevated'
    else:
        return 'normal'

def bp_days_since_last_reading(patient):
    """How many days since last BP observation."""
    if patient.get('observations'):
        latest = patient['observations'][-1]
        delta = datetime.now() - latest['date']
        return delta.days
    return 999

# ============================================================================
# FHIR CLIENT - Fetches from HAPI FHIR R4 demo server
# Falls back to synthetic demo data if server is unavailable
# ============================================================================

FHIR_BASE = "https://hapi.fhir.org/baseR4"

def fetch_fhir_patients(limit=10):
    """Fetch Patient resources from HAPI FHIR server."""
    try:
        resp = http_requests.get(
            f"{FHIR_BASE}/Patient",
            params={"_count": limit, "_format": "json"},
            timeout=5
        )
        resp.raise_for_status()
        bundle = resp.json()
        patients = []
        for entry in bundle.get("entry", [])[:limit]:
            res = entry.get("resource", {})
            name_list = res.get("name", [{}])
            family = name_list[0].get("family", "Unknown") if name_list else "Unknown"
            given  = " ".join(name_list[0].get("given", [])) if name_list else ""
            patients.append({
                "fhir_id": res.get("id"),
                "name": f"{given} {family}".strip(),
                "gender": res.get("gender", "unknown"),
                "birthDate": res.get("birthDate", ""),
            })
        return patients
    except Exception as e:
        print(f"[FHIR] Patient fetch failed: {e} - using demo data")
        return None

def fetch_fhir_bp_observations(fhir_patient_id):
    """
    Fetch blood pressure Observations for a patient.
    LOINC 85354-9 = BP panel; components 8480-6 (systolic), 8462-4 (diastolic).
    """
    try:
        resp = http_requests.get(
            f"{FHIR_BASE}/Observation",
            params={
                "patient": fhir_patient_id,
                "code": "85354-9",
                "_count": 20,
                "_sort": "date",
                "_format": "json"
            },
            timeout=5
        )
        resp.raise_for_status()
        bundle = resp.json()
        observations = []
        for entry in bundle.get("entry", []):
            res = entry.get("resource", {})
            sys_val = dia_val = None
            for comp in res.get("component", []):
                coding = comp.get("code", {}).get("coding", [{}])[0].get("code", "")
                val = comp.get("valueQuantity", {}).get("value")
                if coding == "8480-6":
                    sys_val = val
                elif coding == "8462-4":
                    dia_val = val
            date_str = res.get("effectiveDateTime", "")
            try:
                obs_date = datetime.fromisoformat(date_str[:10])
            except Exception:
                obs_date = datetime.now()
            if sys_val and dia_val:
                observations.append({
                    "date": obs_date,
                    "systolic": int(sys_val),
                    "diastolic": int(dia_val),
                    "category": classify_bp(int(sys_val), int(dia_val))
                })
        return observations
    except Exception as e:
        print(f"[FHIR] Observation fetch failed for {fhir_patient_id}: {e}")
        return []

# ============================================================================
# DEMO / SYNTHETIC PATIENT DATA
# Used when FHIR server is unavailable - mirrors WasteNot's fridge_items pattern
# ============================================================================

def make_demo_observations(base_sys, base_dia, n=12):
    """Generate realistic BP observation history."""
    obs = []
    for i in range(n):
        date = datetime.now() - timedelta(days=(n - i) * 7)
        noise_s = random.randint(-8, 8)
        noise_d = random.randint(-5, 5)
        s = max(90, base_sys + noise_s)
        d = max(60, base_dia + noise_d)
        obs.append({
            "date": date,
            "systolic": s,
            "diastolic": d,
            "category": classify_bp(s, d)
        })
    return obs

DEMO_PATIENTS = [
    {
        "id": 1, "fhir_id": "demo-001",
        "name": "Alice Johnson", "age": 58, "gender": "female",
        "mrn": "MRN-1001",
        "conditions": ["Hypertension", "Type 2 Diabetes"],
        "medications": ["Lisinopril 10mg", "Metformin 500mg"],
        "observations": make_demo_observations(128, 82),
        "heart_rate": 74, "last_visit": "2026-02-15"
    },
    {
        "id": 2, "fhir_id": "demo-002",
        "name": "Robert Chen", "age": 65, "gender": "male",
        "mrn": "MRN-1002",
        "conditions": ["Stage 2 Hypertension", "Atrial Fibrillation"],
        "medications": ["Amlodipine 5mg", "Warfarin 5mg"],
        "observations": make_demo_observations(148, 94),
        "heart_rate": 88, "last_visit": "2026-02-20"
    },
    {
        "id": 3, "fhir_id": "demo-003",
        "name": "Maria Garcia", "age": 45, "gender": "female",
        "mrn": "MRN-1003",
        "conditions": ["Elevated Blood Pressure"],
        "medications": ["Lifestyle modifications"],
        "observations": make_demo_observations(124, 78),
        "heart_rate": 68, "last_visit": "2026-03-01"
    },
    {
        "id": 4, "fhir_id": "demo-004",
        "name": "James Williams", "age": 72, "gender": "male",
        "mrn": "MRN-1004",
        "conditions": ["Hypertensive Crisis (recent)", "CKD Stage 3"],
        "medications": ["Hydralazine 25mg", "Furosemide 40mg", "Metoprolol 50mg"],
        "observations": make_demo_observations(185, 115),
        "heart_rate": 96, "last_visit": "2026-03-05"
    },
    {
        "id": 5, "fhir_id": "demo-005",
        "name": "Sarah Patel", "age": 38, "gender": "female",
        "mrn": "MRN-1005",
        "conditions": ["Normal BP - annual review"],
        "medications": ["None"],
        "observations": make_demo_observations(112, 72),
        "heart_rate": 62, "last_visit": "2026-01-10"
    },
    {
        "id": 6, "fhir_id": "demo-006",
        "name": "David Kim", "age": 55, "gender": "male",
        "mrn": "MRN-1006",
        "conditions": ["Stage 1 Hypertension", "Hyperlipidaemia"],
        "medications": ["Atorvastatin 20mg", "Hydrochlorothiazide 12.5mg"],
        "observations": make_demo_observations(136, 85),
        "heart_rate": 78, "last_visit": "2026-02-28"
    },
]

# Attach current risk category to each demo patient
for p in DEMO_PATIENTS:
    latest = p["observations"][-1] if p["observations"] else {}
    p["latest_systolic"]  = latest.get("systolic")
    p["latest_diastolic"] = latest.get("diastolic")
    p["risk_category"]    = classify_bp(p["latest_systolic"], p["latest_diastolic"])
    p["risk_label"]       = BP_CATEGORIES[p["risk_category"]]["label"]
    p["risk_color"]       = BP_CATEGORIES[p["risk_category"]]["color"]
    p["risk_badge"]       = BP_CATEGORIES[p["risk_category"]]["badge"]

# ============================================================================
# EMAIL ALERT SYSTEM - Reused directly from WasteNot
# ============================================================================

ALERT_EMAIL   = "teamjugaad2@gmail.com"
SMTP_PASSWORD = "nerzvewokletnzvq"

def send_bp_alert(patient_name, systolic, diastolic, risk_label, recipient_email=ALERT_EMAIL):
    """Send cardiac risk alert - mirrors WasteNot's send_expiration_alert()."""
    subject = f"[CardioWatch Alert] {patient_name}: {risk_label}"
    body = f"""
CardioWatch Clinical Alert
===========================

Patient:    {patient_name}
BP Reading: {systolic}/{diastolic} mmHg
Risk Level: {risk_label}
Timestamp:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Recommended Action:
- Stage 2 Hypertension: Schedule urgent follow-up within 1 week
- Hypertensive Crisis: Refer to emergency care immediately

This alert was generated by CardioWatch's 24/7 background monitoring system.

CardioWatch Team
    """
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From']    = ALERT_EMAIL
        msg['To']      = recipient_email
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(ALERT_EMAIL, SMTP_PASSWORD)
        server.send_message(msg)
        server.quit()
        print(f"[EMAIL] Alert sent for {patient_name} - {risk_label}")
        return True
    except Exception as e:
        print(f"[EMAIL] Failed: {e}")
        return False

# ============================================================================
# 24/7 BACKGROUND FHIR MONITOR - Mirrors WasteNot's continuity principle
# ============================================================================

last_alert_sent = {}

def monitor_patients_background():
    """
    Server-side continuity: polls patient BP every 5 minutes,
    sends email alerts for Stage 2+ readings.
    Mirrors WasteNot's check_expiring_items_background().
    """
    global last_alert_sent
    time.sleep(15)  # Let Flask start first

    while True:
        try:
            print(f"\n{'='*60}")
            print(f"[MONITOR] BP Check - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'='*60}")

            today = datetime.now().date()
            alerts = 0

            for patient in DEMO_PATIENTS:
                cat = patient["risk_category"]
                if cat in ("stage2", "crisis"):
                    key = f"{patient['id']}_{today}"
                    if key not in last_alert_sent:
                        ok = send_bp_alert(
                            patient["name"],
                            patient["latest_systolic"],
                            patient["latest_diastolic"],
                            patient["risk_label"]
                        )
                        if ok:
                            last_alert_sent[key] = True
                            alerts += 1
                        print(f"  [ALERT] {patient['name']}: {patient['risk_label']} "
                              f"({patient['latest_systolic']}/{patient['latest_diastolic']})")
                    else:
                        print(f"  [SKIP] {patient['name']}: already alerted today")
                else:
                    print(f"  [OK] {patient['name']}: {patient['risk_label']}")

            print(f"  Alerts sent this cycle: {alerts}")
            print(f"  Next check in 5 minutes")
        except Exception as e:
            print(f"[MONITOR] Error: {e}")

        time.sleep(300)  # 5-minute polling interval

def start_background_monitoring():
    t = threading.Thread(target=monitor_patients_background, daemon=True)
    t.start()
    print("\n" + "="*60)
    print("  CardioWatch 24/7 FHIR Monitor STARTED")
    print("  Polling every 5 minutes for Stage 2+ BP readings")
    print("="*60 + "\n")

# ============================================================================
# JINJA2 CONTEXT
# ============================================================================
@app.context_processor
def inject_globals():
    return {
        'datetime': datetime,
        'now': datetime.now(),
        'BP_CATEGORIES': BP_CATEGORIES,
    }

# ============================================================================
# ROUTES
# ============================================================================

@app.route('/')
def dashboard():
    """Main patient risk dashboard - mirrors WasteNot's dashboard route."""
    crisis_patients = [p for p in DEMO_PATIENTS if p['risk_category'] == 'crisis']
    stage2_patients = [p for p in DEMO_PATIENTS if p['risk_category'] == 'stage2']
    stage1_patients = [p for p in DEMO_PATIENTS if p['risk_category'] == 'stage1']
    elevated_patients = [p for p in DEMO_PATIENTS if p['risk_category'] == 'elevated']
    normal_patients  = [p for p in DEMO_PATIENTS if p['risk_category'] == 'normal']

    # Sort: highest risk first - mirrors WasteNot's urgency sorting
    sorted_patients = sorted(
        DEMO_PATIENTS,
        key=lambda p: BP_CATEGORIES[p['risk_category']]['priority'],
        reverse=True
    )

    return render_template('dashboard.html',
        patients=sorted_patients,
        crisis_count=len(crisis_patients),
        stage2_count=len(stage2_patients),
        stage1_count=len(stage1_patients),
        elevated_count=len(elevated_patients),
        normal_count=len(normal_patients),
        total_patients=len(DEMO_PATIENTS),
        BP_CATEGORIES=BP_CATEGORIES,
    )

@app.route('/patient/<int:patient_id>')
def patient_detail(patient_id):
    """Per-patient detail with BP trend chart."""
    patient = next((p for p in DEMO_PATIENTS if p['id'] == patient_id), None)
    if not patient:
        return "Patient not found", 404

    # Prepare chart data (JSON for Chart.js)
    chart_labels = [o['date'].strftime('%b %d') for o in patient['observations']]
    chart_sys    = [o['systolic']  for o in patient['observations']]
    chart_dia    = [o['diastolic'] for o in patient['observations']]

    return render_template('patient_detail.html',
        patient=patient,
        chart_labels=chart_labels,
        chart_sys=chart_sys,
        chart_dia=chart_dia,
        BP_CATEGORIES=BP_CATEGORIES,
    )

@app.route('/analytics')
def analytics():
    """Population-level BP analytics - mirrors WasteNot's analytics page."""
    category_counts = {k: 0 for k in BP_CATEGORIES}
    for p in DEMO_PATIENTS:
        category_counts[p['risk_category']] += 1

    avg_sys = sum(p['latest_systolic'] for p in DEMO_PATIENTS if p['latest_systolic']) / len(DEMO_PATIENTS)
    avg_dia = sum(p['latest_diastolic'] for p in DEMO_PATIENTS if p['latest_diastolic']) / len(DEMO_PATIENTS)

    return render_template('analytics.html',
        category_counts=category_counts,
        avg_sys=round(avg_sys, 1),
        avg_dia=round(avg_dia, 1),
        total_patients=len(DEMO_PATIENTS),
        BP_CATEGORIES=BP_CATEGORIES,
    )

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/fhir-status')
def fhir_status():
    """Show live FHIR server connection status."""
    try:
        resp = http_requests.get(f"{FHIR_BASE}/metadata", timeout=5)
        connected = resp.status_code == 200
        version = resp.json().get("fhirVersion", "unknown") if connected else "N/A"
    except Exception:
        connected = False
        version = "N/A"
    return render_template('fhir_status.html', connected=connected, fhir_version=version, fhir_base=FHIR_BASE)

# -- API Endpoints ---------------------------------------------------------------

@app.route('/api/test-alert', methods=['POST'])
def test_alert():
    """Manual alert trigger - mirrors WasteNot's /api/test-notification."""
    data = request.json or {}
    patient_id = data.get('patient_id', 4)
    patient = next((p for p in DEMO_PATIENTS if p['id'] == patient_id), DEMO_PATIENTS[3])
    ok = send_bp_alert(
        patient['name'], patient['latest_systolic'],
        patient['latest_diastolic'], patient['risk_label']
    )
    if ok:
        return jsonify({'success': True,  'message': f'Alert sent for {patient["name"]}', 'risk': patient['risk_label']})
    else:
        return jsonify({'success': False, 'message': 'Email failed - check SMTP config'})

@app.route('/api/classify-bp', methods=['POST'])
def api_classify_bp():
    """Classify a BP reading on demand."""
    data = request.json or {}
    sys_val = data.get('systolic')
    dia_val = data.get('diastolic')
    if sys_val is None or dia_val is None:
        return jsonify({'error': 'systolic and diastolic required'}), 400
    cat = classify_bp(int(sys_val), int(dia_val))
    return jsonify({
        'systolic': sys_val, 'diastolic': dia_val,
        'category': cat, 'label': BP_CATEGORIES[cat]['label'],
        'color': BP_CATEGORIES[cat]['color'],
    })

@app.route('/api/patient/<int:patient_id>/latest')
def api_patient_latest(patient_id):
    """Return latest BP for a patient as JSON."""
    patient = next((p for p in DEMO_PATIENTS if p['id'] == patient_id), None)
    if not patient:
        return jsonify({'error': 'not found'}), 404
    return jsonify({
        'name': patient['name'],
        'systolic': patient['latest_systolic'],
        'diastolic': patient['latest_diastolic'],
        'risk_category': patient['risk_category'],
        'risk_label': patient['risk_label'],
    })

@app.route('/api/export-data')
def export_data():
    """HIPAA-aligned data export - mirrors WasteNot's GDPR export."""
    export = {
        'export_date': datetime.now().isoformat(),
        'fhir_server': FHIR_BASE,
        'patients': [
            {
                'id': p['id'], 'name': p['name'], 'mrn': p['mrn'],
                'risk_category': p['risk_category'], 'risk_label': p['risk_label'],
                'latest_bp': f"{p['latest_systolic']}/{p['latest_diastolic']}",
            }
            for p in DEMO_PATIENTS
        ],
        'note': 'All data is synthetic (Synthea-generated). No real patient data.'
    }
    return jsonify(export)

# ============================================================================
# LAUNCH
# ============================================================================
if __name__ == '__main__':
    start_background_monitoring()
    print("\n" + "="*60)
    print("  CardioWatch ACTIVE:")
    print("  [ON] AHA/ACC BP Classifier (rule-based)")
    print("  [ON] FHIR R4 Client (HAPI demo server)")
    print("  [ON] 24/7 Background Monitor (5-min polling)")
    print("  [ON] Email Alerts (Stage 2 + Crisis)")
    print("  [ON] Patient Dashboard + Trend Charts")
    print("="*60 + "\n")
    app.run(debug=True)
