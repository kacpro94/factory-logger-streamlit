import streamlit as st
import pandas as pd
import json
from datetime import datetime, date
from databricks.sdk import WorkspaceClient

# --- CONFIG DATA PLATFORM ---
CATALOG = "data_warehouse_factory"
SCHEMA_BRONZE = "bronze"
SCHEMA_GOLD = "gold"
VOLUME = "employees_raw"
DATABRICKS_WAREHOUSE_ID = "f38fec74b09fdb89"

st.title("Employee Management")

# --- REUSABLE CLIENT ---
@st.cache_resource
def get_databricks_client():
    return WorkspaceClient()

w = get_databricks_client()

# --- 1. FETCH EMPLOYEES FROM GOLD DIMENSION ---
@st.cache_data(ttl=300)
def fetch_employees_from_gold():
    try:
        sql_query = f"""
            SELECT 
                employee_key, 
                employee_full_name, 
                work_center, 
                date_of_employment, 
                date_of_leaving,
                is_active,
                tenure_months
            FROM {CATALOG}.{SCHEMA_GOLD}.dim_employees 
            ORDER BY employee_key DESC
        """
        response = w.statement_execution.execute_statement(
            warehouse_id=DATABRICKS_WAREHOUSE_ID,
            statement=sql_query
        )

        if response.result and response.result.data_array:
            columns = [col.name for col in response.manifest.schema.columns]
            return pd.DataFrame(response.result.data_array, columns=columns)
        return pd.DataFrame()
    except Exception as e:
        st.error(f"Failed to load employees: {e}")
        return pd.DataFrame()

# --- 2. FETCH WORK CENTERS FROM PRODUCTION CELLS ---
@st.cache_data(ttl=600)
def get_available_work_centers():
    try:
        sql_query = f"""
            SELECT DISTINCT upper(trim(line_name)) AS work_center 
            FROM {CATALOG}.{SCHEMA_GOLD}.dim_production_cells 
            WHERE line_name IS NOT NULL AND trim(line_name) != ''
            ORDER BY work_center ASC
        """
        response = w.statement_execution.execute_statement(
            warehouse_id=DATABRICKS_WAREHOUSE_ID,
            statement=sql_query
        )
        if response.result and response.result.data_array:
            return [row[0] for row in response.result.data_array]
        return ["Assembly", "Manufacturing", "Logistics", "Maintenance"]
    except Exception:
        return ["Assembly", "Manufacturing", "Logistics", "Maintenance"]

# --- EMPLOYEES OVERVIEW ---
st.subheader("Employee Directory")

col_refresh, col_filter, _ = st.columns([1, 2, 3])
with col_refresh:
    if st.button("Refresh List"):
        fetch_employees_from_gold.clear()
        st.rerun()

df_employees = fetch_employees_from_gold()
work_center_options = get_available_work_centers()

with col_filter:
    filter_active = st.checkbox("Show active employees only", value=True)

if not df_employees.empty:
    if filter_active and "is_active" in df_employees.columns:
        # Obsługuje zarówno wartości bool (True), jak i stringi ("true", "True", "1")
        is_active_mask = df_employees["is_active"].astype(str).str.lower().isin(["true", "1"])
        display_df = df_employees[is_active_mask]
    else:
        display_df = df_employees
        
    st.dataframe(display_df, use_container_width=True, hide_index=True)
else:
    st.info("No employee records found.")

st.divider()

# --- ADD EMPLOYEE SECTION ---
st.subheader("Add New Employee")

col_status1, col_status2 = st.columns(2)

with col_status1:
    has_left = st.checkbox("Employment terminated")

with col_status2:
    if has_left:
        selected_leaving_date = st.date_input("Date of leaving", date.today())
    else:
        selected_leaving_date = None

# --- FORM ---
with st.form("employee_form", clear_on_submit=True):
    col1, col2 = st.columns(2)
    
    with col1:
        employee_full_name = st.text_input("Full Name")
        work_center = st.selectbox("Work Center", work_center_options)
        
    with col2:
        date_of_employment = st.date_input("Date of employment", date.today())
        if has_left and selected_leaving_date:
            st.caption(f"Termination date: {selected_leaving_date.strftime('%Y-%m-%d')}")

    submit_button = st.form_submit_button("Save Employee")

# --- SAVE LOGIC ---
if submit_button:
    if not employee_full_name.strip():
        st.warning("Full Name field cannot be empty.")
    elif has_left and selected_leaving_date and selected_leaving_date < date_of_employment:
        st.error("Date of leaving cannot be earlier than Date of employment.")
    else:
        entry_data = {
            "employee_full_name": employee_full_name.strip(),
            "work_center": work_center,
            "date_of_employment": date_of_employment.isoformat(),
            "date_of_leaving": selected_leaving_date.isoformat() if has_left and selected_leaving_date else None,
            "ingested_at": datetime.now().isoformat(),
            "source": "streamlit_app"
        }

        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        filename = f"employee_{timestamp_str}.json"

        try:
            full_cloud_path = f"/Volumes/{CATALOG}/{SCHEMA_BRONZE}/{VOLUME}/{filename}"
            json_bytes = json.dumps(entry_data, ensure_ascii=False, indent=4).encode('utf-8')

            w.files.upload(full_cloud_path, json_bytes)
            
            st.success(f"Employee {employee_full_name} saved successfully as {filename}.")
            
            fetch_employees_from_gold.clear()
            
        except Exception as e:
            st.error(f"Error saving record: {str(e)}")

st.divider()

# --- RECENT FILES ---
with st.expander("Recent Bronze Volume Files"):
    if st.button("Load File List"):
        try:
            cloud_dir_path = f"/Volumes/{CATALOG}/{SCHEMA_BRONZE}/{VOLUME}"
            files_list = w.files.list_directory_contents(cloud_dir_path)
            files = sorted([f.name for f in files_list], reverse=True)[:5]
            st.write(files)
        except Exception as e:
            st.warning(f"Unable to load files: {e}")