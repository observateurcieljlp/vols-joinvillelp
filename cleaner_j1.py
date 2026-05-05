"""
cleaner_j1.py
============
Nettoyeur intelligent pour enrichir les données de vol a posteriori.
Gère les retries pour éviter les bans et utilise une cascade de sources.
"""

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
    
    # 1. Tentative lecture secrets.toml (Streamlit format)
    secrets_path = os.path.join(".streamlit", "secrets.toml")
    if os.path.exists(secrets_path):
        try:
            with open(secrets_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line:
                        key, val = line.split("=", 1)
                        config[key.strip()] = val.strip().strip('"').strip("'")
        except Exception as e:
            print(f"⚠️ Erreur lecture secrets.toml : {e}")

    # 2. Tentative lecture config.json (Overide)
    if os.path.exists("config.json"):
        try:
            with open("config.json", "r") as f:
                config.update(json.load(f))
        except Exception as e:
            print(f"⚠️ Erreur lecture config.json : {e}")
    
    # 3. Fallback sur les variables d'environnement
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
    else:
        print("❌ Fichier service_account.json introuvable !")
        return None

def get_worksheet():
    client = get_gsheet_client()
    if not client: return None
    try:
        # Priorité à l'URL (plus fiable)
        sheet_url = CONFIG.get("spreadsheet")
        if sheet_url:
            sh = client.open_by_url(sheet_url)
        else:
            sheet_name = CONFIG.get("GOOGLE_SHEET_NAME", "Radar_Joinville")
            sh = client.open(sheet_name)
        return sh.worksheet("Vols_Joinville")
    except Exception as e:
        print(f"❌ Erreur accès Google Sheet : {e}")
        return None

WORKSHEET_NAME = "Vols_Joinville"

# SCHÉMA GLOBAL UNIQUE (Synchro entre tous les scripts)
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
# GESTION SESSION FLIGHTAWARE (Impersonation Chrome)
# ---------------------------------------------------------------------------

_fa_session: cf_requests.Session | None = None

def _get_fa_session() -> cf_requests.Session:
    global _fa_session
    if _fa_session is None:
        _fa_session = cf_requests.Session(impersonate="chrome124")
    return _fa_session

# ---------------------------------------------------------------------------
# PARSER JSON BOOTSTRAP (Robuste)
# ---------------------------------------------------------------------------

def _parse_trackpoll_bootstrap(html: str, callsign: str) -> list[dict]:
    flights = []
    try:
        match = re.search(r"trackpollBootstrap\s*=\s*({.*?});", html, re.DOTALL)
        if not match: return flights
        
        data = json.loads(match.group(1))
        flights_root = data.get("flights", {})
        
        for flight_key in flights_root:
            raw_flights = flights_root[flight_key].get("activityLog", {}).get("flights", [])
            for f in raw_flights:
                orig = f.get("origin", {})
                dest = f.get("destination", {})

                if orig.get("isValidAirportCode"):
                    dep_code = orig.get("icao") or orig.get("iata")
                else:
                    dep_code = orig.get("friendlyName") or orig.get("icao")

                if dest.get("isValidAirportCode"):
                    arr_code = dest.get("icao") or dest.get("iata")
                else:
                    arr_code = dest.get("friendlyName") or dest.get("icao")
                
                takeoff_ts = (f.get("takeoffTimes", {}).get("actual") or 
                              f.get("takeoffTimes", {}).get("estimated") or 
                              f.get("takeoffTimes", {}).get("scheduled"))
                landing_ts = (f.get("landingTimes", {}).get("actual") or 
                              f.get("landingTimes", {}).get("estimated") or 
                              f.get("landingTimes", {}).get("scheduled"))
                
                dep_utc = datetime.fromtimestamp(takeoff_ts, tz=pytz.utc) if takeoff_ts else None
                arr_utc = datetime.fromtimestamp(landing_ts, tz=pytz.utc) if landing_ts else None
                
                flights.append({
                    "dep_code": dep_code,
                    "arr_code": arr_code,
                    "dep_utc": dep_utc,
                    "arr_utc": arr_utc,
                    "aircraft": f.get("aircraftTypeFriendly") or f.get("aircraft", {}).get("friendlyType") or "",
                    "callsign": callsign
                })
    except Exception as e:
        print(f"        [Parser] Erreur extraction JSON : {e}")
    return flights

def get_flightaware_web_data(
    callsign: str, target_ts: int | float, margin_minutes: int = MATCH_MARGIN_MINUTES
) -> tuple[str | None, str | None, str | None, str | None, dict | None]:
    
    url = f"https://www.flightaware.com/live/flight/{callsign.strip().upper()}"
    session = _get_fa_session()
    tz_paris = pytz.timezone("Europe/Paris")

    try:
        r = session.get(url, timeout=20)
        if r.status_code != 200: return None, None, None, None, None
        html = r.text
    except Exception:
        return None, None, None, None, None

    flights = _parse_trackpoll_bootstrap(html, callsign)
    if not flights: return None, None, None, None, None

    target_dt = datetime.fromtimestamp(target_ts, tz=pytz.utc)
    margin = timedelta(minutes=margin_minutes)
    
    best_match = None
    best_score = float('inf')

    for f in flights:
        dep_utc = f["dep_utc"]
        arr_utc = f["arr_utc"]
        if not dep_utc: continue

        end_boundary = arr_utc if arr_utc else (dep_utc + timedelta(hours=12))
        in_flight = (dep_utc <= target_dt <= end_boundary)
        
        if dep_utc and arr_utc:
            center = dep_utc + (arr_utc - dep_utc) / 2
        else:
            center = dep_utc

        diff_seconds = abs((target_dt - center).total_seconds())
        score = diff_seconds / 1000 if in_flight else diff_seconds

        if target_dt >= (dep_utc - margin) and target_dt <= (end_boundary + margin):
            if score < best_score:
                best_score = score
                best_match = f

    if best_match:
        dt_dep_paris = best_match["dep_utc"].astimezone(tz_paris)
        h_dep = dt_dep_paris.strftime("%H:%M")
        
        h_arr = "--:--"
        if best_match["arr_utc"]:
            dt_arr_paris = best_match["arr_utc"].astimezone(tz_paris)
            h_arr = dt_arr_paris.strftime("%H:%M")

        return best_match["dep_code"], best_match["arr_code"], h_dep, h_arr, best_match

    return None, None, None, None, None

# ---------------------------------------------------------------------------
# FALLBACK OPENSKY
# ---------------------------------------------------------------------------

def get_opensky_flight_history(icao24, timestamp, user, pwd):
    begin, end = int(timestamp - 14400), int(timestamp + 14400)
    url = f"https://opensky-network.org/api/flights/aircraft?icao24={icao24}&begin={begin}&end={end}"
    try:
        response = requests.get(url, auth=(user, pwd), timeout=20)
        if response.status_code == 200:
            flights = response.json()
            if flights:
                best = min(flights, key=lambda f: abs((f.get('firstSeen') or 0) - timestamp))
                dep, arr = best.get('estDepartureAirport') or "Inconnu", best.get('estArrivalAirport') or "Inconnu"
                h_dep = datetime.fromtimestamp(best.get('firstSeen')).strftime('%H:%M') if best.get('firstSeen') else "--:--"
                h_arr = datetime.fromtimestamp(best.get('lastSeen')).strftime('%H:%M') if best.get('lastSeen') else "--:--"
                return dep, arr, h_dep, h_arr
    except: pass
    return None, None, None, None

# ---------------------------------------------------------------------------
# UTILITAIRES AEROPORTS
# ---------------------------------------------------------------------------

_airport_cache: dict[str, str] = {}

def resolve_airport(code: str) -> str:
    code = str(code).strip().upper()
    if not code or code in ("INCONNU", "?", "", "NONE", "NAN"): return "Inconnu"
    if code in _airport_cache: return _airport_cache[code]
    try:
        endpoint = f"https://hexdb.io/api/v1/airport/{'icao' if len(code)==4 else 'iata'}/{code}"
        r = requests.get(endpoint, timeout=5)
        if r.status_code == 200:
            nom = r.json().get("airport", "").strip()
            for s in (" Airport", " International Airport", " Intl", " International"): nom = nom.replace(s, "")
            res = f"{nom} ({code})" if nom else code
            _airport_cache[code] = res
            return res
    except: pass
    _airport_cache[code] = code
    return code

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    print(f"\n{'='*60}\n🧹 DÉMARRAGE DU NETTOYEUR J+1 : {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n{'='*60}")
    
    try:
        user, pwd = CONFIG.get("OPENSKY_USER", "").lower(), CONFIG.get("OPENSKY_PWD", "")
        ws = get_worksheet()
        if not ws: return
        
        # 1. Lecture native via gspread
        raw_rows = ws.get_all_records()
        if not raw_rows:
            print("   Base vide.")
            return

        # S'assurer de la présence des colonnes
        for row in raw_rows:
            for c in COLS:
                if c not in row: row[c] = ""
        
        # 2. Logique d'enrichissement
        success_count = 0
        modified_count = 0
        tz_paris = pytz.timezone("Europe/Paris")

        for row in raw_rows:
            callsign = str(row.get("Identifiant Vol (Callsign)") or "Unknown").strip()
            icao     = str(row.get("Identifiant Appareil (ICAO24)") or "").strip()
            date_str = str(row.get("Date"))
            heure_str = str(row.get("Heure"))
            
            # A. Calcul du timestamp
            ts_matching = None
            try:
                dt = datetime.strptime(f"{date_str} {heure_str}", "%d/%m/%Y %H:%M")
                ts_matching = tz_paris.localize(dt).timestamp()
            except: continue

            # B. Check éligibilité
            try:
                retries = int(row.get("Nettoyage Retries") or 0)
            except: retries = 0
            
            if retries >= MAX_RETRIES: continue

            vides = ["", "inconnu", "nan", "none", "none -> none", "?", "inconnue", "--:--"]
            val_de = str(row.get("De") or "").strip().lower()
            val_a = str(row.get("A") or "").strip().lower()
            val_dep_h = str(row.get("Dep_H") or "").strip().lower()
            val_arr_h = str(row.get("Arr_H") or "").strip().lower()
            
            missing_data = (val_de in vides or val_a in vides or val_dep_h in vides or val_arr_h in vides)
            source = str(row.get("Source") or "").strip()
            SOURCES_NON_FIABLES = ["hexdb", "OpenSky (Live)", ""]
            unreliable_source = source in SOURCES_NON_FIABLES

            if not (missing_data or unreliable_source): continue
            
            # Délai minimal (10 min)
            if ts_matching > (datetime.now().timestamp() - 600): continue

            # C. Enrichissement
            print(f"\n    -> {callsign} | {date_str} {heure_str}")
            dep, arr, h_dep, h_arr, f_info = get_flightaware_web_data(callsign, ts_matching)

            if (not dep or dep == "Inconnu") and icao and icao != "nan" and user:
                print("        [Fallback] Tentative OpenSky...")
                dep, arr, h_dep, h_arr = get_opensky_flight_history(icao, ts_matching, user, pwd)

            if dep and dep != "Inconnu":
                row["De"]     = resolve_airport(dep)
                row["A"]      = resolve_airport(arr) if arr else "Inconnu"
                row["Dep_H"]  = h_dep if h_dep else ""
                row["Arr_H"]  = h_arr if h_arr else ""
                row["Source"] = "FlightAware (Web)" if f_info else "OpenSky (History)"
                if f_info and f_info.get("aircraft"):
                    if not row.get("Modèle Avion") or row.get("Modèle Avion") in ("", "nan"):
                        row["Modèle Avion"] = f_info["aircraft"]
                
                print(f"        ✅ OK : {dep} -> {arr}")
                success_count += 1
            else:
                row["Nettoyage Retries"] = retries + 1
                print(f"        ❌ Échec (Tentative {retries + 1}/{MAX_RETRIES})")
            
            modified_count += 1
            time.sleep(2)

        # 3. Sauvegarde
        if modified_count > 0:
            try:
                # Tri final par Date/Heure
                def sort_key(r):
                    try: return datetime.strptime(f"{r['Date']} {r['Heure']}", "%d/%m/%Y %H:%M")
                    except: return datetime.min
                raw_rows.sort(key=sort_key)

                clean_data = [COLS]
                for row in raw_rows:
                    row_vals = []
                    for c in COLS:
                        val = row.get(c, "")
                        # Sécurité JSON/GSheets
                        if val is None or (isinstance(val, (float, int)) and (val != val or val == float('inf') or val == float('-inf'))):
                            row_vals.append("")
                        else:
                            row_vals.append(val)
                    clean_data.append(row_vals)

                ws.clear()
                ws.update(values=clean_data, range_name='A1')
                print(f"\n💾 GSheets mis à jour via gspread ({success_count} succès).")
            except Exception as update_err:
                print(f"❌ Erreur lors de la mise à jour GSheets : {update_err}")

    except Exception as e:
        print(f"❌ ERREUR CRITIQUE : {e}")
        import traceback
        traceback.print_exc()

    print(f"\n{'='*60}\n✅ FIN\n{'='*60}")

if __name__ == "__main__":
    main()
