import streamlit as st
import pandas as pd
import pydeck as pdk
import re
import json
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
        colonnes_attendues = ['Lat', 'Lon', 'Heading', 'Altitude (m)', 'Identifiant Appareil (ICAO24)', 'Identifiant Vol (Callsign)', 'Immatriculation', 'Compagnie', 'Modèle Avion', 'Evolution Verticale', 'De', 'A', 'Heure', 'Date', 'Source']
        for col in colonnes_attendues:
            if col not in df.columns:
                df[col] = None

        # Prétraitement des colonnes numériques
        for col in ['Lat', 'Lon', 'Heading', 'Altitude (m)']:
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
        JOINVILLE_CENTER = {"lat": 48.818, "lon": 2.47}
        map_df = df_jour.dropna(subset=['Lat', 'Lon']).copy()
        
        # Affichage du tableau avec sélection interactive
        cols_tableau = ['Heure', 'Identifiant Vol (Callsign)', 'Immatriculation', 'Compagnie', 'Modèle Avion', 'Altitude (m)', 'Evolution Verticale', 'De', 'A', 'Source']
        
        event = st.dataframe(
            df_jour[cols_tableau],
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "Altitude (m)": st.column_config.NumberColumn(format="%d m"),
                "Source": st.column_config.TextColumn("Fiabilité")
            }
        )

        # Gestion de la sélection
        selected_row = None
        if event and event.selection.rows:
            selected_idx = event.selection.rows[0]
            selected_row = df_jour.iloc[selected_idx]

        # Logique de coloration (Phase de vol)
        def set_map_color(row):
            if selected_row is not None and row['Identifiant Appareil (ICAO24)'] == selected_row['Identifiant Appareil (ICAO24)']:
                return [255, 0, 0, 255] # Rouge
            trend = str(row.get('Evolution Verticale', '')).lower()
            if 'montée' in trend: return [46, 204, 113, 200] # Vert
            if 'descente' in trend: return [230, 126, 34, 200] # Orange
            return [0, 100, 255, 180] # Bleu

        map_df['color'] = map_df.apply(set_map_color, axis=1)

        # --- Préparation des trajectoires (Breadcrumbs) ---
        def parse_positions(pos_str):
            if not pos_str or pd.isna(pos_str): return []
            paths = []
            points = str(pos_str).split(" | ")
            for p in points:
                try:
                    coords = re.findall(r"[-+]?\d*\.\d+|\d+", p)
                    if len(coords) >= 2: paths.append([float(coords[1]), float(coords[0])])
                except: continue
            return paths

        path_data = []
        for _, row in df_jour.iterrows():
            path = parse_positions(row.get('Positions', ''))
            if len(path) > 1:
                is_sel = (selected_row is not None and row['Identifiant Appareil (ICAO24)'] == selected_row['Identifiant Appareil (ICAO24)'])
                path_data.append({
                    "path": path,
                    "color": [255, 0, 0, 200] if is_sel else [0, 120, 255, 100],
                    "width": 5 if is_sel else 2
                })

        # Calques
        layer_paths = pdk.Layer("PathLayer", path_data, get_path="path", get_color="color", get_width="width", width_min_pixels=2)
        layer_aircraft = pdk.Layer("TextLayer", map_df, get_position=["Lon", "Lat"], get_text="'✈'", get_color="color", get_angle="-Heading + 90", get_size=32, pickable=True)

        # Vue
        has_coords = (selected_row is not None and not pd.isna(selected_row['Lat']) and not pd.isna(selected_row['Lon']))
        view_state = pdk.ViewState(
            latitude=selected_row['Lat'] if has_coords else JOINVILLE_CENTER["lat"],
            longitude=selected_row['Lon'] if has_coords else JOINVILLE_CENTER["lon"],
            zoom=14 if has_coords else 13,
            pitch=0,
        )

        st.pydeck_chart(pdk.Deck(
            map_style=None,
            initial_view_state=view_state,
            layers=[layer_paths, layer_aircraft],
            tooltip={"text": "{Identifiant Vol (Callsign)}\nAlt: {Altitude (m)}m\nCap: {Heading}°"}
        ))

        # 5. Panneau de détails
        if selected_row is not None:
            st.sidebar.markdown("---")
            st.sidebar.subheader(f"🔍 Détails : {selected_row['Identifiant Vol (Callsign)']}")
            st.sidebar.info(f"**Passage à {selected_row['Heure']}**")
            st.sidebar.write(f"✈️ **Modèle :** {selected_row['Modèle Avion']}")
            st.sidebar.write(f"🏢 **Compagnie :** {selected_row['Compagnie']}")
            st.sidebar.write(f"🆔 **Immat :** {selected_row['Immatriculation']}")
            
            col_a, col_b = st.sidebar.columns(2)
            icao = selected_row['Identifiant Appareil (ICAO24)']
            if icao:
                col_a.link_button("🛰️ Trace ADSB", f"https://globe.adsbexchange.com/?icao={icao}")
                col_b.link_button("📷 Photos", f"https://www.planespotters.net/hex/{icao.upper()}")
            
            with st.sidebar.expander("🛠️ Données techniques (JSON)"):
                def safe_json(label, data):
                    if data and not pd.isna(data):
                        st.write(f"**{label}:**")
                        try: st.json(json.loads(str(data)) if isinstance(data, str) else data)
                        except: st.code(str(data))
                safe_json("AirLabs", selected_row.get('Airlabs Info'))
                safe_json("OpenSky", selected_row.get('OpenSky State Info'))
                safe_json("HexDB Route", selected_row.get('Hexdb Route Info'))
                safe_json("HexDB Aircraft", selected_row.get('Hexdb Aircraft Info'))
                safe_json("PlaneSpotters", selected_row.get('Planespotters Info'))

except Exception as e:
    st.error(f"Erreur d'affichage : {e}")
