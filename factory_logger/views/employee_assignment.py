import pandas as pd
import streamlit as st
import json
from datetime import datetime, date
from databricks.sdk import WorkspaceClient

# --- CONFIG DATA PLATFORM ---
CATALOG = "data_warehouse_factory"
SCHEMA_BRONZE = "bronze"
SCHEMA_SILVER = "silver"
VOLUME = "employee_assignments_raw"
DATABRICKS_WAREHOUSE_ID = "f38fec74b09fdb89" 

st.title("👤 Edycja Przypisania Pracowników do Komórek")
st.info("Zdefiniuj domyślną oraz zastępczą obsadę personalną dla poszczególnych linii i komórek produkcyjnych.")

# --- REUSABLE CLIENT ---
@st.cache_resource
def get_databricks_client():
    return WorkspaceClient()

w = get_databricks_client()

# --- FUNKCJE POBIERAJĄCE DANE Z KESZEM (DLA OPTYMALIZACJI COMPUTE DBU) ---
@st.cache_data(ttl=300)
def fetch_production_structure():
    """Pobiera strukturę linii i komórek z warstwy Bronze."""
    try:
        sql_query = f"""
            SELECT DISTINCT line_code, line_name, cell_code, cell_name 
            FROM {CATALOG}.{SCHEMA_BRONZE}.bronze_production_structure
            WHERE line_code IS NOT NULL AND cell_code IS NOT NULL
            ORDER BY line_code, cell_code
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
        st.error(f"⚠️ Nie udało się pobrać struktury produkcyjnej: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=300)
def fetch_employees_list():
    """Pobiera listę pracowników z tabeli Silver wraz z employee_key."""
    try:
        sql_query = f"""
            SELECT employee_key, employee_full_name, work_center
            FROM {CATALOG}.{SCHEMA_SILVER}.silver_employees
            WHERE employee_full_name IS NOT NULL AND (date_of_leaving IS NULL OR date_of_leaving > CURRENT_DATE())
            ORDER BY employee_full_name ASC
        """
        response = w.statement_execution.execute_statement(
            warehouse_id=DATABRICKS_WAREHOUSE_ID,
            statement=sql_query
        )
        if response.result and response.result.data_array:
            return [
                {
                    "employee_key": int(row[0]), 
                    "employee_full_name": row[1],
                    "work_center": row[2]
                }
                for row in response.result.data_array
            ]
        return []
    except Exception as e:
        st.error(f"⚠️ Nie udało się pobrać listy pracowników: {e}")
        return []

# Pobranie danych referencyjnych
df_structure = fetch_production_structure()
employees_options = fetch_employees_list()

if df_structure.empty or not employees_options:
    st.warning("⚠️ Brak wystarczających danych referencyjnych w bazy (sprawdź strukturę oraz listę pracowników).")

# --- KONTROLKI SELEKCJI STRUKTURY (POZA FORMULARZEM) ---
st.subheader("1. Wybór Linii i Komórki Produkcyjnej")

col_line, col_cell = st.columns(2)

with col_line:
    lines_list = df_structure['line_code'].unique().tolist() if not df_structure.empty else []
    selected_line_code = st.selectbox(
        "Linia Produkcyjna",
        options=lines_list,
        format_func=lambda code: f"{code} - {df_structure[df_structure['line_code']==code]['line_name'].iloc[0]}" if not df_structure.empty else code
    )

# Filtrowanie dostępnych komórek dla wybranej linii
filtered_cells = []
if selected_line_code and not df_structure.empty:
    filtered_cells = df_structure[df_structure['line_code'] == selected_line_code]['cell_code'].unique().tolist()

with col_cell:
    selected_cell_code = st.selectbox(
        "Komórka Produkcyjna (Swap Cell)",
        options=filtered_cells,
        format_func=lambda code: f"{code} - {df_structure[df_structure['cell_code']==code]['cell_name'].iloc[0]}" if not df_structure.empty else code
    )

st.divider()

# --- FORMULARZ PRZYPISANIA OBSADY ---
st.subheader("2. Obsada Personalna")

# Opcja "Brak obsady" dla zapasowych pracowników
EMPTY_EMPLOYEE = {"employee_key": None, "employee_full_name": "-- Brak --", "work_center": ""}
employees_options_with_empty = [EMPTY_EMPLOYEE] + employees_options

with st.form("assignment_form", clear_on_submit=False):
    col_def, col_back = st.columns(2)

    with col_def:
        st.markdown("**Główny Pracownik (Default Employee)**")
        default_emp = st.selectbox(
            "Wybierz pracownika domyślnego",
            options=employees_options,
            format_func=lambda emp: f"[{emp['employee_key']}] {emp['employee_full_name']} ({emp['work_center']})"
        )

    with col_back:
        st.markdown("**Zapasowy Pracownik (Backup / Rezerwa)**")
        backup_emp = st.selectbox(
            "Wybierz pracownika zapasowego",
            options=employees_options_with_empty,
            format_func=lambda emp: f"[{emp['employee_key']}] {emp['employee_full_name']} ({emp['work_center']})" if emp['employee_key'] else emp['employee_full_name']
        )

    col_meta1, col_meta2 = st.columns(2)
    with col_meta1:
        valid_from = st.date_input("Obowiązuje od dnia", date.today())
    with col_meta2:
        shift = st.selectbox("Zmiana", ["Wszystkie zmiany", "Zmiana I (Rano)", "Zmiana II (Popołudnie)", "Zmiana III (Noc)"])

    remarks = st.text_area("Uwagi do przypisania (np. powód zastępstwa, wymagane uprawnienia)")

    submit_button = st.form_submit_button("Zapisz przypisanie")

# --- LOGIKA ZAPISU DO DATABRICKS VOLUME ---
if submit_button:
    if not selected_line_code or not selected_cell_code:
        st.error("❌ Wybierz prawidłową Linię oraz Komórkę Produkcyjną!")
    elif default_emp and backup_emp and default_emp["employee_key"] == backup_emp["employee_key"]:
        st.warning("⚠️ Pracownik domyślny i zapasowy nie mogą być tą samą osobą!")
    else:
        line_name_val = df_structure[df_structure['line_code'] == selected_line_code]['line_name'].iloc[0] if not df_structure.empty else ""
        cell_name_val = df_structure[df_structure['cell_code'] == selected_cell_code]['cell_name'].iloc[0] if not df_structure.empty else ""

        assignment_payload = {
            "line_code": selected_line_code,
            "line_name": line_name_val,
            "cell_code": selected_cell_code,
            "cell_name": cell_name_val,
            "default_employee_key": default_emp["employee_key"] if default_emp else None,
            "default_employee_name": default_emp["employee_full_name"] if default_emp else None,
            "backup_employee_key": backup_emp["employee_key"] if backup_emp else None,
            "backup_employee_name": backup_emp["employee_full_name"] if backup_emp else None,
            "valid_from": valid_from.isoformat(),
            "shift": shift,
            "remarks": remarks,
            "ingested_at": datetime.now().isoformat(),
            "source": "streamlit_assignments_app"
        }

        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        filename = f"assign_{selected_line_code}_{selected_cell_code}_{timestamp_str}.json"

        try:
            full_cloud_path = f"/Volumes/{CATALOG}/{SCHEMA_BRONZE}/{VOLUME}/{filename}"
            json_bytes = json.dumps(assignment_payload, ensure_ascii=False, indent=4).encode('utf-8')

            # Bezkosztowy upload do Volume API
            w.files.upload(full_cloud_path, json_bytes)

            st.success(f"✅ Przypisano obsadę dla komórki `{selected_cell_code}`! Plik `{filename}` wysłany do Databricks.")
            st.balloons()
        except Exception as e:
            st.error(f"❌ Błąd zapisu do Databricks Volume: {e}")

st.divider()

# --- PODGLĄD W VOLUME ---
with st.expander("📁 Ostatnie pliki przypisań w Volume"):
    if st.button("Pokaż ostatnie pliki"):
        try:
            cloud_dir_path = f"/Volumes/{CATALOG}/{SCHEMA_BRONZE}/{VOLUME}"
            files_list = w.files.list_directory_contents(cloud_dir_path)
            files = sorted([f.name for f in files_list], reverse=True)[:5]
            st.write(files)
        except Exception as e:
            st.warning(f"Nie można załadować listy plików: {e}")