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

import json

def get_aircraft_info(icao24):
    """Récupère infos avion et le JSON brut depuis la base Parquet locale."""
    try:
        if not os.path.exists(DB_FILE):
            return "Inconnu", "Inconnu", "Inconnu", ""
            
        # On lit la base localement
        df = pd.read_parquet(DB_FILE)
        row = df[df['icao24'] == icao24.lower()]
        if not row.empty:
            r = row.iloc[0]
            # On convertit toute la ligne en dictionnaire pour le raw log
            raw_data = r.to_dict()
            # On gère les types non-JSON (comme les NaN)
            raw_json = json.dumps({k: (v if not pd.isna(v) else None) for k, v in raw_data.items()}, ensure_ascii=False)
            
            return r.get('operator', "Inconnu"), r.get('model', "Inconnu"), r.get('registration', "Inconnu"), raw_json
    except Exception as e:
        print(f"Erreur lecture Parquet : {e}")
    return "Inconnu", "Inconnu", "Inconnu", ""
