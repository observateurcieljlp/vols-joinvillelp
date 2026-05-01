import requests
import pandas as pd
import streamlit as st
from streamlit_gsheets import GSheetsConnection
from datetime import datetime, timedelta
import time

# Configuration
WORKSHEET = "Vols_Joinville"

def get_opensky_flight_history(icao24, timestamp, user, pwd):
    """Interroge l'API historique d'OpenSky pour retrouver les aéroports."""
    # Fenêtre de recherche : +/- 4 heures autour du passage détecté
    begin = timestamp - (4 * 3600)
    end = timestamp + (4 * 3600)
    
    url = f"https://opensky-network.org/api/flights/aircraft?icao24={icao24}&begin={begin}&end={end}"
    try:
        response = requests.get(url, auth=(user, pwd), timeout=20)
        if response.status_code == 200:
            flights = response.json()
            if flights:
                # On prend le vol dont le passage est le plus proche du moment détecté
                # OpenSky renvoie une liste de vols avec estDepartureAirport et estArrivalAirport
                best_flight = min(flights, key=lambda f: abs(f.get('firstSeen', 0) - timestamp))
                
                dep = best_flight.get('estDepartureAirport') or "Inconnu"
                arr = best_flight.get('estArrivalAirport') or "Inconnu"
                
                # Conversion des codes OACI en IATA si possible (optionnel, on garde OACI pour la stabilité)
                return dep, arr
    except Exception as e:
        print(f"Erreur API OpenSky pour {icao24}: {e}")
    return None, None

def main():
    print("--- Démarrage du Nettoyeur J+1 (Enrichissement Historique) ---")
    
    try:
        user = st.secrets["OPENSKY_USER"].lower()
        pwd = st.secrets["OPENSKY_PWD"]
        conn = st.connection("gsheets", type=GSheetsConnection)
        
        # 1. Lecture de la base actuelle
        df = conn.read(worksheet=WORKSHEET, ttl=0)
        if df.empty:
            print("Base vide, rien à nettoyer.")
            return
            
        # 2. Identifier les lignes à compléter (où De ou A == "Inconnu")
        # On ne traite que les vols de plus de 12h pour être sûr que l'API historique est à jour
        mask_incomplet = (df['De'] == "Inconnu") | (df['A'] == "Inconnu")
        
        # Pour retrouver le timestamp, on combine Date et Heure
        # Format attendu : Date (01/05/2026), Heure (21:26)
        try:
            df['dt'] = pd.to_datetime(df['Date'] + ' ' + df['Heure'], format='%d/%m/%Y %H:%M')
            limit_date = datetime.now() - timedelta(hours=12)
            mask_eligible = mask_incomplet & (df['dt'] < limit_date)
        except Exception as e:
            print(f"Erreur conversion dates : {e}")
            return

        df_todo = df[mask_eligible].copy()
        
        if df_todo.empty:
            print("Aucun vol 'Inconnu' éligible pour le nettoyage (attente de 12h requise).")
            return

        print(f"Tentative d'enrichissement pour {len(df_todo)} vols...")
        
        # On a besoin de l'ICAO24 pour l'API historique. 
        # Si on ne l'a pas stocké, on va devoir modifier le collector pour le garder.
        # Vérifions si la colonne Avion contient ou peut nous donner l'ICAO24.
        # Idéalement, il faudrait que collector.py stocke l'ICAO24.
        
        # NOTE: Actuellement collector.py ne stocke PAS l'ICAO24 dans le CSV final.
        # Il faut modifier collector.py pour ajouter une colonne cachée ou visible 'icao24'
        if 'icao24' not in df.columns:
            print("ERREUR: La colonne 'icao24' est absente. Le nettoyage est impossible.")
            print("Je vais d'abord modifier collector.py pour inclure l'icao24.")
            return

        success_count = 0
        for idx, row in df_todo.iterrows():
            icao = row['icao24']
            ts = int(row['dt'].timestamp())
            
            print(f"🔍 Traitement de {row['Avion']} ({icao}) du {row['Date']} {row['Heure']}...")
            
            dep, arr = get_opensky_flight_history(icao, ts, user, pwd)
            
            if dep and dep != "Inconnu":
                df.at[idx, 'De'] = dep
                success_count += 1
            if arr and arr != "Inconnu":
                df.at[idx, 'A'] = arr
                success_count += 1
                
            time.sleep(1) # Respecter le rate limit OpenSky

        if success_count > 0:
            # Nettoyage avant sauvegarde
            df = df.drop(columns=['dt'])
            conn.update(worksheet=WORKSHEET, data=df)
            print(f"SUCCÈS : {success_count} informations d'aéroports complétées.")
        else:
            print("Aucune nouvelle information trouvée via l'API historique.")

    except Exception as e:
        print(f"Erreur critique : {e}")

if __name__ == "__main__":
    main()
