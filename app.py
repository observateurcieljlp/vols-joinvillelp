import streamlit as st
import pandas as pd
from streamlit_gsheets import GSheetsConnection

st.set_page_config(page_title="Radar Joinville", page_icon="✈️", layout="wide")

st.title("✈️ Radar des nuisances - Joinville-le-Pont")
st.markdown("Ce tableau de bord affiche les survols enregistrés automatiquement par notre radar permanent.")

# Connexion au Google Sheet
conn = st.connection("gsheets", type=GSheetsConnection)

try:
    # Lecture des données enregistrées par le bot GitHub
    df = conn.read(worksheet="Vols_Joinville")
    
    if df.empty:
        st.info("Le radar tourne, mais aucun avion n'a encore été enregistré aujourd'hui.")
    else:
        # Filtres pour les riverains
        st.sidebar.header("Filtres")
        jours_dispos = df['Date'].unique()
        jour_choisi = st.sidebar.selectbox("Choisir un jour", sorted(jours_dispos, reverse=True))
        
        # Application du filtre
        df_jour = df[df['Date'] == jour_choisi].copy()
        
        st.metric(f"Avions détectés le {jour_choisi}", len(df_jour))
        
        # Affichage du tableau propre
        st.dataframe(df_jour, use_container_width=True)

except Exception as e:
    st.error(f"Erreur de lecture de la base de données : {e}")