import io
import json
import time
from datetime import date, datetime, time as dtime
import pandas as pd
import streamlit as st
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

# --- UNIWERSALNA FUNKCJA Z OBSŁUGĄ COLD STARTU ---
def execute_sql_safely(sql_query: str) -> pd.DataFrame:
    """Wykonuje zapytanie SQL, bezpiecznie czekając na wybudzenie klastra."""
    # Usunięto on_wait_timeout="CONTINUE" - SDK domyślnie podtrzymuje zapytanie przy podanym wait_timeout
    response = w.statement_execution.execute_statement(
        warehouse_id=DATABRICKS_WAREHOUSE_ID,
        statement=sql_query,
        wait_timeout="50s"
    )
    
    statement_id = response.statement_id
    
    # Odpytywanie w pętli statusu jako stringa (odporne na wersje Enuma w SDK)
    while True:
        state_str = str(response.status.state).upper()
        if any(s in state_str for s in ["PENDING", "RUNNING"]):
            time.sleep(2)
            response = w.statement_execution.get_statement(statement_id)
        else:
            break
            
    final_state = str(response.status.state).upper()
    if "SUCCEEDED" not in final_state:
        error_msg = response.status.error.message if response.status.error else "Unknown execution error"
        raise RuntimeError(f"Databricks SQL Failed ({final_state}): {error_msg}")
        
    if response.result and response.result.data_array:
        columns = [col.name for col in response.manifest.schema.columns]
        return pd.DataFrame(response.result.data_array, columns=columns)
        
    return pd.DataFrame()

# --- 1. FETCH ACTIVE EMPLOYEES FROM GOLD ---
@st.cache_data(ttl=300, show_spinner=False)
def get_employee_options():
    sql_query = f"""
        SELECT CAST(employee_key AS INT), employee_full_name 
        FROM {CATALOG}.{GOLD_SCHEMA}.dim_employees 
        WHERE is_active = TRUE
        ORDER BY employee_full_name ASC
    """
    df = execute_sql_safely(sql_query)
    if not df.empty:
        return [
            {"employee_key": int(row[0]), "employee_full_name": str(row[1])} 
            for row in df.values
        ]
    return []

# --- 2. FETCH EVENT TYPES FROM GOLD ---
@st.cache_data(ttl=300, show_spinner=False)
def get_event_type_options():
    sql_query = f"""
        SELECT 
            event_type_key, 
            event_type_name, 
            event_category, 
            coalesce(is_swap, FALSE) AS is_swap
        FROM {CATALOG}.{GOLD_SCHEMA}.dim_events 
        ORDER BY event_type_key ASC
    """
    df = execute_sql_safely(sql_query)
    if not df.empty:
        return [
            {
                "event_type_key": int(row[0]),
                "event_type_name": str(row[1]),
                "event_category": str(row[2]),
                # Bezpieczna konwersja boolean (chroni przed bool("false") == True)
                "is_swap": str(row[3]).lower() in ["true", "1"]
            }
            for row in df.values
        ]
    return []

# --- 3. FETCH PRODUCTION CELLS FROM GOLD ---
@st.cache_data(ttl=300, show_spinner=False)
def get_production_cell_options():
    sql_query = f"""
        SELECT cell_code, line_name, cell_name, cell_display_label 
        FROM {CATALOG}.{GOLD_SCHEMA}.dim_production_cells 
        ORDER BY line_name, cell_name ASC
    """
    df = execute_sql_safely(sql_query)
    if not df.empty:
        return [
            {
                "cell_code": str(row[0]),
                "line_name": str(row[1]),
                "cell_name": str(row[2]),
                "cell_display_label": str(row[3])
            }
            for row in df.values
        ]
    return []

# --- INICJALIZACJA DANYCH ---
with st.spinner("Łączenie z hurtownią danych (wybudzanie SQL Warehouse)..."):
    try:
        employee_options = get_employee_options()
        event_type_options = get_event_type_options()
        cell_options = get_production_cell_options()
    except Exception as e:
        st.error(f"Nie udało się pobrać danych konfiguracyjnych: {e}")
        employee_options, event_type_options, cell_options = [], [], []

# --- FORMULARZ / INTERFEJS ---
col_left, col_right = st.columns([1, 1], gap="large")

with col_left:
    st.subheader("1. Event Details")
    
    if employee_options:
        selected_employee = st.selectbox(
            "Employee",
            options=employee_options,
            format_func=lambda emp: f"{emp['employee_full_name']} (ID: {emp['employee_key']})"
        )
    else:
        selected_employee = None
        st.warning("No active employees available.")

    if event_type_options:
        selected_event = st.selectbox(
            "Event Type",
            options=event_type_options,
            format_func=lambda ev: f"{ev['event_type_name']} ({ev['event_category']})"
        )
    else:
        selected_event = {"event_type_name": "Inne", "is_swap": False}

    is_swap_event = selected_event.get("is_swap", False) or "Swap" in selected_event.get("event_type_name", "")
    
    if is_swap_event:
        st.markdown("**Target Station Assignment (Required for Swap)**")
        selected_cell = st.selectbox(
            "Target Cell",
            options=cell_options,
            format_func=lambda cell: cell["cell_display_label"]
        )
    else:
        selected_cell = None
        st.caption("Target cell is not required for non-swap events.")

with col_right:
    st.subheader("2. Schedule & Operational Scope")
    
    col_d1, col_t1 = st.columns(2)
    with col_d1:
        start_date = st.date_input("Start Date", date.today())
    with col_t1:
        start_time = st.time_input("Start Time", dtime(6, 0))

    col_d2, col_t2 = st.columns(2)
    with col_d2:
        end_date = st.date_input("End Date", date.today())
    with col_t2:
        end_time = st.time_input("End Time", dtime(14, 0))

    fte = st.number_input("FTE Allocation", min_value=0.1, max_value=1.0, value=1.0, step=0.1)
    remarks = st.text_area("Operational Remarks (Optional)")

st.divider()

# --- ZAPIS DO BRONZE VOLUME ---
col_save, col_refresh = st.columns([1, 4])
with col_save:
    submit_button = st.button("Save Production Event", type="primary", use_container_width=True)

if submit_button:
    start_dt_obj = datetime.combine(start_date, start_time)
    end_dt_obj = datetime.combine(end_date, end_time)

    if end_dt_obj <= start_dt_obj:
        st.error("End date and time must be later than start date and time.")
    elif not selected_employee:
        st.error("Employee selection is required.")
    elif is_swap_event and not selected_cell:
        st.error("Target Cell is mandatory when logging a Role Swap.")
    else:
        emp_key = int(selected_employee["employee_key"])
        emp_name = selected_employee["employee_full_name"]
        chosen_event_name = selected_event["event_type_name"]
        chosen_cell_code = selected_cell["cell_code"] if (is_swap_event and selected_cell) else None

        entry_data = {
            "employee_key": emp_key,
            "employee_full_name": emp_name,
            "event_type": chosen_event_name,
            "swap_cell": chosen_cell_code,
            "start_datetime": start_dt_obj.isoformat(),
            "end_datetime": end_dt_obj.isoformat(),
            "fte": float(fte),
            "remarks": remarks.strip() if remarks else None,
            "ingested_at": datetime.now().isoformat(),
            "source": "streamlit_event_logger"
        }

        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        filename = f"event_{emp_key}_{timestamp_str}.json"

        try:
            full_cloud_path = f"/Volumes/{CATALOG}/{BRONZE_SCHEMA}/{VOLUME}/{filename}"
            json_str = json.dumps(entry_data, ensure_ascii=False, indent=4)
            bytes_stream = io.BytesIO(json_str.encode('utf-8'))
            
            w.files.upload(full_cloud_path, bytes_stream, overwrite=True)
            st.success(f"Zapisano zdarzenie '{chosen_event_name}' dla pracownika: {emp_name}.")
        except Exception as e:
            st.error(f"Błąd podczas zapisu do Databricks Volume: {e}")

st.divider()

# --- DIAGNOSTYKA I CZYSZCZENIE CACHE ---
with st.expander("Narzędzia diagnostyczne i pliki Volume"):
    col_btn1, col_btn2 = st.columns(2)
    with col_btn1:
        if st.button("Wymuś odświeżenie słowników (Clear Cache)"):
            get_employee_options.clear()
            get_event_type_options.clear()
            get_production_cell_options.clear()
            st.rerun()
            
    with col_btn2:
        if st.button("Pokaż ostatnie pliki w Volume"):
            try:
                cloud_dir_path = f"/Volumes/{CATALOG}/{BRONZE_SCHEMA}/{VOLUME}"
                files_list = w.files.list_directory_contents(cloud_dir_path)
                files = sorted([f.name for f in files_list], reverse=True)[:5]
                st.write(files)
            except Exception as e:
                st.warning(f"Nie udało się pobrać listy plików: {e}")