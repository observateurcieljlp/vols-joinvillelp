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
                return [255, 0, 0, 230] # Rouge si sélectionné
            return [0, 120, 255, 160] # Bleu par défaut
            
        def set_map_size(row):
            if selected_row is not None and row['Identifiant Appareil (ICAO24)'] == selected_row['Identifiant Appareil (ICAO24)']:
                return 120
            return 60

        map_df['color'] = map_df.apply(set_map_color, axis=1)
        map_df['size'] = map_df.apply(set_map_size, axis=1)

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

        # 2. Vecteur de direction (uniquement si le Heading est présent)
        map_layers = [layer_points]
        if not map_df['Heading'].isna().all():
            layer_arrows = pdk.Layer(
                "TextLayer",
                map_df.dropna(subset=['Heading']),
                get_position=["Lon", "Lat"],
                get_text="arrow",
                get_color="color",
                get_angle="-Heading",
                get_size=25,
                alignment_baseline="'center'",
            )
            map_layers.append(layer_arrows)

        # État de la vue (centrage intelligent)
        # On ne centre sur l'avion que si ses coordonnées sont valides (pas NaN)
        has_valid_coords = (selected_row is not None and 
                            not pd.isna(selected_row['Lat']) and 
                            not pd.isna(selected_row['Lon']))

        view_state = pdk.ViewState(
            latitude=selected_row['Lat'] if has_valid_coords else JOINVILLE_CENTER["lat"],
            longitude=selected_row['Lon'] if has_valid_coords else JOINVILLE_CENTER["lon"],
            zoom=14 if has_valid_coords else 13,
            pitch=0,
        )

        # --- Préparation des trajectoires (Breadcrumbs) ---
        def parse_positions(pos_str):
            if not pos_str or pd.isna(pos_str): return []
            paths = []
            # Format: (lat, lon) | (lat, lon)
            points = str(pos_str).split(" | ")
            for p in points:
                try:
                    # Extraction des nombres via regex
                    coords = re.findall(r"[-+]?\d*\.\d+|\d+", p)
                    if len(coords) >= 2:
                        # Pydeck attend [lon, lat]
                        paths.append([float(coords[1]), float(coords[0])])
                except: continue
            return paths

        # On crée un DataFrame pour les trajets
        path_data = []
        for _, row in df_jour.iterrows():
            path = parse_positions(row.get('Positions', ''))
            if len(path) > 1:
                is_selected = (selected_row is not None and row['Identifiant Appareil (ICAO24)'] == selected_row['Identifiant Appareil (ICAO24)'])
                path_data.append({
                    "path": path,
                    "color": [255, 0, 0, 200] if is_selected else [0, 120, 255, 100],
                    "width": 5 if is_selected else 2,
                    "callsign": row['Identifiant Vol (Callsign)']
                })

        # Calque des trajets (Lignes)
        layer_paths = pdk.Layer(
            "PathLayer",
            path_data,
            get_path="path",
            get_color="color",
            get_width="width",
            width_min_pixels=2,
            pickable=True
        )

        # Calque des points (Scatter)
        layer_points = pdk.Layer(
            "ScatterplotLayer",
            map_df,
            get_position=["Lon", "Lat"],
            get_color="color",
            get_radius="size",
            pickable=True,
        )

        # Construction de la liste des calques
        map_layers = [layer_paths, layer_points]

        # Ajout du calque de direction seulement si le Heading est présent
        if not map_df['Heading'].isna().all():
            layer_arrows = pdk.Layer(
                "TextLayer",
                map_df.dropna(subset=['Heading']),
                get_position=["Lon", "Lat"],
                get_text="arrow",
                get_color="color",
                get_angle="-Heading",
                get_size=25,
                alignment_baseline="'center'",
            )
            map_layers.append(layer_arrows)

        # Rendu de la carte
        if map_df.empty:
            st.warning("⚠️ Aucune coordonnée précise (Lat/Lon) n'est encore disponible pour les vols de cette journée.")
        
        st.pydeck_chart(pdk.Deck(
            map_style=None,
            initial_view_state=view_state,
            layers=map_layers, # Utilisation de la liste dynamique corrigée
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
            
            # --- INSPECTEUR DE DONNÉES BRUTES ---
            st.sidebar.markdown("---")
            with st.sidebar.expander("🛠️ Données techniques (JSON)"):
                al_raw = selected_row.get('Airlabs Info')
                if al_raw and al_raw != "":
                    st.write("**AirLabs:**")
                    st.json(json.loads(al_raw) if isinstance(al_raw, str) else al_raw)
                
                os_raw = selected_row.get('OpenSky State Info')
                if os_raw and os_raw != "":
                    st.write("**OpenSky:**")
                    st.json(json.loads(os_raw) if isinstance(os_raw, str) else os_raw)
            # ------------------------------------

except Exception as e:
    st.error(f"Erreur d'affichage : {e}")
    st.info("Attendez qu'un nouveau vol soit capturé avec les colonnes Lat/Lon pour voir la carte.")
