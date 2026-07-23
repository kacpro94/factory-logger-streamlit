import streamlit as st

# 1. Główna konfiguracja wyglądu całej aplikacji
st.set_page_config(page_title="Factory Data Hub", layout="centered")

# 2. Definiujemy strony i wskazujemy pliki w folderze views/
page_capacity = st.Page(
    "views/capacity_planner_events.py", 
    title="📊 Capacity Planning", 
    icon="📉",
    default=True # Ta strona otworzy się jako pierwsza
)

page_employees = st.Page(
    "views/employees.py", 
    title="👥 Zarządzanie Pracownikami", 
    icon="👤"
)

# 3. Tworzymy menu nawigacji i uruchamiamy aplikację
pg = st.navigation([page_capacity, page_employees])
pg.run()