# BBox Joinville (élargie pour le test)
# BBOX = {"lamin": 48.75, "lamax": 48.90, "lomin": 2.35, "lomax": 2.60}

import requests
import pandas as pd
from datetime import datetime
import streamlit as st
from streamlit_gsheets import GSheetsConnection
from FlightRadar24 import FlightRadar24API

# Initialisation de l'API FlightRadar24 (version gratuite)
fr_api = FlightRadar24API()

# BBOX Joinville resserrée
BBOX = {"lamin": 48.75, "lamax": 48.90, "lomin": 2.35, "lomax": 2.60}
ALTITUDE_MAX = 3500 

def get_real_flight_info(icao24):
    """Va chercher les vrais aéroports sur FlightRadar24"""
    try:
        # On cherche l'avion par son adresse unique ICAO24
        details = fr_api.get_flight_details(icao24)
        if details and 'airport' in details:
            origin = details['airport'].get('origin', {}).get('code', {}).get('iata', "Inconnu")
            dest = details['airport'].get('destination', {}).get('code', {}).get('iata', "Inconnu")
            
            # Récupération des horaires (si disponibles)
            time_info = details.get('time', {})
            h_dep = datetime.fromtimestamp(time_info['real']['departure']).strftime('%H:%M') if time_info.get('real', {}).get('departure') else "--:--"
            h_arr = datetime.fromtimestamp(time_info['estimated']['arrival']).strftime('%H:%M') if time_info.get('estimated', {}).get('arrival') else "--:--"
            
            return origin, dest, h_dep, h_arr
    except:
        pass
    return "Inconnu", "Inconnu", "--:--", "--:--"

def main():
    print("--- Scan Joinville (Source Hybride : OpenSky + FlightRadar24) ---")
    try:
        USER = st.secrets["OPENSKY_USER"].lower()
        PWD = st.secrets["OPENSKY_PWD"]
        
        url = f"https://opensky-network.org/api/states/all?lamin={BBOX['lamin']}&lomin={BBOX['lomin']}&lamax={BBOX['lamax']}&lomax={BBOX['lomax']}"
        response = requests.get(url, auth=(USER, PWD), timeout=15)
        
        if response.status_code == 200:
            states = response.json().get('states')
            if not states:
                print("Ciel vide.")
                return

            nouveaux_vols = []
            for avion in states:
                icao24 = avion[0] # L'identifiant unique de l'appareil
                callsign = str(avion[1]).strip() if avion[1] else "Inconnu"
                altitude = avion[13] or avion[7] or 0
                
                if not avion[8] and altitude < ALTITUDE_MAX:
                    print(f"✈️ Avion détecté : {callsign}. Recherche des aéroports...")
                    
                    # MAGIE : On demande à FlightRadar24 les infos que OpenSky n'a pas
                    dep, arr, h_dep, h_arr = get_real_flight_info(icao24)

                    nouveaux_vols.append({
                        "Date": datetime.now().strftime("%d/%m/%Y"),
                        "Heure": datetime.now().strftime("%H:%M"),
                        "Avion": callsign,
                        "Altitude": int(altitude),
                        "De": dep,
                        "A": arr,
                        "Dep_H": h_dep,
                        "Arr_H": h_arr
                    })

            if nouveaux_vols:
                conn = st.connection("gsheets", type=GSheetsConnection)
                cols = ["Date", "Heure", "Avion", "Altitude", "De", "A", "Dep_H", "Arr_H"]
                
                try:
                    df_existant = conn.read(worksheet="Vols_Joinville", ttl=0)
                    df_existant = df_existant[cols] # On force l'ordre des colonnes
                except:
                    df_existant = pd.DataFrame(columns=cols)

                df_nouveaux = pd.DataFrame(nouveaux_vols, columns=cols)
                df_final = pd.concat([df_existant, df_nouveaux], ignore_index=True)
                df_final = df_final.drop_duplicates(subset=['Date', 'Heure', 'Avion'], keep='last')
                
                conn.update(worksheet="Vols_Joinville", data=df_final.fillna("").tail(2000))
                print(f"Succès : {len(nouveaux_vols)} vols enrichis enregistrés.")
        else:
            print(f"Erreur OpenSky : {response.status_code}")
    except Exception as e:
        print(f"Erreur : {e}")

if __name__ == "__main__":
    main()