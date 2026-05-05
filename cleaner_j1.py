import os
import sys
import warnings
import logging
import re
import json
import time
import requests
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
from curl_cffi import requests as cf_requests
import pytz

# ---------------------------------------------------------------------------
# Configuration & SECRETS
# ---------------------------------------------------------------------------

def load_config():
    """Charge les secrets depuis secrets.toml, config.json ou variables d'environnement"""
    config = {}
    secrets_path = os.path.join(".streamlit", "secrets.toml")
    if os.path.exists(secrets_path):
        try:
            with open(secrets_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        key, val = line.split("=", 1)
                        config[key.strip()] = val.strip().strip('"').strip("'")
        except: pass

    if os.path.exists("config.json"):
        try:
            with open("config.json", "r") as f:
                config.update(json.load(f))
        except: pass
    
    for key in ["AIRLABS_API_KEY", "OPENSKY_USER", "OPENSKY_PWD", "OPENSKY_CLIENT_ID", "OPENSKY_CLIENT_SECRET", "GOOGLE_SHEET_NAME", "spreadsheet"]:
        if key not in config:
            config[key] = os.environ.get(key, "")
    return config

CONFIG = load_config()

def get_gsheet_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if os.path.exists("service_account.json"):
        creds = Credentials.from_service_account_file("service_account.json", scopes=scope)
        return gspread.authorize(creds)
    return None

def get_worksheet():
    client = get_gsheet_client()
    if not client: return None
    try:
        sheet_url = CONFIG.get("spreadsheet")
        sh = client.open_by_url(sheet_url) if sheet_url else client.open(CONFIG.get("GOOGLE_SHEET_NAME", "Radar_Joinville"))
        return sh.worksheet("Vols_Joinville")
    except: return None

COLS = [
    "Date", "Heure", "Identifiant Vol (Callsign)", "Compagnie", "Modèle Avion", 
    "Immatriculation", "Identifiant Appareil (ICAO24)", "Altitude (m)", 
    "Evolution Verticale", "Lat", "Lon", "Heading", "De", "A", "Dep_H", "Arr_H", 
    "Source", "Planespotters", "Positions", "Nettoyage Retries",
    "Airlabs Info", "OpenSky State Info", "Hexdb Route Info", 
    "Hexdb Aircraft Info", "Planespotters Info", "Aircraft DB Info"
]

MAX_RETRIES = 2
MATCH_MARGIN_MINUTES = 60

# ---------------------------------------------------------------------------
# API Lookups (FlightAware / OpenSky / Airports)
# ---------------------------------------------------------------------------
_fa_session = None
def _get_fa_session():
    global _fa_session
    if _fa_session is None: _fa_session = cf_requests.Session(impersonate="chrome124")
    return _fa_session

def get_flightaware_web_data(callsign, target_ts):
    url = f"https://www.flightaware.com/live/flight/{callsign.strip().upper()}"
    try:
        r = _get_fa_session().get(url, timeout=20)
        if r.status_code != 200: return None, None, None, None, None
        match = re.search(r"trackpollBootstrap\s*=\s*({.*?});", r.text, re.DOTALL)
        if not match: return None, None, None, None, None
        data = json.loads(match.group(1))
        flights = []
        for fk in data.get("flights", {}):
            for f in data["flights"][fk].get("activityLog", {}).get("flights", []):
                o, d = f.get("origin", {}), f.get("destination", {})
                dp = o.get("icao") or o.get("iata") or o.get("friendlyName")
                ar = d.get("icao") or d.get("iata") or d.get("friendlyName")
                ts = f.get("takeoffTimes", {}).get("actual") or f.get("takeoffTimes", {}).get("estimated")
                if ts: flights.append({"dp": dp, "ar": ar, "ts": ts, "ac": f.get("aircraftTypeFriendly", "")})
        
        if not flights: return None, None, None, None, None
        best = min(flights, key=lambda x: abs(x["ts"] - target_ts))
        if abs(best["ts"] - target_ts) > 7200: return None, None, None, None, None
        h_dep = datetime.fromtimestamp(best["ts"], tz=pytz.timezone("Europe/Paris")).strftime("%H:%M")
        return best["dp"], best["ar"], h_dep, "--:--", {"aircraft": best["ac"]}
    except: return None, None, None, None, None

def get_opensky_flight_history(icao24, timestamp, user, pwd):
    url = f"https://opensky-network.org/api/flights/aircraft?icao24={icao24}&begin={int(timestamp-14400)}&end={int(timestamp+14400)}"
    try:
        r = requests.get(url, auth=(user, pwd), timeout=20)
        if r.status_code == 200 and r.json():
            best = min(r.json(), key=lambda f: abs((f.get('firstSeen') or 0) - timestamp))
            return best.get('estDepartureAirport'), best.get('estArrivalAirport'), datetime.fromtimestamp(best['firstSeen']).strftime('%H:%M'), "--:--"
    except: pass
    return None, None, None, None

_airport_cache = {}
def resolve_airport(code):
    code = str(code).strip().upper()
    if not code or code in ("INCONNU", "?", "", "NONE", "NAN"): return "Inconnu"
    if code in _airport_cache: return _airport_cache[code]
    try:
        r = requests.get(f"https://hexdb.io/api/v1/airport/{'icao' if len(code)==4 else 'iata'}/{code}", timeout=5)
        if r.status_code == 200:
            nom = r.json().get("airport", "").strip()
            for s in (" Airport", " International Airport", " Intl", " International"): nom = nom.replace(s, "")
            _airport_cache[code] = f"{nom} ({code})" if nom else code
            return _airport_cache[code]
    except: pass
    return code

# ---------------------------------------------------------------------------
# MAIN (CHIRURGICAL)
# ---------------------------------------------------------------------------

def main():
    print(f"\n{'='*60}\n🧹 DÉMARRAGE DU NETTOYEUR CHIRURGICAL : {datetime.now().strftime('%H:%M:%S')}\n{'='*60}")
    
    try:
        user, pwd = CONFIG.get("OPENSKY_USER", "").lower(), CONFIG.get("OPENSKY_PWD", "")
        ws = get_worksheet()
        if not ws: return

        # 1. Lecture de TOUTES les lignes
        data = ws.get_all_values()
        if len(data) <= 1: 
            print("   Base vide.")
            return

        header = data[0]
        rows = data[1:]
        
        col_map = {name: i for i, name in enumerate(header)}
        for c in COLS:
            if c not in col_map: 
                print(f"❌ Colonne manquante : {c}")
                return

        # 2. Identification des lignes à traiter
        tz_paris = pytz.timezone("Europe/Paris")
        now_ts = datetime.now().timestamp()
        
        success_count = 0
        updates = [] 

        for i, row_vals in enumerate(rows):
            row_num = i + 2
            
            def get_val(name):
                idx = col_map.get(name)
                return row_vals[idx] if idx < len(row_vals) else ""

            callsign = get_val("Identifiant Vol (Callsign)")
            date_str = get_val("Date")
            heure_str = get_val("Heure")
            icao = get_val("Identifiant Appareil (ICAO24)")
            
            try:
                dt = datetime.strptime(f"{date_str} {heure_str}", "%d/%m/%Y %H:%M")
                ts = tz_paris.localize(dt).timestamp()
            except: continue

            # Éligibilité
            if ts > (now_ts - 600): continue 
            
            source = get_val("Source")
            val_de = get_val("De").lower()
            val_a = get_val("A").lower()
            dep_h = get_val("Dep_H")
            arr_h = get_val("Arr_H")
            vides = ["", "inconnu", "nan", "none", "?", "--:--"]
            
            is_unreliable = source in ["", "hexdb", "OpenSky (Live)"]
            is_missing_airport = val_de in vides or val_a in vides
            is_missing_times = dep_h in vides or arr_h in vides
            
            # ELIGIBILITÉ : On nettoie si :
            # 1. La source est peu fiable (on veut monter en gamme vers FlightAware)
            # 2. OU il manque l'aéroport (De/A)
            # 3. OU il manque les heures (Dep_H/Arr_H) même si la source est déjà FlightAware
            should_clean = is_unreliable or is_missing_airport or is_missing_times

            try: retries = int(get_val("Nettoyage Retries") or 0)
            except: retries = 0

            # Condition spécifique "Départ Récent"
            if arr_h in vides and dep_h not in vides:
                try:
                    dt_dep = datetime.strptime(f"{date_str} {dep_h}", "%d/%m/%Y %H:%M")
                    ts_dep = tz_paris.localize(dt_dep).timestamp()
                    if (ts - ts_dep) < 2400: # Capturé < 40 min après départ
                        if now_ts < (ts + 21600): # Attendre 6h après capture
                            continue 
                except: pass

            if should_clean and retries < MAX_RETRIES:
                # 3. Traitement
                print(f"    -> Ligne {row_num}: {callsign} ({date_str})")
                dep, arr, h_dep, h_arr, f_info = get_flightaware_web_data(callsign, ts)
                
                if (not dep or dep == "Inconnu") and icao and user:
                    dep, arr, h_dep, h_arr = get_opensky_flight_history(icao, ts, user, pwd)

                if dep and dep != "Inconnu":
                    new_row = list(row_vals)
                    while len(new_row) < len(COLS): new_row.append("")
                    
                    def set_val(name, val):
                        if name in col_map: new_row[col_map[name]] = val

                    set_val("De", resolve_airport(dep))
                    set_val("A", resolve_airport(arr) if arr else "Inconnu")
                    set_val("Dep_H", h_dep or "")
                    set_val("Arr_H", h_arr or "")
                    set_val("Source", "FlightAware (Web)" if f_info else "OpenSky (History)")
                    if f_info and f_info.get("aircraft"):
                        old_model = get_val("Modèle Avion")
                        if not old_model or old_model in vides: set_val("Modèle Avion", f_info["aircraft"])
                    
                    # Adaptation Locale & Sécurité Formatage
                    for idx, val in enumerate(new_row):
                        col_name = header[idx]
                        if val is None or (isinstance(val, float) and (val != val or val == float('inf') or val == float('-inf'))):
                            new_row[idx] = ""
                        elif col_name in ["Lat", "Lon"] and isinstance(val, (float, int)):
                            new_row[idx] = f"'{float(val):.4f}"
                        elif isinstance(val, float):
                            new_row[idx] = str(val).replace(".", ",")

                    updates.append({'range': f'A{row_num}', 'values': [new_row]})
                    success_count += 1
                    print(f"        ✅ OK : {dep} -> {arr}")
                else:
                    col_letter = ""
                    # Trouver la lettre de colonne pour Nettoyage Retries
                    idx_retries = col_map["Nettoyage Retries"]
                    if idx_retries < 26: col_letter = chr(65 + idx_retries)
                    else: col_letter = "T" # Securité hardcoded pour la colonne T si décalage
                    
                    updates.append({'range': f'{col_letter}{row_num}', 'values': [[retries + 1]]})
                    print(f"        ❌ Échec (Retry {retries + 1})")
                
                time.sleep(2) 

        # 4. Envoi des mises à jour PAR LOTS
        if updates:
            print(f"\n💾 Envoi de {len(updates)} mises à jour chirurgicales...")
            ws.batch_update(updates, value_input_option='USER_ENTERED')
            print(f"✅ Terminé ({success_count} enrichissements réussis).")
        else:
            print("\n✅ Rien à mettre à jour.")

    except Exception as e:
        print(f"❌ ERREUR CRITIQUE : {e}")
        import traceback; traceback.print_exc()

if __name__ == "__main__":
    main()
