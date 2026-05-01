import streamlit as st
import pandas as pd
import trino
from datetime import datetime, time, timezone
from streamlit_gsheets import GSheetsConnection

# ==========================================
# CONFIGURATION DE LA PAGE & CONSTANTES
# ==========================================
st.set_page_config(page_title="Observatoire des Vols - Joinville", page_icon="✈️", layout="wide")

# Bounding box stricte pour Joinville-le-Pont (impossible à changer par l'utilisateur)
BBOX = {
    "lat_min": 48.809,
    "lat_max": 48.828,
    "lon_min": 2.455,
    "lon_max": 2.485
}

st.title("✈️ Observatoire du Ciel - Joinville-le-Pont")
st.markdown("Identifiez les avions passés à basse altitude au-dessus de la commune.")

# ==========================================
# INTERFACE UTILISATEUR (FILTRES)
# ==========================================
st.sidebar.header("Recherche Historique")
st.sidebar.info("La base de données historique requiert une recherche ciblée heure par heure.")

# Sélection de la date et de l'heure
selected_date = st.sidebar.date_input("Date du survol")
selected_hour = st.sidebar.slider("Heure (UTC)", min_value=0, max_value=23, value=12, help="L'heure exacte du passage (Heure universelle UTC).")
altitude_max = st.sidebar.number_input("Altitude Max (mètres)", min_value=500, max_value=5000, value=1500, step=100)

# Création d'une clé de recherche unique pour le cache Google Sheets
search_key = f"{selected_date}_{selected_hour:02d}"

# Calcul du Timestamp UNIX pour le début de l'heure (Exigence stricte de Trino/OpenSky)
dt_start_of_hour = datetime.combine(selected_date, time(hour=selected_hour), tzinfo=timezone.utc)
hour_unix = int(dt_start_of_hour.timestamp())

# ==========================================
# FONCTIONS DE CONNEXION ET RECHERCHE
# ==========================================

def fetch_from_trino(hour_unix, alt_max):
    """Interroge la base de données historique d'OpenSky via Trino"""
    # Récupération des identifiants (OpenSky exige un identifiant en minuscules)
    user = st.secrets["OPENSKY_USER"].lower()
    pwd = st.secrets["OPENSKY_PWD"]
    
    # Connexion au client Trino
    conn = trino.dbapi.connect(
        host='trino.opensky-network.org',
        port=443,
        http_scheme='https',
        user=user,
        auth=trino.auth.BasicAuthentication(user, pwd),
        catalog='minio',
        schema='osky'
    )
    
    # Requête SQL : Filtre strictement sur l'heure (partition), la bounding box et l'altitude
    query = f"""
        SELECT 
            time as timestamp, 
            icao24, 
            callsign, 
            lat, 
            lon, 
            COALESCE(geoaltitude, baroaltitude) as altitude, 
            velocity
        FROM state_vectors_data4
        WHERE hour = {hour_unix}
          AND lat BETWEEN {BBOX['lat_min']} AND {BBOX['lat_max']}
          AND lon BETWEEN {BBOX['lon_min']} AND {BBOX['lon_max']}
          AND COALESCE(geoaltitude, baroaltitude) <= {alt_max}
    """
    
    # Exécution de la requête
    cursor = conn.cursor()
    cursor.execute(query)
    rows = cursor.fetchall()
    
    # Conversion en DataFrame Pandas
    columns = [desc[0] for desc in cursor.description]
    df = pd.DataFrame(rows, columns=columns)
    
    # Nettoyage des données
    if not df.empty:
        df['callsign'] = df['callsign'].astype(str).str.strip()
        # Convertir le timestamp Unix en heure lisible
        df['heure_exacte'] = pd.to_datetime(df['timestamp'], unit='s', utc=True).dt.tz_convert('Europe/Paris').dt.strftime('%H:%M:%S')
    
    return df

# ==========================================
# LOGIQUE PRINCIPALE & GOOGLE SHEETS
# ==========================================

if st.sidebar.button("Rechercher les vols", type="primary"):
    with st.spinner(f"Vérification des données pour le {selected_date} à {selected_hour}h00..."):
        
        # 1. Connexion au Google Sheet
        gsheets_conn = st.connection("gsheets", type=GSheetsConnection)
        
        try:
            # Lecture du cache existant
            df_cache = gsheets_conn.read(worksheet="Vols_Joinville")
        except Exception:
            # Si la feuille est vide ou n'existe pas, on initialise une structure vide
            df_cache = pd.DataFrame(columns=['search_key', 'timestamp', 'heure_exacte', 'icao24', 'callsign', 'lat', 'lon', 'altitude', 'velocity'])

        # 2. Vérification si la donnée est déjà dans le Sheet
        if not df_cache.empty and 'search_key' in df_cache.columns and (df_cache['search_key'] == search_key).any():
            st.success("Données récupérées depuis l'historique local (Google Sheets).")
            df_result = df_cache[df_cache['search_key'] == search_key].copy()
            # On re-filtre l'altitude au cas où l'utilisateur a baissé le curseur par rapport à la recherche en cache
            df_result = df_result[df_result['altitude'] <= altitude_max]
            
        else:
            # 3. Appel de l'API Trino si la donnée n'est pas dans le cache
            st.warning("Nouvelle recherche : Interrogation de la base de données OpenSky en cours...")
            try:
                df_result = fetch_from_trino(hour_unix, altitude_max)
                
                if not df_result.empty:
                    # Ajout de la clé de recherche pour le cache
                    df_result['search_key'] = search_key
                    
                    # Mise à jour du Google Sheet avec les nouvelles données
                    df_updated = pd.concat([df_cache, df_result], ignore_index=True)
                    gsheets_conn.update(worksheet="Vols_Joinville", data=df_updated)
                    st.success("Recherche terminée et enregistrée dans le registre communautaire !")
                else:
                    st.info("Aucun vol trouvé à cette heure, à cette altitude.")
                    
            except Exception as e:
                st.error(f"Erreur lors de la connexion à OpenSky Trino : {e}")
                st.stop()

        # ==========================================
        # AFFICHAGE DES RÉSULTATS
        # ==========================================
        if not df_result.empty:
            col1, col2 = st.columns([2, 1])
            
            with col1:
                st.subheader("Tracé des passages")
                # Affichage simple sur carte des points de passage
                st.map(df_result, latitude="lat", longitude="lon", color="#FF0000", size=50)
            
            with col2:
                st.subheader("Détails des vols")
                st.metric("Nombre de mesures", len(df_result))
                st.metric("Vols distincts (Indicatifs)", df_result['callsign'].replace('', pd.NA).nunique())
                
            st.subheader("Tableau de bord des survols")
            # Affichage d'un tableau propre pour les riverains
            display_df = df_result[['heure_exacte', 'callsign', 'icao24', 'altitude', 'velocity']].copy()
            display_df.rename(columns={
                'heure_exacte': 'Heure (Locale)', 
                'callsign': 'Indicatif Vol', 
                'icao24': 'Code Transpondeur', 
                'altitude': 'Altitude (m)', 
                'velocity': 'Vitesse (m/s)'
            }, inplace=True)
            
            st.dataframe(display_df, use_container_width=True)