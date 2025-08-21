import streamlit as st
import pandas as pd
import requests
import datetime
from supabase import create_client, Client

# -------------------------
# CONFIG
# -------------------------
st.set_page_config(page_title="MF & ETF Tracker", layout="wide")

# Supabase connection (Optional DB persistence)
use_db = False
try:
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    supabase: Client = create_client(url, key)
    use_db = True
except Exception:
    st.warning("Supabase not configured. Data will only persist in session.")

# -------------------------
# Utility Functions
# -------------------------

@st.cache_data(ttl=86400)  # cache for 1 day
def fetch_mf_list():
    """Fetch all MF schemes from AMFI India."""
    url = "https://www.amfiindia.com/spages/NAVAll.txt"
    resp = requests.get(url)
    lines = resp.text.splitlines()
    funds = []
    for line in lines:
        parts = line.split(";")
        if len(parts) >= 6 and parts[0].isdigit():
            funds.append({"scheme_code": parts[0], "scheme_name": parts[3]})
    return pd.DataFrame(funds)

def fetch_latest_nav(scheme_code):
    """Fetch latest NAV for a given MF code from AMFI."""
    url = f"https://api.mfapi.in/mf/{scheme_code}"
    resp = requests.get(url)
    if resp.status_code == 200:
        data = resp.json()
        if "data" in data and len(data["data"]) > 0:
            return float(data["data"][0]["nav"])
    return None

def fetch_captnemo_data(scheme_code):
    """Optional: fetch extra performance data via captnemo API."""
    try:
        url = f"https://api.mfapi.in/mf/{scheme_code}"
        resp = requests.get(url)
        if resp.status_code == 200:
            data = resp.json()
            meta = data.get("meta", {})
            return {
                "fund_house": meta.get("fund_house"),
                "scheme_category": meta.get("scheme_category"),
                "scheme_type": meta.get("scheme_type"),
            }
    except Exception:
        return {}
    return {}

# -------------------------
# DB Operations
# -------------------------

def insert_holding_to_db(row: dict):
    if not use_db:
        return None
    resp = supabase.table("holdings").insert(row).execute()
    return resp.data

def fetch_holdings_from_db():
    if not use_db:
        return pd.DataFrame()
    resp = supabase.table("holdings").select("*").execute()
    rows = resp.data
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df['purchase_date'] = pd.to_datetime(df['purchase_date']).dt.date
    df['purchase_nav'] = pd.to_numeric(df['purchase_nav'])
    df['units'] = pd.to_numeric(df['units'])
    df['amount'] = pd.to_numeric(df['amount'])
    return df

# -------------------------
# MAIN APP
# -------------------------
st.title("📊 Mutual Fund / ETF / SIP / NPS Tracker")

mf_list = fetch_mf_list()

with st.form("entry_form"):
    user_name = st.text_input("User Name", "Suraaj")

    # Autocomplete Fund Selection
    mf_name = st.selectbox(
        "Mutual Fund Scheme",
        options=mf_list["scheme_name"].unique(),
        index=None,
        placeholder="Search & select fund..."
    )

    scheme_code = None
    if mf_name:
        scheme_code = mf_list.loc[mf_list["scheme_name"] == mf_name, "scheme_code"].iloc[0]

    purchase_date = st.date_input("Purchase Date", datetime.date.today())
    purchase_nav = st.number_input("Purchase NAV", min_value=0.0, step=0.01)
    units = st.number_input("Units", min_value=0.0, step=0.01)
    amount = purchase_nav * units

    submitted = st.form_submit_button("Add Holding")

    if submitted and mf_name and scheme_code:
        row = {
            "user_name": user_name,
            "scheme_code": scheme_code,
            "scheme_name": mf_name,
            "purchase_date": str(purchase_date),
            "purchase_nav": float(purchase_nav),
            "units": float(units),
            "amount": float(amount),
        }
        insert_holding_to_db(row)
        st.success(f"Added {mf_name} to holdings.")

# Load holdings
if use_db:
    df = fetch_holdings_from_db()
else:
    if "holdings" not in st
