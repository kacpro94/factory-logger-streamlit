import streamlit as st
import pandas as pd
import json
from datetime import datetime
from databricks.sdk import WorkspaceClient

# --- CONFIG DATA PLATFORM ---
CATALOG = "data_warehouse_factory"
SCHEMA_BRONZE = "bronze"
SCHEMA_SILVER = "silver"
SCHEMA_GOLD = "gold"
VOLUME = "lines_cells_raw"
DATABRICKS_WAREHOUSE_ID = "f38fec74b09fdb89"

st.title("Production Structure Manager")

# --- REUSABLE CLIENT ---
@st.cache_resource
def get_databricks_client():
    return WorkspaceClient()

w = get_databricks_client()

# --- 1. FETCH LINES FROM SILVER ---
@st.cache_data(ttl=300)
def fetch_lines_from_silver():
    try:
        sql_query = f"""
            SELECT line_code, line_name
            FROM {CATALOG}.{SCHEMA_SILVER}.silver_lines
            ORDER BY line_code ASC
        """
        response = w.statement_execution.execute_statement(
            warehouse_id=DATABRICKS_WAREHOUSE_ID,
            statement=sql_query
        )
        if response.result and response.result.data_array:
            columns = [col.name for col in response.manifest.schema.columns]
            return pd.DataFrame(response.result.data_array, columns=columns)
        return pd.DataFrame(columns=["line_code", "line_name"])
    except Exception as e:
        st.error(f"Failed to load production lines: {e}")
        return pd.DataFrame(columns=["line_code", "line_name"])

# --- 2. FETCH CELLS FROM GOLD ---
@st.cache_data(ttl=300)
def fetch_cells_structure_from_gold():
    try:
        sql_query = f"""
            SELECT 
                line_code,
                cell_code,
                cell_name,
                record_type
            FROM {CATALOG}.{SCHEMA_GOLD}.dim_production_cells
            WHERE cell_code IS NOT NULL AND trim(cell_code) != ''
            ORDER BY line_code ASC, cell_code ASC
        """
        response = w.statement_execution.execute_statement(
            warehouse_id=DATABRICKS_WAREHOUSE_ID,
            statement=sql_query
        )
        if response.result and response.result.data_array:
            columns = [col.name for col in response.manifest.schema.columns]
            return pd.DataFrame(response.result.data_array, columns=columns)
        return pd.DataFrame(columns=["line_code", "cell_code", "cell_name", "record_type"])
    except Exception as e:
        st.error(f"Failed to load cells structure: {e}")
        return pd.DataFrame(columns=["line_code", "cell_code", "cell_name", "record_type"])

df_lines = fetch_lines_from_silver()
df_cells = fetch_cells_structure_from_gold()

# --- HELPER: UPLOAD PAYLOAD TO BRONZE VOLUME ---
def upload_payload(payload, filename):
    full_path = f"/Volumes/{CATALOG}/{SCHEMA_BRONZE}/{VOLUME}/{filename}"
    json_bytes = json.dumps(payload, ensure_ascii=False, indent=4).encode('utf-8')
    w.files.upload(full_path, json_bytes)

# --- MAIN TWO-COLUMN MASTER-DETAIL LAYOUT ---
col_master, col_detail = st.columns([1, 2], gap="large")

# ==========================================
# MASTER: PRODUCTION LINES
# ==========================================
with col_master:
    st.subheader("1. Production Line")
    
    # Zasilanie listy rozwijanej z tabeli silver_lines
    if not df_lines.empty:
        line_options = df_lines.to_dict(orient="records")
    else:
        line_options = []

    line_mode = st.radio(
        "Line Action", 
        ["Select Existing Line", "Create New Line"], 
        horizontal=True,
        label_visibility="collapsed"
    )

    selected_line_code = None
    selected_line_name = ""

    if line_mode == "Select Existing Line":
        if line_options:
            selected_line_obj = st.selectbox(
                "Active Line",
                options=line_options,
                format_func=lambda x: f"{x['line_code']} - {x['line_name']}"
            )
            selected_line_code = selected_line_obj["line_code"]
            selected_line_name = selected_line_obj["line_name"]
        else:
            st.info("No lines available. Switch to 'Create New Line'.")

    else:
        with st.form("form_create_line", clear_on_submit=True):
            new_line_code = st.text_input("New Line Code (e.g. L01)").strip().upper()
            new_line_name = st.text_input("Line Description (e.g. Inverter Assembly)").strip()
            submit_new_line = st.form_submit_button("Register Line")

            if submit_new_line:
                if not new_line_code:
                    st.warning("Line Code is required.")
                else:
                    payload = {
                        "record_type": "PRODUCTION_LINE",
                        "line_code": new_line_code,
                        "line_name": new_line_name if new_line_name else new_line_code,
                        "cell_code": None,
                        "cell_name": None,
                        "ingested_at": datetime.now().isoformat(),
                        "source": "streamlit_structure_manager"
                    }
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                    upload_payload(payload, f"line_{new_line_code}_{timestamp}.json")
                    st.success(f"Line {new_line_code} registered.")
                    fetch_lines_from_silver.clear() # Odświeżamy tylko liste linii
                    st.rerun()

# ==========================================
# DETAIL: HIERARCHY TREE & BATCH CELL EDITOR
# ==========================================
with col_detail:
    if selected_line_code:
        st.subheader(f"2. Cell Tree & Editor: {selected_line_code}")
        st.caption(f"Line Name: {selected_line_name}")

        # Filtrowanie komórek przypisanych do wybranej linii (z danych Gold)
        current_line_cells = df_cells[df_cells["line_code"] == selected_line_code]

        # --- VISUAL TREE EXPANDER ---
        with st.expander("Tree Structure Preview", expanded=True):
            st.markdown(f"**Line: `{selected_line_code}`** ({selected_line_name})")
            if not current_line_cells.empty:
                for _, cell in current_line_cells.iterrows():
                    c_code = cell["cell_code"]
                    c_name = cell["cell_name"] if pd.notna(cell["cell_name"]) else ""
                    st.markdown(f"    └── **`{c_code}`** : {c_name}")
            else:
                st.markdown("    └── *(No cells assigned to this line)*")

        st.markdown("##### Batch Add / Edit Cells")
        st.caption("Add rows in the grid below. Suffixes (e.g. C01) will automatically inherit the line prefix.")

        default_grid_data = pd.DataFrame([
            {"cell_suffix": "", "cell_name": ""}
        ])

        edited_cells = st.data_editor(
            default_grid_data,
            num_rows="dynamic",
            use_container_width=True,
            column_config={
                "cell_suffix": st.column_config.TextColumn(
                    "Cell Suffix / Code",
                    help="Enter suffix (e.g. C01 or full A01-C01)",
                    required=True
                ),
                "cell_name": st.column_config.TextColumn(
                    "Station Name / Description",
                    help="Enter cell name (e.g. Pre-assembly Station)"
                )
            },
            key="cell_batch_editor"
        )

        if st.button(f"Save Cells to Line {selected_line_code}"):
            valid_rows = edited_cells[edited_cells["cell_suffix"].str.strip() != ""]
            
            if valid_rows.empty:
                st.warning("Please enter at least one cell suffix before saving.")
            else:
                saved_count = 0
                for _, row in valid_rows.iterrows():
                    suffix = row["cell_suffix"].strip().upper()
                    c_name = row["cell_name"].strip() if pd.notna(row["cell_name"]) else ""
                    
                    full_code = f"{selected_line_code}-{suffix}" if not suffix.startswith(selected_line_code) else suffix
                    
                    payload = {
                        "record_type": "PRODUCTION_CELL",
                        "line_code": selected_line_code,
                        "line_name": selected_line_name,
                        "cell_code": full_code,
                        "cell_name": c_name if c_name else full_code,
                        "ingested_at": datetime.now().isoformat(),
                        "source": "streamlit_structure_manager"
                    }
                    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                    upload_payload(payload, f"cell_{full_code}_{timestamp}.json")
                    saved_count += 1

                st.success(f"Successfully saved {saved_count} cell(s) for line {selected_line_code}.")
                fetch_cells_structure_from_gold.clear() # Odświeżamy liste komórek
                st.rerun()

    else:
        st.subheader("2. Cell Hierarchy")
        st.info("Select or register a production line on the left to view and configure its cell hierarchy.")

st.divider()

# --- REFRESH & RAW TABLE VIEW ---
col_ref, _ = st.columns([1, 4])
with col_ref:
    if st.button("Refresh Cache"):
        fetch_lines_from_silver.clear()
        fetch_cells_structure_from_gold.clear()
        st.rerun()

with st.expander("Full Cell Structure Table (`dim_production_cells`)"):
    if not df_cells.empty:
        st.dataframe(df_cells, use_container_width=True, hide_index=True)
    else:
        st.write("Cell structure table is empty.")