# app.py
import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from supabase import create_client, Client
import os

st.set_page_config(page_title="My MF Tracker", layout="wide")

# --- Supabase config (set as Streamlit secrets or env vars) ---
SUPABASE_URL = st.secrets.get("SUPABASE_URL") or os.getenv("SUPABASE_URL")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY") or os.getenv("SUPABASE_KEY")

use_db = bool(SUPABASE_URL and SUPABASE_KEY)

if use_db:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- Helper functions ---
def years_between(d1: date, d2: date) -> float:
    days = (d1 - d2).days
    return days / 365.25

def cagr(start_value, end_value, years):
    if years <= 0 or start_value <= 0:
        return None
    try:
        return (end_value / start_value) ** (1/years) - 1
    except:
        return None

def fetch_holdings_from_db():
    if not use_db:
        return pd.DataFrame()
    resp = supabase.table("holdings").select("*").execute()
    rows = resp.data  # the new client puts rows directly in .data
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # convert types
    df['purchase_date'] = pd.to_datetime(df['purchase_date']).dt.date
    df['purchase_nav'] = pd.to_numeric(df['purchase_nav'])
    df['units'] = pd.to_numeric(df['units'])
    df['amount'] = pd.to_numeric(df['amount'])
    return df

def insert_holding_to_db(row: dict):
    if not use_db:
        st.warning("DB not configured. Entry saved only in session.")
        return None
    resp = supabase.table("holdings").insert(row).execute()
        return resp.data  # will be [] if nothing inserted

# --- UI ---
st.title("📊 Personal Mutual Fund / SIP / ETF Tracker")

# Sidebar: add entry
st.sidebar.header("Add / Save an Entry")
with st.sidebar.form("entry_form"):
    user_name = st.text_input("User name", value="Me")
    instrument_name = st.text_input("Fund/ETF name")
    instrument_type = st.selectbox("Type", ["MF", "SIP", "ETF", "NPS", "Other"])
    purchase_date = st.date_input("Purchase date", value=date.today())
    purchase_nav = st.number_input("Purchase NAV (per unit)", min_value=0.0, format="%.4f")
    units = st.number_input("Units bought", min_value=0.0, format="%.4f")
    amount = st.number_input("Amount invested (₹ or local)", min_value=0.0, format="%.2f")
    category = st.text_input("Category (optional)", value="")
    crisil_rating = st.text_input("CRISIL rating (optional)", value="")
    notes = st.text_area("Notes (optional)", value="")
    submitted = st.form_submit_button("Save entry")
    if submitted:
        row = {
            "user_name": user_name,
            "instrument_name": instrument_name,
            "instrument_type": instrument_type,
            "purchase_date": purchase_date.isoformat(),
            "purchase_nav": float(purchase_nav),
            "units": float(units),
            "amount": float(amount),
            "category": category,
            "crisil_rating": crisil_rating,
            "notes": notes,
            "created_at": datetime.utcnow().isoformat()
        }
        if use_db:
            res = insert_holding_to_db(row)
            if res:
                st.success("Saved to DB.")
        else:
            # keep in session state as fallback
            if "local_holdings" not in st.session_state:
                st.session_state["local_holdings"] = []
            st.session_state["local_holdings"].append(row)
            st.success("Saved locally in this session (no DB configured).")

# Load holdings
if use_db:
    df = fetch_holdings_from_db()
else:
    df = pd.DataFrame(st.session_state.get("local_holdings", []))

# If no records
if df.empty:
    st.info("No holdings yet. Add entries from the sidebar.")
    st.stop()

# Main: portfolio table & calculations
st.header("Portfolio")
st.write(f"Loaded {len(df)} holdings.")

# For each holding allow entering current NAV (manual) and compute metrics
current_navs = {}
st.markdown("### Enter current NAVs (or leave blank to skip a row)")
cols = st.columns((2,1,1,1,1,1))
cols[0].write("Instrument")
cols[1].write("Units")
cols[2].write("Purchase NAV")
cols[3].write("Amount")
cols[4].write("Current NAV")
cols[5].write("Current Value")

metrics = []
for i, row in df.iterrows():
    rcols = st.columns((2,1,1,1,1,1))
    rcols[0].write(f"**{row['instrument_name']}** ({row['instrument_type']})")
    rcols[1].write(f"{row['units']:.4f}")
    rcols[2].write(f"{row['purchase_nav']:.4f}")
    rcols[3].write(f"{row['amount']:.2f}")
    cur_nav = rcols[4].number_input(f"cur_nav_{i}", value=0.0, format="%.4f", key=f"nav_{i}")
    current_navs[i] = cur_nav
    cur_value = cur_nav * row['units'] if cur_nav and row['units'] else 0.0
    rcols[5].write(f"{cur_value:.2f}")
    # compute returns
    today = date.today()
    years = years_between(today, pd.to_datetime(row['purchase_date']).date())
    abs_return_pct = ((cur_value - row['amount']) / row['amount'] * 100) if row['amount'] > 0 else None
    cagr_val = cagr(row['amount'], cur_value, years) if row['amount'] > 0 else None
    metrics.append({
        "idx": i,
        "instrument_name": row['instrument_name'],
        "type": row['instrument_type'],
        "units": row['units'],
        "amount": row['amount'],
        "purchase_date": row['purchase_date'],
        "current_nav": cur_nav,
        "current_value": cur_value,
        "abs_return_pct": abs_return_pct,
        "cagr_pct": (cagr_val * 100) if cagr_val is not None else None,
        "years": years,
        "category": row.get("category", ""),
        "crisil_rating": row.get("crisil_rating", "")
    })

# Build metrics DataFrame
m_df = pd.DataFrame(metrics)
if m_df.empty:
    st.warning("No calculated metrics. Enter current NAVs.")
    st.stop()

# Show table & allow sorting and top performers
st.subheader("Calculated metrics")
sort_by = st.selectbox("Sort by", ["cagr_pct", "abs_return_pct", "current_value"], index=0)
ascending = st.checkbox("Ascending", value=False)
display_df = m_df.copy()
display_df = display_df.sort_values(by=[sort_by], ascending=ascending)
display_df = display_df[["instrument_name","type","category","current_nav","current_value","amount","abs_return_pct","cagr_pct","years","crisil_rating"]]
display_df = display_df.rename(columns={
    "instrument_name":"Instrument",
    "type":"Type",
    "category":"Category",
    "current_nav":"Current NAV",
    "current_value":"Current Value",
    "amount":"Amount Invested",
    "abs_return_pct":"Abs Return (%)",
    "cagr_pct":"CAGR (%)",
    "years":"Holding (yrs)",
    "crisil_rating":"CRISIL"
})
st.dataframe(display_df.style.format({
    "Current NAV":"{:.4f}",
    "Current Value":"{:.2f}",
    "Amount Invested":"{:.2f}",
    "Abs Return (%)":"{:.2f}",
    "CAGR (%)":"{:.2f}",
    "Holding (yrs)":"{:.2f}"
}), height=400)

# Top performer panel
st.subheader("Top performers")
top_k = st.slider("Top K", min_value=1, max_value=min(10, len(display_df)), value=3)
top_by = st.selectbox("Choose metric for top performer", ["CAGR (%)", "Abs Return (%)", "Current Value"])
if top_by == "CAGR (%)":
    top = display_df.sort_values(by="CAGR (%)", ascending=False).head(top_k)
elif top_by == "Abs Return (%)":
    top = display_df.sort_values(by="Abs Return (%)", ascending=False).head(top_k)
else:
    top = display_df.sort_values(by="Current Value", ascending=False).head(top_k)
st.table(top)

# Export options
st.subheader("Export / Backup")
if st.button("Download portfolio CSV"):
    st.download_button("Download CSV", data=display_df.to_csv(index=False), file_name="portfolio.csv")

st.caption("Notes: This app uses manual current NAV entry by default. For true 1–5 year performance, integrate NAV history or upload CSV of historical NAVs. CRISIL ratings are optional manual fields for now.")
