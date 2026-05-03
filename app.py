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

st.title("✈️ Survols à basse altitude - Joinville-le-Pont")
st.markdown("Surveillance citoyenne des survols à basse altitude (<3500m) au-dessus de Joinville-le-pont. Sources : opensky, hexdb, airlabs")

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
        # --- GESTION DE LA MIGRATION DU SCHÉMA ---
        # On s'assure que toutes les colonnes attendues existent pour éviter les plantages
        colonnes_attendues = ['Lat', 'Lon', 'Heading', 'Altitude (m)', 'Identifiant Appareil (ICAO24)', 'Identifiant Vol (Callsign)', 'Immatriculation', 'Compagnie', 'Modèle Avion', 'Evolution Verticale', 'De', 'A', 'Heure', 'Date']
        for col in colonnes_attendues:
            if col not in df.columns:
                df[col] = None # On initialise les colonnes manquantes

        # Prétraitement des colonnes numériques
        for col in ['Lat', 'Lon', 'Heading', 'Altitude (m)']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        # ----------------------------------------

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

        # Logique de coloration et taille pour la carte (plus robuste via apply)
        def set_map_color(row):
            if selected_row is not None and row['Identifiant Appareil (ICAO24)'] == selected_row['Identifiant Appareil (ICAO24)']:
                return [255, 0, 0, 255] # Rouge si sélectionné
            return [0, 100, 255, 200] # Bleu par défaut

        map_df['color'] = map_df.apply(set_map_color, axis=1)

        # Configuration de l'icône Avion
        # On utilise une URL d'icône SVG pour un avion vue de dessus
        ICON_URL = "https://raw.githubusercontent.com/visgl/deck.gl-data/master/website/icon-atlas.png"
        # Coordonnées dans l'atlas pour l'icône de marqueur (on va plutôt utiliser une flèche SVG propre)
        # Mais pour faire simple et robuste sans dépendances, on peut utiliser un TextLayer d'avion Unicode
        # ou un IconLayer avec une icône d'avion standard.
        
        # Calques Pydeck
        # 1. Trajectoires
        layer_paths = pdk.Layer(
            "PathLayer",
            path_data,
            get_path="path",
            get_color="color",
            get_width=3,
            width_min_pixels=2,
            pickable=True
        )

        # 2. Icônes Avion (Remplacement des cercles)
        # On utilise un TextLayer avec l'avion Unicode ✈ qui est très bien géré
        layer_aircraft = pdk.Layer(
            "TextLayer",
            map_df,
            get_position=["Lon", "Lat"],
            get_text="icon",
            get_color="color",
            get_angle="-Heading + 90", # On ajuste car l'icône ✈ pointe vers la droite par défaut
            get_size=30,
            pickable=True,
        )
        map_df['icon'] = "✈"

        # Rendu de la carte
        map_layers = [layer_paths, layer_aircraft]
        
        if map_df.empty:
            st.warning("⚠️ Aucune coordonnée précise (Lat/Lon) n'est encore disponible.")
        
        st.pydeck_chart(pdk.Deck(
            map_style=None,
            initial_view_state=view_state,
            layers=map_layers,
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
            
            # --- INSPECTEUR DE DONNÉES BRUTES ---
            st.sidebar.markdown("---")
            with st.sidebar.expander("🛠️ Données techniques (JSON)"):
                def safe_json_display(label, data):
                    if data is None or pd.isna(data) or str(data).strip() == "":
                        return
                    st.write(f"**{label}:**")
                    try:
                        # Si c'est déjà un dictionnaire/liste (objet Python)
                        if isinstance(data, (dict, list)):
                            st.json(data)
                        else:
                            # Tentative de parsing si c'est une chaîne
                            st.json(json.loads(str(data)))
                    except Exception:
                        # Fallback en texte brut si le JSON est malformé
                        st.code(str(data), language="text")

                safe_json_display("AirLabs", selected_row.get('Airlabs Info'))
                safe_json_display("OpenSky", selected_row.get('OpenSky State Info'))
                safe_json_display("HexDB Route", selected_row.get('Hexdb Route Info'))
                safe_json_display("HexDB Aircraft", selected_row.get('Hexdb Aircraft Info'))
                safe_json_display("PlaneSpotters", selected_row.get('Planespotters Info'))
            # ------------------------------------

except Exception as e:
    st.error(f"Erreur d'affichage : {e}")
    st.info("Attendez qu'un nouveau vol soit capturé avec les colonnes Lat/Lon pour voir la carte.")
