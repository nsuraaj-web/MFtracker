import streamlit as st
import pandas as pd
import requests
from io import StringIO
from supabase import create_client, Client
from datetime import date, datetime
import uuid
from typing import Optional

st.set_page_config(page_title="MF Tracker (Scheme Code)", layout="wide")

# -------------------------
# 1) Supabase credentials
# -------------------------
SUPABASE_URL = st.secrets.get("SUPABASE_URL") or (st.secrets.get("supabase") or {}).get("url")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY") or (st.secrets.get("supabase") or {}).get("key")

supabase: Optional[Client] = None
use_db = False
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        use_db = True
    except Exception as e:
        st.warning("Could not initialize Supabase client. Falling back to CSV storage.")
        use_db = False
else:
    st.info("Supabase credentials not found. Using CSV storage fallback.")

# -------------------------
# 2) CSV fallback
# -------------------------
LOCAL_CSV = "holdings.csv"

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
        try:
            df = pd.read_csv(LOCAL_CSV)
            for c in cols:
                if c not in df.columns:
                    df[c] = None
            return df[cols]
        except FileNotFoundError:
            return pd.DataFrame(columns=cols)

def save_csv(df: pd.DataFrame):
    df.to_csv(LOCAL_CSV, index=False)

# -------------------------
# 3) AMFI NAV loader
# -------------------------
AMFI_URL = "https://www.amfiindia.com/spages/NAVAll.txt"

@st.cache_data(ttl=3600)
def fetch_amfi_nav_df() -> pd.DataFrame:
    try:
        r = requests.get(AMFI_URL, timeout=15)
        r.raise_for_status()
        txt = r.text
        df = pd.read_csv(StringIO(txt), sep=";", header=None, on_bad_lines="skip", engine="python")
        if df.shape[1] >= 6:
            df = df.iloc[:, :6]
            df.columns = ["Scheme Code", "ISIN1", "ISIN2", "Scheme Name", "Net Asset Value", "Date"]
        df["Scheme Code"] = df["Scheme Code"].astype(str).str.strip()
        df["Scheme Name"] = df["Scheme Name"].astype(str).str.strip()
        df["Net Asset Value"] = pd.to_numeric(df["Net Asset Value"].astype(str).str.replace(",", ""), errors="coerce")
        df = df.dropna(subset=["Scheme Code", "Scheme Name"])
        return df
    except Exception as e:
        st.warning(f"Failed to fetch AMFI NAV: {e}")
        return pd.DataFrame(columns=["Scheme Code", "ISIN1", "ISIN2", "Scheme Name", "Net Asset Value", "Date"])

amfi_df = fetch_amfi_nav_df()
SCHEME_DISPLAY = (amfi_df["Scheme Name"] + " (" + amfi_df["Scheme Code"] + ")").tolist() if not amfi_df.empty else []

# -------------------------
# 4) DB + CSV CRUD wrappers
# -------------------------
TABLE_NAME = "mf_transactions"

def insert_record(record: dict):
    record = dict(record)
    record["id"] = str(uuid.uuid4()) if "id" not in record else record["id"]
    record["created_at"] = datetime.utcnow().isoformat()
    if use_db:
        supabase.table(TABLE_NAME).insert(record).execute()
    # always save to CSV
    df = fetch_all_records()
    df = pd.concat([df, pd.DataFrame([record])], ignore_index=True)
    save_csv(df)

def update_record(record_id: str, updates: dict):
    if use_db:
        supabase.table(TABLE_NAME).update(updates).eq("id", record_id).execute()
    df = fetch_all_records()
    for col, val in updates.items():
        df.loc[df["id"]==record_id, col] = val
    save_csv(df)

def delete_record(record_id: str):
    if use_db:
        supabase.table(TABLE_NAME).delete().eq("id", record_id).execute()
    df = fetch_all_records()
    df = df[df["id"] != record_id]
    save_csv(df)

def fetch_records_for_user(user_name: str) -> pd.DataFrame:
    df = fetch_all_records()
    return df[df["user_name"] == user_name]

# -------------------------
# 5) Sync CSV -> DB on start
# -------------------------
if use_db:
    st.info("Syncing CSV to Supabase...")

    df_csv = fetch_all_records()
    db_resp = supabase.table(TABLE_NAME).select("*").execute()
    db_df = pd.DataFrame(db_resp.data or [])

    # Ensure 'id' column exists in both
    if "id" not in df_csv.columns:
        df_csv["id"] = [str(uuid.uuid4()) for _ in range(len(df_csv))]
    if "id" not in db_df.columns:
        db_df["id"] = [str(uuid.uuid4()) for _ in range(len(db_df))]

    # push CSV-only records
    csv_only = df_csv[~df_csv["id"].isin(db_df["id"])]
    for _, r in csv_only.iterrows():
        supabase.table(TABLE_NAME).insert(r.to_dict()).execute()

    # update CSV with full DB records
    df_combined = pd.concat([db_df, csv_only], ignore_index=True)
    save_csv(df_combined)
    st.success("Sync complete.")

# -------------------------
# 6) Helper: compute amount/units
# -------------------------
def compute_amount_units(amount: float, units: float, purchase_nav: float):
    if purchase_nav and amount and not units:
        units = amount / purchase_nav
    elif purchase_nav and units and not amount:
        amount = units * purchase_nav
    return float(amount or 0.0), float(units or 0.0)

# -------------------------
# 7) Refresh NAVs
# -------------------------
def refresh_navs_for_all():
    df = fetch_all_records()
    for idx, r in df.iterrows():
        code = str(r.get("scheme_code", "")).strip()
        if not code:
            continue
        match = amfi_df[amfi_df["Scheme Code"] == code]
        if not match.empty:
            latest_nav = float(match.iloc[0]["Net Asset Value"])
            profit_loss = (latest_nav - float(r.get("purchase_nav", 0))) * float(r.get("units", 0))
            df.at[idx, "current_nav"] = latest_nav
            df.at[idx, "profit_loss"] = profit_loss
            # update DB too if available
            if use_db:
                supabase.table(TABLE_NAME).update({"current_nav": latest_nav, "profit_loss": profit_loss}).eq("id", r["id"]).execute()
    save_csv(df)

# -------------------------
# 8) UI
# -------------------------
st.title("📊 Mutual Fund Tracker (Scheme Code + CSV fallback)")

# Sidebar user
user_name_input = st.sidebar.text_input("Enter user name", value="You")
selected_user = st.sidebar.button("Load holdings") and user_name_input or user_name_input

if st.sidebar.button("Refresh NAVs now"):
    with st.spinner("Refreshing NAVs..."):
        refresh_navs_for_all()
    st.success("NAVs updated.")

# Add Holding Form
st.header("➕ Add New Holding")
with st.form("add_form"):
    scheme_choice = st.selectbox("Select scheme", SCHEME_DISPLAY)
    scheme_code = scheme_choice.split("(")[-1].strip(")") if scheme_choice else ""
    scheme_name = "(".join(scheme_choice.split("(")[:-1]).strip() if scheme_choice else ""
    purchase_date = st.date_input("Purchase Date", value=date.today())
    default_nav = float(amfi_df.loc[amfi_df["Scheme Code"]==scheme_code, "Net Asset Value"].values[0]) if not amfi_df.empty and scheme_code else 0.0
    purchase_nav = st.number_input("Purchase NAV", value=default_nav, format="%.4f")
    col1, col2 = st.columns(2)
    with col1:
        amount_input = st.number_input("Amount (₹)", min_value=0.0, format="%.2f", value=0.0)
    with col2:
        units_input = st.number_input("Units", min_value=0.0, format="%.6f", value=0.0)
    notes = st.text_area("Notes (optional)")
    amount_preview, units_preview = compute_amount_units(amount_input, units_input, purchase_nav)
    st.info(f"Preview — Amount: ₹{amount_preview:.2f} | Units: {units_preview:.6f}")
    if st.form_submit_button("Save holding"):
        if not scheme_code or purchase_nav<=0 or (amount_preview<=0 and units_preview<=0):
            st.error("Provide scheme, purchase NAV, and either amount or units")
        else:
            rec = {
                "user_name": selected_user,
                "scheme_code": scheme_code,
                "mf_name": scheme_name,
                "purchase_date": str(purchase_date),
                "purchase_nav": purchase_nav,
                "units": units_preview,
                "amount": amount_preview,
                "current_nav": None,
                "profit_loss": None,
                "notes": notes
            }
            insert_record(rec)
            st.success("✅ Holding saved!")

# Show Portfolio
st.header(f"📂 Holdings for {selected_user}")
df_user = fetch_records_for_user(selected_user)
if df_user.empty:
    st.info("No holdings yet.")
else:
    # merge with latest NAV
    for idx, r in df_user.iterrows():
        code = str(r.get("scheme_code","")).strip()
        match = amfi_df[amfi_df["Scheme Code"] == code]
        if not match.empty:
            df_user.at[idx, "current_nav"] = float(match.iloc[0]["Net Asset Value"])
            df_user.at[idx, "profit_loss"] = (df_user.at[idx, "current_nav"] - float(r.get("purchase_nav",0))) * float(r.get("units",0))
    save_csv(df_user)
    st.dataframe(df_user[["user_name","mf_name","scheme_code","purchase_date","purchase_nav","units","amount","current_nav","profit_loss","notes"]])

    # CRUD buttons
    st.write("Select a holding to update or delete:")
    selected_row = st.selectbox("Pick holding (by MF name & date)", df_user["mf_name"] + " | " + df_user["purchase_date"])
    if selected_row:
        idx = df_user.index[df_user["mf_name"] + " | " + df_user["purchase_date"] == selected_row][0]
        row_data = df_user.loc[idx]
        st.write("Update values:")
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
                new_amount, new_units = compute_amount_units(amount_update, units_update, purchase_nav_update)
                update_record(row_data["id"], {
                    "amount": new_amount, "units": new_units, "purchase_nav": purchase_nav_update, "notes": notes_update
                })
                st.success("Updated!")
        with col4:
            if st.button("Delete this holding"):
                delete_record(row_data["id"])
                st.success("Deleted!")

st.info("MF Tracker ready. CSV fallback ensures persistence if Supabase unavailable.")
