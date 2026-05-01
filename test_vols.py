import requests
import streamlit as st
from collector import get_fr24_flights_in_area, get_real_flight_info
from cleaner_j1 import get_opensky_flight_history
from datetime import datetime

# L'avion que tu as vu : SAH59P
# 4AC94B est l'adresse unique ICAO24 (l'ID matériel) de cet avion précis.
TEST_ICAO = "4AC94B" 
TEST_CALLSIGN = "SAH59P"

# On simule le passage à l'heure où tu l'as vu (21:26 aujourd'hui)
now = datetime.now()
TEST_TS = int(datetime(now.year, now.month, now.day, 21, 26).timestamp())

def test():
    print(f"=== TEST ENRICHISSEMENT POUR {TEST_CALLSIGN} ({TEST_ICAO}) ===")
    
    # 1. TEST FLIGHTRADAR24 (Source Live)
    print("\n1. Test FlightRadar24 (Live API)...")
    fr24_flights = get_fr24_flights_in_area()
    dep, arr, h_dep, h_arr = get_real_flight_info(TEST_ICAO, fr24_flights)
    print(f"   Résultat FR24 : {dep} -> {arr} ({h_dep} -> {h_arr})")
    if dep == "Inconnu":
        print("   [Note] Normal : l'avion n'est plus en l'air, donc absent du flux Live de FR24.")

    # 2. TEST OPENSKY HISTORIQUE (Source J+1)
    print("\n2. Test OpenSky Historique...")
    try:
        user = st.secrets["OPENSKY_USER"]
        pwd = st.secrets["OPENSKY_PWD"]
        dep, arr = get_opensky_flight_history(TEST_ICAO, TEST_TS, user, pwd)
        print(f"   Résultat OpenSky : {dep} -> {arr}")
        if dep and dep != "Inconnu":
            print("   ✅ L'API historique d'OpenSky a bien trouvé le vol !")
        else:
            print("   ❌ Pas encore de données historiques. OpenSky met parfois 24h à indexer.")
    except Exception as e:
        print(f"   ❌ Erreur d'accès aux secrets ou API : {e}")

if __name__ == "__main__":
    test()
