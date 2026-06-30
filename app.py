import streamlit as st
import pandas as pd
import pydeck as pdk
import json
from streamlit_gsheets import GSheetsConnection

st.set_page_config(page_title="Observateur du Ciel", page_icon="✈️", layout="wide")

# Style CSS personnalisé
st.markdown("""
    <style>
    .main { background-color: #f8f9fb; }
    [data-testid="stMetricValue"] { font-size: 24px; color: #1f77b4; }
    .stDataFrame { border-radius: 10px; overflow: hidden; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
    </style>
""", unsafe_allow_html=True)

# =============================================================================
# CONFIGURATION DES SITES D'OBSERVATION
# =============================================================================
SITES = {
    "Joinville-le-Pont": {
        "worksheet":   "Vols_Joinville",
        "center":      {"lat": 48.8230, "lon": 2.4736},
        "description": "Surveillance citoyenne des survols à basse altitude autour de **Joinville-le-Pont**. Sources : OpenSky, HexDB, AirLabs.",
        "zoom":        13.5,
    },
    "Pessac": {
        "worksheet":   "Vols_Pessac",
        "center":      {"lat": 44.81385, "lon": -0.61735},
        "description": "Surveillance citoyenne des survols à basse altitude autour de **Pessac**. Sources : OpenSky, HexDB, AirLabs.",
        "zoom":        13.0,
    },
}

# --- Sélecteur de site (défini avant tout chargement de données) ---
st.sidebar.header("🌍 Site d'observation")
selected_site_name = st.sidebar.selectbox("Choisir un site", list(SITES.keys()), key="site_selector")
site = SITES[selected_site_name]

st.title(f"✈️ Survols à basse altitude - {selected_site_name}")
st.markdown(site["description"])

# Connexion — onglet dynamique selon le site sélectionné
try:
    conn = st.connection("gsheets", type=GSheetsConnection)
    df = conn.read(worksheet=site["worksheet"], ttl=0)
except Exception as e:
    st.error(f"Erreur connexion : {e}")
    st.stop()

if df is None or df.empty:
    st.info("Aucun vol enregistré.")
else:
    # 1. Nettoyage et conversion
    df['Date'] = df['Date'].astype(str)
    for col in ['Lat', 'Lon', 'Heading', 'Altitude (m)']:
        df[col] = pd.to_numeric(df[col], errors='coerce')
    if 'Identifiant Appareil (ICAO24)' in df.columns:
        df['Identifiant Appareil (ICAO24)'] = df['Identifiant Appareil (ICAO24)'].astype(str)

    # 2. Sidebar et Filtres
    st.sidebar.header("🗓️ Filtres")
    jour_choisi = st.sidebar.selectbox("Journée", sorted(df['Date'].unique(), reverse=True), key="jour_selector")
    if st.sidebar.button("🔄 Actualiser"): st.rerun()
    
    df_jour = df[df['Date'] == jour_choisi].copy().reset_index(drop=True)
    st.metric(f"Vols détectés le {jour_choisi}", len(df_jour))

    # 3. Tableau
    st.subheader("📋 Liste des survols")
    cols_tableau = ['Heure', 'Identifiant Vol (Callsign)', 'Immatriculation', 'Compagnie', 'Modèle Avion', 'Altitude (m)', 'Evolution Verticale', 'De', 'A', 'Source']
    event = st.dataframe(df_jour[cols_tableau], use_container_width=True, on_select="rerun", selection_mode="single-row",
                         column_config={"Altitude (m)": st.column_config.NumberColumn(format="%d m"), "Source": st.column_config.TextColumn("Fiabilité")})

    # Gestion de la sélection
    selected_row = None
    if event and event.selection.rows:
        selected_idx = event.selection.rows[0]
        selected_row = df_jour.iloc[selected_idx]

    # 4. Carte
    st.subheader("📍 Position sur la carte")
    map_df = df_jour.dropna(subset=['Lat', 'Lon']).copy()

    # Couleur
    def set_map_color(row):
        if selected_row is not None and row['Identifiant Appareil (ICAO24)'] == selected_row['Identifiant Appareil (ICAO24)']:
            return [255, 0, 0, 255] # Rouge
        trend = str(row.get('Evolution Verticale', '')).lower()
        if 'montée' in trend: return [46, 204, 113, 200]
        if 'descente' in trend: return [230, 126, 34, 200]
        return [0, 100, 255, 180]

    map_df['color'] = map_df.apply(set_map_color, axis=1)

    # Icône Avion
    icon_data = {
        "url": "https://img.icons8.com/color/96/000000/airplane-mode-on.png",
        "width": 128,
        "height": 128,
        "anchorY": 64,
    }
    map_df["icon_data"] = [icon_data] * len(map_df)

    # Couleur et Taille basées sur la sélection
    def set_map_size(row):
        if selected_row is not None and row['Identifiant Appareil (ICAO24)'] == selected_row['Identifiant Appareil (ICAO24)']:
            return 80 # Plus gros si sélectionné
        return 40

    map_df['size'] = map_df.apply(set_map_size, axis=1)

    # État de la vue — centre dynamique selon le site
    SITE_CENTER = site["center"]
    has_coords = (selected_row is not None and not pd.isna(selected_row['Lat']) and not pd.isna(selected_row['Lon']))
    view_state = pdk.ViewState(
        latitude=selected_row['Lat'] if has_coords else SITE_CENTER["lat"],
        longitude=selected_row['Lon'] if has_coords else SITE_CENTER["lon"],
        zoom=site["zoom"],
        pitch=0,
    )
    
    st.pydeck_chart(pdk.Deck(
        map_style=None,
        initial_view_state=view_state,
        layers=[
            pdk.Layer("IconLayer", map_df, get_position=["Lon", "Lat"], get_icon="icon_data", get_size="size", get_color="color", get_angle="90-Heading", pickable=True)
        ],
        tooltip={"text": "{Identifiant Vol (Callsign)}\nAlt: {Altitude (m)}m\nCap: {Heading}°"}
    ))

    # 5. Légende et Analytics sous la carte
    st.markdown("### 🎨 Légende")
    st.markdown("- 🟢 Montée | 🟠 Descente | 🔵 Stable | 🔴 Sélectionné")

    st.markdown("---")
    col_stats1, col_stats2 = st.columns(2)
    
    with col_stats1:
        st.subheader("🛫 Origines (Départs)")
        df_dep = df_jour['De'].value_counts().reset_index()
        df_dep.columns = ['Aéroport', 'Vols']
        if not df_dep.empty:
            st.bar_chart(df_dep.set_index('Aéroport'))
        else:
            st.info("Aucune donnée d'origine.")

    with col_stats2:
        st.subheader("🛬 Destinations (Arrivées)")
        df_arr = df_jour['A'].value_counts().reset_index()
        df_arr.columns = ['Aéroport', 'Vols']
        if not df_arr.empty:
            st.bar_chart(df_arr.set_index('Aéroport'))
        else:
            st.info("Aucune donnée de destination.")

    # 6. Détails Sidebar
    if selected_row is not None:
        st.sidebar.markdown("---")
        st.sidebar.subheader(f"🔍 Détails : {selected_row['Identifiant Vol (Callsign)']}")
        st.sidebar.info(f"**Passage à {selected_row['Heure']}**")
        st.sidebar.write(f"✈️ **Modèle :** {selected_row['Modèle Avion']}")
        st.sidebar.write(f"🏢 **Compagnie :** {selected_row['Compagnie']}")
        st.sidebar.write(f"🆔 **Immat :** {selected_row['Immatriculation']}")
        
        col_a, col_b = st.sidebar.columns(2)
        icao = str(selected_row['Identifiant Appareil (ICAO24)'])
        if icao and icao != 'nan':
            try:
                # 1. Formatage de la date
                date_val = pd.to_datetime(selected_row['Date'], format='%d/%m/%Y', errors='coerce')
                date_formatted = date_val.strftime("%Y-%m-%d")

                # 2. Extraction du timestamp depuis OpenSky State Info (4ème élément, index 3)
                timestamp = ""
                os_raw = selected_row.get('OpenSky State Info')
                if os_raw and not pd.isna(os_raw):
                    try:
                        os_data = json.loads(str(os_raw))
                        if isinstance(os_data, list) and len(os_data) > 3:
                            timestamp = f"&timestamp={os_data[3]+4}"
                    except: pass
                lat, lon = selected_row['Lat'], selected_row['Lon']
                adsb_url = f"https://globe.adsbexchange.com/?icao={icao}&showTrace={date_formatted}{timestamp}&lat={lat}&lon={lon}&zoom=13.5"
            except:
                adsb_url = f"https://globe.adsbexchange.com/?icao={icao}"

            col_a.link_button("🛰️ Trace ADSB", adsb_url)
            col_b.link_button("📷 Photos", f"https://www.planespotters.net/hex/{icao.upper()}")
        
        with st.sidebar.expander("🛠️ Données techniques (JSON)"):
            def safe_json_display(label, data):
                if data and not pd.isna(data) and str(data).strip() != "":
                    st.write(f"**{label}:**")
                    try: st.json(json.loads(str(data)) if isinstance(data, str) else data)
                    except: st.code(str(data))
            safe_json_display("AirLabs", selected_row.get('Airlabs Info'))
            safe_json_display("OpenSky", selected_row.get('OpenSky State Info'))
            safe_json_display("HexDB Route", selected_row.get('Hexdb Route Info'))
            safe_json_display("HexDB Aircraft", selected_row.get('Hexdb Aircraft Info'))
            safe_json_display("PlaneSpotters", selected_row.get('Planespotters Info'))
            safe_json_display("Base Locale (OpenSky)", selected_row.get('Aircraft DB Info'))
