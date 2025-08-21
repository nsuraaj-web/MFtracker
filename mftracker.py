import streamlit as st
import pandas as pd
from supabase import create_client, Client
from datetime import date, datetime
import uuid
import os

st.set_page_config(page_title="MF Tracker Basic", layout="wide")

# --------------------------
# 1) Supabase setup
# --------------------------
SUPABASE_URL = st.secrets.get("SUPABASE_URL")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY")
TABLE_NAME = "mf_holdings"
LOCAL_CSV = "mf_holdings.csv"

use_db = False
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        use_db = True
    except Exception as e:
        st.warning("Could not connect to Supabase, using CSV fallback.")

# --------------------------
# 2) CSV helper functions
# --------------------------
def fetch_all_records():
    cols = ["id","user_name","mf_name","purchase_date","purchase_nav","units","amount","notes","created_at"]
    if use_db:
        try:
            resp = supabase.table(TABLE_NAME).select("*").execute()
            df = pd.DataFrame(resp.data or [])
            for c in cols:
                if c not in df.columns:
                    df[c] = None
            return df[cols]
        except:
            return pd.DataFrame(columns=cols)
    else:
        if os.path.exists(LOCAL_CSV):
            df = pd.read_csv(LOCAL_CSV)
            for c in cols:
                if c not in df.columns:
                    df[c] = None
            return df[cols]
        else:
            return pd.DataFrame(columns=cols)

def save_csv(df):
    df.to_csv(LOCAL_CSV, index=False)

def insert_record(record):
    record = dict(record)
    record["id"] = str(uuid.uuid4())
    record["created_at"] = datetime.utcnow().isoformat()
    if use_db:
        supabase.table(TABLE_NAME).insert(record).execute()
    df = fetch_all_records()
    df = pd.concat([df, pd.DataFrame([record])], ignore_index=True)
    save_csv(df)

def update_record(record_id, updates: dict):
    df = fetch_all_records()
    for col, val in updates.items():
        df.loc[df["id"] == record_id, col] = val
    save_csv(df)
    if use_db:
        supabase.table(TABLE_NAME).update(updates).eq("id", record_id).execute()

def delete_record(record_id):
    df = fetch_all_records()
    df = df[df["id"] != record_id]
    save_csv(df)
    if use_db:
        supabase.table(TABLE_NAME).delete().eq("id", record_id).execute()

def compute_units_amount(amount, units, nav):
    if amount > 0 and (units is None or units == 0):
        units = amount / nav
    elif units > 0 and (amount is None or amount == 0):
        amount = units * nav
    return amount, units

# --------------------------
# 3) Load AMFI schemes CSV
# --------------------------
@st.cache_data
def load_amfi_data():
    df = pd.read_csv("amfi_schemes.csv", sep=";", encoding="utf-8", on_bad_lines="skip")
    df.columns = df.columns.str.strip()
    df["Scheme Name"] = df["Scheme Name"].astype(str)
    df["Net Asset Value"] = pd.to_numeric(df["Net Asset Value"].astype(str).str.replace(",",""), errors="coerce")
    return df

amfi_df = load_amfi_data()
scheme_names = amfi_df["Scheme Name"].tolist()

# --------------------------
# 4) Streamlit UI
# --------------------------
st.title("📊 MF Tracker Basic with Autocomplete")

# --- User selection
user_name = st.text_input("Enter your name:", value="Guest")

# --- Add Holding Form
st.header("➕ Add / Update Holding")
with st.form("holding_form"):
    # Autocomplete MF name
    mf_name = st.selectbox("Mutual Fund Name", options=scheme_names)
    nav_row = amfi_df[amfi_df["Scheme Name"] == mf_name]
    default_nav = float(nav_row["Net Asset Value"].values[0]) if not nav_row.empty else 0.0

    purchase_date = st.date_input("Purchase Date", value=date.today())
    purchase_nav = st.number_input("Purchase NAV", value=default_nav, format="%.4f")
    
    col1, col2 = st.columns(2)
    with col1:
        amount = st.number_input("Amount (₹)", min_value=0.0, format="%.2f", value=0.0)
    with col2:
        units = st.number_input("Units", min_value=0.0, format="%.6f", value=0.0)
    notes = st.text_area("Notes (optional)")

    # auto-calc
    amount, units = compute_units_amount(amount, units, purchase_nav)
    st.info(f"Preview — Amount: ₹{amount:.2f} | Units: {units:.6f}")

    if st.form_submit_button("Save Holding"):
        if not mf_name or purchase_nav <= 0 or (amount <= 0 and units <= 0):
            st.error("Provide MF name, purchase NAV, and either amount or units")
        else:
            insert_record({
                "user_name": user_name,
                "mf_name": mf_name,
                "purchase_date": str(purchase_date),
                "purchase_nav": purchase_nav,
                "units": units,
                "amount": amount,
                "notes": notes
            })
            st.success("✅ Holding saved!")

# --- Show Holdings
st.header(f"📂 Holdings for {user_name}")
df_user = fetch_all_records()
df_user = df_user[df_user["user_name"] == user_name]

if df_user.empty:
    st.info("No holdings yet.")
else:
    st.dataframe(df_user[["mf_name","purchase_date","purchase_nav","units","amount","notes"]])

    # --- Update/Delete selection
    st.write("Select a holding to update or delete:")
    selected_row = st.selectbox("Pick holding", df_user["mf_name"] + " | " + df_user["purchase_date"])
    if selected_row:
        idx = df_user.index[df_user["mf_name"] + " | " + df_user["purchase_date"] == selected_row][0]
        row_data = df_user.loc[idx]
        st.write("Update values:")
        col1, col2 = st.columns(2)
        with col1:
            amount_update = st.number_input("Amount", value=float(row_data["amount"]))
        with col2:
            units_update = st.number_input("Units", value=float(row_data["units"]))
        purchase_nav_update = st.number_input("Purchase NAV", value=float(row_data["purchase_nav"]))
        notes_update = st.text_area("Notes", value=row_data["notes"])
        col3, col4 = st.columns(2)
        with col3:
            if st.button("Update this holding"):
                amount_final, units_final = compute_units_amount(amount_update, units_update, purchase_nav_update)
                update_record(row_data["id"], {
                    "amount": amount_final, "units": units_final,
                    "purchase_nav": purchase_nav_update, "notes": notes_update
                })
                st.success("✅ Updated!")
        with col4:
            if st.button("Delete this holding"):
                delete_record(row_data["id"])
                st.success("❌ Deleted!")
