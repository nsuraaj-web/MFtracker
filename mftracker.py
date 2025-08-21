import streamlit as st
import pandas as pd
import datetime
from supabase import create_client, Client

# -----------------------------
# 1. Supabase Config
# -----------------------------
SUPABASE_URL = st.secrets.get("SUPABASE_URL", None)
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", None)
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# -----------------------------
# 2. Load AMFI data (local CSV)
# -----------------------------
@st.cache_data
def load_amfi_data():
    # Download latest file from https://www.amfiindia.com/spages/NAVAll.txt
    # Assume pre-converted CSV stored in app repo as "amfi_schemes.csv"
    df = pd.read_csv("amfi_schemes.csv")
    df = df.dropna(subset=["Scheme Name"])
    return df

amfi_df = load_amfi_data()
scheme_names = amfi_df["Scheme Name"].unique().tolist()

# -----------------------------
# 3. Supabase helpers
# -----------------------------
def fetch_holdings(user_name: str):
    if not supabase:
        return pd.DataFrame([])
    resp = supabase.table("holdings").select("*").eq("user_name", user_name).execute()
    return pd.DataFrame(resp.data) if resp.data else pd.DataFrame([])

def insert_holding(row: dict):
    if not supabase:
        st.warning("Supabase not configured. Data will only persist in session.")
        return
    supabase.table("holdings").insert(row).execute()

def delete_holding(row_id: str):
    if supabase:
        supabase.table("holdings").delete().eq("id", row_id).execute()

# -----------------------------
# 4. Utility: CAGR
# -----------------------------
def calculate_cagr(start_value, end_value, years):
    if start_value <= 0 or years <= 0:
        return 0
    return ((end_value / start_value) ** (1 / years) - 1) * 100

# -----------------------------
# 5. Streamlit UI
# -----------------------------
st.title("📊 Mutual Fund Tracker")

user_name = st.text_input("Enter your name:", value="Guest")

# -------- Add Holding --------
st.header("➕ Add New Holding")
with st.form("add_form"):
    instrument_name = st.selectbox("Mutual Fund Scheme", scheme_names)
    instrument_type = st.selectbox("Type", ["MF", "SIP", "ETF", "NPS", "Other"])
    purchase_date = st.date_input("Purchase Date", datetime.date.today())
    amount = st.number_input("Investment Amount (₹)", min_value=100.0, step=100.0)
    
    # Lookup NAV from AMFI for default
    nav_row = amfi_df[amfi_df["Scheme Name"] == instrument_name].head(1)
    purchase_nav = float(nav_row["Net Asset Value"].values[0]) if not nav_row.empty else 10.0
    st.write(f"📌 Auto-filled NAV: {purchase_nav}")
    
    units = amount / purchase_nav
    notes = st.text_area("Notes", "")
    
    submitted = st.form_submit_button("Add Holding")
    if submitted:
        row = {
            "user_name": user_name,
            "instrument_name": instrument_name,
            "instrument_type": instrument_type,
            "purchase_date": str(purchase_date),
            "purchase_nav": purchase_nav,
            "units": units,
            "amount": amount,
            "category": nav_row["Scheme Category"].values[0] if not nav_row.empty else "Unknown",
            "crisil_rating": None,
            "notes": notes,
        }
        insert_holding(row)
        st.success("✅ Holding added!")

# -------- Portfolio --------
st.header("📂 My Portfolio")
df = fetch_holdings(user_name)
if df.empty:
    st.info("No holdings yet.")
else:
    # Merge with AMFI NAVs
    merged = df.merge(amfi_df, left_on="instrument_name", right_on="Scheme Name", how="left")
    merged["Current NAV"] = merged["Net Asset Value"].astype(float)
    merged["Current Value"] = merged["Current NAV"] * merged["units"].astype(float)
    merged["Gain/Loss"] = merged["Current Value"] - merged["amount"].astype(float)
    merged["Years"] = (
        (pd.to_datetime("today") - pd.to_datetime(merged["purchase_date"])).dt.days / 365
    )
    merged["CAGR %"] = merged.apply(
        lambda x: calculate_cagr(x["amount"], x["Current Value"], x["Years"]), axis=1
    )
    
    st.dataframe(
        merged[[
            "instrument_name", "category", "purchase_date", "amount", 
            "purchase_nav", "units", "Current NAV", "Current Value", 
            "Gain/Loss", "CAGR %"
        ]]
    )
    
    total_invested = merged["amount"].sum()
    total_value = merged["Current Value"].sum()
    st.metric("💰 Total Invested", f"₹{total_invested:,.0f}")
    st.metric("📈 Current Value", f"₹{total_value:,.0f}")
    st.metric("🔄 Overall Gain/Loss", f"₹{(total_value-total_invested):,.0f}")

# -------- Peer Comparison --------
st.header("📊 Peer Comparison")
if not df.empty:
    selected_fund = st.selectbox("Select Fund for Peer Comparison", df["instrument_name"].unique())
    fund_category = (
        amfi_df.loc[amfi_df["Scheme Name"] == selected_fund, "Scheme Category"].values[0]
    )
    st.write(f"Comparing **{selected_fund}** with peers in **{fund_category}**")
    
    peer_df = amfi_df[amfi_df["Scheme Category"] == fund_category]
    st.dataframe(peer_df[["Scheme Name", "Net Asset Value"]].sort_values("Net Asset Value", ascending=False).head(10))
