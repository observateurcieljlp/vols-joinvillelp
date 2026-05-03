import streamlit as st
import pandas as pd
import pydeck as pdk
import re
from streamlit_gsheets import GSheetsConnection

st.set_page_config(page_title="Radar Joinville", page_icon="✈️", layout="wide")

# Style CSS personnalisé
st.markdown("""
    <style>
    .main { background-color: #f8f9fb; }
    [data-testid="stMetricValue"] { font-size: 24px; color: #1f77b4; }
    .stDataFrame { border-radius: 10px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
    </style>
""", unsafe_allow_html=True)

st.title("✈️ Radar des nuisances - Joinville-le-Pont")
st.markdown("Surveillance citoyenne des survols à basse altitude.")

# Connexion au Google Sheet
try:
    if "connections" not in st.secrets or "gsheets" not in st.secrets.connections:
        st.error("❌ Configuration Google Sheets manquante dans les secrets.")
        st.stop()
    conn = st.connection("gsheets", type=GSheetsConnection)
except Exception as e:
    st.error(f"Erreur d'initialisation : {e}")
    st.stop()

try:
    # 1. Lecture des données
    df = conn.read(worksheet="Vols_Joinville", ttl=0)
    
    if df is None or df.empty:
        st.info("Le radar est actif, mais aucun vol n'a encore été enregistré.")
    else:
        # Prétraitement des colonnes numériques
        for col in ['Lat', 'Lon', 'Heading', 'Altitude (m)']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # 2. Filtres en Sidebar
        st.sidebar.header("🗓️ Historique")
        jours_dispos = sorted(df['Date'].unique(), reverse=True)
        jour_choisi = st.sidebar.selectbox("Choisir une journée", jours_dispos)
        
        # Filtrage
        df_jour = df[df['Date'] == jour_choisi].copy().reset_index(drop=True)

        # 3. Métriques du jour
        m1, m2, m3 = st.columns(3)
        m1.metric("Vols détectés", len(df_jour))
        if 'Altitude (m)' in df_jour.columns:
            m2.metric("Altitude moy.", f"{int(df_jour['Altitude (m)'].mean())} m")
        m3.metric("Date", jour_choisi)

        # 4. Carte Interactive et Tableau
        st.subheader("📍 Visualisation des trajectoires")
        
        # --- Zone Carte ---
        # On définit le point central (Joinville)
        JOINVILLE_CENTER = {"lat": 48.818, "lon": 2.47}
        
        # Préparation des données carte
        map_df = df_jour.dropna(subset=['Lat', 'Lon']).copy()
        map_df['arrow'] = "▲" # Symbole pour l'orientation
        
        # Affichage du tableau avec sélection interactive
        # On ne montre pas les colonnes techniques de log
        cols_tableau = ['Heure', 'Identifiant Vol (Callsign)', 'Compagnie', 'Modèle Avion', 'Altitude (m)', 'Evolution Verticale', 'De', 'A']
        
        event = st.dataframe(
            df_jour[cols_tableau],
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row"
        )

        # Gestion de la sélection
        selected_row = None
        if event and event.selection.rows:
            selected_idx = event.selection.rows[0]
            selected_row = df_jour.iloc[selected_idx]

        # Logique de coloration pour la carte
        map_df['color'] = [[0, 120, 255, 160]] * len(map_df)
        map_df['size'] = [60] * len(map_df)
        
        if selected_row is not None:
            # On surligne l'avion sélectionné
            mask = map_df['Identifiant Appareil (ICAO24)'] == selected_row['Identifiant Appareil (ICAO24)']
            map_df.loc[mask, 'color'] = [[255, 0, 0, 230]]
            map_df.loc[mask, 'size'] = 120

        # Calques Pydeck
        # 1. Point d'impact
        layer_points = pdk.Layer(
            "ScatterplotLayer",
            map_df,
            get_position=["Lon", "Lat"],
            get_color="color",
            get_radius="size",
            pickable=True,
        )

        # 2. Vecteur de direction
        layer_arrows = pdk.Layer(
            "TextLayer",
            map_df,
            get_position=["Lon", "Lat"],
            get_text="arrow",
            get_color="color",
            get_angle="-Heading", # Inversion pour sens trigo
            get_size=25,
            alignment_baseline="'center'",
        )

        # État de la vue
        view_state = pdk.ViewState(
            latitude=selected_row['Lat'] if selected_row is not None else JOINVILLE_CENTER["lat"],
            longitude=selected_row['Lon'] if selected_row is not None else JOINVILLE_CENTER["lon"],
            zoom=14 if selected_row is not None else 13,
            pitch=0,
        )

        # Rendu de la carte
        st.pydeck_chart(pdk.Deck(
            map_style='mapbox://styles/mapbox/light-v9',
            initial_view_state=view_state,
            layers=[layer_points, layer_arrows],
            tooltip={"text": "{Identifiant Vol (Callsign)}\nAlt: {Altitude (m)}m\nCap: {Heading}°"}
        ))

        # 5. Panneau de détails (Sidebar) si sélectionné
        if selected_row is not None:
            st.sidebar.markdown("---")
            st.sidebar.subheader(f"🔍 Détails : {selected_row['Identifiant Vol (Callsign)']}")
            
            st.sidebar.info(f"**Passage à {selected_row['Heure']}**")
            st.sidebar.write(f"✈️ **Modèle :** {selected_row['Modèle Avion']}")
            st.sidebar.write(f"🏢 **Compagnie :** {selected_row['Compagnie']}")
            st.sidebar.write(f"🆔 **Immat :** {selected_row['Immatriculation']}")
            
            # Liens utiles
            st.sidebar.markdown("**Liens externes :**")
            col_a, col_b = st.sidebar.columns(2)
            
            icao = selected_row['Identifiant Appareil (ICAO24)']
            if icao:
                col_a.link_button("🛰️ Trace ADSB", f"https://globe.adsbexchange.com/?icao={icao}")
            
            immat = selected_row['Immatriculation']
            if immat:
                col_b.link_button("📷 Photos", f"https://www.planespotters.net/search?q={immat}")

except Exception as e:
    st.error(f"Erreur d'affichage : {e}")
    st.info("Attendez qu'un nouveau vol soit capturé avec les colonnes Lat/Lon pour voir la carte.")
