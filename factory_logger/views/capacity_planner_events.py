import streamlit as st
import json
from datetime import datetime, date, time
from databricks.sdk import WorkspaceClient

# --- CONFIG DATA PLATFORM ---
# Podaj parametry swojego katalogu, schematu i Volume
CATALOG = "data_warehouse_factory"
SCHEMA = "bronze"
VOLUME = "capacity_planner_events_raw"

st.title("🏭 Rejestrator Zdarzeń Produkcyjnych")
st.info("Wprowadź dane poniżej. Zostaną one wysłane przez API wprost do warstwy Bronze w Databricks.")

# --- FORMULARZ ---
with st.form("event_form", clear_on_submit=True):
    col1, col2 = st.columns(2)
    
    with col1:
        id_entry = st.number_input("ID", min_value=1, step=1)
        employee = st.text_input("Employee (Imię i Nazwisko)")
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
    
    entry_data = {
        "id": id_entry,
        "employee": employee,
        "event_type": event_type,
        "swap_cell": swap_cell,
        "start_datetime": start_dt,
        "end_datetime": end_dt,
        "fte": fte,
        "remarks": remarks,
        "ingested_at": datetime.now().isoformat(),
        "source": "streamlit_app"
    }

    filename = f"event_{id_entry}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    try:
        
        w = WorkspaceClient()

        
        full_cloud_path = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}/{filename}"

        
        json_bytes = json.dumps(entry_data, ensure_ascii=False, indent=4).encode('utf-8')

      
        with w.files.upload(full_cloud_path, json_bytes) as response:
            pass
        
        st.success(f"✅ Sukces! Plik {filename} został przesłany do Databricks!")
        st.balloons()
        
    except Exception as e:
        st.error(f"❌ Błąd komunikacji z Databricks: {str(e)}")

# --- PODGLĄD DANYCH ---
# --- POPRAWIONY PODGLĄD DANYCH ---
if st.checkbox("Pokaż ostatnie wpisy w Volume"):
    try:
        w = WorkspaceClient()
        cloud_dir_path = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"
        
        # Listujemy katalog przez Files API
        files_list = w.files.list_directory_contents(cloud_dir_path)
        
        # Wyciągamy nazwy plików z obiektów DirectoryEntry i sortujemy
        files = sorted([f.name for f in files_list], reverse=True)[:5]
        st.write(files)
    except Exception as e:
        st.warning(f"Nie można załadować listy plików: {e}")