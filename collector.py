import requests
import pandas as pd
from datetime import datetime
import streamlit as st
from streamlit_gsheets import GSheetsConnection
from FlightRadar24 import FlightRadar24API

# Suppression du warning Pandas futur
pd.set_option('future.no_silent_downcasting', True)

# Initialisation de l'API FlightRadar24 (version gratuite)
fr_api = FlightRadar24API()

# BBOX Joinville resserrée
BBOX = {"lamin": 48.75, "lamax": 48.90, "lomin": 2.35, "lomax": 2.60}
ALTITUDE_MAX = 3500 

def get_fr24_flights_in_area():
    """Récupère tous les vols FR24 dans la zone pour croisement"""
    try:
        # Format FR24: "north,south,west,east"
        bounds = f"{BBOX['lamax']},{BBOX['lamin']},{BBOX['lomin']},{BBOX['lomax']}"
        return fr_api.get_flights(bounds=bounds)
    except:
        return []

def get_real_flight_info(icao24, fr24_flights):
    """Va chercher les vrais aéroports sur FlightRadar24 en croisant avec les vols déjà récupérés"""
    try:
        # On cherche le vol correspondant à l'ICAO24 dans notre liste FR24
        flight = next((f for f in fr24_flights if f.icao_24bit and f.icao_24bit.lower() == icao24.lower()), None)
        
        if flight:
            details = fr_api.get_flight_details(flight)
            flight.set_flight_details(details)
            
            origin = flight.origin_airport_iata if flight.origin_airport_iata != "N/A" else "Inconnu"
            dest = flight.destination_airport_iata if flight.destination_airport_iata != "N/A" else "Inconnu"
            
            # Récupération des horaires (si disponibles dans les détails)
            h_dep = "--:--"
            h_arr = "--:--"
            
            if details and 'time' in details:
                time_info = details.get('time', {})
                if time_info.get('real', {}).get('departure'):
                    h_dep = datetime.fromtimestamp(time_info['real']['departure']).strftime('%H:%M')
                if time_info.get('estimated', {}).get('arrival'):
                    h_arr = datetime.fromtimestamp(time_info['estimated']['arrival']).strftime('%H:%M')
            
            return origin, dest, h_dep, h_arr
    except Exception as e:
        print(f"  [Info] Erreur enrichment pour {icao24}: {e}")
    
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

            # 1. Filtrage préliminaire sur l'altitude pour ne pas appeler FR24 inutilement
            vols_potitiels = []
            for avion in states:
                altitude = avion[13] or avion[7] or 0
                au_sol = avion[8]
                if not au_sol and altitude < ALTITUDE_MAX:
                    vols_potitiels.append(avion)

            if not vols_potitiels:
                print(f"Aucun des {len(states)} avions détectés n'est en basse altitude (< {ALTITUDE_MAX}m).")
                return

            print(f"Détection de {len(vols_potitiels)} avions en basse altitude. Vérification du cache...")

            # 2. Lecture du cache GSheets AVANT d'appeler FR24 pour éviter les doublons d'appels
            conn = st.connection("gsheets", type=GSheetsConnection)
            cols = ["Date", "Heure", "Avion", "icao24", "Altitude", "De", "A", "Dep_H", "Arr_H"]
            try:
                df_existant = conn.read(worksheet="Vols_Joinville", ttl=0)
                # S'assurer que les colonnes indispensables sont là
                if "icao24" not in df_existant.columns:
                    df_existant = pd.DataFrame(columns=cols)
                else:
                    df_existant = df_existant[cols]
            except:
                df_existant = pd.DataFrame(columns=cols)

            # 3. Filtrage des avions déjà traités récemment (< 15 min)
            vols_a_enrichir = []
            now = datetime.now()
            for avion in vols_potitiels:
                icao24 = avion[0]
                callsign = str(avion[1]).strip() if avion[1] else "Inconnu"
                
                # Vérifier si cet icao24 est déjà dans les 15 dernières minutes
                deja_vu = False
                if not df_existant.empty:
                    # On filtre sur le même jour et le même icao24
                    today_str = now.strftime("%d/%m/%Y")
                    match_icao = df_existant[(df_existant['icao24'] == icao24) & (df_existant['Date'] == today_str)]
                    
                    for _, row in match_icao.iterrows():
                        try:
                            heure_v = datetime.strptime(row['Heure'], "%H:%M")
                            heure_now = datetime.strptime(now.strftime("%H:%M"), "%H:%M")
                            diff_min = abs((heure_now - heure_v).total_seconds() / 60)
                            if diff_min < 15: # Si vu il y a moins de 15 minutes, on ignore
                                deja_vu = True
                                break
                        except: pass
                
                if not deja_vu:
                    vols_a_enrichir.append(avion)
                else:
                    print(f"  [Skip] {callsign} ({icao24}) déjà enregistré récemment.")

            if not vols_a_enrichir:
                print("Tous les avions détectés ont déjà été traités récemment.")
                return

            # 4. On n'appelle FR24 que pour les nouveaux
            print(f"Enrichissement de {len(vols_a_enrichir)} nouveaux passages via FlightRadar24...")
            fr24_flights = get_fr24_flights_in_area()

            nouveaux_vols = []
            for avion in vols_a_enrichir:
                icao24 = avion[0]
                callsign = str(avion[1]).strip() if avion[1] else "Inconnu"
                altitude = avion[13] or avion[7] or 0

                print(f"✈️ Nouveau passage : {callsign} ({int(altitude)}m). Enrichissement...")
                dep, arr, h_dep, h_arr = get_real_flight_info(icao24, fr24_flights)

                nouveaux_vols.append({
                    "Date": now.strftime("%d/%m/%Y"),
                    "Heure": now.strftime("%H:%M"),
                    "Avion": callsign,
                    "icao24": icao24,
                    "Altitude (m)": int(altitude),
                    "De": dep,
                    "A": arr,
                    "Dep_H": h_dep,
                    "Arr_H": h_arr,
                    "Source": "FR24" if dep != "Inconnu" else "OpenSky (Live)"
                })

            if nouveaux_vols:
                df_nouveaux = pd.DataFrame(nouveaux_vols)
                cols = ["Date", "Heure", "Avion", "icao24", "Altitude (m)", "De", "A", "Dep_H", "Arr_H", "Source"]
                df_final = pd.concat([df_existant, df_nouveaux], ignore_index=True)
                # Sécurité supplémentaire contre les doublons exacts
                df_final = df_final.drop_duplicates(subset=['Date', 'Heure', 'Avion'], keep='last')
                
                conn.update(worksheet="Vols_Joinville", data=df_final.fillna("").tail(2000))
                print(f"Succès : {len(nouveaux_vols)} nouveaux passages enregistrés.")
            else:
                print("Aucun avion ne correspond aux critères (altitude < 3500m).")
        else:
            print(f"Erreur OpenSky : {response.status_code}")
    except Exception as e:
        print(f"Erreur : {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()