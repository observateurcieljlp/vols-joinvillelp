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
import pandas as pd
import streamlit as st
from streamlit_gsheets import GSheetsConnection
from datetime import datetime, timedelta
from curl_cffi import requests as cf_requests
import pytz

# Désactiver ABSOLUMENT TOUS les logs internes (Streamlit, GSheets, etc.)
logging.disable(logging.CRITICAL)
warnings.filterwarnings("ignore")
os.environ["STREAMLIT_LOG_LEVEL"] = "error"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WORKSHEET = "Vols_Joinville"

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
    begin, end = timestamp - 14400, timestamp + 14400
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
        user, pwd = st.secrets.get("OPENSKY_USER", "").lower(), st.secrets.get("OPENSKY_PWD", "")
        conn = st.connection("gsheets", type=GSheetsConnection)
        
        # 1. Lecture et mise en forme
        df = conn.read(worksheet="Vols_Joinville", ttl=0)
        if df.empty:
            print("   Base vide.")
            return

        # S'assurer de la présence des colonnes
        for c in COLS:
            if c not in df.columns: df[c] = ""
        df = df[COLS] # Force l'ordre et la présence pour éviter de dropper des colonnes
        
        # 2. Préparation du timestamp de matching (Paris -> UTC)
        def get_ts_paris(row):
            try:
                dt = datetime.strptime(f"{row['Date']} {row['Heure']}", "%d/%m/%Y %H:%M")
                return pytz.timezone("Europe/Paris").localize(dt).timestamp()
            except: return None

        df["ts_matching"] = df.apply(get_ts_paris, axis=1)

        # 3. Logique d'éligibilité avec Compteur de Retries
        def check_eligibility(row):
            callsign = str(row.get("Identifiant Vol (Callsign)") or "Unknown")
            
            # A. Trop de retries ?
            try:
                retries_raw = row.get("Nettoyage Retries")
                retries = int(retries_raw) if (retries_raw and str(retries_raw).strip() != "") else 0
            except: 
                retries = 0
            
            if retries >= MAX_RETRIES:
                return False

            # B. Est-ce que la donnée actuelle est complète et fiable ?
            vides = ["", "inconnu", "nan", "none", "none -> none", "?", "inconnue", "--:--"]
            
            val_de = str(row.get("De") or "").strip().lower()
            val_a = str(row.get("A") or "").strip().lower()
            val_dep_h = str(row.get("Dep_H") or "").strip().lower()
            val_arr_h = str(row.get("Arr_H") or "").strip().lower()
            
            missing_data = (val_de in vides or val_a in vides or val_dep_h in vides or val_arr_h in vides)
            
            source = str(row.get("Source") or "").strip()
            SOURCES_NON_FIABLES = ["hexdb", "OpenSky (Live)", ""]
            unreliable_source = source in SOURCES_NON_FIABLES

            if not (missing_data or unreliable_source):
                return False

            # C. Délai minimal (10 min)
            ts = row.get("ts_matching")
            if not ts: 
                return False
            
            is_ready = ts < (datetime.now().timestamp() - 600)
            
            if is_ready:
                reason = "Data missing" if missing_data else f"Unreliable source ({source})"
                print(f"    [Eligible] {callsign} ({reason}, Retries: {retries})")
            
            return is_ready

        df["is_eligible"] = df.apply(check_eligibility, axis=1)
        df_todo = df[df["is_eligible"] == True].copy()

        if df_todo.empty:
            print("    ✅ Aucun vol à traiter.")
            return

        print(f"    🔍 {len(df_todo)} vol(s) à enrichir.")

        # 4. Boucle d'enrichissement
        success_count = 0
        df_modified = False

        for idx, row in df_todo.iterrows():
            callsign = str(row["Identifiant Vol (Callsign)"]).strip()
            icao     = str(row["Identifiant Appareil (ICAO24)"]).strip()
            ts       = row["ts_matching"]

            print(f"\n    -> {callsign} | {row['Date']} {row['Heure']}")

            # Tentative FA
            dep, arr, h_dep, h_arr, f_info = get_flightaware_web_data(callsign, ts)

            # Fallback OpenSky
            if (not dep or dep == "Inconnu") and icao and icao != "nan" and user:
                print("        [Fallback] Tentative OpenSky...")
                dep, arr, h_dep, h_arr = get_opensky_flight_history(icao, ts, user, pwd)

            if dep and dep != "Inconnu":
                # SUCCÈS
                df.at[idx, "De"]     = resolve_airport(dep)
                df.at[idx, "A"]      = resolve_airport(arr) if arr else "Inconnu"
                df.at[idx, "Dep_H"]  = h_dep if h_dep else ""
                df.at[idx, "Arr_H"]  = h_arr if h_arr else ""
                df.at[idx, "Source"] = "FlightAware (Web)" if f_info else "OpenSky (History)"
                if f_info and f_info.get("aircraft"):
                    if not df.at[idx, "Modèle Avion"] or df.at[idx, "Modèle Avion"] in ("", "nan"):
                        df.at[idx, "Modèle Avion"] = f_info["aircraft"]
                
                print(f"        ✅ OK : {dep} -> {arr}")
                success_count += 1
            else:
                # ÉCHEC : On incrémente le compteur de retries
                try:
                    current_retries = int(df.at[idx, "Nettoyage Retries"] or 0)
                except: current_retries = 0
                df.at[idx, "Nettoyage Retries"] = current_retries + 1
                print(f"        ❌ Échec (Tentative {current_retries + 1}/{MAX_RETRIES})")
            
            df_modified = True
            time.sleep(2) # Politesse

        # 5. Sauvegarde
        if df_modified:
            cols_to_drop = [c for c in ["ts_matching", "is_eligible"] if c in df.columns]
            df_final = df.drop(columns=cols_to_drop)
            conn.update(worksheet=WORKSHEET, data=df_final.fillna(""))
            print(f"\n💾 GSheets mis à jour ({success_count} succès).")

    except Exception as e:
        print(f"❌ ERREUR CRITIQUE : {e}")
        import traceback
        traceback.print_exc()

    print(f"\n{'='*60}\n✅ FIN\n{'='*60}")

if __name__ == "__main__":
    main()
