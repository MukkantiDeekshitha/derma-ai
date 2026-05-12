import streamlit as st
import google.generativeai as genai
import json
import os
import sqlite3
import hashlib
import base64
from datetime import datetime
from PIL import Image
import io

# ── Page config ────────────────────────────────────────────
st.set_page_config(
    page_title="DermAI — Skin Intelligence",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ── Custom CSS ─────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600&display=swap');

html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }

.main { background: #0a0d0f; }

.hero-title {
    font-size: 3rem;
    font-weight: 300;
    color: #e8eaed;
    line-height: 1.1;
    margin-bottom: 1rem;
}
.hero-title em { color: #4ade80; font-style: italic; }

.card {
    background: #111518;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 14px;
    padding: 1.5rem;
    margin-bottom: 1rem;
}

.disease-name {
    font-size: 2rem;
    font-weight: 600;
    color: #e8eaed;
    margin: 0.5rem 0;
}

.conf-high   { background: rgba(74,222,128,.15); color: #4ade80; padding: 4px 12px; border-radius: 50px; font-size: 0.8rem; font-weight: 600; }
.conf-medium { background: rgba(251,191,36,.15);  color: #fbbf24; padding: 4px 12px; border-radius: 50px; font-size: 0.8rem; font-weight: 600; }
.conf-low    { background: rgba(248,113,113,.15); color: #f87171; padding: 4px 12px; border-radius: 50px; font-size: 0.8rem; font-weight: 600; }

.result-item {
    padding: 8px 0;
    border-bottom: 1px solid rgba(255,255,255,0.05);
    color: #8b9199;
    font-size: 0.92rem;
}
.result-item::before { content: "→ "; color: #4ade80; }

.disclaimer {
    background: rgba(251,191,36,.06);
    border: 1px solid rgba(251,191,36,.2);
    border-radius: 8px;
    padding: 1rem;
    font-size: 0.85rem;
    color: #8b9199;
    margin-top: 1rem;
}

.stButton > button {
    background: #4ade80 !important;
    color: #000 !important;
    border: none !important;
    border-radius: 50px !important;
    font-weight: 600 !important;
    padding: 0.5rem 2rem !important;
    width: 100%;
}
.stButton > button:hover { background: #22c55e !important; }

div[data-testid="stSidebarNav"] { display: none; }

.stTextArea textarea {
    background: #111518 !important;
    color: #e8eaed !important;
    border: 1px solid rgba(255,255,255,0.12) !important;
    border-radius: 8px !important;
}

.history-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 0.8rem 1rem;
    background: #111518;
    border: 1px solid rgba(255,255,255,0.07);
    border-radius: 8px;
    margin-bottom: 0.5rem;
}
</style>
""", unsafe_allow_html=True)

# ── Database ────────────────────────────────────────────────
DB_PATH = "dermai.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        type TEXT NOT NULL,
        input_data TEXT,
        disease_name TEXT,
        confidence TEXT,
        description TEXT,
        treatments TEXT,
        remedies TEXT,
        precautions TEXT,
        when_to_see TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    conn.commit()
    conn.close()

init_db()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def create_user(username, email, password):
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
                  (username, email, hash_password(password)))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False

def get_user(identifier, password):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE (username=? OR email=?) AND password_hash=?",
              (identifier, identifier, hash_password(password)))
    user = c.fetchone()
    conn.close()
    return user

def save_analysis(user_id, analysis_type, input_data, result):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO analyses
        (user_id, type, input_data, disease_name, confidence, description, treatments, remedies, precautions, when_to_see)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (user_id, analysis_type, input_data,
         result.get('disease_name', ''),
         result.get('confidence', ''),
         result.get('description', ''),
         json.dumps(result.get('treatments', [])),
         json.dumps(result.get('remedies', [])),
         json.dumps(result.get('precautions', [])),
         result.get('when_to_see_doctor', '')))
    conn.commit()
    conn.close()

def get_history(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT * FROM analyses WHERE user_id=? ORDER BY created_at DESC", (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def delete_analysis(analysis_id, user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM analyses WHERE id=? AND user_id=?", (analysis_id, user_id))
    conn.commit()
    conn.close()

# ── AI ──────────────────────────────────────────────────────
PROMPT = """You are DermAI, an expert dermatology AI assistant.
Analyze the provided input and return ONLY a valid JSON object (no markdown, no extra text) with this exact structure:
{
  "disease_name": "Name of the most likely skin condition",
  "confidence": "High",
  "description": "Clear 2-3 sentence description",
  "treatments": ["Treatment 1", "Treatment 2", "Treatment 3"],
  "remedies": ["Home remedy 1", "Home remedy 2", "Home remedy 3"],
  "precautions": ["Precaution 1", "Precaution 2", "Precaution 3"],
  "when_to_see_doctor": "Guidance on when to see a doctor"
}
confidence must be exactly: High, Medium, or Low"""

def parse_response(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.split('\n')
        text = '\n'.join(lines[1:-1]) if lines[-1].strip() == '```' else '\n'.join(lines[1:])
    start = text.find('{')
    end = text.rfind('}') + 1
    if start != -1 and end > start:
        text = text[start:end]
    return json.loads(text)

def get_model():
    api_key = os.environ.get('GEMINI_API_KEY') or st.secrets.get('GEMINI_API_KEY', '')
    if not api_key:
        st.error("GEMINI_API_KEY not set. Add it in Streamlit secrets.")
        st.stop()
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(
        model_name="gemini-2.0-flash",
        generation_config=genai.types.GenerationConfig(max_output_tokens=1024, temperature=0.3)
    )

def analyze_image(image_bytes, mime_type):
    model = get_model()
    image_part = {"mime_type": mime_type, "data": image_bytes}
    prompt = PROMPT + "\n\nAnalyze this skin image. Return ONLY the JSON."
    response = model.generate_content([prompt, image_part])
    return parse_response(response.text)

def analyze_symptoms(symptoms_text):
    model = get_model()
    prompt = PROMPT + f"\n\nSkin symptoms: {symptoms_text}\n\nReturn ONLY the JSON."
    response = model.generate_content(prompt)
    return parse_response(response.text)

# ── Session state ───────────────────────────────────────────
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'user' not in st.session_state:
    st.session_state.user = None
if 'page' not in st.session_state:
    st.session_state.page = 'home'
if 'last_result' not in st.session_state:
    st.session_state.last_result = None

# ── Sidebar ─────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🔬 DermAI")
    st.markdown("---")

    if st.session_state.logged_in:
        st.markdown(f"👤 **{st.session_state.user[1]}**")
        st.markdown("---")
        if st.button("🏠 Dashboard"):   st.session_state.page = 'dashboard'
        if st.button("📸 Image Scan"):  st.session_state.page = 'image'
        if st.button("📝 Symptoms"):    st.session_state.page = 'symptoms'
        if st.button("📂 History"):     st.session_state.page = 'history'
        st.markdown("---")
        if st.button("🚪 Sign Out"):
            st.session_state.logged_in = False
            st.session_state.user = None
            st.session_state.page = 'home'
            st.rerun()
    else:
        if st.button("🏠 Home"):     st.session_state.page = 'home'
        if st.button("🔑 Sign In"):  st.session_state.page = 'login'
        if st.button("✨ Sign Up"):  st.session_state.page = 'register'

    st.markdown("---")
    st.markdown("<small style='color:#8b9199'>AI-powered skin analysis<br>Not a medical diagnosis</small>", unsafe_allow_html=True)

# ── Pages ───────────────────────────────────────────────────

# HOME
if st.session_state.page == 'home':
    st.markdown('<h1 class="hero-title">Know Your <em>Skin.</em><br>Instantly.</h1>', unsafe_allow_html=True)
    st.markdown("Upload a photo or describe your symptoms. DermAI analyses your skin condition and provides personalised treatment guidance in seconds.")
    st.markdown("")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("📸 Scan an Image →"):
            st.session_state.page = 'image' if st.session_state.logged_in else 'login'
            st.rerun()
    with col2:
        if st.button("📝 Check Symptoms →"):
            st.session_state.page = 'symptoms' if st.session_state.logged_in else 'login'
            st.rerun()

    st.markdown("---")
    st.markdown("### How it works")
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.markdown("**🔬 AI Image Analysis**\nUpload a photo for instant analysis")
    with c2: st.markdown("**📋 Symptom Checker**\nDescribe symptoms in plain language")
    with c3: st.markdown("**💊 Treatment Plans**\nGet treatments, remedies & precautions")
    with c4: st.markdown("**📂 History**\nTrack your skin health over time")

# LOGIN
elif st.session_state.page == 'login':
    st.markdown("## Sign In")
    with st.form("login_form"):
        identifier = st.text_input("Username or Email")
        password   = st.text_input("Password", type="password")
        submitted  = st.form_submit_button("Sign In →")
        if submitted:
            user = get_user(identifier, password)
            if user:
                st.session_state.logged_in = True
                st.session_state.user = user
                st.session_state.page = 'dashboard'
                st.success(f"Welcome back, {user[1]}!")
                st.rerun()
            else:
                st.error("Invalid credentials. Please try again.")
    st.markdown("Don't have an account?")
    if st.button("Create account →"):
        st.session_state.page = 'register'
        st.rerun()

# REGISTER
elif st.session_state.page == 'register':
    st.markdown("## Create Account")
    with st.form("register_form"):
        username = st.text_input("Username")
        email    = st.text_input("Email")
        password = st.text_input("Password", type="password")
        confirm  = st.text_input("Confirm Password", type="password")
        submitted = st.form_submit_button("Create Account →")
        if submitted:
            if not all([username, email, password, confirm]):
                st.error("All fields are required.")
            elif password != confirm:
                st.error("Passwords do not match.")
            elif len(password) < 6:
                st.error("Password must be at least 6 characters.")
            else:
                if create_user(username, email, password):
                    user = get_user(username, password)
                    st.session_state.logged_in = True
                    st.session_state.user = user
                    st.session_state.page = 'dashboard'
                    st.success("Welcome to DermAI!")
                    st.rerun()
                else:
                    st.error("Username or email already exists.")
    if st.button("Already have an account? Sign in"):
        st.session_state.page = 'login'
        st.rerun()

# DASHBOARD
elif st.session_state.page == 'dashboard':
    if not st.session_state.logged_in:
        st.session_state.page = 'login'; st.rerun()

    user = st.session_state.user
    st.markdown(f"## Welcome back, *{user[1]}* 👋")

    history = get_history(user[0])
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Analyses", len(history))
    with col2:
        st.metric("Recent Scans", min(len(history), 5))
    with col3:
        st.metric("AI Model", "Gemini")

    st.markdown("---")
    st.markdown("### What would you like to do?")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**📸 Image Scan**\nUpload a photo for AI analysis")
        if st.button("Scan Image →"):
            st.session_state.page = 'image'; st.rerun()
    with c2:
        st.markdown("**📝 Symptom Checker**\nDescribe your symptoms")
        if st.button("Check Symptoms →"):
            st.session_state.page = 'symptoms'; st.rerun()
    with c3:
        st.markdown("**📂 My History**\nView past analyses")
        if st.button("View History →"):
            st.session_state.page = 'history'; st.rerun()

    if history:
        st.markdown("---")
        st.markdown("### Recent Analyses")
        for row in history[:5]:
            col1, col2, col3, col4 = st.columns([3,1,1,1])
            with col1: st.markdown(f"**{row[4]}**")
            with col2: st.markdown(f"`{row[2]}`")
            with col3: st.markdown(f"{row[5]}")
            with col4: st.markdown(f"{row[11][:10]}")

# IMAGE SCAN
elif st.session_state.page == 'image':
    if not st.session_state.logged_in:
        st.session_state.page = 'login'; st.rerun()

    st.markdown("## 📸 Image Scan")
    st.markdown("Upload a clear, well-lit photo of the affected skin area.")

    uploaded = st.file_uploader("Choose an image", type=['jpg','jpeg','png','webp'])

    if uploaded:
        image = Image.open(uploaded)
        st.image(image, caption="Uploaded image", use_column_width=True)

        st.markdown("""
        **Tips for best results:**
        - Good lighting — natural or bright indoor light
        - Close-up shot of the affected area
        - No filters — original unedited photos
        """)

        st.markdown('<div class="disclaimer">⚠️ <strong>Medical Disclaimer:</strong> DermAI is for informational purposes only. Always consult a qualified dermatologist.</div>', unsafe_allow_html=True)

        if st.button("🔬 Analyse Image →"):
            with st.spinner("Analysing your skin image..."):
                try:
                    image_bytes = uploaded.getvalue()
                    ext = uploaded.name.rsplit('.', 1)[-1].lower()
                    mime_map = {'jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png','webp':'image/webp'}
                    mime_type = mime_map.get(ext, 'image/jpeg')
                    result = analyze_image(image_bytes, mime_type)
                    save_analysis(st.session_state.user[0], 'image', uploaded.name, result)
                    st.session_state.last_result = result
                    st.session_state.page = 'result'
                    st.rerun()
                except Exception as e:
                    st.error(f"Analysis failed: {str(e)}")

# SYMPTOMS
elif st.session_state.page == 'symptoms':
    if not st.session_state.logged_in:
        st.session_state.page = 'login'; st.rerun()

    st.markdown("## 📝 Symptom Checker")
    st.markdown("Describe your skin symptoms in plain language.")

    symptoms = st.text_area(
        "Describe your symptoms",
        placeholder="Example: I have red, itchy patches on my forearm that appeared 3 days ago. The skin feels dry and slightly raised...",
        height=180
    )
    st.markdown(f"*{len(symptoms)} characters*")

    st.markdown("**Include details like:** location, duration, appearance, sensation, triggers")

    st.markdown('<div class="disclaimer">⚠️ <strong>Medical Disclaimer:</strong> DermAI is for informational purposes only. Always consult a qualified dermatologist.</div>', unsafe_allow_html=True)

    if st.button("🔬 Analyse Symptoms →"):
        if len(symptoms.strip()) < 10:
            st.error("Please describe your symptoms in more detail (at least 10 characters).")
        else:
            with st.spinner("Analysing your symptoms..."):
                try:
                    result = analyze_symptoms(symptoms)
                    save_analysis(st.session_state.user[0], 'symptoms', symptoms, result)
                    st.session_state.last_result = result
                    st.session_state.page = 'result'
                    st.rerun()
                except Exception as e:
                    st.error(f"Analysis failed: {str(e)}")

# RESULT
elif st.session_state.page == 'result':
    if not st.session_state.logged_in:
        st.session_state.page = 'login'; st.rerun()

    result = st.session_state.last_result
    if not result:
        st.session_state.page = 'dashboard'; st.rerun()

    st.markdown("## 🔬 Analysis Result")

    conf = result.get('confidence', 'N/A').lower()
    conf_class = f"conf-{conf}" if conf in ['high','medium','low'] else ''

    st.markdown(f"""
    <div class="card">
        <div class="disease-name">{result.get('disease_name', 'Unknown')}</div>
        <span class="{conf_class}">{'●'} {result.get('confidence', 'N/A')} Confidence</span>
        <p style="color:#8b9199; margin-top:1rem;">{result.get('description', '')}</p>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown("### 💊 Medical Treatments")
        for t in result.get('treatments', []):
            st.markdown(f'<div class="result-item">{t}</div>', unsafe_allow_html=True)

    with col2:
        st.markdown("### 🌿 Home Remedies")
        for r in result.get('remedies', []):
            st.markdown(f'<div class="result-item">{r}</div>', unsafe_allow_html=True)

    with col3:
        st.markdown("### 🛡️ Precautions")
        for p in result.get('precautions', []):
            st.markdown(f'<div class="result-item">{p}</div>', unsafe_allow_html=True)

    if result.get('when_to_see_doctor'):
        st.markdown("### 🏥 When to See a Doctor")
        st.info(result.get('when_to_see_doctor'))

    st.markdown('<div class="disclaimer">⚠️ <strong>Medical Disclaimer:</strong> This AI analysis is for informational purposes only and is not a substitute for professional medical advice, diagnosis, or treatment.</div>', unsafe_allow_html=True)

    st.markdown("---")
    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("📸 New Image Scan"):
            st.session_state.page = 'image'; st.rerun()
    with col2:
        if st.button("📝 Check Symptoms"):
            st.session_state.page = 'symptoms'; st.rerun()
    with col3:
        if st.button("📂 View History"):
            st.session_state.page = 'history'; st.rerun()

# HISTORY
elif st.session_state.page == 'history':
    if not st.session_state.logged_in:
        st.session_state.page = 'login'; st.rerun()

    st.markdown("## 📂 Analysis History")
    history = get_history(st.session_state.user[0])

    if not history:
        st.info("No analyses yet. Run your first scan!")
        if st.button("Start First Scan →"):
            st.session_state.page = 'image'; st.rerun()
    else:
        for row in history:
            col1, col2, col3, col4, col5 = st.columns([3,1,1,2,1])
            with col1: st.markdown(f"**{row[4]}**")
            with col2: st.markdown(f"`{row[2]}`")
            with col3: st.markdown(row[5] or "N/A")
            with col4: st.markdown(row[11][:16] if row[11] else "")
            with col5:
                if st.button("🗑️", key=f"del_{row[0]}"):
                    delete_analysis(row[0], st.session_state.user[0])
                    st.rerun()
