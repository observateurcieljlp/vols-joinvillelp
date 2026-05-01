import pandas as pd
import os
import time

DB_FILE = "aircraft_db.parquet"

def refresh_aircraft_db():
    """Vérifie si la base existe."""
    if not os.path.exists(DB_FILE):
        print("Erreur: Base aircraft_db.parquet introuvable.")

def get_aircraft_info(icao24):
    """Récupère infos avion depuis la base Parquet optimisée."""
    try:
        # On lit la base localement (chargement très rapide avec Parquet)
        df = pd.read_parquet(DB_FILE)
        # La colonne est 'icao24'
        row = df[df['icao24'] == icao24.lower()]
        if not row.empty:
            r = row.iloc[0]
            return r.get('operator', "Inconnu"), r.get('model', "Inconnu"), r.get('registration', "Inconnu")
    except Exception as e:
        print(f"Erreur lecture Parquet : {e}")
    return "Inconnu", "Inconnu", "Inconnu"
