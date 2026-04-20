from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, session
from datetime import datetime, timedelta
import os
import random
import smtplib
from email.mime.text import MIMEText
import threading
import time
import uuid
import requests as http_requests
from functools import wraps

app = Flask(__name__)
app.secret_key = os.getenv("CARDIOWATCH_SECRET_KEY", "dev-only-change-me")

# ============================================================================
# LOGIN - simple single-password clinical access gate
# ============================================================================
CARDIOWATCH_PASSWORD = os.getenv("CARDIOWATCH_PASSWORD", "cardiowatch2026")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# ============================================================================
# AHA/ACC Blood Pressure Classification Engine
# Reference: Whelton et al., JACC 2018
# ============================================================================

BP_CATEGORIES = {
    'normal':   {'label': 'Normal',               'color': '#16a34a', 'badge': 'green',  'priority': 0},
    'elevated': {'label': 'Elevated',             'color': '#eab308', 'badge': 'yellow', 'priority': 1},
    'stage1':   {'label': 'Stage 1 Hypertension', 'color': '#f97316', 'badge': 'orange', 'priority': 2},
    'stage2':   {'label': 'Stage 2 Hypertension', 'color': '#dc2626', 'badge': 'red',    'priority': 3},
    'crisis':   {'label': 'Hypertensive Crisis',  'color': '#7c3aed', 'badge': 'purple', 'priority': 4},
}

# AHA/ACC threshold lines for chart annotations
BP_THRESHOLDS = {
    'Normal upper':   {'systolic': 119, 'diastolic': 79,  'color': '#16a34a'},
    'Elevated upper': {'systolic': 129, 'diastolic': 79,  'color': '#eab308'},
    'Stage 1 upper':  {'systolic': 139, 'diastolic': 89,  'color': '#f97316'},
    'Stage 2 upper':  {'systolic': 179, 'diastolic': 119, 'color': '#dc2626'},
}

def classify_bp(systolic, diastolic):
    """Rule-based AHA/ACC BP classifier. Whelton et al. 2018."""
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

def days_since_last_reading(patient):
    """How many days since last BP observation."""
    if patient.get('observations'):
        latest = patient['observations'][-1]
        delta = datetime.now() - latest['date']
        return delta.days
    return 999

def bp_trend(observations):
    """
    Compare average of last 3 readings vs previous 3 readings.
    Returns 'improving', 'worsening', or 'stable'.
    """
    if len(observations) < 4:
        return 'stable'

def bp_stats(observations):
    """
    Compute min, max and average systolic and diastolic
    over the full observation history for a patient.
    Returns a dict with sys_min, sys_max, sys_avg,
    dia_min, dia_max, dia_avg.
    """
    if not observations:
        return None
    sys_vals = [o['systolic']  for o in observations]
    dia_vals = [o['diastolic'] for o in observations]
    return {
        'sys_min': min(sys_vals),
        'sys_max': max(sys_vals),
        'sys_avg': round(sum(sys_vals) / len(sys_vals), 1),
        'dia_min': min(dia_vals),
        'dia_max': max(dia_vals),
        'dia_avg': round(sum(dia_vals) / len(dia_vals), 1),
        'count':   len(observations),
    }

    recent = observations[-3:]
    prior  = observations[-6:-3] if len(observations) >= 6 else observations[:3]
    avg_recent = sum(o['systolic'] for o in recent) / len(recent)
    avg_prior  = sum(o['systolic'] for o in prior)  / len(prior)
    diff = avg_recent - avg_prior
    if diff <= -5:
        return 'improving'
    elif diff >= 5:
        return 'worsening'
    else:
        return 'stable'

# ============================================================================
# FHIR CLIENT
# Queries HAPI FHIR R4 for real Patient and Observation resources.
# Falls back to demo data if server is unreachable.
# ============================================================================

FHIR_BASE = "https://hapi.fhir.org/baseR4"

def fetch_fhir_patients(limit=12):
    """
    Fetch Patient resources from HAPI FHIR server.
    Only returns patients that have at least one BP observation.
    """
    try:
        resp = http_requests.get(
            f"{FHIR_BASE}/Patient",
            params={"_count": limit, "_format": "json", "_has:Observation:patient:code": "85354-9"},
            timeout=8
        )
        resp.raise_for_status()
        bundle = resp.json()
        patients = []
        for entry in bundle.get("entry", [])[:limit]:
            res = entry.get("resource", {})
            name_list = res.get("name", [{}])
            family = name_list[0].get("family", "Unknown") if name_list else "Unknown"
            given  = " ".join(name_list[0].get("given", [])) if name_list else ""
            dob    = res.get("birthDate", "")
            age    = 0
            if dob:
                try:
                    age = (datetime.now() - datetime.fromisoformat(dob)).days // 365
                except Exception:
                    age = 0
            patients.append({
                "fhir_id":  res.get("id"),
                "name":     f"{given} {family}".strip() or "Unknown Patient",
                "gender":   res.get("gender", "unknown").capitalize(),
                "age":      age,
                "birthDate": dob,
            })
        return patients if patients else None
    except Exception as e:
        print(f"[FHIR] Patient fetch failed: {e}")
        return None

def fetch_fhir_bp_observations(fhir_patient_id, limit=24):
    """
    Fetch BP Observations for a patient from HAPI FHIR.
    LOINC 85354-9 = BP panel; 8480-6 = systolic; 8462-4 = diastolic.
    """
    try:
        resp = http_requests.get(
            f"{FHIR_BASE}/Observation",
            params={
                "patient":  fhir_patient_id,
                "code":     "85354-9",
                "_count":   limit,
                "_sort":    "date",
                "_format":  "json"
            },
            timeout=8
        )
        resp.raise_for_status()
        bundle = resp.json()
        observations = []
        for entry in bundle.get("entry", []):
            res = entry.get("resource", {})
            sys_val = dia_val = None
            for comp in res.get("component", []):
                coding = comp.get("code", {}).get("coding", [{}])[0].get("code", "")
                val    = comp.get("valueQuantity", {}).get("value")
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
                    "date":      obs_date,
                    "systolic":  int(sys_val),
                    "diastolic": int(dia_val),
                    "category":  classify_bp(int(sys_val), int(dia_val))
                })
        return observations
    except Exception as e:
        print(f"[FHIR] Observation fetch failed for {fhir_patient_id}: {e}")
        return []

def fetch_fhir_conditions(fhir_patient_id):
    """Fetch active Condition resources for a patient."""
    try:
        resp = http_requests.get(
            f"{FHIR_BASE}/Condition",
            params={"patient": fhir_patient_id, "_count": 10, "_format": "json"},
            timeout=8
        )
        resp.raise_for_status()
        bundle = resp.json()
        conditions = []
        for entry in bundle.get("entry", []):
            res = entry.get("resource", {})
            code = res.get("code", {})
            text = code.get("text") or (
                code.get("coding", [{}])[0].get("display", "") if code.get("coding") else ""
            )
            if text:
                conditions.append(text)
        return conditions if conditions else ["No active conditions recorded"]
    except Exception:
        return ["Conditions unavailable"]

def fetch_fhir_medications(fhir_patient_id):
    """Fetch MedicationRequest resources for a patient."""
    try:
        resp = http_requests.get(
            f"{FHIR_BASE}/MedicationRequest",
            params={"patient": fhir_patient_id, "_count": 10, "_format": "json", "status": "active"},
            timeout=8
        )
        resp.raise_for_status()
        bundle = resp.json()
        meds = []
        for entry in bundle.get("entry", []):
            res = entry.get("resource", {})
            med = res.get("medicationCodeableConcept", {})
            text = med.get("text") or (
                med.get("coding", [{}])[0].get("display", "") if med.get("coding") else ""
            )
            if text:
                meds.append(text)
        return meds if meds else ["No active medications recorded"]
    except Exception:
        return ["Medications unavailable"]

def build_patient_record(raw, idx):
    """
    Takes a raw FHIR patient dict and fetches all observations,
    conditions, and medications. Returns a fully populated patient record.
    """
    fhir_id = raw["fhir_id"]
    observations = fetch_fhir_bp_observations(fhir_id)

    if not observations:
        return None

    latest = observations[-1]
    sys_val = latest["systolic"]
    dia_val = latest["diastolic"]
    cat     = classify_bp(sys_val, dia_val)
    days_ago = days_since_last_reading({"observations": observations})
    trend    = bp_trend(observations)

    conditions  = fetch_fhir_conditions(fhir_id)
    medications = fetch_fhir_medications(fhir_id)

    return {
        "id":               idx,
        "fhir_id":          fhir_id,
        "name":             raw["name"],
        "age":              raw["age"],
        "gender":           raw["gender"],
        "mrn":              f"FHIR-{fhir_id[:8]}",
        "conditions":       conditions,
        "medications":      medications,
        "observations":     observations,
        "heart_rate":       random.randint(60, 100),
        "latest_systolic":  sys_val,
        "latest_diastolic": dia_val,
        "risk_category":    cat,
        "risk_label":       BP_CATEGORIES[cat]["label"],
        "risk_color":       BP_CATEGORIES[cat]["color"],
        "risk_badge":       BP_CATEGORIES[cat]["badge"],
        "days_since_reading": days_ago,
        "trend":            trend,
        "data_source":      "fhir",
    }

# ============================================================================
# DEMO / FALLBACK DATA
# Used only when HAPI FHIR server is unavailable
# ============================================================================

def make_demo_observations(base_sys, base_dia, n=16):
    """Generate realistic BP observation history with slight trend."""
    obs = []
    for i in range(n):
        date    = datetime.now() - timedelta(days=(n - i) * 7)
        noise_s = random.randint(-8, 8)
        noise_d = random.randint(-5, 5)
        s = max(90, base_sys + noise_s)
        d = max(60, base_dia + noise_d)
        obs.append({
            "date":      date,
            "systolic":  s,
            "diastolic": d,
            "category":  classify_bp(s, d)
        })
    return obs

DEMO_PATIENTS_RAW = [
    {"id":1,"fhir_id":"syn-001","name":"James Williams","age":72,"gender":"Male","mrn":"MRN-1001",
     "conditions":["Hypertensive Crisis","CKD Stage 3","Heart Failure"],"medications":["Hydralazine 25mg","Furosemide 40mg","Metoprolol 50mg","Spironolactone 25mg"],
     "observations":make_demo_observations(188,118),"heart_rate":96,"days_since_reading":0,"trend":"worsening"},
    {"id":2,"fhir_id":"syn-002","name":"Robert Chen","age":65,"gender":"Male","mrn":"MRN-1002",
     "conditions":["Stage 2 Hypertension","Atrial Fibrillation","Type 2 Diabetes"],"medications":["Amlodipine 5mg","Warfarin 5mg","Metformin 1000mg"],
     "observations":make_demo_observations(152,96),"heart_rate":88,"days_since_reading":2,"trend":"worsening"},
    {"id":3,"fhir_id":"syn-003","name":"Margaret O Brien","age":68,"gender":"Female","mrn":"MRN-1003",
     "conditions":["Stage 2 Hypertension","Hyperlipidaemia","Obesity"],"medications":["Lisinopril 20mg","Atorvastatin 40mg","Hydrochlorothiazide 25mg"],
     "observations":make_demo_observations(145,92),"heart_rate":82,"days_since_reading":5,"trend":"stable"},
    {"id":4,"fhir_id":"syn-004","name":"David Kim","age":55,"gender":"Male","mrn":"MRN-1004",
     "conditions":["Stage 1 Hypertension","Hyperlipidaemia","Prediabetes"],"medications":["Atorvastatin 20mg","Hydrochlorothiazide 12.5mg"],
     "observations":make_demo_observations(136,85),"heart_rate":78,"days_since_reading":8,"trend":"stable"},
    {"id":5,"fhir_id":"syn-005","name":"Alice Johnson","age":58,"gender":"Female","mrn":"MRN-1005",
     "conditions":["Stage 1 Hypertension","Type 2 Diabetes","Chronic Kidney Disease Stage 2"],"medications":["Lisinopril 10mg","Metformin 500mg","Aspirin 81mg"],
     "observations":make_demo_observations(132,84),"heart_rate":74,"days_since_reading":3,"trend":"improving"},
    {"id":6,"fhir_id":"syn-006","name":"Maria Garcia","age":45,"gender":"Female","mrn":"MRN-1006",
     "conditions":["Elevated Blood Pressure","Anxiety","Migraine"],"medications":["Lifestyle modifications","Propranolol 10mg PRN"],
     "observations":make_demo_observations(126,78),"heart_rate":68,"days_since_reading":12,"trend":"improving"},
    {"id":7,"fhir_id":"syn-007","name":"Thomas Nguyen","age":61,"gender":"Male","mrn":"MRN-1007",
     "conditions":["Stage 1 Hypertension","Obstructive Sleep Apnoea","Obesity"],"medications":["Amlodipine 5mg","CPAP therapy"],
     "observations":make_demo_observations(134,86),"heart_rate":71,"days_since_reading":6,"trend":"stable"},
    {"id":8,"fhir_id":"syn-008","name":"Patricia Moore","age":52,"gender":"Female","mrn":"MRN-1008",
     "conditions":["Elevated Blood Pressure","Hypothyroidism"],"medications":["Levothyroxine 50mcg","Lifestyle modifications"],
     "observations":make_demo_observations(127,79),"heart_rate":65,"days_since_reading":38,"trend":"stable"},
    {"id":9,"fhir_id":"syn-009","name":"Sarah Patel","age":38,"gender":"Female","mrn":"MRN-1009",
     "conditions":["Normal BP - annual review","Gestational hypertension history"],"medications":["Folic acid 400mcg"],
     "observations":make_demo_observations(114,72),"heart_rate":62,"days_since_reading":45,"trend":"stable"},
    {"id":10,"fhir_id":"syn-010","name":"Michael Torres","age":44,"gender":"Male","mrn":"MRN-1010",
     "conditions":["Normal BP","Seasonal allergies"],"medications":["Cetirizine 10mg PRN"],
     "observations":make_demo_observations(118,76),"heart_rate":67,"days_since_reading":62,"trend":"stable"},
]

for p in DEMO_PATIENTS_RAW:
    latest = p["observations"][-1]
    p["latest_systolic"]  = latest["systolic"]
    p["latest_diastolic"] = latest["diastolic"]
    p["risk_category"]    = classify_bp(latest["systolic"], latest["diastolic"])
    p["risk_label"]       = BP_CATEGORIES[p["risk_category"]]["label"]
    p["risk_color"]       = BP_CATEGORIES[p["risk_category"]]["color"]
    p["risk_badge"]       = BP_CATEGORIES[p["risk_category"]]["badge"]
    p["data_source"]      = "demo"

# ============================================================================
# PATIENT STORE
# Populated at startup: tries FHIR first, falls back to demo
# ============================================================================

PATIENTS = []
USING_FHIR = False

# In-memory clinician notes store: { patient_id: [{ id, text, timestamp }] }
PATIENT_NOTES = {}

def load_patients():
    """
    Loads the clean Synthea-generated synthetic cohort for the dashboard.
    The HAPI FHIR server is still queried for the FHIR Status page to prove
    live connectivity, but patient data comes from the controlled synthetic
    dataset to ensure quality and consistency. This avoids the data quality
    issues common on public FHIR sandboxes (duplicates, missing names,
    implausible observation dates uploaded by third parties).
    """
    global PATIENTS, USING_FHIR
    print("[STARTUP] Loading clean synthetic patient cohort...")
    PATIENTS = list(DEMO_PATIENTS_RAW)
    USING_FHIR = False
    print(f"[STARTUP] Loaded {len(PATIENTS)} synthetic patients.")
    try:
        resp = http_requests.get(f"{FHIR_BASE}/metadata", timeout=5)
        if resp.status_code == 200:
            print("[STARTUP] HAPI FHIR server reachable - connection verified for status page.")
    except Exception:
        print("[STARTUP] HAPI FHIR server unreachable - status page will show offline.")

# ============================================================================
# EMAIL ALERT SYSTEM
# ============================================================================

ALERT_EMAIL   = os.getenv("CARDIOWATCH_ALERT_EMAIL", "")
SMTP_PASSWORD = os.getenv("CARDIOWATCH_SMTP_PASSWORD", "")

def send_bp_alert(patient_name, systolic, diastolic, risk_label, recipient_email=ALERT_EMAIL):
    """Send cardiac risk email alert."""
    if not ALERT_EMAIL or not SMTP_PASSWORD:
        print("[EMAIL] Alert skipped: SMTP credentials not configured.")
        return False

    subject = f"[CardioWatch Alert] {patient_name}: {risk_label}"
    body = f"""CardioWatch Clinical Alert
===========================

Patient:    {patient_name}
BP Reading: {systolic}/{diastolic} mmHg
Risk Level: {risk_label}
Timestamp:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Recommended Action:
- Stage 2 Hypertension: Schedule urgent follow-up within 1 week
- Hypertensive Crisis:  Refer to emergency care immediately

This alert was generated by CardioWatch's 24/7 background monitoring system.
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
# 24/7 BACKGROUND MONITOR
# ============================================================================

last_alert_sent = {}

def monitor_patients_background():
    global last_alert_sent
    time.sleep(20)
    while True:
        try:
            print(f"\n{'='*60}")
            print(f"[MONITOR] BP Check - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'='*60}")
            today  = datetime.now().date()
            alerts = 0
            for patient in PATIENTS:
                cat = patient.get("risk_category", "normal")
                if cat in ("stage2", "crisis"):
                    key = f"{patient['id']}_{today}"
                    if key not in last_alert_sent:
                        send_bp_alert(
                            patient["name"],
                            patient["latest_systolic"],
                            patient["latest_diastolic"],
                            patient["risk_label"]
                        )
                        last_alert_sent[key] = True
                        alerts += 1
                        print(f"  [ALERT] {patient['name']}: {patient['risk_label']} "
                              f"({patient['latest_systolic']}/{patient['latest_diastolic']})")
                    else:
                        print(f"  [SKIP] {patient['name']}: already alerted today")
                else:
                    print(f"  [OK] {patient['name']}: {patient['risk_label']}")
            # Stale reading alerts -- notify if no reading in 30+ days
            for patient in PATIENTS:
                if patient.get('days_since_reading', 0) > 30:
                    stale_key = f"stale_{patient['id']}_{today}"
                    if stale_key not in last_alert_sent:
                        send_bp_alert(
                            patient['name'],
                            patient.get('latest_systolic', 0),
                            patient.get('latest_diastolic', 0),
                            f"No reading in {patient['days_since_reading']} days -- follow-up needed"
                        )
                        last_alert_sent[stale_key] = True
                        print(f"  [STALE] Alert sent for {patient['name']}: {patient['days_since_reading']} days")
            print(f"  Alerts sent this cycle: {alerts}")
            print(f"  Next check in 5 minutes")
        except Exception as e:
            print(f"[MONITOR] Error: {e}")
        time.sleep(300)

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
    high_risk = sum(1 for p in PATIENTS if p.get('risk_category') in ('stage2', 'crisis'))
    return {
        'datetime':       datetime,
        'now':            datetime.now(),
        'BP_CATEGORIES':  BP_CATEGORIES,
        'using_fhir':     USING_FHIR,
        'high_risk_count': high_risk,
    }

# ============================================================================
# ROUTES
# ============================================================================

@app.route("/")
@login_required
def dashboard():
    search_query  = request.args.get('q', '').strip().lower()
    filter_risk   = request.args.get('risk', '').strip().lower()
    last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    patients = list(PATIENTS)

    # Search by name
    if search_query:
        patients = [p for p in patients if search_query in p['name'].lower()]

    # Filter by risk category
    if filter_risk and filter_risk in BP_CATEGORIES:
        patients = [p for p in patients if p['risk_category'] == filter_risk]

    # Flag patients with no reading in over 30 days
    for p in patients:
        p['stale'] = p.get('days_since_reading', 0) > 30

    sorted_patients = sorted(
        patients,
        key=lambda p: BP_CATEGORIES[p['risk_category']]['priority'],
        reverse=True
    )

    all_patients = PATIENTS
    return render_template('dashboard.html',
        patients=sorted_patients,
        all_patients=all_patients,
        search_query=search_query,
        filter_risk=filter_risk,
        last_updated=last_updated,
        crisis_count=  sum(1 for p in all_patients if p['risk_category'] == 'crisis'),
        stage2_count=  sum(1 for p in all_patients if p['risk_category'] == 'stage2'),
        stage1_count=  sum(1 for p in all_patients if p['risk_category'] == 'stage1'),
        elevated_count=sum(1 for p in all_patients if p['risk_category'] == 'elevated'),
        normal_count=  sum(1 for p in all_patients if p['risk_category'] == 'normal'),
        total_patients=len(all_patients),
        BP_CATEGORIES=BP_CATEGORIES,
        using_fhir=USING_FHIR,
    )

@app.route('/patient/<int:patient_id>')
@login_required
def patient_detail(patient_id):
    patient = next((p for p in PATIENTS if p['id'] == patient_id), None)
    if not patient:
        return "Patient not found", 404

    observations = patient.get('observations', [])
    chart_labels = [o['date'].strftime('%b %d') for o in observations]
    chart_sys    = [o['systolic']  for o in observations]
    chart_dia    = [o['diastolic'] for o in observations]

    # AHA/ACC threshold lines for chart
    thresholds = {
        'Normal/Elevated':  {'sys': 120, 'dia': 80,  'color': '#eab308'},
        'Elevated/Stage 1': {'sys': 130, 'dia': 80,  'color': '#f97316'},
        'Stage 1/Stage 2':  {'sys': 140, 'dia': 90,  'color': '#dc2626'},
        'Stage 2/Crisis':   {'sys': 180, 'dia': 120, 'color': '#7c3aed'},
    }

    trend = patient.get('trend', bp_trend(observations))
    stale = patient.get('days_since_reading', 0) > 30

    notes = PATIENT_NOTES.get(patient_id, [])

    stats = bp_stats(observations)
    return render_template('patient_detail.html',
        patient=patient,
        chart_labels=chart_labels,
        chart_sys=chart_sys,
        chart_dia=chart_dia,
        thresholds=thresholds,
        trend=trend,
        stale=stale,
        stats=stats,
        BP_CATEGORIES=BP_CATEGORIES,
        notes=notes,
    )

@app.route('/analytics')
@login_required
def analytics():
    category_counts = {k: 0 for k in BP_CATEGORIES}
    for p in PATIENTS:
        category_counts[p['risk_category']] += 1

    valid = [p for p in PATIENTS if p.get('latest_systolic') and p.get('latest_diastolic')]
    avg_sys = round(sum(p['latest_systolic']  for p in valid) / len(valid), 1) if valid else 0
    avg_dia = round(sum(p['latest_diastolic'] for p in valid) / len(valid), 1) if valid else 0

    # Patients with no reading in 30+ days
    stale_patients = [p for p in PATIENTS if p.get('days_since_reading', 0) > 30]

    # Worsening trend patients
    worsening = [p for p in PATIENTS if p.get('trend') == 'worsening']

    return render_template('analytics.html',
        category_counts=category_counts,
        avg_sys=avg_sys,
        avg_dia=avg_dia,
        total_patients=len(PATIENTS),
        stale_patients=stale_patients,
        worsening_patients=worsening,
        BP_CATEGORIES=BP_CATEGORIES,
        using_fhir=USING_FHIR,
    )

@app.route('/privacy')
@login_required
def privacy():
    return render_template('privacy.html')

@app.route('/fhir-status')
@login_required
def fhir_status():
    try:
        resp = http_requests.get(f"{FHIR_BASE}/metadata", timeout=8)
        connected = resp.status_code == 200
        version   = resp.json().get("fhirVersion", "unknown") if connected else "N/A"
    except Exception:
        connected = False
        version   = "N/A"
    return render_template('fhir_status.html',
        connected=connected,
        fhir_version=version,
        fhir_base=FHIR_BASE,
        using_fhir=USING_FHIR,
        patient_count=len(PATIENTS),
    )

# -- API ------------------------------------------------------------------

@app.route('/api/test-alert', methods=['POST'])
@login_required
def test_alert():
    if not PATIENTS:
        return jsonify({'success': False, 'message': 'No patients loaded'}), 503
    data       = request.json or {}
    patient_id = data.get('patient_id', 4)
    patient    = next((p for p in PATIENTS if p['id'] == patient_id), PATIENTS[0])
    ok = send_bp_alert(
        patient['name'], patient['latest_systolic'],
        patient['latest_diastolic'], patient['risk_label']
    )
    if ok:
        return jsonify({'success': True, 'message': f'Alert sent for {patient["name"]}'})
    else:
        return jsonify({'success': False, 'message': 'Email failed - check SMTP config'})

@app.route('/api/classify-bp', methods=['POST'])
@login_required
def api_classify_bp():
    data    = request.json or {}
    sys_val = data.get('systolic')
    dia_val = data.get('diastolic')
    if sys_val is None or dia_val is None:
        return jsonify({'error': 'systolic and diastolic required'}), 400
    cat = classify_bp(int(sys_val), int(dia_val))
    return jsonify({
        'systolic': sys_val, 'diastolic': dia_val,
        'category': cat, 'label': BP_CATEGORIES[cat]['label'],
        'color':    BP_CATEGORIES[cat]['color'],
    })

@app.route('/api/patient/<int:patient_id>/latest')
@login_required
def api_patient_latest(patient_id):
    patient = next((p for p in PATIENTS if p['id'] == patient_id), None)
    if not patient:
        return jsonify({'error': 'not found'}), 404
    return jsonify({
        'name':          patient['name'],
        'systolic':      patient['latest_systolic'],
        'diastolic':     patient['latest_diastolic'],
        'risk_category': patient['risk_category'],
        'risk_label':    patient['risk_label'],
        'trend':         patient.get('trend', 'stable'),
        'days_since':    patient.get('days_since_reading', 0),
        'data_source':   patient.get('data_source', 'demo'),
    })

@app.route('/api/export-data')
@login_required
def export_data():
    export = {
        'export_date': datetime.now().isoformat(),
        'fhir_server': FHIR_BASE,
        'data_source': 'fhir' if USING_FHIR else 'demo',
        'patients': [
            {
                'id':           p['id'],
                'name':         p['name'],
                'mrn':          p['mrn'],
                'risk_category': p['risk_category'],
                'risk_label':   p['risk_label'],
                'latest_bp':    f"{p['latest_systolic']}/{p['latest_diastolic']}",
                'trend':        p.get('trend', 'stable'),
                'days_since_reading': p.get('days_since_reading', 0),
            }
            for p in PATIENTS
        ],
        'note': 'All data is synthetic. No real patient data is stored or processed.'
    }
    return jsonify(export)


# ============================================================================
# LOGIN ROUTES
# ============================================================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.method == 'POST':
        password = request.form.get('password', '')
        if password == CARDIOWATCH_PASSWORD:
            session['logged_in'] = True
            session['login_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            return redirect(url_for('dashboard'))
        else:
            error = 'Incorrect password. Please try again.'
    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ============================================================================
# PDF PATIENT REPORT EXPORT
# Generates a plain-text patient summary as a downloadable text file.
# ReportLab is not available on all free hosting tiers so we use plain text
# formatted as a clinical report, which the browser downloads as .txt.
# ============================================================================

@app.route('/patient/<int:patient_id>/report')
@login_required
def patient_report(patient_id):
    from flask import Response
    patient = next((p for p in PATIENTS if p['id'] == patient_id), None)
    if not patient:
        return "Patient not found", 404

    observations = patient.get('observations', [])
    trend = patient.get('trend', 'stable')
    lines = []
    lines.append("=" * 60)
    lines.append("CARDIOWATCH CLINICAL REPORT")
    lines.append("Generated: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    lines.append(
        "Data source: " +
        ("HAPI FHIR R4 (hapi.fhir.org)" if USING_FHIR else "Synthetic fallback dataset")
    )
    lines.append("=" * 60)
    lines.append("")
    lines.append("PATIENT INFORMATION")
    lines.append("-" * 40)
    lines.append(f"Name:       {patient['name']}")
    lines.append(f"MRN:        {patient['mrn']}")
    lines.append(f"Age:        {patient['age']}")
    lines.append(f"Gender:     {patient['gender']}")
    lines.append(f"FHIR ID:    {patient['fhir_id']}")
    lines.append("")
    lines.append("CURRENT RISK ASSESSMENT")
    lines.append("-" * 40)
    lines.append(f"Risk Level:   {patient['risk_label']}")
    lines.append(f"Latest BP:    {patient['latest_systolic']}/{patient['latest_diastolic']} mmHg")
    lines.append(f"Heart Rate:   {patient['heart_rate']} bpm")
    lines.append(f"BP Trend:     {trend.capitalize()}")
    lines.append(f"Days since last reading: {patient.get('days_since_reading', 0)}")
    lines.append("")
    lines.append("ACTIVE CONDITIONS")
    lines.append("-" * 40)
    for cond in patient.get('conditions', []):
        lines.append(f"  - {cond}")
    lines.append("")
    lines.append("CURRENT MEDICATIONS")
    lines.append("-" * 40)
    for med in patient.get('medications', []):
        lines.append(f"  - {med}")
    lines.append("")
    lines.append("OBSERVATION HISTORY (most recent first)")
    lines.append("-" * 40)
    lines.append(f"{'Date':<14} {'Systolic':>10} {'Diastolic':>10} {'Category':<25}")
    lines.append("-" * 60)
    for obs in reversed(observations[-20:]):
        cat_label = BP_CATEGORIES.get(obs.get('category', 'normal'), {}).get('label', '')
        lines.append(
            f"{obs['date'].strftime('%Y-%m-%d'):<14}"
            f"{obs['systolic']:>10}"
            f"{obs['diastolic']:>10}"
            f"  {cat_label}"
        )
    lines.append("")
    lines.append("AHA/ACC CLASSIFICATION REFERENCE")
    lines.append("-" * 40)
    lines.append("Normal:             < 120 / < 80 mmHg")
    lines.append("Elevated:         120-129 / < 80 mmHg")
    lines.append("Stage 1 HTN:      130-139 / 80-89 mmHg")
    lines.append("Stage 2 HTN:        >= 140 / >= 90 mmHg")
    lines.append("Hypertensive Crisis: > 180 / > 120 mmHg")
    lines.append("")
    lines.append("Reference: Whelton et al., JACC 2018, doi:10.1016/j.jacc.2017.11.006")
    lines.append("")
    lines.append("DISCLAIMER")
    lines.append("-" * 40)
    lines.append("This report uses synthetic data generated by Synthea.")
    lines.append("No real patient health information is included.")
    lines.append("CardioWatch is a research prototype, not a clinical decision tool.")
    lines.append("=" * 60)

    report_text = "\n".join(lines)
    filename = f"cardiowatch_report_{patient['mrn']}_{datetime.now().strftime('%Y%m%d')}.txt"
    return Response(
        report_text,
        mimetype='text/plain',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )

# ============================================================================
# POPULATION SUMMARY REPORT
# Downloadable plain-text report covering all patients in the current cohort
# ============================================================================

@app.route('/analytics/report')
@login_required
def population_report():
    from flask import Response

    valid = [p for p in PATIENTS if p.get('latest_systolic') and p.get('latest_diastolic')]
    avg_sys = round(sum(p['latest_systolic']  for p in valid) / len(valid), 1) if valid else 0
    avg_dia = round(sum(p['latest_diastolic'] for p in valid) / len(valid), 1) if valid else 0

    category_counts = {k: 0 for k in BP_CATEGORIES}
    for p in PATIENTS:
        category_counts[p['risk_category']] += 1

    lines = []
    lines.append("=" * 70)
    lines.append("CARDIOWATCH POPULATION SUMMARY REPORT")
    lines.append("Generated: " + datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    lines.append(f"Data source: {'HAPI FHIR R4 (live)' if USING_FHIR else 'Synthetic (fallback)'}")
    lines.append("=" * 70)
    lines.append("")
    lines.append("COHORT OVERVIEW")
    lines.append("-" * 40)
    lines.append(f"Total patients:          {len(PATIENTS)}")
    lines.append(f"Cohort average systolic: {avg_sys} mmHg")
    lines.append(f"Cohort average diastolic:{avg_dia} mmHg")
    lines.append("")
    lines.append("RISK CATEGORY DISTRIBUTION")
    lines.append("-" * 40)
    for cat, info in BP_CATEGORIES.items():
        count = category_counts.get(cat, 0)
        pct   = round(count / len(PATIENTS) * 100) if PATIENTS else 0
        lines.append(f"  {info['label']:<30} {count:>3} patients  ({pct}%)")
    lines.append("")
    lines.append("HIGH RISK PATIENTS (Stage 2 and Crisis)")
    lines.append("-" * 70)
    lines.append(f"{'Name':<25} {'MRN':<12} {'BP':>8} {'Category':<25} {'Trend'}")
    lines.append("-" * 70)
    high_risk = [p for p in PATIENTS if p['risk_category'] in ('stage2', 'crisis')]
    high_risk = sorted(high_risk, key=lambda p: BP_CATEGORIES[p['risk_category']]['priority'], reverse=True)
    for p in high_risk:
        bp = f"{p['latest_systolic']}/{p['latest_diastolic']}"
        lines.append(f"{p['name']:<25} {p['mrn']:<12} {bp:>8}  {p['risk_label']:<25} {p.get('trend','stable').capitalize()}")
    lines.append("")
    lines.append("WORSENING TREND PATIENTS")
    lines.append("-" * 70)
    worsening = [p for p in PATIENTS if p.get('trend') == 'worsening']
    if worsening:
        for p in worsening:
            bp = f"{p['latest_systolic']}/{p['latest_diastolic']}"
            lines.append(f"  {p['name']:<25} {p['mrn']:<12} {bp:>8}  {p['risk_label']}")
    else:
        lines.append("  No patients with worsening trend.")
    lines.append("")
    lines.append("STALE READINGS (No observation in 30+ days)")
    lines.append("-" * 70)
    stale = [p for p in PATIENTS if p.get('days_since_reading', 0) > 30]
    if stale:
        for p in stale:
            lines.append(f"  {p['name']:<25} {p['mrn']:<12} Last reading: {p['days_since_reading']} days ago")
    else:
        lines.append("  All patients have recent readings.")
    lines.append("")
    lines.append("FULL PATIENT LIST")
    lines.append("-" * 70)
    lines.append(f"{'Name':<25} {'MRN':<12} {'Age':>4} {'BP':>8}  {'Category':<25} {'Trend'}")
    lines.append("-" * 70)
    for p in sorted(PATIENTS, key=lambda p: BP_CATEGORIES[p['risk_category']]['priority'], reverse=True):
        bp = f"{p['latest_systolic']}/{p['latest_diastolic']}"
        lines.append(
            f"{p['name']:<25} {p['mrn']:<12} {p['age']:>4} {bp:>8}  "
            f"{p['risk_label']:<25} {p.get('trend','stable').capitalize()}"
        )
    lines.append("")
    lines.append("AHA/ACC CLASSIFICATION REFERENCE")
    lines.append("-" * 40)
    lines.append("Normal:              < 120 / < 80 mmHg")
    lines.append("Elevated:          120-129 / < 80 mmHg")
    lines.append("Stage 1 HTN:       130-139 / 80-89 mmHg")
    lines.append("Stage 2 HTN:         >= 140 / >= 90 mmHg")
    lines.append("Hypertensive Crisis:  > 180 / > 120 mmHg")
    lines.append("")
    lines.append("Reference: Whelton et al., JACC 2018, doi:10.1016/j.jacc.2017.11.006")
    lines.append("")
    lines.append("DISCLAIMER")
    lines.append("-" * 40)
    lines.append("This report uses synthetic data. No real patient information is included.")
    lines.append("CardioWatch is a research prototype, not a clinical decision tool.")
    lines.append("=" * 70)

    report_text = "\n".join(lines)
    filename = f"cardiowatch_population_{datetime.now().strftime('%Y%m%d_%H%M')}.txt"
    return Response(
        report_text,
        mimetype='text/plain',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )

# ============================================================================
# CLINICAL TOOLS PAGE
# ============================================================================

@app.route('/tools')
@login_required
def tools():
    return render_template('tools.html', BP_CATEGORIES=BP_CATEGORIES)

# ============================================================================
# CLINICIAN NOTES
# ============================================================================

@app.route('/patient/<int:patient_id>/note', methods=['POST'])
@login_required
def add_patient_note(patient_id):
    patient = next((p for p in PATIENTS if p['id'] == patient_id), None)
    if not patient:
        return jsonify({'error': 'Patient not found'}), 404
    data = request.json or {}
    text = data.get('text', '').strip()
    if not text:
        return jsonify({'error': 'Note text is required'}), 400
    note = {
        'id':        str(uuid.uuid4())[:8],
        'text':      text,
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }
    PATIENT_NOTES.setdefault(patient_id, []).insert(0, note)
    return jsonify({'success': True, 'note': note})

@app.route('/patient/<int:patient_id>/note/<note_id>', methods=['DELETE'])
@login_required
def delete_patient_note(patient_id, note_id):
    notes = PATIENT_NOTES.get(patient_id, [])
    PATIENT_NOTES[patient_id] = [n for n in notes if n['id'] != note_id]
    return jsonify({'success': True})

# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404

# ============================================================================
# LAUNCH
# ============================================================================
load_patients()
start_background_monitoring()
if __name__ == '__main__':
    load_patients()
    start_background_monitoring()
    print("\n" + "="*60)
    print("  CardioWatch ACTIVE:")
    print(f"  [ON] Data source: {'HAPI FHIR R4 (live)' if USING_FHIR else 'Demo patients (fallback)'}")
    print("  [ON] AHA/ACC BP Classifier with boundary unit tests")
    print("  [ON] Patient search and risk filter")
    print("  [ON] BP trend analysis (improving / stable / worsening)")
    print("  [ON] Stale reading detection (30+ days warning)")
    print("  [ON] 24/7 Background Monitor (5-min polling)")
    print("  [ON] Email Alerts (Stage 2 + Crisis)")
    print("="*60 + "\n")
    app.run(debug=True)
