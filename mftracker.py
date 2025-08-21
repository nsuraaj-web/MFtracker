import streamlit as st
import pandas as pd
import requests
from io import StringIO
from supabase import create_client, Client
from datetime import date, datetime
import uuid
import os

st.set_page_config(page_title="MF Tracker", layout="wide")

# -----------------------------
# 1. Supabase Config
# -----------------------------
SUPABASE_URL = st.secrets.get("SUPABASE_URL")
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY")
TABLE_NAME = "mf_transactions"
LOCAL_CSV = "mf_holdings.csv"

supabase = None
use_db = False
if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        use_db = True
    except Exception as e:
        st.warning("Supabase init failed. Using CSV fallback.")

# -----------------------------
# 2. Load AMFI data from URL
# -----------------------------
AMFI_URL = "https://www.amfiindia.com/spages/NAVAll.txt"

@st.cache_data(ttl=3600)
def load_amfi_data():
    try:
        r = requests.get(AMFI_URL, timeout=15)
        r.raise_for_status()
        txt = r.text
        df = pd.read_csv(StringIO(txt), sep=";", header=None, engine="python", on_bad_lines="skip")
        df = df.iloc[:, :6]
        df.columns = ["Scheme Code","ISIN1","ISIN2","Scheme Name","Net Asset Value","Date"]
        df["Scheme Code"] = df["Scheme Code"].astype(str).str.strip()
        df["Scheme Name"] = df["Scheme Name"].astype(str).str.strip()
        df["Net Asset Value"] = pd.to_numeric(df["Net Asset Value"].astype(str).str.replace(",", ""), errors="coerce")
        df = df.dropna(subset=["Scheme Code","Scheme Name"])
        return df
    except:
        return pd.DataFrame(columns=["Scheme Code","ISIN1","ISIN2","Scheme Name","Net Asset Value","Date"])

amfi_df = load_amfi_data()
SCHEME_DISPLAY = (amfi_df["Scheme Name"] + " (" + amfi_df["Scheme Code"] + ")").tolist() if not amfi_df.empty else []

# -----------------------------
# 3. CRUD functions
# -----------------------------
def fetch_all_records() -> pd.DataFrame:
    cols = ["id","user_name","scheme_code","mf_name","purchase_date",
            "purchase_nav","units","amount","current_nav","profit_loss","notes","created_at"]
    if use_db:
        resp = supabase.table(TABLE_NAME).select("*").execute()
        df = pd.DataFrame(resp.data or [])
    else:
        if os.path.exists(LOCAL_CSV):
            df = pd.read_csv(LOCAL_CSV)
        else:
            df = pd.DataFrame(columns=cols)
    for c in cols:
        if c not in df.columns:
            df[c] = None
    return df[cols]

def save_csv(df: pd.DataFrame):
    df.to_csv(LOCAL_CSV, index=False)

def insert_record(record: dict):
    record = dict(record)
    record["id"] = str(uuid.uuid4())
    record["created_at"] = datetime.utcnow().isoformat()
    df = fetch_all_records()
    df = pd.concat([df, pd.DataFrame([record])], ignore_index=True)
    save_csv(df)
    if use_db:
        try:
            supabase.table(TABLE_NAME).insert(record).execute()
        except Exception as e:
            st.warning(f"Supabase insert failed: {e}")

def update_record(record_id: str, updates: dict):
    df = fetch_all_records()
    for col, val in updates.items():
        df.loc[df["id"]==record_id, col] = val
    save_csv(df)
    if use_db:
        supabase.table(TABLE_NAME).update(updates).eq("id", record_id).execute()

def delete_record(record_id: str):
    df = fetch_all_records()
    df = df[df["id"] != record_id]
    save_csv(df)
    if use_db:
        supabase.table(TABLE_NAME).delete().eq("id", record_id).execute()

def fetch_records_for_user(user_name: str) -> pd.DataFrame:
    df = fetch_all_records()
    return df[df["user_name"]==user_name]

# -----------------------------
# 4. Compute units/amount
# -----------------------------
def compute_amount_units(amount: float, units: float, nav: float):
    if nav and amount and not units:
        units = amount / nav
    elif nav and units and not amount:
        amount = units * nav
    return float(amount or 0.0), float(units or 0.0)

# -----------------------------
# 5. Refresh NAVs
# -----------------------------
def refresh_navs():
    df = fetch_all_records()
    for idx, r in df.iterrows():
        code = str(r.get("scheme_code","")).strip()
        match = amfi_df[amfi_df["Scheme Code"] == code]
        if not match.empty:
            nav = float(match.iloc[0]["Net Asset Value"])
            df.at[idx,"current_nav"] = nav
            df.at[idx,"profit_loss"] = (nav - float(r.get("purchase_nav",0))) * float(r.get("units",0))
            if use_db:
                supabase.table(TABLE_NAME).update({"current_nav": nav, "profit_loss": df.at[idx,"profit_loss"]}).eq("id", r["id"]).execute()
    save_csv(df)

# -----------------------------
# 6. Streamlit UI
# -----------------------------
st.title("📊 Mutual Fund Tracker (Scheme Code)")

user_name = st.text_input("Enter your name", value="Guest")
st.button("Refresh NAVs now", on_click=refresh_navs)

st.header("➕ Add New Holding")
with st.form("add_form"):
    scheme_choice = st.selectbox("Select scheme", SCHEME_DISPLAY)
    scheme_code = scheme_choice.split("(")[-1].strip(")") if scheme_choice else ""
    scheme_name = "(".join(scheme_choice.split("(")[:-1]).strip() if scheme_choice else ""
    default_nav = float(amfi_df.loc[amfi_df["Scheme Code"]==scheme_code, "Net Asset Value"].values[0]) if scheme_code else 0.0
    purchase_nav = st.number_input("Purchase NAV", value=default_nav, format="%.4f")
    col1,col2 = st.columns(2)
    with col1:
        amount_input = st.number_input("Amount (₹)", min_value=0.0, format="%.2f", value=0.0)
    with col2:
        units_input = st.number_input("Units", min_value=0.0, format="%.6f", value=0.0)
    purchase_date = st.date_input("Purchase Date", value=date.today())
    notes = st.text_area("Notes (optional)")
    amount_preview, units_preview = compute_amount_units(amount_input, units_input, purchase_nav)
    st.info(f"Preview — Amount: ₹{amount_preview:.2f} | Units: {units_preview:.6f}")
    if st.form_submit_button("Save holding"):
        if not scheme_code or purchase_nav<=0 or (amount_preview<=0 and units_preview<=0):
            st.error("Provide scheme, purchase NAV, and either amount or units")
        else:
            rec = {
                "user_name": user_name,
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

st.header(f"📂 Holdings for {user_name}")
df_user = fetch_records_for_user(user_name)
if df_user.empty:
    st.info("No holdings yet.")
else:
    refresh_navs()  # ensure latest NAVs
    st.dataframe(df_user[["mf_name","scheme_code","purchase_date","purchase_nav","units","amount","current_nav","profit_loss","notes"]])

    # CRUD
    st.write("Update / Delete holdings")
    selected_row = st.selectbox("Pick holding", df_user["mf_name"] + " | " + df_user["purchase_date"])
    if selected_row:
        idx = df_user.index[df_user["mf_name"] + " | " + df_user["purchase_date"] == selected_row][0]
        row = df_user.loc[idx]
        col1,col2 = st.columns(2)
        with col1:
            amount_upd = st.number_input("Amount", value=float(row["amount"]))
            units_upd = st.number_input("Units", value=float(row["units"]))
        with col2:
            nav_upd = st.number_input("Purchase NAV", value=float(row["purchase_nav"]))
        notes_upd = st.text_area("Notes", value=row["notes"])
        col3,col4 = st.columns(2)
        with col3:
            if st.button("Update this holding"):
                amt, unt = compute_amount_units(amount_upd, units_upd, nav_upd)
                update_record(row["id"], {"amount": amt,"units":unt,"purchase_nav":nav_upd,"notes":notes_upd})
                st.success("✅ Updated!")
        with col4:
            if st.button("Delete this holding"):
                delete_record(row["id"])
                st.success("❌ Deleted!")
