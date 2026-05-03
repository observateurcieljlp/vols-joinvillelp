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

# =============================================================================
# CONFIGURATION DES ZONES (BBOX)
# =============================================================================

BBOX_WATCH = {
    "lamin": 48.40, 
    "lamax": 49.20, 
    "lomin": 2.00, 
    "lomax": 3.00
}

BBOX_JOINVILLE = {
    "lamin": 48.809,
    "lamax": 48.828,
    "lomin": 2.455,
    "lomax": 2.485
}

ALTITUDE_MAX = 3500    
HEARTBEAT_MAX = 180    
MARGE_SECURITE = 30    

# NOUVELLES COLONNES AJOUTEES A LA FIN
COLS_GSHEET = [
    "Date", "Heure", "Identifiant Vol (Callsign)", "Compagnie", "Modèle Avion", 
    "Immatriculation", "Identifiant Appareil (ICAO24)", "Altitude (m)", 
    "Evolution Verticale", "De", "A", "Dep_H", "Arr_H", "Source", 
    "Planespotters", "Positions", "Airlabs Info", 
    "OpenSky State Info", "Hexdb Route Info", "Aircraft DB Info"
]

# =============================================================================
# INITIALISATION
# =============================================================================
pd.set_option('future.no_silent_downcasting', True)
fr_api = FlightRadar24API()
refresh_aircraft_db()

session = requests.Session()
retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retry)
session.mount("https://", adapter)

# =============================================================================
# LOGIQUE DE CALCUL DE TRAJECTOIRE
# =============================================================================

def estimate_eta(lat, lon, heading, velocity):
    if not heading or not velocity or velocity < 5:
        return None

    j_lat, j_lon = 48.818, 2.47

    if BBOX_JOINVILLE["lamin"] <= lat <= BBOX_JOINVILLE["lamax"] and \
       BBOX_JOINVILLE["lomin"] <= lon <= BBOX_JOINVILLE["lomax"]:
        return 0

    d_lat = j_lat - lat
    d_lon = (j_lon - lon) * math.cos(math.radians(j_lat))
    bearing_to_j = math.degrees(math.atan2(d_lon, d_lat)) % 360

    diff = abs(heading - bearing_to_j)
    if diff > 180: diff = 360 - diff 

    if diff < 45:
        dist_m = math.sqrt(d_lat**2 + d_lon**2) * 111000
        return dist_m / velocity
    
    return None

# =============================================================================
# ENRICHISSEMENT & API EXTERNES (Avec récupération RAW)
# =============================================================================

def clean(v) -> str:
    INVALIDES = {"inconnu", "unknown", "n/a", "none", "null"}
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
        print(f"    [API AirLabs]   aucun résultat ou HTTP {r.status_code}")
    except Exception as e:
        print(f"    [API AirLabs]   exception : {e}")
    return None

def get_route_hexdb(callsign: str) -> tuple[str, str, str] | None:
    """Retourne Dep, Arr et le JSON brut."""
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
            if len(parts) == 2 and all(parts):
                return parts[0], parts[1], raw_json
    except: pass
    return None

def get_aircraft_hexdb(icao24: str) -> tuple[str, str, str, str]:
    """Retourne Owner, Type, Reg, et le JSON brut."""
    try:
        url = f"https://hexdb.io/api/v1/aircraft/{icao24.lower()}"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            d = r.json()
            raw_json = json.dumps(d, ensure_ascii=False)
            return d.get("RegisteredOwners", ""), d.get("Type", ""), d.get("Registration", ""), raw_json
    except: pass
    return "", "", "", ""

def get_aircraft_planespotters(icao24: str) -> tuple[str, str, str]:
    """Retourne Airline, Type, et le JSON brut."""
    try:
        url = f"https://api.planespotters.net/pub/photos/hex/{icao24.lower()}"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            raw_json = json.dumps(data, ensure_ascii=False)
            if data.get("photos"):
                info = data["photos"][0]
                return info.get("airline", {}).get("name", ""), info.get("aircraft_type", ""), raw_json
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
    """Enrichissement et agrégation des données brutes DB."""
    make, model, reg = get_aircraft_info(icao24)
    make, model, reg = clean(make), clean(model), clean(reg)
    raw_db_dict = {}

    if not make or not model or not reg:
        hx_make, hx_model, hx_reg, hx_raw = get_aircraft_hexdb(icao24)
        if hx_raw: raw_db_dict["hexdb"] = json.loads(hx_raw)
        
        if not make: make = clean(hx_make)
        if not model: model = clean(hx_model)
        if not reg: reg = clean(hx_reg)

        if not make or not model:
            ps_make, ps_model, ps_raw = get_aircraft_planespotters(icao24)
            if ps_raw: raw_db_dict["planespotters"] = json.loads(ps_raw)
            
            if not make: make = clean(ps_make)
            if not model: model = clean(ps_model)
            
    raw_db_json = json.dumps(raw_db_dict, ensure_ascii=False) if raw_db_dict else ""
    return make, model, reg, raw_db_json

# =============================================================================
# GESTION DU TOKEN OPENSKY (OAuth2)
# =============================================================================

_opensky_token = None
_token_expiry = 0

def get_opensky_token():
    global _opensky_token, _token_expiry
    if _opensky_token and time.time() < _token_expiry - 60:
        return _opensky_token

    print("🔑 Rafraîchissement du Token OpenSky...")
    try:
        client_id = st.secrets.get("OPENSKY_CLIENT_ID")
        client_secret = st.secrets.get("OPENSKY_CLIENT_SECRET")
        if not client_id: client_id = st.secrets.get("OPENSKY_USER")
        if not client_secret: client_secret = st.secrets.get("OPENSKY_PWD")

        auth_url = "https://auth.opensky-network.org/auth/realms/opensky-network/protocol/openid-connect/token"
        payload = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}
        
        r = requests.post(auth_url, data=payload, timeout=15)
        if r.status_code == 200:
            data = r.json()
            _opensky_token = data.get("access_token")
            expires_in = data.get("expires_in", 1800)
            _token_expiry = time.time() + expires_in
            print(f"✅ Nouveau Token obtenu (expire dans {int(expires_in/60)} min)")
            return _opensky_token
        else:
            print(f"❌ Échec de l'authentification OpenSky : {r.status_code} - {r.text}")
    except Exception as e:
        print(f"❌ Erreur lors de l'échange de token : {e}")
    return None

# =============================================================================
# BOUCLE PRINCIPALE
# =============================================================================

def run_scan():
    now_dt = datetime.now()
    print(f"\n{'='*60}")
    print(f"📡 SCAN DU CIEL : {now_dt.strftime('%d/%m/%Y %H:%M:%S')}")
    print(f"{'='*60}")
    
    next_sleep = HEARTBEAT_MAX
    decision_reason = "Default Heartbeat (3 min)"

    try:
        token = get_opensky_token()
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = f"https://opensky-network.org/api/states/all?lamin={BBOX_WATCH['lamin']}&lomin={BBOX_WATCH['lomin']}&lamax={BBOX_WATCH['lamax']}&lomax={BBOX_WATCH['lomax']}"
        response = session.get(url, headers=headers, timeout=30)
        
        credits_restants = response.headers.get("X-Rate-Limit-Remaining", "Inconnu")
        print(f"💰 CRÉDITS OPENSKY : {credits_restants}")

        if response.status_code != 200:
            print(f"❌ ERREUR API OpenSky : HTTP {response.status_code}")
            return next_sleep

        states = response.json().get('states') or []
        print(f"🔍 {len(states)} avion(s) détecté(s) dans la Watch Zone")
        
        candidates = []
        for avion in states:
            icao24, callsign, au_sol = avion[0], str(avion[1]).strip() or "Inconnu", avion[8]
            lat, lon, heading, velocity = avion[6], avion[5], avion[10], avion[9]
            altitude = avion[13] or avion[7] or 0
            
            if altitude < ALTITUDE_MAX:
                if altitude < 10 or au_sol: continue
                j_lat, j_lon = 48.818, 2.47
                d_lat, d_lon = j_lat - lat, (j_lon - lon) * math.cos(math.radians(j_lat))
                bearing_to_j = math.degrees(math.atan2(d_lon, d_lat)) % 360
                dist_km = math.sqrt(d_lat**2 + d_lon**2) * 111
                eta = estimate_eta(lat, lon, heading, velocity)
                info_nav = f"à {int(altitude)}m - Dist: {dist_km:.1f}km, Cap: {int(heading or 0)}°, Vit: {int((velocity or 0)*3.6)}km/h, Gis: {int(bearing_to_j)}°"

                if eta is not None:
                    if eta == 0:
                        print(f"  🎯 [ZONE] {callsign} ({icao24}) {info_nav} - SUR JOINVILLE !")
                        candidates.append(avion)
                    elif eta < HEARTBEAT_MAX:
                        eta_min, eta_sec = int(eta // 60), int(eta % 60)
                        if eta > 60:
                            potential_sleep = max(30, int(eta) - MARGE_SECURITE)
                            type_appro = "APPROCHE"
                        else:
                            potential_sleep = int(eta) + 5
                            type_appro = "INTERCEPTION"
                        print(f"  ➡️ [{type_appro}] {callsign} ({icao24}) {info_nav}. ETA: {eta_min}m {eta_sec}s. Réveil: {potential_sleep}s")
                        if potential_sleep < next_sleep:
                            next_sleep, decision_reason = potential_sleep, f"{type_appro} de {callsign}"
                else:
                    print(f"  ✈️ [HORS TRAJECTOIRE] {callsign} ({icao24}) {info_nav}")
            else:
                print(f"  ☁️ [TROP HAUT] {callsign} à {int(altitude)}m - Ignoré")

        if candidates:
            print(f"\n📝 MISE À JOUR GSHEETS : {len(candidates)} avion(s) cible(s)...")
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
                altitude = int(avion[13] or avion[7] or 0)
                v_rate, lat, lon = avion[11] or 0, avion[6], avion[5]
                pos_str, trend = f"({lat:.4f}, {lon:.4f})", ("⬆️ Montée" if v_rate > 0.5 else ("⬇️ Descente" if v_rate < -0.5 else "➡️ Stable"))

                # Sauvegarde du JSON OpenSky du vol
                opensky_state_raw = json.dumps(avion, ensure_ascii=False)

                match = df[(df["Identifiant Appareil (ICAO24)"] == icao24) & (df["Date"] == now_dt.strftime("%d/%m/%Y"))]
                updated = False
                for idx in match.index:
                    try:
                        if abs((datetime.strptime(df.at[idx, "Heure"], "%H:%M") - now_dt).total_seconds() / 60) < 15:
                            if pos_str not in str(df.at[idx, "Positions"]):
                                df.at[idx, "Positions"] = (str(df.at[idx, "Positions"]) + " | " + pos_str).strip(" | ")
                                print(f"    ✅ Position ajoutée pour {callsign}")
                            df.at[idx, "Altitude (m)"], df.at[idx, "Evolution Verticale"] = altitude, trend
                            df.at[idx, "OpenSky State Info"] = opensky_state_raw # Met à jour la position raw aussi
                            updated = True
                            break
                    except: pass
                
                if not updated:
                    print(f"    🆕 Nouvel enregistrement pour {callsign}")
                    make, model, reg, db_info_raw = get_real_flight_info(icao24)
                    dep, arr, h_dep, h_arr, airlabs_raw, source, hexdb_route_raw = "Inconnu", "Inconnu", "--:--", "--:--", "", "OpenSky (Live)", ""
                    
                    al_data = get_flight_airlabs(icao24)
                    if al_data:
                        dep = al_data.get("dep_iata") or al_data.get("dep_icao") or "Inconnu"
                        arr = al_data.get("arr_iata") or al_data.get("arr_icao") or "Inconnu"
                        h_dep = al_data.get("dep_time") or "--:--"
                        h_arr = al_data.get("arr_time") or "--:--"
                        source = "AirLabs"
                        
                        # --- MODIFICATION DE LA CASCADE POUR LA COMPAGNIE ---
                        if not make or make == "Inconnu":
                            make = clean(al_data.get("airline_name"))
                            if not make: make = clean(al_data.get("airline_icao"))
                            if not make: make = clean(al_data.get("airline_iata"))
                        # ----------------------------------------------------
                        
                        if not model or model == "Inconnu": model = clean(al_data.get("model"))
                        if not reg or reg == "Inconnu": reg = clean(al_data.get("reg_number"))
                        airlabs_raw = json.dumps(al_data, ensure_ascii=False)
                    
                    if dep == "Inconnu":
                        hexdb_result = get_route_hexdb(callsign)
                        if hexdb_result: 
                            dep, arr, hexdb_route_raw = hexdb_result[0], hexdb_result[1], hexdb_result[2]
                            source = "hexdb"

                    new_entries.append({
                        "Date": now_dt.strftime("%d/%m/%Y"), "Heure": now_dt.strftime("%H:%M"),
                        "Identifiant Vol (Callsign)": callsign, "Compagnie": make, "Modèle Avion": model, "Immatriculation": reg,
                        "Identifiant Appareil (ICAO24)": icao24, "Altitude (m)": altitude, "Evolution Verticale": trend,
                        "De": resolve_airport(dep), "A": resolve_airport(arr), "Dep_H": h_dep, "Arr_H": h_arr,
                        "Source": source, "Planespotters": f'=HYPERLINK("https://www.planespotters.net/hex/{icao24.upper()}","{icao24.upper()}")',
                        "Positions": pos_str, "Airlabs Info": airlabs_raw,
                        "OpenSky State Info": opensky_state_raw,
                        "Hexdb Route Info": hexdb_route_raw,
                        "Aircraft DB Info": db_info_raw
                    })

            if new_entries or updated:
                df_final = pd.concat([df, pd.DataFrame(new_entries)], ignore_index=True)
                df_final = df_final.drop_duplicates(subset=["Date", "Heure", "Identifiant Vol (Callsign)"], keep="last").tail(2000).fillna("")
                conn.update(worksheet="Vols_Joinville", data=df_final)
                print(f"    💾 Google Sheet mis à jour.")
    except Exception as e:
        print(f"❌ ERREUR CRITIQUE : {e}")
        import traceback
        traceback.print_exc()

    print(f"\n💤 DÉCISION : {decision_reason}\n⏰ SOMMEIL : {next_sleep} secondes")
    return next_sleep

def main():
    print("=====================================================")
    print("DÉMARRAGE DU RADAR PRÉDICTIF INFINI (JOINVILLE)")
    print(f"Watch Zone: {BBOX_WATCH['lamin']}/{BBOX_WATCH['lamax']}")
    print("=====================================================")
    while True:
        wait_time = run_scan()
        print(f"Mise en veille. Prochain scan dans {wait_time}s...")
        time.sleep(wait_time)

if __name__ == "__main__":
    main()