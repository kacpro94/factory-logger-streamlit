import streamlit as st
import json
import pandas as pd
from datetime import datetime, date, time
from databricks.sdk import WorkspaceClient

# --- CONFIG DATA PLATFORM ---
CATALOG = "data_warehouse_factory"
BRONZE_SCHEMA = "bronze"
GOLD_SCHEMA = "gold"
VOLUME = "capacity_planner_events_raw"
DATABRICKS_WAREHOUSE_ID = "f38fec74b09fdb89"

st.title("Production Event Logger")

# --- REUSABLE CLIENT ---
@st.cache_resource
def get_databricks_client():
    return WorkspaceClient()

w = get_databricks_client()

# --- 1. FETCH ACTIVE EMPLOYEES FROM GOLD ---
@st.cache_data(ttl=60)
def get_employee_options():
    try:
        sql_query = f"""
            SELECT employee_key, employee_full_name 
            FROM {CATALOG}.{GOLD_SCHEMA}.dim_employees 
            WHERE is_active = TRUE
            ORDER BY employee_full_name ASC
        """
        response = w.statement_execution.execute_statement(
            warehouse_id=DATABRICKS_WAREHOUSE_ID,
            statement=sql_query
        )
        if response.result and response.result.data_array:
            return [
                {"employee_key": int(row[0]), "employee_full_name": row[1]} 
                for row in response.result.data_array
            ]
        return []
    except Exception as e:
        st.error(f"Failed to load employees: {e}")
        return []

# --- 2. FETCH EVENT TYPES FROM GOLD ---
@st.cache_data(ttl=300)
def get_event_type_options():
    try:
        sql_query = f"""
            SELECT event_type_key, event_type_name, event_category, affects_default_assignment
            FROM {CATALOG}.{GOLD_SCHEMA}.dim_events 
            ORDER BY event_type_key ASC
        """
        response = w.statement_execution.execute_statement(
            warehouse_id=DATABRICKS_WAREHOUSE_ID,
            statement=sql_query
        )
        if response.result and response.result.data_array:
            return [
                {
                    "event_type_key": int(row[0]),
                    "event_type_name": row[1],
                    "event_category": row[2],
                    "affects_default_assignment": bool(row[3])
                }
                for row in response.result.data_array
            ]
        return []
    except Exception as e:
        st.error(f"Failed to load event types: {e}")
        return []

# --- 3. FETCH PRODUCTION CELLS FROM GOLD ---
@st.cache_data(ttl=300)
def get_production_cell_options():
    try:
        sql_query = f"""
            SELECT cell_code, line_name, cell_name, cell_display_label 
            FROM {CATALOG}.{GOLD_SCHEMA}.dim_production_cells 
            ORDER BY line_name, cell_name ASC
        """
        response = w.statement_execution.execute_statement(
            warehouse_id=DATABRICKS_WAREHOUSE_ID,
            statement=sql_query
        )
        if response.result and response.result.data_array:
            return [
                {
                    "cell_code": row[0],
                    "line_name": row[1],
                    "cell_name": row[2],
                    "cell_display_label": row[3]
                }
                for row in response.result.data_array
            ]
        return []
    except Exception as e:
        st.error(f"Failed to load production cells: {e}")
        return []

# Load cached dimensions
employee_options = get_employee_options()
event_type_options = get_event_type_options()
cell_options = get_production_cell_options()

# --- FORM ---
with st.form("event_form", clear_on_submit=True):
    col1, col2 = st.columns(2)
    
    with col1:
        if employee_options:
            selected_employee = st.selectbox(
                "Employee",
                options=employee_options,
                format_func=lambda emp: f"{emp['employee_full_name']} (ID: {emp['employee_key']})"
            )
        else:
            selected_employee = None
            st.warning("No active employees found in dim_employees.")

        if event_type_options:
            selected_event = st.selectbox(
                "Event Type",
                options=event_type_options,
                format_func=lambda ev: f"{ev['event_type_name']} - {ev['event_category']}"
            )
        else:
            selected_event = {"event_type_name": "Other"}
            st.warning("No event types found in dim_events.")

        if cell_options:
            selected_cell = st.selectbox(
                "Target Cell (Optional)",
                options=[{"cell_code": "", "cell_display_label": "None / Not Applicable"}] + cell_options,
                format_func=lambda cell: cell["cell_display_label"]
            )
        else:
            selected_cell = {"cell_code": ""}

    with col2:
        start_date = st.date_input("Start Date", date.today())
        start_time = st.time_input("Start Time", time(8, 0))
        
        end_date = st.date_input("End Date", date.today())
        end_time = st.time_input("End Time", time(16, 0))
        
        fte = st.number_input("FTE Allocation", min_value=0.1, max_value=1.0, value=1.0, step=0.1)

    remarks = st.text_area("Operational Remarks")
    submit_button = st.form_submit_button("Save Event")

# --- SAVE LOGIC ---
if submit_button:
    start_dt_obj = datetime.combine(start_date, start_time)
    end_dt_obj = datetime.combine(end_date, end_time)
    
    if end_dt_obj <= start_dt_obj:
        st.error("End date and time must be later than start date and time.")
    elif not selected_employee:
        st.error("Employee selection is required.")
    else:
        emp_key = selected_employee["employee_key"]
        emp_name = selected_employee["employee_full_name"]
        chosen_event_name = selected_event["event_type_name"]
        chosen_cell_code = selected_cell["cell_code"] if selected_cell["cell_code"] else None

        entry_data = {
            "employee_key": emp_key,
            "employee_full_name": emp_name,
            "event_type": chosen_event_name,
            "swap_cell": chosen_cell_code,
            "start_datetime": start_dt_obj.isoformat(),
            "end_datetime": end_dt_obj.isoformat(),
            "fte": fte,
            "remarks": remarks.strip() if remarks else None,
            "ingested_at": datetime.now().isoformat(),
            "source": "streamlit_app"
        }

        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        filename = f"event_{timestamp_str}.json"

        try:
            full_cloud_path = f"/Volumes/{CATALOG}/{BRONZE_SCHEMA}/{VOLUME}/{filename}"
            json_bytes = json.dumps(entry_data, ensure_ascii=False, indent=4).encode('utf-8')

            w.files.upload(full_cloud_path, json_bytes)
            
            st.success(f"Event '{chosen_event_name}' for {emp_name} saved successfully as {filename}.")
            
        except Exception as e:
            st.error(f"Failed to upload event to Databricks Volume: {str(e)}")

st.divider()

# --- RECENT FILES PREVIEW ---
with st.expander("Recent Bronze Volume Files"):
    if st.button("Load File List"):
        try:
            cloud_dir_path = f"/Volumes/{CATALOG}/{BRONZE_SCHEMA}/{VOLUME}"
            files_list = w.files.list_directory_contents(cloud_dir_path)
            files = sorted([f.name for f in files_list], reverse=True)[:5]
            st.write(files)
        except Exception as e:
            st.warning(f"Unable to load files: {e}")