import pandas as pd
import streamlit as st
import json
from datetime import datetime, date, time
from databricks.sdk import WorkspaceClient

# --- CONFIG DATA PLATFORM ---
CATALOG = "data_warehouse_factory"
SCHEMA_BRONZE = "bronze"
SCHEMA_SILVER = "silver"  # Możesz zmienić na BRONZE jeśli nie masz jeszcze tabeli Silver
VOLUME = "production_plan_raw"
DATABRICKS_WAREHOUSE_ID = "f38fec74b09fdb89" 


st.title("📅 Plan Pracy Linii i Komórek (Production Plan)")
st.info("Zdefiniuj okna czasowe i statusy pracy dla poszczególnych komórek produkcyjnych.")

# --- REUSABLE CLIENT ---
@st.cache_resource
def get_databricks_client():
    return WorkspaceClient()

w = get_databricks_client()

# --- FUNKCJA POBIERAJĄCA STRUKTURĘ PRODUKCYJNĄ (Z KESZEM) ---
@st.cache_data(ttl=300)
def fetch_production_structure():
    try:
        # Pobieramy unikalne parowania linii i komórek z bazy
        sql_query = f"""
            SELECT DISTINCT 
                line_code, 
                line_name, 
                cell_code, 
                cell_name 
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

df_structure = fetch_production_structure()

if df_structure.empty:
    st.warning("⚠️ Brak danych o liniach i komórkach w tabeli `bronze_production_structure`.")

# --- SEKCJA KONTROLEK SELEKCJI (POZA FORMULARZEM DLA DYNAMICZNEGO PRZEŁADOWANIA) ---
st.subheader("1. Wybór obszaru produkcyjnego")

col_line, col_cell = st.columns(2)

with col_line:
    lines_list = df_structure['line_code'].unique().tolist() if not df_structure.empty else []
    
    # Przechowujemy etykietę: "LINE_01 - Linia Montażu A"
    selected_line_code = st.selectbox(
        "Linia Produkcyjna",
        options=lines_list,
        format_func=lambda code: f"{code} - {df_structure[df_structure['line_code']==code]['line_name'].iloc[0]}" if not df_structure.empty else code
    )

# Filtrowanie komórek na podstawie wybranej linii
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

# --- SEKCJA FORMULARZA PLANOWANIA ---
st.subheader("2. Szczegóły Planu Pracy")

with st.form("production_plan_form", clear_on_submit=True):
    col_start, col_end = st.columns(2)

    with col_start:
        st.markdown("**Rozpoczęcie**")
        start_date = st.date_input("Data rozpoczęcia", date.today())
        start_time = st.time_input("Godzina rozpoczęcia", time(6, 0))

    with col_end:
        st.markdown("**Zakończenie**")
        end_date = st.date_input("Data zakończenia", date.today())
        end_time = st.time_input("Godzina zakończenia", time(14, 0))

    col_meta1, col_meta2 = st.columns(2)
    with col_meta1:
        plan_status = st.selectbox(
            "Status Planu", 
            ["Planowana Produkcja", "Przegląd Techniczny", "Postój Wyłączony", "Praca na pół gwizdka"]
        )
    with col_meta2:
        planned_capacity = st.number_input("Planowane obciążenie / Wydajność (%)", min_value=0, max_value=100, value=100, step=5)

    remarks = st.text_area("Uwagi do planu (np. zmiana, produkty)")

    submit_button = st.form_submit_button("Zapisz Plan Produkcji")

# --- LOGIKA ZAPISU DO DATABRICKS VOLUME ---
if submit_button:
    if not selected_line_code or not selected_cell_code:
        st.error("❌ Musisz wybrać Linię oraz Komórkę Produkcyjną!")
    else:
        start_dt = datetime.combine(start_date, start_time).isoformat()
        end_dt = datetime.combine(end_date, end_time).isoformat()

        # Wyciągamy nazwy dla kompletności payloadu
        line_name_val = df_structure[df_structure['line_code'] == selected_line_code]['line_name'].iloc[0] if not df_structure.empty else ""
        cell_name_val = df_structure[df_structure['cell_code'] == selected_cell_code]['cell_name'].iloc[0] if not df_structure.empty else ""

        plan_data = {
            "line_code": selected_line_code,
            "line_name": line_name_val,
            "cell_code": selected_cell_code,
            "cell_name": cell_name_val,
            "start_datetime": start_dt,
            "end_datetime": end_dt,
            "plan_status": plan_status,
            "planned_capacity_pct": planned_capacity,
            "remarks": remarks,
            "ingested_at": datetime.now().isoformat(),
            "source": "streamlit_production_planner"
        }

        timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        filename = f"plan_{selected_line_code}_{selected_cell_code}_{timestamp_str}.json"

        try:
            full_cloud_path = f"/Volumes/{CATALOG}/{SCHEMA_BRONZE}/{VOLUME}/{filename}"
            json_bytes = json.dumps(plan_data, ensure_ascii=False, indent=4).encode('utf-8')

            # Wysyłka bez zużycia DBU SQL Warehouse
            w.files.upload(full_cloud_path, json_bytes)

            st.success(f"✅ Zapisano plan dla komórki {selected_cell_code}! Plik `{filename}` wysłany do Databricks.")
            st.balloons()
        except Exception as e:
            st.error(f"❌ Błąd zapisu do Databricks Volume: {e}")

st.divider()

# --- PODGLĄD DANYCH Z VOLUME ---
with st.expander("📁 Ostatnie pliki planów w Volume"):
    if st.button("Pokaż ostatnie wpisy"):
        try:
            cloud_dir_path = f"/Volumes/{CATALOG}/{SCHEMA_BRONZE}/{VOLUME}"
            files_list = w.files.list_directory_contents(cloud_dir_path)
            files = sorted([f.name for f in files_list], reverse=True)[:5]
            st.write(files)
        except Exception as e:
            st.warning(f"Nie można załadować listy plików: {e}")