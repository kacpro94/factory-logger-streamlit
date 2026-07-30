import pandas as pd
import streamlit as st
import json
from datetime import datetime
from databricks.sdk import WorkspaceClient

# --- CONFIG DATA PLATFORM ---
CATALOG = "data_warehouse_factory"
SCHEMA = "bronze"
VOLUME = "lines_cells_raw"  # Dedykowany Volume na strukturę produkcyjną
DATABRICKS_WAREHOUSE_ID = "f38fec74b09fdb89"  # ⚠️ Podaj ID swojego SQL Warehouse z Databricks

st.title("🏭 Linie i Komórki Produkcyjne")
st.info("Zarządzaj liniami produkcyjnymi (np. A01) oraz przypisanymi do nich komórkami (np. A01-C01).")

# --- SEKCJA 1: FORMULARZE REJESTRACJI ---
col1, col2 = st.columns(2)

with col1:
    st.subheader("➕ Nowa Linia Produkcyjna")
    with st.form("form_line", clear_on_submit=True):
        line_code = st.text_input("Kod Linii (np. A01)", help="Wprowadź unikalny kod linii produkcyjnej").strip().upper()
        line_name = st.text_input("Nazwa / Opis Linii", help="np. Linia Montażu Głównego")
        submit_line = st.form_submit_button("Dodaj Linię")

    if submit_line:
        if not line_code:
            st.warning("⚠️ Kod linii jest wymagany!")
        else:
            payload = {
                "record_type": "PRODUCTION_LINE",
                "line_code": line_code,
                "line_name": line_name,
                "ingested_at": datetime.now().isoformat()
            }
            
            timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"line_{line_code}_{timestamp_str}.json"
            
            try:
                w = WorkspaceClient()
                full_path = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}/{filename}"
                json_bytes = json.dumps(payload, ensure_ascii=False, indent=4).encode('utf-8')
                
                w.files.upload(full_path, json_bytes)
                st.success(f"✅ Dodano linię **{line_code}**! Plik: `{filename}`")
                st.rerun()
            except Exception as e:
                st.error(f"❌ Błąd zapisu do Volume: {e}")

with col2:
    st.subheader("🔗 Nowa Komórka Produkcyjna")
    with st.form("form_cell", clear_on_submit=True):
        parent_line_code = st.text_input("Kod Linii Nadrzędnej (np. A01)", help="Wprowadź kod linii, do której przypisujesz komórkę").strip().upper()
        cell_suffix = st.text_input("Suffix / Identyfikator Komórki (np. C01)", help="np. C01").strip().upper()
        cell_name = st.text_input("Nazwa / Opis Komórki", help="np. Stacja Spawania")
        submit_cell = st.form_submit_button("Dodaj i Przypnij Komórkę")

    if submit_cell:
        if not parent_line_code or not cell_suffix:
            st.warning("⚠️ Wymagany jest zarówno kod linii, jak i suffix komórki!")
        else:
            # Tworzenie pełnego kodu komórki np. A01-C01
            full_cell_code = f"{parent_line_code}-{cell_suffix}" if not cell_suffix.startswith(parent_line_code) else cell_suffix
            
            payload = {
                "record_type": "PRODUCTION_CELL",
                "line_code": parent_line_code,
                "cell_code": full_cell_code,
                "cell_name": cell_name,
                "ingested_at": datetime.now().isoformat()
            }
            
            timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"cell_{full_cell_code}_{timestamp_str}.json"
            
            try:
                w = WorkspaceClient()
                full_path = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}/{filename}"
                json_bytes = json.dumps(payload, ensure_ascii=False, indent=4).encode('utf-8')
                
                w.files.upload(full_path, json_bytes)
                st.success(f"✅ Przypięto komórkę **{full_cell_code}** do linii **{parent_line_code}**!")
                st.rerun()
            except Exception as e:
                st.error(f"❌ Błąd zapisu do Volume: {e}")

st.divider()

# --- SEKCJA 2: WYŚWIETLANIE STRUKTURY Z DATABRICKS ---
st.subheader("📊 Aktualna Struktura Produkcyjna (Databricks SQL)")

if st.button("🔄 Odśwież dane z bazy"):
    st.rerun()

try:
    w = WorkspaceClient()
    
    # Zapytanie łączące linie i przypisane komórki z tabeli Bronze lub Silver
    sql_query = f"""
        SELECT 
            line_code AS `Kod Linii`,
            line_name AS `Nazwa Linii`,
            cell_code AS `Kod Komórki`,
            cell_name AS `Nazwa Komórki`,
            _ingested_at AS `Data Zgłoszenia`
        FROM {CATALOG}.{SCHEMA}.bronze_production_structure
        ORDER BY line_code ASC, cell_code ASC
    """

    response = w.statement_execution.execute_statement(
        warehouse_id=DATABRICKS_WAREHOUSE_ID,
        statement=sql_query
    )

    if response.result and response.result.data_array:
        columns = [col.name for col in response.manifest.schema.columns]
        df = pd.DataFrame(response.result.data_array, columns=columns)
        
        # Widok tabelaryczny
        st.dataframe(df, use_container_width=True)
        
        # Widok ustrukturyzowany / drzewiasty
        with st.expander("🌳 Widok hierarchiczny (Linia -> Komórki)"):
            lines = df['Kod Linii'].unique()
            for line in lines:
                sub_df = df[df['Kod Linii'] == line]
                st.markdown(f"**Linia `{line}`**")
                cells = sub_df['Kod Komórki'].dropna().tolist()
                if cells:
                    st.write("└─ Komórki: " + ", ".join([f"`{c}`" for c in cells]))
                else:
                    st.write("└─ *Brak przypisanych komórek*")
    else:
        st.info("Tabela struktury produkcyjnej jest obecnie pusta.")

except Exception as e:
    st.error(f"Błąd odczytu z tabeli Databricks: {e}")