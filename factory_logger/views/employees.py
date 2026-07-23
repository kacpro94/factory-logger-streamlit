import streamlit as st
import json
from datetime import datetime, date, time
from databricks.sdk import WorkspaceClient

# --- CONFIG DATA PLATFORM ---
# Podaj parametry swojego katalogu, schematu i Volume
CATALOG = "data_warehouse_factory"
SCHEMA = "bronze"
VOLUME = "employees_raw"

st.title("Pracownicy")
st.info("Wprowadź dane poniżej. Zostaną one wysłane przez API wprost do warstwy Bronze w Databricks.")

# --- FORMULARZ ---
with st.form("event_form", clear_on_submit=True):

    #id_entry = st.number_input("ID", min_value=1, step=1)
    employee_full_name = st.text_input("Employee")
    work_center = st.selectbox("Work Center", ["Assembly","Manufacturing","Logistics","Maintenance"])
    date_of_employment = st.date_input("Date of employment")
    date_of_leaving = st.date_input("Date of leaving")

    submit_button = st.form_submit_button("Zapisz do systemu")

# --- LOGIKA ZAPISU PRZEZ API (DATABRICKS SDK) ---
if submit_button:
    
    next_id = 1
    
    # 2. Tworzymy słownik z danymi pracownika
    entry_data = {
        "employee_id": next_id,
        "employee_full_name": employee_full_name,
        "work_center": work_center,
        "date_of_employment": date_of_employment.isoformat(), # Konwertujemy datę na tekst ISO
        "date_of_leaving": date_of_leaving.isoformat() if date_of_leaving else None,
        "ingested_at": datetime.now().isoformat()
    }

    filename = f"employee_{next_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

    try:
        
        w = WorkspaceClient()

        
        full_cloud_path = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}/{filename}"

        
        json_bytes = json.dumps(entry_data, ensure_ascii=False, indent=4).encode('utf-8')

      
        w.files.upload(full_cloud_path, json_bytes)
        
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