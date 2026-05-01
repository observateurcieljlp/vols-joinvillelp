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

# Chargement base locale OpenSky (statique)
try:
    AIRCRAFT_DB = pd.read_csv("https://opensky-network.org/datasets/metadata/aircraftDatabase.csv", low_memory=False)
    # On indexe sur icao24 pour recherche rapide
    AIRCRAFT_DB = AIRCRAFT_DB.set_index('icao24')
except:
    AIRCRAFT_DB = pd.DataFrame()

# BBOX Joinville resserrée
BBOX = {"lamin": 48.75, "lamax": 48.90, "lomin": 2.35, "lomax": 2.60}
ALTITUDE_MAX = 3500 

def get_aircraft_info(icao24):
    """Récupère infos avion depuis la base OpenSky statique"""
    icao_hex = icao24.upper()
    if icao_hex in AIRCRAFT_DB.index:
        row = AIRCRAFT_DB.loc[icao_hex]
        return row.get('manufacturerName', "Inconnu"), row.get('model', "Inconnu"), row.get('registration', "Inconnu")
    return "Inconnu", "Inconnu", "Inconnu"

def get_fr24_flights_in_area():
    """Récupère tous les vols FR24 dans la zone pour croisement"""
    try:
        bounds = f"{BBOX['lamax']},{BBOX['lamin']},{BBOX['lomin']},{BBOX['lomax']}"
        return fr_api.get_flights(bounds=bounds)
    except:
        return []

def get_real_flight_info(icao24, fr24_flights):
    """Enrichissement hybride : FR24 + Base OpenSky"""
    dep, arr, h_dep, h_arr = "Inconnu", "Inconnu", "--:--", "--:--"
    
    # 1. Infos FR24 (Live)
    try:
        flight = next((f for f in fr24_flights if f.icao_24bit and f.icao_24bit.lower() == icao24.lower()), None)
        if flight:
            details = fr_api.get_flight_details(flight)
            flight.set_flight_details(details)
            dep = flight.origin_airport_iata if flight.origin_airport_iata != "N/A" else "Inconnu"
            arr = flight.destination_airport_iata if flight.destination_airport_iata != "N/A" else "Inconnu"
            if details and 'time' in details:
                time_info = details.get('time', {})
                if time_info.get('real', {}).get('departure'): h_dep = datetime.fromtimestamp(time_info['real']['departure']).strftime('%H:%M')
                if time_info.get('estimated', {}).get('arrival'): h_arr = datetime.fromtimestamp(time_info['estimated']['arrival']).strftime('%H:%M')
    except: pass

    # 2. Infos Avion via Base OpenSky (Statique, aucun risque de blocage)
    make, model, reg = get_aircraft_info(icao24)
    
    return dep, arr, h_dep, h_arr, make, model, reg

def main():
    print("--- Scan Joinville (Hybride OpenSky/FR24) ---")
    try:
        USER = st.secrets["OPENSKY_USER"].lower()
        PWD = st.secrets["OPENSKY_PWD"]
        
        url = f"https://opensky-network.org/api/states/all?lamin={BBOX['lamin']}&lomin={BBOX['lomin']}&lamax={BBOX['lamax']}&lomax={BBOX['lomax']}"
        response = requests.get(url, auth=(USER, PWD), timeout=15)
        
        if response.status_code == 200:
            states = response.json().get('states') or []
            vols_potitiels = [s for s in states if not s[8] and (s[13] or s[7] or 0) < ALTITUDE_MAX]

            if not vols_potitiels: return

            # Lecture du cache GSheets + Migration schéma
            conn = st.connection("gsheets", type=GSheetsConnection)
            cols = ["Date", "Heure", "Identifiant Vol (Callsign)", "Compagnie", "Modèle Avion", "Immatriculation", "Identifiant Appareil (ICAO24)", "Altitude (m)", "De", "A", "Dep_H", "Arr_H", "Source"]
            try:
                df_existant = conn.read(worksheet="Vols_Joinville", ttl=0)
                # Migration auto des anciennes colonnes
                rename_map = {"Avion": "Identifiant Vol (Callsign)", "icao24": "Identifiant Appareil (ICAO24)", "Altitude": "Altitude (m)"}
                df_existant = df_existant.rename(columns=rename_map)
                for c in cols:
                    if c not in df_existant.columns: df_existant[c] = "Inconnu"
                df_existant = df_existant[cols]
            except:
                df_existant = pd.DataFrame(columns=cols)

            # Filtrage anti-doublon (15 min)
            vols_a_enrichir = []
            now = datetime.now()
            for avion in vols_potitiels:
                icao24 = avion[0]
                deja_vu = False
                if not df_existant.empty:
                    today_str = now.strftime("%d/%m/%Y")
                    match = df_existant[(df_existant['Identifiant Appareil (ICAO24)'] == icao24) & (df_existant['Date'] == today_str)]
                    for _, row in match.iterrows():
                        try:
                            if abs((datetime.strptime(row['Heure'], "%H:%M") - datetime.strptime(now.strftime("%H:%M"), "%H:%M")).total_seconds() / 60) < 15:
                                deja_vu = True; break
                        except: pass
                if not deja_vu: vols_a_enrichir.append(avion)

            if not vols_a_enrichir: return

            fr24_flights = get_fr24_flights_in_area()
            nouveaux_vols = []
            for avion in vols_a_enrichir:
                icao24 = avion[0]
                callsign = str(avion[1]).strip() or "Inconnu"
                altitude = avion[13] or avion[7] or 0
                dep, arr, h_dep, h_arr, make, model, reg = get_real_flight_info(icao24, fr24_flights)

                nouveaux_vols.append({
                    "Date": now.strftime("%d/%m/%Y"),
                    "Heure": now.strftime("%H:%M"),
                    "Identifiant Vol (Callsign)": callsign,
                    "Compagnie": make,
                    "Modèle Avion": model,
                    "Immatriculation": reg,
                    "Identifiant Appareil (ICAO24)": icao24,
                    "Altitude (m)": int(altitude),
                    "De": dep,
                    "A": arr,
                    "Dep_H": h_dep,
                    "Arr_H": h_arr,
                    "Source": "FR24" if dep != "Inconnu" else "OpenSky (Live)"
                })

            if nouveaux_vols:
                df_nouveaux = pd.DataFrame(nouveaux_vols)
                df_final = pd.concat([df_existant, df_nouveaux], ignore_index=True)
                df_final = df_final.drop_duplicates(subset=['Date', 'Heure', 'Identifiant Vol (Callsign)'], keep='last')
                conn.update(worksheet="Vols_Joinville", data=df_final.fillna("").tail(2000))
