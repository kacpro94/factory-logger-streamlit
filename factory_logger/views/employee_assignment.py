import streamlit as st
import pandas as pd
import json
from datetime import datetime, date
from databricks.sdk import WorkspaceClient

# --- CONFIG DATA PLATFORM ---
CATALOG = "data_warehouse_factory"
SCHEMA_BRONZE = "bronze"
SCHEMA_GOLD = "gold"
VOLUME = "employee_assignments_raw"
DATABRICKS_WAREHOUSE_ID = "f38fec74b09fdb89"

st.title("Line Staffing & Cell Assignments")

# --- REUSABLE CLIENT ---
@st.cache_resource
def get_databricks_client():
    return WorkspaceClient()

w = get_databricks_client()

# --- 1. FETCH PRODUCTION STRUCTURE FROM GOLD ---
@st.cache_data(ttl=300)
def fetch_production_structure():
    try:
        sql_query = f"""
            SELECT 
                line_code, 
                line_name, 
                cell_code, 
                cell_name 
            FROM {CATALOG}.{SCHEMA_GOLD}.dim_production_cells
            WHERE line_code IS NOT NULL AND cell_code IS NOT NULL
            ORDER BY line_code ASC, cell_code ASC
        """
        response = w.statement_execution.execute_statement(
            warehouse_id=DATABRICKS_WAREHOUSE_ID,
            statement=sql_query
        )
        if response.result and response.result.data_array:
            columns = [col.name for col in response.manifest.schema.columns]
            return pd.DataFrame(response.result.data_array, columns=columns)
        return pd.DataFrame(columns=["line_code", "line_name", "cell_code", "cell_name"])
    except Exception as e:
        st.error(f"Failed to load production structure: {e}")
        return pd.DataFrame(columns=["line_code", "line_name", "cell_code", "cell_name"])

# --- 2. FETCH ACTIVE EMPLOYEES FROM GOLD ---
@st.cache_data(ttl=300)
def fetch_active_employees():
    try:
        sql_query = f"""
            SELECT 
                employee_key, 
                employee_full_name, 
                work_center
            FROM {CATALOG}.{SCHEMA_GOLD}.dim_employees
            WHERE is_active = TRUE
            ORDER BY employee_full_name ASC
        """
        response = w.statement_execution.execute_statement(
            warehouse_id=DATABRICKS_WAREHOUSE_ID,
            statement=sql_query
        )
        if response.result and response.result.data_array:
            return pd.DataFrame(response.result.data_array, columns=[col.name for col in response.manifest.schema.columns])
        return pd.DataFrame(columns=["employee_key", "employee_full_name", "work_center"])
    except Exception as e:
        st.error(f"Failed to load employees: {e}")
        return pd.DataFrame(columns=["employee_key", "employee_full_name", "work_center"])

# --- 3. FETCH CURRENT ACTIVE ASSIGNMENTS FROM GOLD ---
@st.cache_data(ttl=60)
def fetch_current_assignments():
    try:
        sql_query = f"""
            SELECT 
                line_code,
                cell_code,
                shift,
                default_employee_key,
                default_employee_name,
                backup_employee_key,
                backup_employee_name,
                valid_from
            FROM {CATALOG}.{SCHEMA_GOLD}.dim_employee_assignments
            WHERE is_current = TRUE
        """
        response = w.statement_execution.execute_statement(
            warehouse_id=DATABRICKS_WAREHOUSE_ID,
            statement=sql_query
        )
        if response.result and response.result.data_array:
            columns = [col.name for col in response.manifest.schema.columns]
            return pd.DataFrame(response.result.data_array, columns=columns)
        return pd.DataFrame(columns=["line_code", "cell_code", "shift", "default_employee_name", "backup_employee_name"])
    except Exception:
        return pd.DataFrame(columns=["line_code", "cell_code", "shift", "default_employee_name", "backup_employee_name"])

df_structure = fetch_production_structure()
df_employees = fetch_active_employees()
df_current_assignments = fetch_current_assignments()

# Prepare employee mapping dictionaries
NONE_OPTION = "--- None ---"
employee_name_to_key = dict(zip(df_employees["employee_full_name"], df_employees["employee_key"])) if not df_employees.empty else {}
employee_options_list = [NONE_OPTION] + (df_employees["employee_full_name"].tolist() if not df_employees.empty else [])

# --- LAYOUT: MASTER / DETAIL ---
col_master, col_detail = st.columns([1, 2], gap="large")

# ==========================================
# 1. MASTER: PRODUCTION LINE SELECTION
# ==========================================
with col_master:
    st.subheader("1. Line Selection")
    
    if not df_structure.empty and "line_code" in df_structure.columns:
        lines_df = df_structure[["line_code", "line_name"]].drop_duplicates()
        line_options = lines_df.to_dict(orient="records")
        
        selected_line_obj = st.selectbox(
            "Select Line",
            options=line_options,
            format_func=lambda x: f"{x['line_code']} - {x['line_name']}"
        )
        selected_line_code = selected_line_obj["line_code"]
        selected_line_name = selected_line_obj["line_name"]
    else:
        st.warning("No production lines available in dim_production_cells.")
        selected_line_code = None
        selected_line_name = ""

    st.markdown("##### Global Settings")
    target_shift = st.selectbox("Target Shift", ["All Shifts", "Shift 1", "Shift 2", "Shift 3"])
    effective_date = st.date_input("Effective From Date", date.today())
    general_remarks = st.text_input("Operational Remarks", placeholder="e.g. Q3 Rotation / Reassignment")

# ==========================================
# 2. DETAIL: TREE VIEW & BATCH ASSIGNMENT EDITOR
# ==========================================
with col_detail:
    if selected_line_code:
        st.subheader(f"2. Line Staffing Overview: {selected_line_code}")
        st.caption(f"Line Description: {selected_line_name}")

        # Filter cells and existing assignments for this line
        line_cells = df_structure[df_structure["line_code"] == selected_line_code].copy()
        
        # Merge with current assignments for visual tree
        merged_view = pd.merge(
            line_cells,
            df_current_assignments[df_current_assignments["line_code"] == selected_line_code],
            on=["line_code", "cell_code"],
            how="left"
        )

        # --- TREE VIEW EXPANDER ---
        with st.expander("Live Staffing Hierarchy Tree", expanded=True):
            st.markdown(f"**Line `{selected_line_code}`** ({selected_line_name})")
            
            vacant_count = 0
            if not merged_view.empty:
                for _, row in merged_view.iterrows():
                    c_code = row["cell_code"]
                    c_name = row["cell_name"] if pd.notna(row["cell_name"]) else ""
                    def_emp = row["default_employee_name"] if pd.notna(row["default_employee_name"]) and row["default_employee_name"] else None
                    back_emp = row["backup_employee_name"] if pd.notna(row["backup_employee_name"]) and row["backup_employee_name"] else None
                    
                    if def_emp and back_emp:
                        status_badge = f"🟢 **`{c_code}`** ({c_name}) &rarr; Primary: **{def_emp}** | Backup: {back_emp}"
                    elif def_emp:
                        status_badge = f"🟡 **`{c_code}`** ({c_name}) &rarr; Primary: **{def_emp}** | *No Backup*"
                    else:
                        status_badge = f"🔴 **`{c_code}`** ({c_name}) &rarr; **VACANT (No assignment)**"
                        vacant_count += 1
                        
                    st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;└── {status_badge}")
            else:
                st.markdown("&nbsp;&nbsp;&nbsp;&nbsp;└── *(No cells found for this line)*")
                
            if vacant_count > 0:
                st.caption(f"⚠️ {vacant_count} station(s) currently unassigned on this line.")

        st.markdown("##### Batch Staffing Grid")
        st.caption("Update operators for each cell below and click save to apply assignments.")

        # Prepare initial data for the editor
        editor_prep = []
        for _, cell_row in line_cells.iterrows():
            c_code = cell_row["cell_code"]
            c_name = cell_row["cell_name"] if pd.notna(cell_row["cell_name"]) else ""
            
            # Find existing assignment if present
            existing = df_current_assignments[
                (df_current_assignments["line_code"] == selected_line_code) & 
                (df_current_assignments["cell_code"] == c_code)
            ]
            
            curr_def = existing["default_employee_name"].iloc[0] if not existing.empty and pd.notna(existing["default_employee_name"].iloc[0]) else NONE_OPTION
            curr_back = existing["backup_employee_name"].iloc[0] if not existing.empty and pd.notna(existing["backup_employee_name"].iloc[0]) else NONE_OPTION
            
            if curr_def not in employee_options_list:
                curr_def = NONE_OPTION
            if curr_back not in employee_options_list:
                curr_back = NONE_OPTION

            editor_prep.append({
                "cell_code": c_code,
                "cell_name": c_name,
                "default_operator": curr_def,
                "backup_operator": curr_back
            })

        df_editor_input = pd.DataFrame(editor_prep)

        edited_assignments = st.data_editor(
            df_editor_input,
            hide_index=True,
            use_container_width=True,
            column_config={
                "cell_code": st.column_config.TextColumn("Cell Code", disabled=True),
                "cell_name": st.column_config.TextColumn("Cell Name", disabled=True),
                "default_operator": st.column_config.SelectboxColumn(
                    "Primary Operator",
                    options=employee_options_list,
                    required=True
                ),
                "backup_operator": st.column_config.SelectboxColumn(
                    "Backup Operator",
                    options=employee_options_list,
                    required=False
                )
            },
            key="staffing_batch_editor"
        )

        if st.button(f"Save All Assignments for Line {selected_line_code}"):
            saved_count = 0
            validation_error = False

            for _, row in edited_assignments.iterrows():
                c_code = row["cell_code"]
                c_name = row["cell_name"]
                def_op = row["default_operator"]
                back_op = row["backup_operator"]

                if def_op != NONE_OPTION and back_op != NONE_OPTION and def_op == back_op:
                    st.warning(f"Cell {c_code}: Primary and Backup operator cannot be the same person.")
                    validation_error = True
                    break

            if not validation_error:
                for _, row in edited_assignments.iterrows():
                    c_code = row["cell_code"]
                    c_name = row["cell_name"]
                    def_op = row["default_operator"]
                    back_op = row["backup_operator"]

                    # Skip cells left completely unassigned
                    if def_op == NONE_OPTION and back_op == NONE_OPTION:
                        continue

                    def_key = int(employee_name_to_key[def_op]) if def_op in employee_name_to_key else None
                    back_key = int(employee_name_to_key[back_op]) if back_op in employee_name_to_key else None

                    assignment_payload = {
                        "line_code": selected_line_code,
                        "line_name": selected_line_name,
                        "cell_code": c_code,
                        "cell_name": c_name,
                        "default_employee_key": def_key,
                        "default_employee_name": def_op if def_op != NONE_OPTION else None,
                        "backup_employee_key": back_key,
                        "backup_employee_name": back_op if back_op != NONE_OPTION else None,
                        "valid_from": effective_date.isoformat(),
                        "shift": target_shift,
                        "remarks": general_remarks.strip() if general_remarks else None,
                        "ingested_at": datetime.now().isoformat(),
                        "source": "streamlit_assignments_tree_editor"
                    }

                    timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                    filename = f"assign_{selected_line_code}_{c_code}_{timestamp_str}.json"
                    full_cloud_path = f"/Volumes/{CATALOG}/{SCHEMA_BRONZE}/{VOLUME}/{filename}"
                    json_bytes = json.dumps(assignment_payload, ensure_ascii=False, indent=4).encode('utf-8')

                    w.files.upload(full_cloud_path, json_bytes)
                    saved_count += 1

                st.success(f"Successfully deployed assignments for {saved_count} cell(s) on line {selected_line_code}.")
                fetch_current_assignments.clear()
                st.rerun()

    else:
        st.info("Select a production line on the left to inspect and configure its staffing tree.")

st.divider()

# --- REFRESH CACHE ---
col_refresh, _ = st.columns([1, 4])
with col_refresh:
    if st.button("Refresh Cache & View"):
        fetch_production_structure.clear()
        fetch_active_employees.clear()
        fetch_current_assignments.clear()
        st.rerun()