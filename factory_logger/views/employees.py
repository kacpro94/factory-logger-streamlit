import pandas as pd
import streamlit as st
import json
from datetime import datetime, date, time
from databricks.sdk import WorkspaceClient

CATALOG = "data_warehouse_factory"
SCHEMA = "bronze"
VOLUME = "employees_raw"

DATABRICKS_WAREHOUSE_ID = "f38fec74b09fdb89" 

st.title("Pracownicy")
st.info("Wprowadź dane poniżej. Zostaną one wysłane przez API wprost do warstwy Bronze w Databricks.")


with st.form("event_form", clear_on_submit=True):
    employee_full_name = st.text_input("Employee")
    work_center = st.selectbox("Work Center", ["Assembly", "Manufacturing", "Logistics", "Maintenance"])
    date_of_employment = st.date_input("Date of employment")
    date_of_leaving = st.date_input("Date of leaving")

    submit_button = st.form_submit_button("Zapisz do systemu")

# --- LOGIKA ZAPISU PRZEZ API (DATABRICKS SDK) ---
if submit_button:
    entry_data = {
        "employee_full_name": employee_full_name,
        "work_center": work_center,
        "date_of_employment": date_of_employment.isoformat(),
        "date_of_leaving": date_of_leaving.isoformat() if date_of_leaving else None,
        "ingested_at": datetime.now().isoformat()
    }

    # Wygenerowanie unikalnej nazwy pliku
    timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f"employee_{timestamp_str}.json"

    try:
        w = WorkspaceClient()
        full_cloud_path = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}/{filename}"
        json_bytes = json.dumps(entry_data, ensure_ascii=False, indent=4).encode('utf-8')

        w.files.upload(full_cloud_path, json_bytes)
        
        st.success(f"✅ Sukces! Plik {filename} został przesłany do Databricks!")
        st.balloons()
        
    except Exception as e:
        st.error(f"❌ Błąd komunikacji z Databricks: {str(e)}")

st.divider()

# --- SEKCJA: WYŚWIETLANIE LISTY PRACOWNIKÓW Z TABELI ---
st.subheader("📋 Lista pracowników z bazy danych (Databricks)")

# Przycisk do ręcznego odświeżania danych
if st.button("🔄 Odśwież listę pracowników"):
    st.rerun()

try:
    w = WorkspaceClient()
    
    # Zapytanie SQL do tabeli (możesz zmienić bronze_employees na silver_employees)
    sql_query = f"SELECT * FROM {CATALOG}.{SCHEMA}.bronze_employees ORDER BY _ingested_at DESC"

    # Wykonanie zapytania przez Statement Execution API
    response = w.statement_execution.execute_statement(
        warehouse_id=DATABRICKS_WAREHOUSE_ID,
        statement=sql_query
    )

    # Sprawdzamy czy zapytanie zwróciło dane
    if response.result and response.result.data_array:
        # Pobieramy nazwy kolumn
        columns = [col.name for col in response.manifest.schema.columns]
        
        # Tworzymy DataFrame
        df = pd.DataFrame(response.result.data_array, columns=columns)
        
        # Wyświetlamy tabelę w Streamlit
        st.dataframe(df, use_container_width=True)
    else:
        st.info("Tabela jest pusta lub brak wyników.")

except Exception as e:
        st.error(f"Nie udało się pobrać danych z tabeli SQL: {e}")

# --- PODGLĄD PLIKÓW W VOLUME ---
with st.expander("📁 Pokaż ostatnie pliki JSON w Volume"):
    try:
        w = WorkspaceClient()
        cloud_dir_path = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"
        
        files_list = w.files.list_directory_contents(cloud_dir_path)
        files = sorted([f.name for f in files_list], reverse=True)[:5]
        st.write(files)
    except Exception as e:
        st.warning(f"Nie można załadować listy plików: {e}")