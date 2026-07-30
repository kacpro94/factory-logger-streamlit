import pandas as pd
import streamlit as st
import json
from datetime import datetime, date
from databricks.sdk import WorkspaceClient

# --- CONFIG DATA PLATFORM ---
CATALOG = "data_warehouse_factory"
SCHEMA_BRONZE = "bronze"
SCHEMA_SILVER = "silver"
VOLUME = "employees_raw"
DATABRICKS_WAREHOUSE_ID = "f38fec74b09fdb89" 

st.title("👥 Zarządzanie Pracownikami")

# --- REUSABLE CLIENT ---
@st.cache_resource
def get_databricks_client():
    return WorkspaceClient()

w = get_databricks_client()

# --- FUNKCJA Z KESZOWANIEM ZAPYTANIA SQL (ZMNIEJSZA ZUŻYCIE COMPUTE) ---
@st.cache_data(ttl=300)  # Kesz na 5 minut (300 sek)
def fetch_employees_from_sql():
    try:
        # Pobieramy tylko potrzebne kolumny biznesowe zamiast SELECT *
        sql_query = f"""
            SELECT employee_key, employee_full_name, work_center, date_of_employment, date_of_leaving 
            FROM {CATALOG}.{SCHEMA_SILVER}.silver_employees 
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
        st.error(f"Nie udało się pobrać danych z tabeli SQL: {e}")
        return pd.DataFrame()

# --- SEKCJA: WYŚWIETLANIE LISTY PRACOWNIKÓW ---
st.subheader("📋 Lista pracowników (Databricks Silver)")

col_refresh, _ = st.columns([1, 4])
with col_refresh:
    # Manualne czyszczenie cache i ponowne pobranie danych
    if st.button("🔄 Odśwież listę"):
        fetch_employees_from_sql.clear()
        st.rerun()

df_employees = fetch_employees_from_sql()

if not df_employees.empty:
    st.dataframe(df_employees, use_container_width=True)
else:
    st.info("Brak danych do wyświetlenia lub tabela jest pusta.")

st.divider()

# --- SEKCJA: FORMULARZ DANYCH ---
st.subheader("➕ Dodaj nowego pracownika")

with st.form("employee_form", clear_on_submit=True):
    employee_full_name = st.text_input("Imię i Nazwisko (Employee)")
    work_center = st.selectbox("Work Center", ["Assembly", "Manufacturing", "Logistics", "Maintenance"])
    date_of_employment = st.date_input("Date of employment", date.today())
    
    # 2. Dynamiczne wywoływanie kontrolek (checkbox + date_input)
    # Reaguje dynamicznie wewnątrz formularza, jeśli użyjemy pojedynczego kontenera / kolumny
    has_left = st.checkbox("Pracownik zakończył zatrudnienie")
    
    # Do wybrania daty używamy widżetu, który aktywuje się tylko przy zaznaczonym checkboxie
    date_of_leaving = None
    if has_left:
        date_of_leaving = st.date_input("Date of leaving", date.today())

    submit_button = st.form_submit_button("Zapisz pracownika")

# --- LOGIKA ZAPISU (UPLOAD DO VOLUME JEST DARMOWY / BEZ COMPUTE DBU) ---
if submit_button:
    if not employee_full_name.strip():
        st.warning("⚠️ Pole Imię i Nazwisko nie może być puste!")
    else:
        entry_data = {
            "employee_full_name": employee_full_name.strip(),
            "work_center": work_center,
            "date_of_employment": date_of_employment.isoformat(),
            "date_of_leaving": date_of_leaving.isoformat() if date_of_leaving else None,
            "ingested_at": datetime.now().isoformat(),
            "source": "streamlit_app"
        }

        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        filename = f"employee_{timestamp_str}.json"

        try:
            full_cloud_path = f"/Volumes/{CATALOG}/{SCHEMA_BRONZE}/{VOLUME}/{filename}"
            json_bytes = json.dumps(entry_data, ensure_ascii=False, indent=4).encode('utf-8')

            # Wysyłka pliku przez Files API nie zużywa DBU SQL Warehouse!
            w.files.upload(full_cloud_path, json_bytes)
            
            st.success(f"✅ Dodano pracownika {employee_full_name}! Plik {filename} zapisany w Volume.")
            st.balloons()
            
            # Unieważniamy cache po dodaniu nowego pracownika
            fetch_employees_from_sql.clear()
            
        except Exception as e:
            st.error(f"❌ Błąd zapisu pliku: {str(e)}")

st.divider()

# --- PODGLĄD PLIKÓW W VOLUME (OPCJONALNY, W EXPANDERZE) ---
with st.expander("📁 Podgląd ostatnich plików w Volume"):
    if st.button("Załaduj listę plików"):
        try:
            cloud_dir_path = f"/Volumes/{CATALOG}/{SCHEMA_BRONZE}/{VOLUME}"
            files_list = w.files.list_directory_contents(cloud_dir_path)
            files = sorted([f.name for f in files_list], reverse=True)[:5]
            st.write(files)
        except Exception as e:
            st.warning(f"Nie można załadować listy plików: {e}")