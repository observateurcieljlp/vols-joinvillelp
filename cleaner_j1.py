import requests
import pandas as pd
import streamlit as st
from streamlit_gsheets import GSheetsConnection
from datetime import datetime, timedelta
import time

# Configuration
WORKSHEET = "Vols_Joinville"
COLS = ["Date", "Heure", "Identifiant Vol (Callsign)", "Compagnie", "Modèle Avion", "Immatriculation", "Age Avion", "Identifiant Appareil (ICAO24)", "Altitude (m)", "De", "A", "Dep_H", "Arr_H", "Source"]

def get_opensky_flight_history(icao24, timestamp, user, pwd):
    """Interroge l'API historique d'OpenSky pour retrouver les aéroports."""
    begin = timestamp - (4 * 3600)
    end = timestamp + (4 * 3600)
    
    url = f"https://opensky-network.org/api/flights/aircraft?icao24={icao24}&begin={begin}&end={end}"
    try:
        response = requests.get(url, auth=(user, pwd), timeout=20)
        if response.status_code == 200:
            flights = response.json()
            if flights:
                best_flight = min(flights, key=lambda f: abs(f.get('firstSeen', 0) - timestamp))
                dep = best_flight.get('estDepartureAirport') or "Inconnu"
                arr = best_flight.get('estArrivalAirport') or "Inconnu"
                
                # Formatage horaires
                h_dep = datetime.fromtimestamp(best_flight.get('firstSeen')).strftime('%H:%M') if best_flight.get('firstSeen') else "--:--"
                h_arr = datetime.fromtimestamp(best_flight.get('lastSeen')).strftime('%H:%M') if best_flight.get('lastSeen') else "--:--"
                
                return dep, arr, h_dep, h_arr
    except Exception as e:
        print(f"Erreur API OpenSky pour {icao24}: {e}")
    return None, None, None, None

def main():
    print("--- Démarrage du Nettoyeur J+1 (Enrichissement Historique) ---")
    
    try:
        user = st.secrets["OPENSKY_USER"].lower()
        pwd = st.secrets["OPENSKY_PWD"]
        conn = st.connection("gsheets", type=GSheetsConnection)
        
        # 1. Lecture de la base
        df = conn.read(worksheet=WORKSHEET, ttl=0)
        if df.empty: return
            
        # 2. Identifier les lignes "Inconnu"
        mask_incomplet = (df['De'] == "Inconnu") | (df['A'] == "Inconnu")
        df['dt'] = pd.to_datetime(df['Date'] + ' ' + df['Heure'], format='%d/%m/%Y %H:%M')
        mask_eligible = mask_incomplet & (df['dt'] < datetime.now() - timedelta(hours=12))

        df_todo = df[mask_eligible].copy()
        if df_todo.empty: return

        print(f"Tentative d'enrichissement pour {len(df_todo)} vols...")
        
        success_count = 0
        for idx, row in df_todo.iterrows():
            icao = row['Identifiant Appareil (ICAO24)']
            ts = int(row['dt'].timestamp())
            
            dep, arr, h_dep, h_arr = get_opensky_flight_history(icao, ts, user, pwd)
            
            if dep and dep != "Inconnu":
                df.at[idx, 'De'] = dep
                df.at[idx, 'Source'] = "OpenSky (J+1)"
                success_count += 1
            if arr and arr != "Inconnu":
                df.at[idx, 'A'] = arr
                df.at[idx, 'Source'] = "OpenSky (J+1)"
                success_count += 1
            if h_dep != "--:--": df.at[idx, 'Dep_H'] = h_dep
            if h_arr != "--:--": df.at[idx, 'Arr_H'] = h_arr
                
            time.sleep(1)

        if success_count > 0:
            df = df.drop(columns=['dt'])
            conn.update(worksheet=WORKSHEET, data=df.fillna(""))
            print(f"SUCCÈS : {success_count} vols complétés.")
