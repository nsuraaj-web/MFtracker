import streamlit as st
import pandas as pd
import uuid
from datetime import datetime
from supabase import create_client, Client
import os

# -----------------------------
# 1. Supabase Config
# -----------------------------
SUPABASE_URL = st.secrets.get("SUPABASE_URL", None)
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", None)
supabase: Client = None
TABLE_NAME = "mf_transactions"
LOCAL_CSV = "mf_holdings.csv"

if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    use_db = True
else:
    use_db = False
    st.warning("Supabase not configured, using CSV fallback.")

# -----------------------------
# 2. Load AMFI data (local CSV)
# -----------------------------
@st.cache_data
def load_amfi_data():
    df = pd.read_csv("amfi_schemes.csv", sep=';', encoding='utf-8', on_bad_lines='skip')
    df.columns = df.columns.str.strip()
    return df

amfi_df = load_amfi_data()

# -----------------------------
# 3. Helper Functions
# -----------------------------
def compute_units_amount(amount, units, nav):
    """Compute missing amount or units depending on which one is zero"""
    if amount > 0 and (units == 0 or units is None):
        units = amount / nav
    elif units > 0 and (amount == 0 or amount is None):
        amount = units * nav
    return amount, units

def fetch_all_records() -> pd.DataFrame:
    cols = ["id","user_name","scheme_code","mf_name","purchase_date",
            "purchase_nav","units","amount","current_nav","profit_loss","notes","created_at"]
    if use_db:
        resp = supabase.table(TABLE_NAME).select("*").execute()
        data = resp.data or []
        df = pd.DataFrame(data)
        for c in cols:
            if c not in df.columns:
                df[c] = None
        return df[cols]
    else:
        if os.path.exists(LOCAL_CSV):
            df = pd.read_csv(LOCAL_CSV)
            for c in cols:
                if c not in df.columns:
                    df[c] = None
            return df[cols]
        return pd.DataFrame(columns=cols)

def save_csv(df):
    df.to_csv(LOCAL_CSV, index=False)

def insert_record(record: dict):
    # Fill defaults
    record.setdefault("id", str(uuid.uuid4()))
    record.setdefault("created_at", datetime.utcnow().isoformat())
    record.setdefault("current_nav", None)
    record.setdefault("profit_loss", None)
    record.setdefault("notes", "")

    # CSV fallback
    df = fetch_all_records()
    df = df.append(record, ignore_index=True)
    save_csv(df)

    # Supabase
    if use_db:
        try:
            supabase.table(TABLE_NAME).insert(record).execute()
        except Exception as e:
            st.error(f"Supabase insert failed: {e}")

def update_record(record_id, updates: dict):
    df = fetch_all_records()
    df.loc[df["id"] == record_id, list(updates.keys())] = list(updates.values())
    save_csv(df)
    if use_db:
        supabase.table(TABLE_NAME).update(updates).eq("id", record_id).execute()

def delete_record(record_id):
    df = fetch_all_records()
    df = df[df["id"] != record_id]
    save_csv(df)
    if use_db:
        supabase.table(TABLE_NAME).delete().eq("id", record_id).execute()

def fetch_records_for_user(user_name: str):
    df = fetch_all_records()
    if "user_name" not in df.columns:
        df["user_name"] = None
    return df[df["user_name"] == user_name]

# -----------------------------
# 4. Sync CSV -> DB on startup
# -----------------------------
if use_db:
    st.info("Syncing CSV to Supabase...")
    df_csv = fetch_all_records()
    resp = supabase.table(TABLE_NAME).select("*").execute()
    db_df = pd.DataFrame(resp.data or [])
    for c in ["id","user_name","scheme_code","mf_name","purchase_date","purchase_nav","units","amount","current_nav","profit_loss","notes","created_at"]:
        if c not in df_csv.columns:
            df_csv[c] = None
        if c not in db_df.columns:
            db_df[c] = None
    # insert CSV-only rows
    csv_only = df_csv[~df_csv["id"].isin(db_df["id"])]
    for _, r in csv_only.iterrows():
        supabase.table(TABLE_NAME).insert(r.to_dict()).execute()
    st.success("CSV ↔ DB sync complete!")

# -----------------------------
# 5. Streamlit UI
# -----------------------------
st.title("📊 Mutual Fund Tracker")

selected_user = st.text_input("Enter your name:", value="Guest")

# Add new holding
st.header("➕ Add / Update Holding")
with st.form("add_form"):
    mf_name = st.selectbox("Mutual Fund Scheme", amfi_df["Scheme Name"].unique())
    scheme_code_row = amfi_df[amfi_df["Scheme Name"] == mf_name].head(1)
    scheme_code = str(scheme_code_row["Scheme Code"].values[0]) if not scheme_code_row.empty else ""
    purchase_nav = float(scheme_code_row["Net Asset Value"].values[0]) if not scheme_code_row.empty else 10.0
    purchase_date = st.date_input("Purchase Date", datetime.today())
    amount = st.number_input("Amount (₹)", min_value=0.0, step=100.0)
    units = st.number_input("Units", min_value=0.0, step=1.0)

    # Auto calculate
    amount, units = compute_units_amount(amount, units, purchase_nav)

    notes = st.text_area("Notes", "")
    submitted = st.form_submit_button("Save Holding")
    if submitted:
        rec = {
            "user_name": selected_user,
            "mf_name": mf_name,
            "scheme_code": scheme_code,
            "purchase_date": str(purchase_date),
            "purchase_nav": purchase_nav,
            "units": units,
            "amount": amount,
            "notes": notes,
            "current_nav": None,
            "profit_loss": None
        }
        insert_record(rec)
        st.success("✅ Holding saved!")

# Show holdings
st.header(f"📂 Holdings for {selected_user}")
df_user = fetch_records_for_user(selected_user)

if df_user.empty:
    st.info("No holdings yet.")
else:
    # Update current NAV and P/L
    for idx, r in df_user.iterrows():
        code = str(r.get("scheme_code","")).strip()
        match = amfi_df[amfi_df["Scheme Code"] == code]
        if not match.empty:
            df_user.at[idx, "current_nav"] = float(match.iloc[0]["Net Asset Value"])
            df_user.at[idx, "profit_loss"] = (df_user.at[idx, "current_nav"] - float(r.get("purchase_nav",0))) * float(r.get("units",0))
    save_csv(df_user)

    st.dataframe(df_user[["user_name","mf_name","scheme_code","purchase_date","purchase_nav","units","amount","current_nav","profit_loss","notes"]])

    # CRUD interface
    st.write("Select a holding to update or delete:")
    selected_row = st.selectbox("Pick holding", df_user["mf_name"] + " | " + df_user["purchase_date"])
    if selected_row:
        idx = df_user.index[df_user["mf_name"] + " | " + df_user["purchase_date"] == selected_row][0]
        row_data = df_user.loc[idx]
        col1, col2 = st.columns(2)
        with col1:
            amount_update = st.number_input("Amount", value=float(row_data["amount"]))
            units_update = st.number_input("Units", value=float(row_data["units"]))
        with col2:
            purchase_nav_update = st.number_input("Purchase NAV", value=float(row_data["purchase_nav"]))
        notes_update = st.text_area("Notes", value=row_data["notes"])
        col3, col4 = st.columns(2)
        with col3:
            if st.button("Update this holding"):
                new_amount, new_units = compute_units_amount(amount_update, units_update, purchase_nav_update)
                update_record(row_data["id"], {
                    "amount": new_amount,
                    "units": new_units,
                    "purchase_nav": purchase_nav_update,
                    "notes": notes_update
                })
                st.success("✅ Updated!")
        with col4:
            if st.button("Delete this holding"):
                delete_record(row_data["id"])
                st.success("❌ Deleted!")

st.info("MF Tracker ready. CSV fallback ensures persistence if Supabase unavailable.")
