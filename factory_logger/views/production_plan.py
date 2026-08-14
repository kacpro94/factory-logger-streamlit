import streamlit as st
import pandas as pd
import json
from datetime import datetime, date, time
from databricks.sdk import WorkspaceClient

# --- CONFIG DATA PLATFORM ---
CATALOG = "data_warehouse_factory"
SCHEMA_BRONZE = "bronze"
SCHEMA_SILVER = "silver"
SCHEMA_GOLD = "gold"
VOLUME = "production_plan_raw"
DATABRICKS_WAREHOUSE_ID = "f38fec74b09fdb89"

st.title("Production Line Scheduling")

# --- REUSABLE CLIENT ---
@st.cache_resource
def get_databricks_client():
    return WorkspaceClient()

w = get_databricks_client()

# --- 1. FETCH DISTINCT PRODUCTION LINES ---
@st.cache_data(ttl=300)
def fetch_lines_and_cells():
    try:
        sql_query = f"""
            SELECT 
                line_code, 
                line_name, 
                cell_code, 
                cell_name 
            FROM {CATALOG}.{SCHEMA_GOLD}.dim_production_cells
            WHERE line_code IS NOT NULL
            ORDER BY line_code ASC, cell_code ASC
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
        st.error(f"Failed to fetch production structure: {e}")
        return pd.DataFrame()

# --- 2. FETCH UPCOMING SCHEDULES FOR SELECTED LINE ---
@st.cache_data(ttl=60)
def fetch_upcoming_plans(line_code):
    try:
        # UWAGA: Dopasuj nazwy kolumn do tego, jak finalnie wygląda Twoja tabela Silver
        sql_query = f"""
            SELECT 
                CAST(start_datetime AS TIMESTAMP) AS start_time,
                CAST(end_datetime AS TIMESTAMP) AS end_time,
                plan_status,
                planned_capacity_pct AS capacity_pct,
                remarks
            FROM {CATALOG}.{SCHEMA_SILVER}.silver_production_plan
            WHERE line_code = '{line_code}' 
              AND CAST(end_datetime AS TIMESTAMP) >= CURRENT_TIMESTAMP()
            ORDER BY start_time ASC
            LIMIT 20
        """
        response = w.statement_execution.execute_statement(
            warehouse_id=DATABRICKS_WAREHOUSE_ID,
            statement=sql_query
        )

        if response.result and response.result.data_array:
            columns = [col.name for col in response.manifest.schema.columns]
            df = pd.DataFrame(response.result.data_array, columns=columns)
            
            # Formatowanie dat do czytelnego widoku
            df['start_time'] = pd.to_datetime(df['start_time']).dt.strftime('%Y-%m-%d %H:%M')
            df['end_time'] = pd.to_datetime(df['end_time']).dt.strftime('%Y-%m-%d %H:%M')
            return df
        return pd.DataFrame()
    except Exception:
        # Zwraca pusty DataFrame jeśli tabela Silver jeszcze nie istnieje
        return pd.DataFrame()

df_structure = fetch_lines_and_cells()

if df_structure.empty:
    st.warning("No production line structure found in dim_production_cells.")

# --- PRODUCTION LINE SELECTION (MASTER) ---
st.subheader("1. Production Line Selection")

if not df_structure.empty:
    distinct_lines = df_structure[["line_code", "line_name"]].drop_duplicates().to_dict(orient="records")
    selected_line = st.selectbox(
        "Production Line",
        options=distinct_lines,
        format_func=lambda x: f"{x['line_code']} - {x['line_name']}"
    )
    
    line_cells_df = df_structure[df_structure["line_code"] == selected_line["line_code"]]
    assigned_cells_list = [
        {"cell_code": row["cell_code"], "cell_name": row["cell_name"]}
        for _, row in line_cells_df.iterrows()
        if pd.notna(row["cell_code"])
    ]
    
    cell_codes_display = [c["cell_code"] for c in assigned_cells_list]
    if cell_codes_display:
        st.caption(f"Assigned cells affected by this plan: {', '.join(cell_codes_display)}")
    else:
        st.caption("No individual cells found for this line. The plan will apply to the line directly.")
else:
    selected_line = None
    assigned_cells_list = []

st.divider()

# --- SPLIT LAYOUT: FORM (LEFT) vs CURRENT PLANS (RIGHT) ---
if selected_line:
    col_form, col_view = st.columns([1, 1], gap="large")

    # === LEFT COLUMN: FORM ===
    with col_form:
        st.subheader("2. Schedule Details")
        
        with st.form("production_plan_form", clear_on_submit=True):
            col_start, col_end = st.columns(2)
            with col_start:
                start_date = st.date_input("Start Date", date.today())
                start_time = st.time_input("Start Time", time(6, 0))
            with col_end:
                end_date = st.date_input("End Date", date.today())
                end_time = st.time_input("End Time", time(14, 0))

            col_meta1, col_meta2 = st.columns(2)
            with col_meta1:
                plan_status = st.selectbox(
                    "Plan Status", 
                    ["Planned Production", "Maintenance / Setup", "Line Shutdown", "Reduced Capacity"]
                )
            with col_meta2:
                planned_capacity = st.number_input("Target Utilization (%)", min_value=0, max_value=100, value=100, step=5)

            remarks = st.text_area("Operational Remarks")

            submit_button = st.form_submit_button("Save Line Schedule")

        # --- SAVE TO BRONZE VOLUME ---
        if submit_button:
            start_dt_obj = datetime.combine(start_date, start_time)
            end_dt_obj = datetime.combine(end_date, end_time)

            if end_dt_obj <= start_dt_obj:
                st.error("End date and time must be later than start date and time.")
            else:
                payload = {
                    "record_type": "LINE_SCHEDULE",
                    "line_code": selected_line["line_code"],
                    "line_name": selected_line["line_name"],
                    "assigned_cells": assigned_cells_list,
                    "start_datetime": start_dt_obj.isoformat(),
                    "end_datetime": end_dt_obj.isoformat(),
                    "plan_status": plan_status,
                    "planned_capacity_pct": planned_capacity,
                    "remarks": remarks.strip() if remarks else None,
                    "ingested_at": datetime.now().isoformat(),
                    "source": "streamlit_production_planner"
                }

                timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                filename = f"plan_line_{selected_line['line_code']}_{timestamp_str}.json"

                try:
                    full_cloud_path = f"/Volumes/{CATALOG}/{SCHEMA_BRONZE}/{VOLUME}/{filename}"
                    json_bytes = json.dumps(payload, ensure_ascii=False, indent=4).encode('utf-8')

                    w.files.upload(full_cloud_path, json_bytes)

                    st.success(f"Production plan saved successfully as {filename}.")
                    
                    # Wymuszenie odświeżenia widoku planów po zapisie
                    fetch_upcoming_plans.clear()
                    st.rerun()
                except Exception as e:
                    st.error(f"Failed to save plan to Databricks Volume: {e}")

    # === RIGHT COLUMN: UPCOMING PLANS ===
    with col_view:
        st.subheader(f"Upcoming Schedules: {selected_line['line_code']}")
        
        df_plans = fetch_upcoming_plans(selected_line["line_code"])
        
        if not df_plans.empty:
            st.dataframe(
                df_plans, 
                use_container_width=True, 
                hide_index=True,
                column_config={
                    "start_time": st.column_config.TextColumn("Start"),
                    "end_time": st.column_config.TextColumn("End"),
                    "plan_status": st.column_config.TextColumn("Status"),
                    "capacity_pct": st.column_config.ProgressColumn("Capacity %", min_value=0, max_value=100),
                    "remarks": st.column_config.TextColumn("Remarks")
                }
            )
        else:
            st.info("No upcoming schedules found for this line. (Or silver table is not yet processed)")
            
        if st.button("Refresh Schedule List"):
            fetch_upcoming_plans.clear()
            st.rerun()

st.divider()

# --- RECENT FILES PREVIEW ---
with st.expander("Recent Bronze Volume Files"):
    if st.button("Load File List"):
        try:
            cloud_dir_path = f"/Volumes/{CATALOG}/{SCHEMA_BRONZE}/{VOLUME}"
            files_list = w.files.list_directory_contents(cloud_dir_path)
            files = sorted([f.name for f in files_list], reverse=True)[:5]
            st.write(files)
        except Exception as e:
            st.warning(f"Unable to load files: {e}")