import requests
import pandas as pd
import streamlit as st
from streamlit_gsheets import GSheetsConnection
from datetime import datetime, timedelta
import time

# Configuration
WORKSHEET = "Vols_Joinville"

# Schéma complet aligné sur infinite_collector.py
COLS = [
    "Date", "Heure", "Identifiant Vol (Callsign)", "Compagnie", "Modèle Avion", 
    "Immatriculation", "Identifiant Appareil (ICAO24)", "Altitude (m)", 
    "Evolution Verticale", "De", "A", "Dep_H", "Arr_H", "Source", 
    "Planespotters", "Positions", "Airlabs Info"
]

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
                # On prend le vol dont le passage est le plus proche du moment détecté
                best_flight = min(flights, key=lambda f: abs((f.get('firstSeen') or 0) - timestamp))
                
                dep = best_flight.get('estDepartureAirport') or "Inconnu"
                arr = best_flight.get('estArrivalAirport') or "Inconnu"
                
                # Formatage horaires
                h_dep = datetime.fromtimestamp(best_flight.get('firstSeen')).strftime('%H:%M') if best_flight.get('firstSeen') else "--:--"
                h_arr = datetime.fromtimestamp(best_flight.get('lastSeen')).strftime('%H:%M') if best_flight.get('lastSeen') else "--:--"
                
                return dep, arr, h_dep, h_arr
    except Exception as e:
        print(f"    [Nettoyeur] Erreur API OpenSky pour {icao24}: {e}")
    return None, None, None, None

def main():
    print(f"\n{'='*60}")
    print(f"🧹 DÉMARRAGE DU NETTOYEUR J+1 : {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*60}")
    
    try:
        user = st.secrets["OPENSKY_USER"].lower()
        pwd = st.secrets["OPENSKY_PWD"]
        conn = st.connection("gsheets", type=GSheetsConnection)
        
        # 1. Lecture de la base
        df = conn.read(worksheet=WORKSHEET, ttl=0)
        if df.empty:
            print("    Base vide, rien à nettoyer.")
            return
            
        # 2. Migration du schéma si nécessaire (pour être robuste aux anciennes versions)
        rename_map = {
            "Avion":    "Identifiant Vol (Callsign)",
            "icao24":   "Identifiant Appareil (ICAO24)",
            "Altitude": "Altitude (m)",
        }
        df = df.rename(columns=rename_map)
        for c in COLS:
            if c not in df.columns:
                df[c] = ""
        df = df[COLS]

        # 3. Identifier les lignes à nettoyer
        # On traite :
        # - Les lignes où la route est "Inconnu"
        # - Les lignes dont la Source de route est jugée peu fiable (hexdb ou OpenSky Live)
        SOURCES_NON_FIABLES = ["hexdb", "OpenSky (Live)"]
        mask_incomplet = (df['De'] == "Inconnu") | (df['A'] == "Inconnu") | (df['Source'].isin(SOURCES_NON_FIABLES))
        
        # Conversion sécurisée des dates pour le filtrage
        df['dt_temp'] = pd.to_datetime(df['Date'] + ' ' + df['Heure'], format='%d/%m/%Y %H:%M', errors='coerce')
        limit_date = datetime.now() - timedelta(hours=12)
        mask_eligible = mask_incomplet & (df['dt_temp'] < limit_date)

        df_todo = df[mask_eligible].copy()
        
        if df_todo.empty:
            print("    Aucun vol 'Inconnu' éligible pour le nettoyage (> 12h d'attente requise).")
            return

        print(f"    🔍 {len(df_todo)} vol(s) à enrichir via l'historique OpenSky...")
        
        success_count = 0
        for idx, row in df_todo.iterrows():
            icao = row['Identifiant Appareil (ICAO24)']
            ts = int(row['dt_temp'].timestamp())
            
            print(f"    -> Traitement de {row['Identifiant Vol (Callsign)']} ({icao}) du {row['Date']} {row['Heure']}...")
            
            dep, arr, h_dep, h_arr = get_opensky_flight_history(icao, ts, user, pwd)
            
            updated = False
            if dep and dep != "Inconnu":
                df.at[idx, 'De'] = dep
                updated = True
            if arr and arr != "Inconnu":
                df.at[idx, 'A'] = arr
                updated = True
            if h_dep and h_dep != "--:--":
                df.at[idx, 'Dep_H'] = h_dep
                updated = True
            if h_arr and h_arr != "--:--":
                df.at[idx, 'Arr_H'] = h_arr
                updated = True
                
            if updated:
                df.at[idx, 'Source'] = "OpenSky (J+1)"
                success_count += 1
                print(f"       ✅ Enrichi : {dep} -> {arr}")
            else:
                print(f"       ❌ Aucune donnée historique trouvée pour le moment.")
                
            time.sleep(1) # Rate limit OpenSky

        if success_count > 0:
            # Nettoyage de la colonne temporaire et sauvegarde
            df = df.drop(columns=['dt_temp'])
            conn.update(worksheet=WORKSHEET, data=df.fillna(""))
            print(f"\n    💾 SUCCÈS : {success_count} vol(s) complété(s) dans GSheets.")
        else:
            print("\n    Fin du nettoyage. Aucune modification nécessaire.")

    except Exception as e:
        print(f"❌ ERREUR CRITIQUE : {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
