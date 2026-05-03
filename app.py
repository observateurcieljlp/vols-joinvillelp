import streamlit as st
import pandas as pd
from streamlit_gsheets import GSheetsConnection

st.set_page_config(page_title="Radar Joinville", page_icon="✈️", layout="wide")

st.title("✈️ Radar des nuisances - Joinville-le-Pont")
st.markdown("Ce tableau de bord affiche les survols enregistrés automatiquement par notre radar permanent.")

# Connexion au Google Sheet
try:
    if "connections" not in st.secrets or "gsheets" not in st.secrets.connections:
        st.error("❌ Configuration Google Sheets manquante dans les Secrets Streamlit.")
        st.info("Assurez-vous d'avoir ajouté le bloc [connections.gsheets] dans les secrets de votre application Streamlit Cloud.")
        st.stop()
        
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"Erreur d'initialisation de la connexion : {e}")
    st.stop()

try:
    # 1. Lecture des données
    df = conn.read(worksheet="Vols_Joinville", ttl=0)
    
    if df is None or df.empty:
        st.info("Le radar tourne, mais aucun avion n'a encore été enregistré aujourd'hui.")
    else:
        # 2. Préparation des filtres en sidebar
        st.sidebar.header("Filtres")
        jours_dispos = df['Date'].unique()
        jour_choisi = st.sidebar.selectbox("Choisir un jour", sorted(jours_dispos, reverse=True))
        
        # 3. Filtrage des données (on garde l'ICAO24 caché pour les liens)
        df_jour = df[df['Date'] == jour_choisi].copy()

        # --- NOUVEAU : GÉNÉRATION DES LIENS ---
        def get_adsb_link(icao24, date):
            if not icao24 or pd.isna(icao24): return None
            return f"https://globe.adsbexchange.com/?icao={str(icao24).strip()}&showHistory={date}"

        # On crée la colonne de liens (sans l'afficher telle quelle)
        df_jour['Trajet'] = df_jour.apply(
            lambda row: get_adsb_link(row['Identifiant Appareil (ICAO24)'], row['Date']), 
            axis=1
        )
        # --------------------------------------

        # 4. Affichage des Metrics (Valeur ajoutée)
        st.metric(f"Avions détectés le {jour_choisi}", len(df_jour))
        
        # 5. Nettoyage de l'affichage
        # On définit ici les colonnes qu'on veut vraiment montrer
        colonnes_visibles = ['Heure', 'Indicatif', 'Altitude (m)', 'Trajet'] 
        # Assurez-vous que ces noms correspondent exactement à votre Google Sheet
        
        # 6. Affichage du tableau avec configuration spéciale pour le lien
        st.dataframe(
            df_jour[colonnes_visibles], 
            column_config={
                "Trajet": st.column_config.LinkColumn(
                    "Revoir le trajet", 
                    display_text="🗺️ Voir sur la carte"
                ),
                "Altitude (m)": st.column_config.NumberColumn(format="%d m")
            },
            use_container_width=True,
            hide_index=True # Pour enlever la colonne d'index 0, 1, 2...
        )

except Exception as e:
    st.error(f"Erreur : {e}")