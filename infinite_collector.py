import requests
import pandas as pd
import os
import time
import math
import json
from datetime import datetime
import streamlit as st
from streamlit_gsheets import GSheetsConnection
from FlightRadar24 import FlightRadar24API
from utils_aircraft import refresh_aircraft_db, get_aircraft_info
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import warnings
import logging

# Silence total
logging.disable(logging.CRITICAL)
warnings.filterwarnings("ignore")
os.environ["STREAMLIT_LOG_LEVEL"] = "error"
os.environ["STREAMLIT_SERVER_GATHER_USAGE_STATS"] = "false"

# =============================================================================
# CONFIGURATION
# =============================================================================

BBOX_WATCH = {"lamin": 48.40, "lamax": 49.20, "lomin": 2.00, "lomax": 3.00}
BBOX_JOINVILLE = {"lamin": 48.809, "lamax": 48.828, "lomin": 2.455, "lomax": 2.485}

ALTITUDE_MAX = 3500    
HEARTBEAT_MAX = 180    
MARGE_SECURITE = 30    

# SCHÉMA GLOBAL UNIQUE (Synchro entre tous les scripts)
COLS_GSHEET = [
    "Date", "Heure", "Identifiant Vol (Callsign)", "Compagnie", "Modèle Avion", 
    "Immatriculation", "Identifiant Appareil (ICAO24)", "Altitude (m)", 
    "Evolution Verticale", "Lat", "Lon", "Heading", "De", "A", "Dep_H", "Arr_H", 
    "Source", "Planespotters", "Positions", "Nettoyage Retries",
    "Airlabs Info", "OpenSky State Info", "Hexdb Route Info", 
    "Hexdb Aircraft Info", "Planespotters Info", "Aircraft DB Info"
]

# =============================================================================
# INITIALISATION & API
# =============================================================================
pd.set_option('future.no_silent_downcasting', True)
fr_api = FlightRadar24API()
refresh_aircraft_db()

session = requests.Session()
retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retry)
session.mount("https://", adapter)

def estimate_eta(lat, lon, heading, velocity):
    if not heading or not velocity or velocity < 5: return None
    j_lat, j_lon = 48.818, 2.47
    if BBOX_JOINVILLE["lamin"] <= lat <= BBOX_JOINVILLE["lamax"] and BBOX_JOINVILLE["lomin"] <= lon <= BBOX_JOINVILLE["lomax"]: return 0
    d_lat, d_lon = j_lat - lat, (j_lon - lon) * math.cos(math.radians(j_lat))
    bearing_to_j = math.degrees(math.atan2(d_lon, d_lat)) % 360
    diff = abs(heading - bearing_to_j)
    if diff > 180: diff = 360 - diff 
    if diff < 45:
        dist_m = math.sqrt(d_lat**2 + d_lon**2) * 111000
        return dist_m / velocity
    return None

def clean(v) -> str:
    INVALIDES = {"inconnu", "unknown", "n/a", "none", "null", "nan"}
    s = str(v).strip() if v is not None else ""
    return "" if s.lower() in INVALIDES else s

def get_flight_airlabs(icao24: str) -> dict | None:
    try:
        api_key = st.secrets.get("AIRLABS_API_KEY", "")
        if not api_key: return None
        url = f"https://airlabs.co/api/v9/flights?hex={icao24.lower()}&api_key={api_key}"
        print(f"    [API AirLabs]   requête -> {url}")
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "response" in data and isinstance(data["response"], list) and len(data["response"]) > 0:
                print(f"    [API AirLabs]   réponse OK")
                return data["response"][0]
    except Exception as e: print(f"    [API AirLabs]   exception : {e}")
    return None

def get_route_hexdb(callsign: str) -> tuple[str, str, str] | None:
    cs = callsign.strip().upper()
    if not cs or cs == "INCONNU": return None
    url = f"https://hexdb.io/api/v1/route/icao/{cs}"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            raw_json = json.dumps(data, ensure_ascii=False)
            route = data.get("route", "")
            parts = route.split("-")
            if len(parts) == 2 and all(parts): return parts[0], parts[1], raw_json
    except: pass
    return None

def get_aircraft_hexdb(icao24: str) -> tuple[str, str, str, str]:
    url = f"https://hexdb.io/api/v1/aircraft/{icao24.lower()}"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            d = r.json()
            return d.get("RegisteredOwners", ""), d.get("Type", ""), d.get("Registration", ""), json.dumps(d, ensure_ascii=False)
    except: pass
    return "", "", "", ""

def get_aircraft_planespotters(icao24: str) -> tuple[str, str, str]:
    url = f"https://api.planespotters.net/pub/photos/hex/{icao24.lower()}"
    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data.get("photos"):
                info = data["photos"][0]
                return info.get("airline", {}).get("name", ""), info.get("aircraft_type", ""), json.dumps(data, ensure_ascii=False)
    except: pass
    return "", "", ""

_airport_cache: dict[str, str] = {}
def resolve_airport(code: str) -> str:
    code = code.strip().upper()
    if not code or code in ("INCONNU", "?", ""): return code
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

def get_real_flight_info(icao24):
    make, model, reg, db_info_raw = get_aircraft_info(icao24)
    make, model, reg = clean(make), clean(model), clean(reg)
    hexdb_raw, ps_raw = "", ""
    if not make or not model or not reg:
        hx_make, hx_model, hx_reg, hx_raw = get_aircraft_hexdb(icao24)
        hexdb_raw = hx_raw
        if not make: make = clean(hx_make)
        if not model: model = clean(hx_model)
        if not reg: reg = clean(hx_reg)
        if not make or not model:
            ps_make, ps_model, p_raw = get_aircraft_planespotters(icao24)
            ps_raw = p_raw
            if not make: make = clean(ps_make)
            if not model: model = clean(ps_model)
    return make, model, reg, db_info_raw, hexdb_raw, ps_raw

# =============================================================================
# TOKEN & SCAN
# =============================================================================

_opensky_token, _token_expiry = None, 0
def get_opensky_token():
    global _opensky_token, _token_expiry
    if _opensky_token and time.time() < _token_expiry - 60: return _opensky_token
    try:
        client_id = st.secrets.get("OPENSKY_CLIENT_ID") or st.secrets.get("OPENSKY_USER")
        client_secret = st.secrets.get("OPENSKY_CLIENT_SECRET") or st.secrets.get("OPENSKY_PWD")
        payload = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}
        r = requests.post("https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token", data=payload, timeout=15)
        if r.status_code == 200:
            d = r.json()
            _opensky_token, _token_expiry = d.get("access_token"), time.time() + d.get("expires_in", 1800)
            return _opensky_token
    except: pass
    return None

def run_scan():
    now_dt = datetime.now()
    print(f"\n{'='*60}\n📡 SCAN DU CIEL : {now_dt.strftime('%d/%m/%Y %H:%M:%S')}\n{'='*60}")
    next_sleep, decision_reason = HEARTBEAT_MAX, "Default Heartbeat (3 min)"
    try:
        token = get_opensky_token()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = f"https://opensky-network.org/api/states/all?lamin={BBOX_WATCH['lamin']}&lomin={BBOX_WATCH['lomin']}&lamax={BBOX_WATCH['lamax']}&lomax={BBOX_WATCH['lomax']}"
        response = session.get(url, headers=headers, timeout=30)
        print(f"💰 CRÉDITS OPENSKY : {response.headers.get('X-Rate-Limit-Remaining', 'Inconnu')}")
        if response.status_code != 200: return next_sleep
        states = response.json().get('states') or []
        print(f"🔍 {len(states)} avion(s) détecté(s) dans la Watch Zone")
        candidates = []
        for avion in states:
            icao24, callsign, au_sol = avion[0], str(avion[1]).strip() or "Inconnu", avion[8]
            lat, lon, heading, velocity, altitude = avion[6], avion[5], avion[10] or 0, avion[9], avion[13] or avion[7] or 0
            if altitude < ALTITUDE_MAX:
                if altitude < 10 or au_sol: continue
                j_lat, j_lon = 48.818, 2.47
                d_lat, d_lon = j_lat - lat, (j_lon - lon) * math.cos(math.radians(j_lat))
                bearing_to_j = math.degrees(math.atan2(d_lon, d_lat)) % 360
                dist_km = math.sqrt(d_lat**2 + d_lon**2) * 111
                eta = estimate_eta(lat, lon, heading, velocity)
                info_nav = f"à {int(altitude)}m - Dist: {dist_km:.1f}km, Cap: {int(heading)}°, Vit: {int((velocity or 0)*3.6)}km/h, Gis: {int(bearing_to_j)}°"
                if eta is not None:
                    if eta == 0:
                        print(f"  🎯 [ZONE] {callsign} ({icao24}) {info_nav} - SUR JOINVILLE !")
                        candidates.append(avion)
                        if 30 < next_sleep: next_sleep, decision_reason = 30, f"Suivi haute précision de {callsign}"
                    elif eta < HEARTBEAT_MAX:
                        potential_sleep = max(30, int(eta) - MARGE_SECURITE) if eta > 60 else int(eta) + 5
                        print(f"  ➡️ [APPROCHE] {callsign} ({icao24}) {info_nav}. Réveil: {potential_sleep}s")
                        if potential_sleep < next_sleep: next_sleep, decision_reason = potential_sleep, f"Interception de {callsign}"
                else: print(f"  ✈️ [HORS TRAJECTOIRE] {callsign} ({icao24}) {info_nav}")
            else: print(f"  ☁️ [TROP HAUT] {callsign} à {int(altitude)}m - Ignoré")

        if candidates:
            conn = st.connection("gsheets", type=GSheetsConnection)
            try:
                df = conn.read(worksheet="Vols_Joinville", ttl=0)
                df = df.rename(columns={"Avion":"Identifiant Vol (Callsign)","icao24":"Identifiant Appareil (ICAO24)","Altitude":"Altitude (m)"})
                for c in COLS_GSHEET:
                    if c not in df.columns: df[c] = ""
                df = df[COLS_GSHEET]
            except: df = pd.DataFrame(columns=COLS_GSHEET)

            new_entries = []
            for avion in candidates:
                icao24, callsign = avion[0], str(avion[1]).strip() or "Inconnu"
                altitude, v_rate, lat, lon, heading = int(avion[13] or avion[7] or 0), avion[11] or 0, avion[6], avion[5], avion[10] or 0
                
                # Format enrichi : (Lat, Lon, Alt, Heading)
                pos_entry = f"({lat:.4f}, {lon:.4f}, {altitude}, {int(heading)})"
                trend = "⬆️ Montée" if v_rate > 0.5 else ("⬇️ Descente" if v_rate < -0.5 else "➡️ Stable")
                
                match = df[(df["Identifiant Appareil (ICAO24)"] == icao24) & (df["Date"] == now_dt.strftime("%d/%m/%Y"))]
                updated = False
                for idx in match.index:
                    try:
                        if abs((datetime.strptime(df.at[idx, "Heure"], "%H:%M") - now_dt).total_seconds() / 60) < 15:
                            # Ajout de la position si elle est nouvelle
                            current_pos = str(df.at[idx, "Positions"])
                            if pos_entry not in current_pos:
                                df.at[idx, "Positions"] = (current_pos + " | " + pos_entry).strip(" | ")
                                print(f"    ✅ Position enrichie ajoutée pour {callsign}")
                            
                            df.at[idx, "Altitude (m)"], df.at[idx, "Evolution Verticale"] = altitude, trend
                            df.at[idx, "Lat"], df.at[idx, "Lon"], df.at[idx, "Heading"] = lat, lon, heading
                            df.at[idx, "OpenSky State Info"] = json.dumps(avion, ensure_ascii=False)
                            updated = True; break
                    except: pass
                
                if not updated:
                    print(f"    🆕 Nouvel enregistrement pour {callsign}")
                    make, model, reg, hx_raw, ps_raw = get_real_flight_info(icao24)
                    # ... (reste du bloc new_entries identique mais avec pos_entry)
                    dep, arr, h_dep, h_arr, airlabs_raw, source, hexdb_route_raw = "Inconnu", "Inconnu", "--:--", "--:--", "", "OpenSky (Live)", ""
                    al_data = get_flight_airlabs(icao24)
                    if al_data:
                        dep, arr, h_dep, h_arr, source = al_data.get("dep_iata") or al_data.get("dep_icao") or "Inconnu", al_data.get("arr_iata") or al_data.get("arr_icao") or "Inconnu", al_data.get("dep_time") or "--:--", al_data.get("arr_time") or "--:--", "AirLabs"
                        if not make or make == "Inconnu": make = clean(al_data.get("airline_name")) or clean(al_data.get("airline_icao"))
                        if not model or model == "Inconnu": model = clean(al_data.get("model"))
                        if not reg or reg == "Inconnu": reg = clean(al_data.get("reg_number"))
                        airlabs_raw = json.dumps(al_data, ensure_ascii=False)
                    if dep == "Inconnu":
                        hexdb_result = get_route_hexdb(callsign)
                        if hexdb_result: dep, arr, hexdb_route_raw, source = hexdb_result[0], hexdb_result[1], hexdb_result[2], "hexdb"
                    new_entries.append({
                        "Date": now_dt.strftime("%d/%m/%Y"), "Heure": now_dt.strftime("%H:%M"), "Identifiant Vol (Callsign)": callsign, "Compagnie": make, "Modèle Avion": model, "Immatriculation": reg, "Identifiant Appareil (ICAO24)": icao24, "Altitude (m)": altitude, "Evolution Verticale": trend, "Lat": lat, "Lon": lon, "Heading": heading, "De": resolve_airport(dep), "A": resolve_airport(arr), "Dep_H": h_dep, "Arr_H": h_arr, "Source": source, "Planespotters": f'=HYPERLINK("https://www.planespotters.net/hex/{icao24.upper()}","{icao24.upper()}")', "Positions": pos_str, "Airlabs Info": airlabs_raw, "OpenSky State Info": json.dumps(avion, ensure_ascii=False), "Hexdb Route Info": hexdb_route_raw, "Hexdb Aircraft Info": hx_raw, "Planespotters Info": ps_raw, "Aircraft DB Info": "", "Nettoyage Retries": 0
                    })
            if new_entries or updated:
                df_final = pd.concat([df, pd.DataFrame(new_entries)], ignore_index=True)
                df_final = df_final.reindex(columns=COLS_GSHEET).fillna("")
                df_final = df_final.drop_duplicates(subset=["Date", "Heure", "Identifiant Vol (Callsign)"], keep="last").tail(2000)
                conn.update(worksheet="Vols_Joinville", data=df_final)
                print(f"    💾 Google Sheet mis à jour.")
    except Exception as e: print(f"❌ ERREUR CRITIQUE : {e}")
    print(f"\n💤 DÉCISION : {decision_reason}\n⏰ SOMMEIL : {next_sleep} secondes")
    return next_sleep

def main():
    print("=====================================================\nDÉMARRAGE DU RADAR PRÉDICTIF INFINI (JOINVILLE)\n=====================================================")
    while True:
        wait_time = run_scan()
        time.sleep(wait_time)

if __name__ == "__main__":
    main()
