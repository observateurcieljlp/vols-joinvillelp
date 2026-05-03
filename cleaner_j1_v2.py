"""
flightaware_scraper_v2.py
======================
Récupère l'historique de vols depuis FlightAware (via JSON Bootstrap)
Filtre strictement selon ta logique d'origine.
"""

import re
import time
import json
import requests
import pandas as pd
import streamlit as st
from streamlit_gsheets import GSheetsConnection
from datetime import datetime, timedelta
from curl_cffi import requests as cf_requests
import pytz

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WORKSHEET = "Vols_Joinville"

COLS = [
    "Date", "Heure", "Identifiant Vol (Callsign)", "Compagnie", "Modèle Avion",
    "Immatriculation", "Identifiant Appareil (ICAO24)", "Altitude (m)",
    "Evolution Verticale", "De", "A", "Dep_H", "Arr_H", "Source",
    "Planespotters", "Positions", "Airlabs Info"
]

SOURCES_NON_FIABLES = ["hexdb", "OpenSky (Live)"]
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
                # Extraction intelligente des codes ou noms d'aéroports
                orig = f.get("origin", {})
                dest = f.get("destination", {})

                # Si le code OACI n'est pas valide (ex: coordonnées GPS), on prend le nom friendly
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
    tz_paris = pytz.timezone("Europe/Paris") # Le fuseau cible

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
        # --- CORRECTION ICI : Conversion en Heure de Paris ---
        dt_dep_paris = best_match["dep_utc"].astimezone(tz_paris)
        h_dep = dt_dep_paris.strftime("%H:%M")
        
        if best_match["arr_utc"]:
            dt_arr_paris = best_match["arr_utc"].astimezone(tz_paris)
            h_arr = dt_arr_paris.strftime("%H:%M")
        else:
            h_arr = "--:--"
            
        print(f"        [Scraper FA]  ✅ Match : {best_match['dep_code']} ({h_dep}) → {best_match['arr_code']} ({h_arr})")
        return best_match["dep_code"], best_match["arr_code"], h_dep, h_arr, best_match

    return None, None, None, None, None

# ---------------------------------------------------------------------------
# OpenSky (fallback)
# ---------------------------------------------------------------------------

def get_opensky_flight_history(
    icao24: str, timestamp: int | float, user: str, pwd: str
) -> tuple[str | None, str | None, str | None, str | None]:
    begin, end = int(timestamp) - 14400, int(timestamp) + 14400
    url = f"https://opensky-network.org/api/flights/aircraft?icao24={icao24}&begin={begin}&end={end}"
    try:
        response = requests.get(url, auth=(user, pwd), timeout=20)
        if response.status_code == 200:
            flights = response.json()
            if flights:
                best = min(flights, key=lambda f: abs((f.get("firstSeen") or 0) - timestamp))
                dep = best.get("estDepartureAirport") or "Inconnu"
                arr = best.get("estArrivalAirport") or "Inconnu"
                h_dep = datetime.fromtimestamp(best["firstSeen"], tz=pytz.utc).strftime("%H:%M") if best.get("firstSeen") else "--:--"
                h_arr = datetime.fromtimestamp(best["lastSeen"], tz=pytz.utc).strftime("%H:%M") if best.get("lastSeen") else "--:--"
                return dep, arr, h_dep, h_arr
    except Exception as e:
        print(f"        [OpenSky]  Erreur : {e}")
    return None, None, None, None

# ---------------------------------------------------------------------------
# Résolution lisible d'un code aéroport
# ---------------------------------------------------------------------------

_airport_cache: dict[str, str] = {}

def resolve_airport(code: str) -> str:
    code = code.strip().upper()
    if not code or code in ("INCONNU", "?", ""): return code
    if code in _airport_cache: return _airport_cache[code]
    try:
        kind = "icao" if len(code) == 4 else "iata"
        r = requests.get(f"https://hexdb.io/api/v1/airport/{kind}/{code}", timeout=5)
        if r.status_code == 200:
            nom = r.json().get("airport", "").strip()
            for suffix in (" Airport", " International Airport", " Intl", " International"):
                nom = nom.replace(suffix, "")
            res = f"{nom} ({code})" if nom else code
            _airport_cache[code] = res
            return res
    except: pass
    _airport_cache[code] = code
    return code

# ---------------------------------------------------------------------------
# Main 
# ---------------------------------------------------------------------------

def main():
    print(
        f"\n{'='*60}\n"
        f"🧹 DÉMARRAGE DU NETTOYEUR J+1 : "
        f"{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n"
        f"{'='*60}"
    )

    # 1. Secrets OpenSky
    try:
        user = st.secrets["OPENSKY_USER"].lower()
        pwd  = st.secrets["OPENSKY_PWD"]
    except Exception:
        user, pwd = "", ""
        print("    ⚠️ Secrets OpenSky non trouvés.")

    # 2. Lecture de la feuille
    conn = st.connection("gsheets", type=GSheetsConnection)
    df = conn.read(worksheet=WORKSHEET, ttl=0)

    if df.empty:
        print("    Feuille vide, rien à faire.")
        return

    # 3. Normalisation des colonnes
    rename_map = {
        "Avion": "Identifiant Vol (Callsign)",
        "icao24": "Identifiant Appareil (ICAO24)",
        "Altitude": "Altitude (m)",
    }
    df = df.rename(columns=rename_map)
    for c in COLS:
        if c not in df.columns:
            df[c] = ""
    df = df[COLS]

    # 4. GESTION DU TEMPS (Conversion Paris -> UTC)
    tz_paris = pytz.timezone("Europe/Paris")
    
    def get_utc_ts(row):
        try:
            # On combine Date (DD/MM/YYYY) et Heure (HH:MM)
            dt_str = f"{str(row['Date']).strip()} {str(row['Heure']).strip()}"
            # On l'interprète comme heure locale Paris
            dt_naive = datetime.strptime(dt_str, "%d/%m/%Y %H:%M")
            dt_paris = tz_paris.localize(dt_naive)
            # On retourne le timestamp UTC
            return dt_paris.timestamp()
        except:
            return None

    df["ts_matching"] = df.apply(get_utc_ts, axis=1)

# 5. Filtrage des lignes à traiter (Logique intelligente)
    def check_eligibility(row):
        # On définit ce qu'on considère comme "vide" ou "incomplet"
        vides = ["", "Inconnu", "nan", None, "--:--"]
        
        missing_de = str(row["De"]).strip().lower() in vides
        missing_a  = str(row["A"]).strip().lower() in vides
        missing_dep_h = str(row["Dep_H"]).strip() in vides
        missing_arr_h = str(row["Arr_H"]).strip() in vides

        # Si tout est déjà rempli, on ne touche à rien
        if not (missing_de or missing_a or missing_dep_h or missing_arr_h):
            return False

        # Gestion du délai (Wait Condition)
        ts = row["ts_matching"]
        if not ts: return False
        
        now_ts = datetime.now().timestamp()
        
        # SI l'arrivée est manquante (A ou Arr_H), on attend 1h (3600s)
        # SINON (si c'est juste le départ), 10 min (600s) suffisent
        if missing_a or missing_arr_h:
            margin = 3600 
        else:
            margin = 600
            
        return ts < (now_ts - margin)

    # On applique la fonction et on crée le DataFrame de travail
    df["is_eligible"] = df.apply(check_eligibility, axis=1)
    df_todo = df[df["is_eligible"] == True].copy()

    # --- ANCIEN CODE SUPPRIMÉ ICI (C'est ce qui causait l'erreur) ---

    if df_todo.empty:
        print("    ✅ Aucun vol 'Inconnu' à traiter pour le moment.")
        if "ts_matching" in df.columns: df = df.drop(columns=["ts_matching"])
        if "is_eligible" in df.columns: df = df.drop(columns=["is_eligible"])
        return

    print(f"    🔍 {len(df_todo)} vol(s) à enrichir.")

    # 6. Boucle d'enrichissement
    success_count = 0

    for idx, row in df_todo.iterrows():
        icao     = str(row["Identifiant Appareil (ICAO24)"]).strip()
        callsign = str(row["Identifiant Vol (Callsign)"]).strip()
        ts       = row["ts_matching"]

        if not ts or not callsign or callsign == "nan":
            continue

        print(f"\n    -> {callsign} | Capture à {row['Heure']} (Paris)")

        dep, arr, h_dep, h_arr, flight_info = None, None, None, None, None

        # A. Tentative FlightAware (Nouveau Scraper JSON)
        dep, arr, h_dep, h_arr, flight_info = get_flightaware_web_data(callsign, ts)

        # B. Fallback OpenSky
        if (not dep or dep == "Inconnu") and icao and icao != "nan" and user:
            print("        [Fallback] Tentative OpenSky...")
            dep, arr, h_dep, h_arr = get_opensky_flight_history(icao, ts, user, pwd)
            flight_info = None

        # C. Mise à jour si succès
        if dep and dep != "Inconnu":
            df.at[idx, "De"]     = resolve_airport(dep)
            df.at[idx, "A"]      = resolve_airport(arr) if arr else "Inconnu"
            df.at[idx, "Dep_H"]  = h_dep if h_dep else ""
            df.at[idx, "Arr_H"]  = h_arr if h_arr else ""
            df.at[idx, "Source"] = "FlightAware (Web)" if flight_info else "OpenSky (History)"

            # Enrichissement avion
            if flight_info and flight_info.get("aircraft"):
                if not df.at[idx, "Modèle Avion"] or df.at[idx, "Modèle Avion"] in ("", "nan"):
                    df.at[idx, "Modèle Avion"] = flight_info["aircraft"]

            success_count += 1
            print(f"        ✅ OK : {dep} -> {arr}")
        else:
            print("        ❌ Match introuvable.")

        time.sleep(1.5) # Politesse

    # 7. Sauvegarde
    if "ts_matching" in df.columns: df = df.drop(columns=["ts_matching"])
    if "is_eligible" in df.columns: df = df.drop(columns=["is_eligible"])

    if success_count > 0:
        conn.update(worksheet=WORKSHEET, data=df.fillna(""))
        print(f"\n💾 Mis à jour terminé : {success_count} vols corrigés.")
    else:
        print("\n   Rien n'a été modifié.")

    print(f"\n{'='*60}\n✅ FIN\n{'='*60}")

if __name__ == "__main__":
    main()