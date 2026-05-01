import requests
import pandas as pd
import os
from datetime import datetime
import time

DB_FILE = "aircraftDatabase.csv"

def refresh_aircraft_db():
    """Télécharge la base si elle n'existe pas ou a plus de 30 jours."""
    if not os.path.exists(DB_FILE) or (time.time() - os.path.getmtime(DB_FILE) > 30 * 86400):
        print("Mise à jour de la base OpenSky locale...")
        url = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"
        try:
            r = requests.get(url, timeout=30)
            with open(DB_FILE, "wb") as f:
                f.write(r.content)
            print("Base mise à jour.")
        except Exception as e:
            print(f"Erreur téléchargement base : {e}")

def get_aircraft_info(icao24):
    """Récupère infos avion depuis la base OpenSky locale."""
    try:
        # On lit la base localement (optimisation : charger en mémoire au besoin)
        df = pd.read_csv(DB_FILE, low_memory=False)
        row = df[df['icao24'] == icao24.upper()]
        if not row.empty:
            r = row.iloc[0]
            return r.get('operator', "Inconnu"), r.get('model', "Inconnu"), r.get('registration', "Inconnu")
    except: pass
    return "Inconnu", "Inconnu", "Inconnu"
