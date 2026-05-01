import requests
import pandas as pd
import os
import time

DB_FILE = "aircraft_db.parquet"

def refresh_aircraft_db():
    """Télécharge et convertit la base si absente ou âgée de > 30 jours."""
    if os.path.exists(DB_FILE) and (time.time() - os.path.getmtime(DB_FILE) < 30 * 86400):
        return

    print("Mise à jour de la base OpenSky locale (conversion Parquet)...")
    url = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"
    try:
        # Téléchargement et conversion directe en mémoire pour éviter le fichier temporaire
        df = pd.read_csv(url, low_memory=False)
        df.to_parquet(DB_FILE, compression='snappy')
        print("Base mise à jour avec succès.")
    except Exception as e:
        print(f"Erreur mise à jour base : {e}")

def get_aircraft_info(icao24):
    """Récupère infos avion depuis la base Parquet optimisée."""
    try:
        if not os.path.exists(DB_FILE):
            return "Inconnu", "Inconnu", "Inconnu"
            
        # On lit la base localement
        df = pd.read_parquet(DB_FILE)
        row = df[df['icao24'] == icao24.lower()]
        if not row.empty:
            r = row.iloc[0]
            return r.get('operator', "Inconnu"), r.get('model', "Inconnu"), r.get('registration', "Inconnu")
    except Exception as e:
        print(f"Erreur lecture Parquet : {e}")
    return "Inconnu", "Inconnu", "Inconnu"
