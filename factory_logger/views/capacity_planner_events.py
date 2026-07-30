import streamlit as st
import json
import pandas as pd
from datetime import datetime, date, time
from databricks.sdk import WorkspaceClient

# --- CONFIG DATA PLATFORM ---
CATALOG = "data_warehouse_factory"
BRONZE_SCHEMA = "bronze"
SILVER_SCHEMA = "silver"
VOLUME = "capacity_planner_events_raw"
DATABRICKS_WAREHOUSE_ID = "f38fec74b09fdb89" 

st.title("🏭 Rejestrator Zdarzeń Produkcyjnych")
st.info("Wprowadź dane poniżej. Zostaną one wysłane przez API wprost do warstwy Bronze w Databricks.")

# --- FUNKCJA POBIERAJĄCA PRACOWNIKÓW Z TABELI SILVER ---
@st.cache_data(ttl=60)
def get_employee_options():
    try:
        w = WorkspaceClient()
        sql_query = f"""
            SELECT employee_key, employee_full_name 
            FROM {CATALOG}.{SILVER_SCHEMA}.silver_employees 
            WHERE employee_full_name IS NOT NULL AND employee_full_name != ''
            ORDER BY employee_key ASC
        """
        response = w.statement_execution.execute_statement(
            warehouse_id=DATABRICKS_WAREHOUSE_ID,
            statement=sql_query
        )
        
        if response.result and response.result.data_array:
            employees = [
                {"employee_key": int(row[0]), "employee_full_name": row[1]} 
                for row in response.result.data_array
            ]
            return employees
        return []
    except Exception as e:
        st.error(f"⚠️ Błąd podczas pobierania listy pracowników z Silver: {e}")
        return []

# Pobranie listy słowników z pracownikami
employee_options = get_employee_options()

# --- FORMULARZ ---
with st.form("event_form", clear_on_submit=True):
    col1, col2 = st.columns(2)
    
    with col1:
        # Wybór pracownika wg klucza ID z Silver
        if employee_options:
            selected_employee = st.selectbox(
                "Pracownik (ID - Imię i Nazwisko)",
                options=employee_options,
                format_func=lambda emp: f"[{emp['employee_key']}] {emp['employee_full_name']}"
            )
        else:
            selected_employee = None
            st.warning("Brak pracowników w tabeli Silver lub brak połączenia.")
            
        event_type = st.selectbox("Event Type", ["Awaria", "Przerwa", "Zmiana oprzyrządowania", "Inne"])
        swap_cell = st.text_input("Swap Cell (Komórka)")

    with col2:
        start_date = st.date_input("Start Date", date.today())
        start_time = st.time_input("Start Time", time(8, 0))
        
        end_date = st.date_input("End Date", date.today())
        end_time = st.time_input("End Time", time(16, 0))
        
        fte = st.number_input("FTE", min_value=0.0, max_value=10.0, value=1.0, step=0.1)

    remarks = st.text_area("Remarks (Uwagi)")
    submit_button = st.form_submit_button("Zapisz do systemu")

# --- LOGIKA ZAPISU PRZEZ API (DATABRICKS SDK) ---
if submit_button:
    start_dt = datetime.combine(start_date, start_time).isoformat()
    end_dt = datetime.combine(end_date, end_time).isoformat()
    
    emp_key = selected_employee["employee_key"] if selected_employee else None
    emp_name = selected_employee["employee_full_name"] if selected_employee else None

    # Zbiór danych bez pola ID
    entry_data = {
        "employee_key": emp_key,
        "employee_full_name": emp_name,
        "event_type": event_type,
        "swap_cell": swap_cell,
        "start_datetime": start_dt,
        "end_datetime": end_dt,
        "fte": fte,
        "remarks": remarks,
        "ingested_at": datetime.now().isoformat(),
        "source": "streamlit_app"
    }

    # Nazwa pliku generowana wyłącznie na podstawie znacznika czasu
    timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    filename = f"event_{timestamp_str}.json"

    try:
        w = WorkspaceClient()
        full_cloud_path = f"/Volumes/{CATALOG}/{BRONZE_SCHEMA}/{VOLUME}/{filename}"
        json_bytes = json.dumps(entry_data, ensure_ascii=False, indent=4).encode('utf-8')

        w.files.upload(full_cloud_path, json_bytes)
        
        st.success(f"✅ Sukces! Zdarzenie zostało przesłane do Databricks jako {filename}")
        st.balloons()
        
    except Exception as e:
        st.error(f"❌ Błąd komunikacji z Databricks: {str(e)}")

# --- PODGLĄD DANYCH ---
if st.checkbox("Pokaż ostatnie wpisy w Volume"):
    try:
        w = WorkspaceClient()
        cloud_dir_path = f"/Volumes/{CATALOG}/{BRONZE_SCHEMA}/{VOLUME}"
        
        files_list = w.files.list_directory_contents(cloud_dir_path)
        files = sorted([f.name for f in files_list], reverse=True)[:5]
        st.write(files)
    except Exception as e:
        st.warning(f"Nie można załadować listy plików: {e}")