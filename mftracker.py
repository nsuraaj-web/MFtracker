# app.py
import streamlit as st
import pandas as pd
import requests
from supabase import create_client, Client
from datetime import date, datetime
import os
from typing import Optional

st.set_page_config(page_title="MF Tracker (Supabase)", layout="wide")

# -------------------------
# Supabase init (from secrets)
# -------------------------
SUPABASE_URL = st.secrets.get("SUPABASE_URL", None)
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", None)
supabase: Optional[Client] = None
use_db = False

if SUPABASE_URL and SUPABASE_KEY:
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        use_db = True
    except Exception as e:
        st.error("Failed to initialize Supabase client. Falling back to session-only storage.")
        use_db = False
else:
    st.info("Supabase not configured in secrets. Using session-only storage.")

# -------------------------
# AMFI NAV loader (robust)
# -------------------------
AMFI_URL = "https://www.amfiindia.com/spages/NAVAll.txt"

@st.cache_data(ttl=3600)
def fetch_amfi_nav_df() -> pd.DataFrame:
    """
    Fetch the AMFI NAVAll file and parse into DataFrame.
    The NAVAll text is semicolon-separated. We parse robustly and return columns:
    Scheme Code,ISIN1,ISIN2,Scheme Name,Net Asset Value,Date
    """
    try:
        r = requests.get(AMFI_URL, timeout=15)
        r.raise_for_status()
        txt = r.text
        # The file uses ';' separator; parse with pandas from string
        from io import StringIO
        df = pd.read_csv(StringIO(txt), sep=";", header=None, on_bad_lines="skip", engine="python")
        # The file may or may not include header row. Ensure we have >=6 columns
        if df.shape[1] >= 6:
            df = df.iloc[:, :6]
            df.columns = ["Scheme Code", "ISIN1", "ISIN2", "Scheme Name", "Net Asset Value", "Date"]
        else:
            # fallback: try comma
            df = pd.read_csv(StringIO(txt), sep=",", header=None, on_bad_lines="skip", engine="python")
            df = df.iloc[:, :6]
            df.columns = ["Scheme Code", "ISIN1", "ISIN2", "Scheme Name", "Net Asset Value", "Date"]
        # Clean and convert
        df["Scheme Name"] = df["Scheme Name"].astype(str).str.strip()
        df["Net Asset Value"] = pd.to_numeric(df["Net Asset Value"].astype(str).str.replace(",", ""), errors="coerce")
        df = df.dropna(subset=["Scheme Name"])
        return df
    except Exception as e:
        st.warning(f"Could not fetch AMFI NAVs: {e}")
        # return empty dataframe with columns
        return pd.DataFrame(columns=["Scheme Code", "ISIN1", "ISIN2", "Scheme Name", "Net Asset Value", "Date"])

# -------------------------
# DB wrappers (Supabase or session fallback)
# -------------------------
def insert_record(record: dict):
    """Insert record to Supabase table or session-state fallback."""
    if use_db:
        supabase.table("mf_transactions").insert(record).execute()
    else:
        if "mf_transactions" not in st.session_state:
            st.session_state["mf_transactions"] = []
        # add created_at
        record["created_at"] = datetime.utcnow().isoformat()
        st.session_state["mf_transactions"].append(record)

def update_record(record_id: str, updates: dict):
    if use_db:
        supabase.table("mf_transactions").update(updates).eq("id", record_id).execute()
    else:
        # find in session list and update
        if "mf_transactions" in st.session_state:
            for r in st.session_state["mf_transactions"]:
                if r.get("id") == record_id:
                    r.update(updates)
                    break

def delete_record(record_id: str):
    if use_db:
        supabase.table("mf_transactions").delete().eq("id", record_id).execute()
    else:
        if "mf_transactions" in st.session_state:
            st.session_state["mf_transactions"] = [r for r in st.session_state["mf_transactions"] if r.get("id") != record_id]

def fetch_all_records_for_user(user_name: str) -> pd.DataFrame:
    if use_db:
        resp = supabase.table("mf_transactions").select("*").eq("user_name", user_name).order("created_at", desc=False).execute()
        data = resp.data or []
        return pd.DataFrame(data)
    else:
        rows = st.session_state.get("mf_transactions", [])
        # filter by user_name
        data = [r for r in rows if r.get("user_name") == user_name]
        return pd.DataFrame(data)

# -------------------------
# Update NAVs on launch
# -------------------------
def refresh_navs_for_all():
    """
    Fetch AMFI NAVs, then update current_nav and profit_loss for all records in DB.
    Matching done with case-insensitive substring match; first match used.
    """
    nav_df = fetch_amfi_nav_df()
    # return quickly if no navs
    if nav_df.empty:
        return 0

    # fetch all records (regardless of user) when using Supabase
    if use_db:
        all_rows = supabase.table("mf_transactions").select("*").execute().data or []
        count = 0
        for r in all_rows:
            mf_name = r.get("mf_name", "")
            # fuzzy match: find first nav row which contains mf_name substring (case-insensitive)
            match = nav_df[nav_df["Scheme Name"].str.contains(mf_name, case=False, na=False)]
            if match.shape[0] > 0:
                latest_nav = float(match.iloc[0]["Net Asset Value"])
                profit_loss = (latest_nav - float(r.get("purchase_nav", 0))) * float(r.get("units", 0))
                # update
                supabase.table("mf_transactions").update({
                    "current_nav": latest_nav,
                    "profit_loss": profit_loss
                }).eq("id", r.get("id")).execute()
                count += 1
        return count
    else:
        # session fallback
        count = 0
        for r in st.session_state.get("mf_transactions", []):
            mf_name = r.get("mf_name", "")
            match = nav_df[nav_df["Scheme Name"].str.contains(mf_name, case=False, na=False)]
            if match.shape[0] > 0:
                latest_nav = float(match.iloc[0]["Net Asset Value"])
                profit_loss = (latest_nav - float(r.get("purchase_nav", 0))) * float(r.get("units", 0))
                r["current_nav"] = latest_nav
                r["profit_loss"] = profit_loss
                count += 1
        return count

# Run refresh when app starts
with st.spinner("Fetching latest NAVs and updating holdings..."):
    updated_count = refresh_navs_for_all()
if updated_count:
    st.success(f"Updated {updated_count} holdings with latest NAVs.")
else:
    st.info("No NAV updates performed (no matching funds or NAV fetch failed).")

# -------------------------
# Helper: auto-calc units/amount
# -------------------------
def compute_other(amount: float, units: float, purchase_nav: float):
    """
    If amount provided and units 0 -> units = amount / purchase_nav
    If units provided and amount 0 -> amount = units * purchase_nav
    Returns (amount, units)
    """
    if purchase_nav and amount and not units:
        units = amount / purchase_nav
    elif purchase_nav and units and not amount:
        amount = units * purchase_nav
    return amount, units

# -------------------------
# UI
# -------------------------
st.title("📊 Mutual Fund Tracker (Supabase-backed)")

# sidebar: choose user or create
st.sidebar.header("User & Actions")
user_input = st.sidebar.text_input("Enter user name", value="You")
if st.sidebar.button("Load holdings"):
    st.session_state["selected_user"] = user_input

selected_user = st.session_state.get("selected_user", user_input)

st.sidebar.markdown("---")
if st.sidebar.button("Add new holding (open form)"):
    st.session_state["show_add_form"] = True

if st.sidebar.button("Refresh NAVs now"):
    with st.spinner("Refreshing NAVs..."):
        updated = refresh_navs_for_all()
    st.success(f"Updated {updated} holdings.")

# -------- Add / Update form area --------
if st.session_state.get("show_add_form", False):
    st.header("➕ Add New Holding")
    with st.form("add_holding_form", clear_on_submit=False):
        user_name = st.text_input("User Name", value=selected_user)
        mf_name = st.text_input("Mutual Fund Name (must match AMFI name substring for auto NAV)")
        purchase_date = st.date_input("Purchase Date", value=date.today())
        purchase_nav = st.number_input("Purchase NAV (per unit)", min_value=0.0, format="%.4f")
        col1, col2 = st.columns(2)
        with col1:
            amount = st.number_input("Amount (₹)", min_value=0.0, format="%.2f")
        with col2:
            units = st.number_input("Units", min_value=0.0, format="%.6f")
        notes = st.text_area("Notes (optional)")

        # Auto-calc preview
        amount_preview, units_preview = compute_other(amount, units, purchase_nav)
        st.info(f"Preview — Amount: ₹{amount_preview:.2f} | Units: {units_preview:.6f}")

        submitted = st.form_submit_button("Save new holding")
        if submitted:
            # basic validation
            if not user_name or not mf_name or purchase_nav <= 0 or (amount_preview <= 0 and units_preview <= 0):
                st.error("Please fill User, MF name, purchase nav, and either Amount or Units.")
            else:
                new_record = {
                    "user_name": user_name,
                    "mf_name": mf_name,
                    "purchase_date": str(purchase_date),
                    "purchase_nav": float(purchase_nav),
                    "units": float(units_preview),
                    "amount": float(amount_preview),
                    "current_nav": None,
                    "profit_loss": None,
                    "notes": notes
                }
                insert_record(new_record)
                st.success("Holding added.")
                # hide form
                st.session_state["show_add_form"] = False

# -------- Display holdings for selected user --------
st.header(f"Holdings for: {selected_user}")
df_holdings = fetch_all_records_for_user(selected_user)

if df_holdings.empty:
    st.info("No holdings found for this user. Add a new holding from the sidebar or using the form above.")
else:
    # Ensure numeric types
    for col in ["purchase_nav", "units", "amount", "current_nav", "profit_loss"]:
        if col in df_holdings.columns:
            df_holdings[col] = pd.to_numeric(df_holdings[col], errors="coerce")

    # compute current value column
    df_holdings["current_value"] = df_holdings.apply(
        lambda r: (r["current_nav"] * r["units"]) if pd.notna(r.get("current_nav")) else None, axis=1
    )

    # display in table with selection
    st.dataframe(df_holdings[[
        "id", "user_name", "mf_name", "purchase_date", "purchase_nav", "units",
        "amount", "current_nav", "current_value", "profit_loss", "notes"
    ]].rename(columns={
        "mf_name": "MF Name", "purchase_nav": "Purchase NAV", "current_nav": "Current NAV",
        "current_value": "Current Value", "profit_loss": "Profit/Loss", "purchase_date": "Purchase Date"
    }), use_container_width=True)

    # Row selection to update/delete
    st.markdown("### Select a holding to Update / Delete")
    id_list = df_holdings["id"].astype(str).tolist()
    selected_id = st.selectbox("Select record id", options=[""] + id_list)
    if selected_id:
        rec = df_holdings[df_holdings["id"].astype(str) == selected_id].iloc[0].to_dict()
        st.write("Selected:", rec.get("mf_name"))
        with st.form("update_form"):
            upd_mf_name = st.text_input("MF Name", value=rec.get("mf_name"))
            upd_purchase_date = st.date_input("Purchase Date", value=pd.to_datetime(rec.get("purchase_date")).date())
            upd_purchase_nav = st.number_input("Purchase NAV", value=float(rec.get("purchase_nav") or 0.0), format="%.4f")
            col1, col2 = st.columns(2)
            with col1:
                upd_amount = st.number_input("Amount (₹)", value=float(rec.get("amount") or 0.0), format="%.2f")
            with col2:
                upd_units = st.number_input("Units", value=float(rec.get("units") or 0.0), format="%.6f")
            upd_notes = st.text_area("Notes", value=rec.get("notes") or "")
            do_update = st.form_submit_button("Update record")
            do_delete = st.form_submit_button("Delete record")

            if do_update:
                # auto-calc if needed
                a, u = compute_other(upd_amount, upd_units, upd_purchase_nav)
                updates = {
                    "mf_name": upd_mf_name,
                    "purchase_date": str(upd_purchase_date),
                    "purchase_nav": float(upd_purchase_nav),
                    "units": float(u),
                    "amount": float(a),
                    "notes": upd_notes
                }
                update_record(selected_id, updates)
                st.success("Record updated. Re-run/refresh to see latest values.")
            if do_delete:
                delete_record(selected_id)
                st.success("Record deleted.")

# -------------------------
# Export CSV button
# -------------------------
st.sidebar.markdown("---")
if st.sidebar.button("Download holdings CSV for selected user"):
    if not df_holdings.empty:
        csv = df_holdings.to_csv(index=False)
        st.sidebar.download_button("Download CSV", data=csv, file_name=f"{selected_user}_holdings.csv")
    else:
        st.sidebar.warning("No holdings to download.")
