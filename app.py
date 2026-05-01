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
    # On force le rafraîchissement avec ttl=0 pour éviter l'erreur 400 liée au cache
    df = conn.read(worksheet="Vols_Joinville", ttl=0)
    
    if df is None or df.empty:
        st.info("Le radar tourne, mais aucun avion n'a encore été enregistré aujourd'hui.")
    else:
        # Nettoyage : On ne montre pas la colonne technique icao24 aux riverains
        cols_affichage = [c for c in df.columns if c != 'icao24']
        df_display = df[cols_affichage].copy()

        # Filtres pour les riverains
        st.sidebar.header("Filtres")
        jours_dispos = df_display['Date'].unique()
        jour_choisi = st.sidebar.selectbox("Choisir un jour", sorted(jours_dispos, reverse=True))
        
        # Application du filtre
        df_jour = df_display[df_display['Date'] == jour_choisi].copy()
        
        st.metric(f"Avions détectés le {jour_choisi}", len(df_jour))
        
        # Affichage du tableau propre
        st.dataframe(df_jour, use_container_width=True)

except Exception as e:
    st.error(f"Erreur de lecture de la base de données : {e}")