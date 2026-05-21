import requests
import sqlite3
import os
import time
import csv
import json
import io

DB_FILE = "aircraft_db.sqlite"

def refresh_aircraft_db():
    """Télécharge et convertit la base OpenSky en SQLite si absente ou âgée de > 30 jours."""
    if os.path.exists(DB_FILE) and (time.time() - os.path.getmtime(DB_FILE) < 30 * 86400):
        return

    print("Mise à jour de la base OpenSky locale (SQLite)...")
    url = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"
    
    try:
        # On utilise une connexion SQLite avec un timeout de 30s pour éviter les "database is locked"
        conn = sqlite3.connect(DB_FILE, timeout=30)
        cursor = conn.cursor()
        
        # Création de la table
        cursor.execute("DROP TABLE IF EXISTS aircraft")
        cursor.execute("""
            CREATE TABLE aircraft (
                icao24 TEXT PRIMARY KEY,
                registration TEXT,
                manufacturericao TEXT,
                manufacturername TEXT,
                model TEXT,
                typecode TEXT,
                serialnumber TEXT,
                linenumber TEXT,
                icaoaircrafttype TEXT,
                operator TEXT,
                operatorcallsign TEXT,
                operatoricao TEXT,
                operatoriata TEXT,
                owner TEXT,
                testreg TEXT,
                registered TEXT,
                reguntil TEXT,
                status TEXT,
                built TEXT,
                firstflightdate TEXT,
                seatconfigexp TEXT,
                seatconfigtxt TEXT,
                notes TEXT
            )
        """)
        
        # Téléchargement en streaming pour économiser la RAM
        response = requests.get(url, stream=True, timeout=30)
        if response.status_code == 200:
            # On utilise iter_lines pour éviter les problèmes de flux fermé
            lines = response.iter_lines(decode_unicode=True)
            header = next(lines) # Sauter le header
            csv_reader = csv.reader(lines)
            
            # Préparation de l'insertion massive (bulk insert)
            buffer = []
            count = 0
            for row in csv_reader:
                if len(row) >= 23:
                    # On ne garde que les 23 premières colonnes pour correspondre à notre schéma
                    buffer.append(tuple(row[:23]))
                    count += 1
                
                if len(buffer) >= 1000:
                    cursor.executemany("INSERT OR REPLACE INTO aircraft VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", buffer)
                    buffer = []
                    if count % 100000 == 0:
                        print(f"  ... {count} avions importés")
            
            if buffer:
                cursor.executemany("INSERT OR REPLACE INTO aircraft VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", buffer)
            
            # Indexation pour des recherches instantanées
            cursor.execute("CREATE INDEX idx_icao ON aircraft(icao24)")
            conn.commit()
            print(f"Base SQLite mise à jour avec succès ({count} avions).")
        
        conn.close()
    except Exception as e:
        print(f"Erreur mise à jour base SQLite : {e}")

def get_aircraft_info(icao24):
    """Récupère infos avion et le JSON brut depuis la base SQLite."""
    try:
        if not os.path.exists(DB_FILE):
            return "Inconnu", "Inconnu", "Inconnu", ""
            
        # Timeout de 30s ici aussi pour la lecture
        conn = sqlite3.connect(DB_FILE, timeout=30)
        conn.row_factory = sqlite3.Row # Pour accéder aux colonnes par nom
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM aircraft WHERE icao24 = ?", (icao24.lower(),))
        row = cursor.fetchone()
        conn.close()
        
        if row:
            # Conversion en dict pour le JSON brut
            raw_data = dict(row)
            raw_json = json.dumps(raw_data, ensure_ascii=False)
            
            return row['operator'] or "Inconnu", row['model'] or "Inconnu", row['registration'] or "Inconnu", raw_json
    except Exception as e:
        print(f"Erreur lecture SQLite : {e}")
    
    return "Inconnu", "Inconnu", "Inconnu", ""
