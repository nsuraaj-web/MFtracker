import streamlit as st
import pandas as pd
from supabase import create_client, Client
import datetime

# -------------------------------
# Supabase Connection
# -------------------------------
SUPABASE_URL = st.secrets["supabase"]["url"]
SUPABASE_KEY = st.secrets["supabase"]["key"]
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# -------------------------------
# Load AMFI Schemes (static CSV)
# -------------------------------
@st.cache_data
def load_amfi_schemes():
    url = "https://www.amfiindia.com/spages/NAVAll.txt"
    df = pd.read_csv(url, sep=';', header=None, on_bad_lines='skip')
    df.columns = ["Scheme Code", "ISIN Div Payout/ISIN Growth", "ISIN Div Reinvestment",
                  "Scheme Name", "Net Asset Value", "Date"]
    df = df[["Scheme Code", "Scheme Name", "Net Asset Value", "Date"]].dropna()
    df["Scheme Code"] = df["Scheme Code"].astype(str)
    return df

schemes_df = load_amfi_schemes()

# -------------------------------
# Fetch Holdings
# -------------------------------
def get_holdings(user_name):
    response = supabase.table("holdings").select("*").eq("user_name", user_name).execute()
    return pd.DataFrame(response.data) if response.data else pd.DataFrame()

def add_holding(data):
    supabase.table("holdings").insert(data).execute()

def update_holding(record_id, data):
    supabase.table("holdings").update(data).eq("id", record_id).execute()

def delete_holding(record_id):
    supabase.table("holdings").delete().eq("id", record_id).execute()

# -------------------------------
# App UI
# -------------------------------
st.title("📊 Mutual Fund Holdings Tracker (with Supabase + Scheme Code)")

user_name = st.text_input("Enter User Name:")
if not user_name:
    st.stop()

holdings = get_holdings(user_name)

# -------------------------------
# Add New Holding
# -------------------------------
with st.expander("➕ Add New Holding"):
    scheme_search = st.selectbox(
        "Select Mutual Fund Scheme",
        schemes_df["Scheme Name"].tolist()
    )
    scheme_code = schemes_df.loc[schemes_df["Scheme Name"] == scheme_search, "Scheme Code"].values[0]

    purchase_date = st.date_input("Purchase Date", datetime.date.today())
    purchase_nav = st.number_input("Purchase NAV", min_value=0.01, format="%.2f")
    amount = st.number_input("Amount (₹)", min_value=0.0, step=100.0)
    units = st.number_input("Units", min_value=0.0, step=0.01)
    notes = st.text_area("Notes")

    # Auto calculate
    if amount > 0 and units == 0 and purchase_nav > 0:
        units = amount / purchase_nav
        st.info(f"Auto-calculated units: {units:.2f}")
    elif units > 0 and amount == 0 and purchase_nav > 0:
        amount = units * purchase_nav
        st.info(f"Auto-calculated amount: ₹{amount:.2f}")

    if st.button("Save Holding"):
        add_holding({
            "user_name": user_name,
            "scheme_code": scheme_code,
            "instrument_name": scheme_search,
            "instrument_type": "MF",
            "purchase_date": str(purchase_date),
            "purchase_nav": purchase_nav,
            "units": units,
            "amount": amount,
            "notes": notes,
        })
        st.success("✅ Holding added successfully!")

# -------------------------------
# Show Holdings
# -------------------------------
if not holdings.empty:
    # Merge with current NAVs by scheme_code
    merged = holdings.merge(
        schemes_df,
        left_on="scheme_code",
        right_on="Scheme Code",
        how="left"
    )
    merged["Current NAV"] = pd.to_numeric(merged["Net Asset Value"], errors="coerce")
    merged["Current Value"] = merged["units"] * merged["Current NAV"]
    merged["Profit/Loss"] = merged["Current Value"] - merged["amount"]

    st.subheader("📋 Your Holdings")
    st.dataframe(merged[[
        "instrument_name", "purchase_date", "purchase_nav", "units", "amount",
        "Current NAV", "Current Value", "Profit/Loss", "notes"
    ]])

    # Delete/Edit options
    for _, row in merged.iterrows():
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button(f"✏️ Edit {row['instrument_name']}", key=f"edit_{row['id']}"):
                st.session_state["edit_row"] = row
        with col2:
            if st.button(f"🗑️ Delete {row['instrument_name']}", key=f"del_{row['id']}"):
                delete_holding(row["id"])
                st.success("Deleted successfully!")
                st.experimental_rerun()

else:
    st.info("No holdings found. Add one above.")
