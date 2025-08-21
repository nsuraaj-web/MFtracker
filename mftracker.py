import streamlit as st
import pandas as pd
from datetime import date
import uuid
from supabase import create_client, Client
import os

st.set_page_config(page_title="MF Tracker", layout="wide")

# -------------------------
# 1) Supabase config
# -------------------------
SUPABASE_URL = st.secrets.get("SUPABASE_URL")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY")
TABLE_NAME = "mf_transactions"
LOCAL_CSV = "holdings.csv"

use_db = False
supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        use_db = True
        st.info("✅ Supabase connected")
    except Exception as e:
        st.warning(f"Could not connect to Supabase: {e}")

# -------------------------
# 2) CSV helpers
# -------------------------
def fetch_all_records():
    df = pd.DataFrame(columns=["id","user_name","mf_name","purchase_date","purchase_nav","units","amount","notes"])
    if use_db:
        try:
            resp = supabase.table(TABLE_NAME).select("*").execute()
            df = pd.DataFrame(resp.data or [])
            for col in ["id","user_name","mf_name","purchase_date","purchase_nav","units","amount","notes"]:
                if col not in df.columns:
                    df[col] = None
        except Exception as e:
            st.warning(f"Supabase fetch failed, using CSV: {e}")
    if df.empty and os.path.exists(LOCAL_CSV):
        df_csv = pd.read_csv(LOCAL_CSV)
        for col in ["id","user_name","mf_name","purchase_date","purchase_nav","units","amount","notes"]:
            if col not in df_csv.columns:
                df_csv[col] = None
        df = df_csv
    return df

def save_csv(df):
    df.to_csv(LOCAL_CSV, index=False)

def insert_record(record):
    record["id"] = record.get("id", str(uuid.uuid4()))
    if use_db:
        try:
            supabase.table(TABLE_NAME).insert(record).execute()
        except Exception as e:
            st.warning(f"Supabase insert failed: {e}")
    # always save to CSV
    df = fetch_all_records()
    df = pd.concat([df, pd.DataFrame([record])], ignore_index=True)
    save_csv(df)

def update_record(record_id, updates: dict):
    if use_db:
        try:
            supabase.table(TABLE_NAME).update(updates).eq("id", record_id).execute()
        except Exception as e:
            st.warning(f"Supabase update failed: {e}")
    df = fetch_all_records()
    for col, val in updates.items():
        df.loc[df["id"]==record_id, col] = val
    save_csv(df)

def delete_record(record_id):
    if use_db:
        try:
            supabase.table(TABLE_NAME).delete().eq("id", record_id).execute()
        except Exception as e:
            st.warning(f"Supabase delete failed: {e}")
    df = fetch_all_records()
    df = df[df["id"] != record_id]
    save_csv(df)

# -------------------------
# 3) Compute amount/units
# -------------------------
def compute_amount_units(amount, units, purchase_nav):
    if purchase_nav and amount and not units:
        units = amount / purchase_nav
    elif purchase_nav and units and not amount:
        amount = units * purchase_nav
    return float(amount or 0.0), float(units or 0.0)

# -------------------------
# 4) Load AMFI schemes for autocomplete
# -------------------------
amfi_df = pd.read_csv("amfi_schemes.csv", sep=';', encoding='utf-8', on_bad_lines='skip')
amfi_df.columns = amfi_df.columns.str.strip()
scheme_names = amfi_df["Scheme Name"].unique().tolist()

# -------------------------
# 5) Sync CSV -> Supabase on start
# -------------------------
if use_db:
    st.info("Syncing CSV to Supabase...")
    df_csv = pd.DataFrame()
    if os.path.exists(LOCAL_CSV):
        df_csv = pd.read_csv(LOCAL_CSV)
    resp = supabase.table(TABLE_NAME).select("*").execute()
    db_df = pd.DataFrame(resp.data or [])

    if "id" not in df_csv.columns:
        df_csv["id"] = [str(uuid.uuid4()) for _ in range(len(df_csv))]
    if "id" not in db_df.columns:
        db_df["id"] = [str(uuid.uuid4()) for _ in range(len(db_df))]

    # insert CSV-only rows to Supabase
    csv_only = df_csv[~df_csv["id"].isin(db_df["id"])]
    for _, r in csv_only.iterrows():
        try:
            supabase.table(TABLE_NAME).insert(r.to_dict()).execute()
        except Exception as e:
            st.warning(f"Supabase insert during sync failed: {e}")

    # update CSV with full DB data
    resp = supabase.table(TABLE_NAME).select("*").execute()
    db_df = pd.DataFrame(resp.data or [])
    save_csv(db_df)
    st.success("✅ CSV ↔ Supabase sync complete")

# -------------------------
# 6) Streamlit UI
# -------------------------
st.title("📊 MF Tracker - Supabase + CSV backup")

# User selection
user_name = st.text_input("Enter user name", value="Guest")

# ---- Add Holding ----
with st.form("add_holding_form"):
    mf_name = st.selectbox("Mutual Fund Name", scheme_names)
    purchase_date = st.date_input("Purchase Date", value=date.today())
    purchase_nav = st.number_input("Purchase NAV", min_value=0.0, format="%.4f")
    
    col1, col2 = st.columns(2)
    with col1:
        amount_input = st.number_input("Amount (₹)", min_value=0.0, format="%.2f", value=0.0)
    with col2:
        units_input = st.number_input("Units", min_value=0.0, format="%.6f", value=0.0)
    
    notes = st.text_area("Notes (optional)")

    amount, units = compute_amount_units(amount_input, units_input, purchase_nav)
    st.info(f"Preview — Amount: ₹{amount:.2f} | Units: {units:.6f}")

    if st.form_submit_button("Save Holding"):
        record = {
            "id": str(uuid.uuid4()),
            "user_name": user_name,
            "mf_name": mf_name,
            "purchase_date": str(purchase_date),
            "purchase_nav": purchase_nav,
            "units": units,
            "amount": amount,
            "notes": notes
        }
        insert_record(record)
        st.success("✅ Holding saved!")

# ---- Show Holdings ----
st.header(f"📂 Holdings for {user_name}")
df_user = fetch_all_records()
df_user = df_user[df_user["user_name"] == user_name]

if df_user.empty:
    st.info("No holdings yet.")
else:
    for idx, row in df_user.iterrows():
        st.write(f"**{row['mf_name']} | {row['purchase_date']}**")
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            amount_update = st.number_input(f"Amount {row['id']}", value=float(row["amount"]), key=f"amt_{row['id']}")
        with col2:
            units_update = st.number_input(f"Units {row['id']}", value=float(row["units"]), key=f"units_{row['id']}")
        with col3:
            purchase_nav_update = st.number_input(f"Purchase NAV {row['id']}", value=float(row["purchase_nav"]), format="%.4f", key=f"nav_{row['id']}")
        with col4:
            notes_update = st.text_input(f"Notes {row['id']}", value=row.get("notes",""), key=f"notes_{row['id']}")

        col5, col6 = st.columns(2)
        with col5:
            if st.button("Update", key=f"update_{row['id']}"):
                new_amount, new_units = compute_amount_units(amount_update, units_update, purchase_nav_update)
                update_record(row['id'], {
                    "amount": new_amount,
                    "units": new_units,
                    "purchase_nav": purchase_nav_update,
                    "notes": notes_update
                })
                st.experimental_rerun()
        with col6:
            if st.button("Delete", key=f"delete_{row['id']}"):
                delete_record(row['id'])
                st.experimental_rerun()
