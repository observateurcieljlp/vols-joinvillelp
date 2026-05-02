import requests
import pandas as pd
import os
from datetime import datetime
import streamlit as st
from streamlit_gsheets import GSheetsConnection
from FlightRadar24 import FlightRadar24API
from utils_aircraft import refresh_aircraft_db, get_aircraft_info
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

pd.set_option('future.no_silent_downcasting', True)

fr_api = FlightRadar24API()
refresh_aircraft_db()

session = requests.Session()
retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
adapter = HTTPAdapter(max_retries=retry)
session.mount("https://", adapter)

BBOX = {"lamin": 48.75, "lamax": 48.90, "lomin": 2.35, "lomax": 2.60}
ALTITUDE_MAX = 3500


# --- hexdb.io : route par callsign -------------------------------------------

def get_route_hexdb(callsign: str) -> tuple[str, str] | None:
    cs = callsign.strip().upper()
    if not cs or cs == "INCONNU":
        return None

    url = f"https://hexdb.io/api/v1/route/icao/{cs}"
    try:
        print(f"    [API hexdb]     route -> {url}")
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            route = r.json().get("route", "")
            parts = route.split("-")
            if len(parts) == 2 and all(parts):
                return parts[0], parts[1]
        print(f"    [API hexdb]     route -> HTTP {r.status_code} (introuvable)")
    except Exception as e:
        print(f"    [API hexdb]     route -> exception : {e}")
    
    return None


def get_aircraft_hexdb(icao24: str) -> tuple[str, str, str]:
    try:
        url = f"https://hexdb.io/api/v1/aircraft/{icao24.lower()}"
        print(f"    [API hexdb]     aircraft -> {url}")
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            d = r.json()
            compagnie = d.get("RegisteredOwners", "")
            modele    = d.get("Type", "")
            immat     = d.get("Registration", "")
            return compagnie, modele, immat
        print(f"    [API hexdb]     aircraft -> HTTP {r.status_code}")
    except Exception as e:
        print(f"    [API hexdb]     aircraft -> exception : {e}")
    return "", "", ""


# --- hexdb.io : resolution nom d'aeroport (avec cache) -----------------------

_airport_cache: dict[str, str] = {}

def resolve_airport(code: str) -> str:
    code = code.strip().upper()
    if not code or code in ("INCONNU", "?", ""):
        return code

    if code in _airport_cache:
        print(f"    [CACHE airport] {code} -> {_airport_cache[code]}")
        return _airport_cache[code]

    try:
        if len(code) == 4:
            endpoint = f"https://hexdb.io/api/v1/airport/icao/{code}"
        else:
            endpoint = f"https://hexdb.io/api/v1/airport/iata/{code}"

        print(f"    [API hexdb]     airport -> {endpoint}")
        r = requests.get(endpoint, timeout=5)
        if r.status_code == 200:
            nom = r.json().get("airport", "").strip()
            for suffixe in (" Airport", " International Airport", " Intl", " International"):
                nom = nom.replace(suffixe, "")
            result = f"{nom} ({code})" if nom else code
            _airport_cache[code] = result
            return result
        print(f"    [API hexdb]     airport -> HTTP {r.status_code}")
    except Exception as e:
        print(f"    [API hexdb]     airport -> exception : {e}")

    _airport_cache[code] = code
    return code


# --- FR24 : fallback si hexdb ne trouve rien ----------------------------------

def get_fr24_flights_in_area():
    try:
        bounds = f"{BBOX['lamax']},{BBOX['lamin']},{BBOX['lomin']},{BBOX['lomax']}"
        print(f"    [API FR24]      scan zone -> bounds={bounds}")
        return fr_api.get_flights(bounds=bounds)
    except Exception as e:
        print(f"    [API FR24]      scan zone -> exception : {e}")
        return []


def get_route_fr24(icao24: str, fr24_flights: list) -> tuple[str, str, str, str]:
    dep, arr, h_dep, h_arr = "Inconnu", "Inconnu", "--:--", "--:--"
    
    # Neutralisation volontaire de la fonction (erreur 403)
    return dep, arr, h_dep, h_arr
    
    try:
        flight = next(
            (f for f in fr24_flights
             if f.icao_24bit and f.icao_24bit.lower() == icao24.lower()),
            None
        )
        if flight:
            print(f"    [API FR24]      details vol -> icao24={icao24}")
            details = fr_api.get_flight_details(flight)
            flight.set_flight_details(details)
            dep = flight.origin_airport_iata      if flight.origin_airport_iata      != "N/A" else "Inconnu"
            arr = flight.destination_airport_iata if flight.destination_airport_iata != "N/A" else "Inconnu"
            if details and "time" in details:
                time_info = details.get("time", {})
                if time_info.get("real", {}).get("departure"):
                    h_dep = datetime.fromtimestamp(time_info["real"]["departure"]).strftime("%H:%M")
                if time_info.get("estimated", {}).get("arrival"):
                    h_arr = datetime.fromtimestamp(time_info["estimated"]["arrival"]).strftime("%H:%M")
        else:
            print(f"    [API FR24]      icao24={icao24} non trouve dans les vols de la zone")
    except Exception as e:
        print(f"    [API FR24]      exception : {e}")
    return dep, arr, h_dep, h_arr


# --- Helper ------------------------------------------------------------------

def is_valid(*values) -> bool:
    """Retourne True si au moins une valeur est non-vide et non-inconnue."""
    INVALIDES = {"", "inconnu", "unknown", "n/a", "none", "null"}
    return any(
        str(v).strip().lower() not in INVALIDES
        for v in values
        if v is not None
    )


def clean(v) -> str:
    """Convertit None ou valeurs inconnues en chaine vide."""
    INVALIDES = {"inconnu", "unknown", "n/a", "none", "null"}
    s = str(v).strip() if v is not None else ""
    return "" if s.lower() in INVALIDES else s


# --- Main --------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-mode", action="store_true",
                        help="Enrichit tous les avions sans filtre altitude")
    args = parser.parse_args()

    print("--- Scan Joinville (OpenSky + hexdb.io -> FR24 fallback) ---")
    try:
        USER = st.secrets["OPENSKY_USER"].lower()
        PWD  = st.secrets["OPENSKY_PWD"]

        url = (
            f"https://opensky-network.org/api/states/all"
            f"?lamin={BBOX['lamin']}&lomin={BBOX['lomin']}"
            f"&lamax={BBOX['lamax']}&lomax={BBOX['lomax']}"
        )
        print(f"[API OpenSky]   scan zone -> {BBOX}")
        response = session.get(url, auth=(USER, PWD), timeout=30)

        if response.status_code != 200:
            print(f"[API OpenSky]   erreur HTTP {response.status_code}")
            return

        states = response.json().get("states") or []
        print(f"--- Analyse de {len(states)} avion(s) detecte(s) ---")

        vols_potentiels = []
        for avion in states:
            altitude = avion[13] or avion[7] or 0
            au_sol   = avion[8]
            callsign = str(avion[1]).strip() or "Inconnu"

            if args.test_mode or (not au_sol and altitude < ALTITUDE_MAX):
                tag = "[TEST]" if args.test_mode else "[Candidat]"
                print(f"  {tag} {callsign} ({int(altitude)} m) - eligible")
                vols_potentiels.append(avion)
            else:
                print(f"  [Ignore]    {callsign} ({int(altitude)} m) - trop haut ou au sol")

        if not vols_potentiels:
            print("Aucun vol eligible.")
            return

        # -- Lecture GSheets + migration schema --------------------------------
        conn = st.connection("gsheets", type=GSheetsConnection)
        cols = [
            "Date", "Heure", "Identifiant Vol (Callsign)", "Compagnie",
            "Modele Avion", "Immatriculation", "Identifiant Appareil (ICAO24)",
            "Altitude (m)", "Evolution Verticale", "De", "A", "Dep_H", "Arr_H", 
            "Source", "Planespotters", "Positions"
        ]
        try:
            print("[GSheets]       lecture feuille Vols_Joinville...")
            df_existant = conn.read(worksheet="Vols_Joinville", ttl=0)
            rename_map = {
                "Avion":    "Identifiant Vol (Callsign)",
                "icao24":   "Identifiant Appareil (ICAO24)",
                "Altitude": "Altitude (m)",
            }
            df_existant = df_existant.rename(columns=rename_map)
            for c in cols:
                if c not in df_existant.columns:
                    df_existant[c] = ""
            df_existant = df_existant[cols]
            print(f"[GSheets]       {len(df_existant)} ligne(s) existante(s) chargee(s)")
        except Exception as e:
            print(f"[GSheets]       impossible de lire la feuille ({e}) -> demarrage a vide")
            df_existant = pd.DataFrame(columns=cols)

        # -- Analyse et Filtrage --------------------------------------
        now = datetime.now()
        vols_a_enrichir = []
        updated_any = False

        for avion in vols_potentiels:
            icao24    = avion[0]
            callsign  = str(avion[1]).strip() or "Inconnu"
            lat, lon  = avion[6], avion[5]
            v_rate    = avion[11] or 0
            altitude  = avion[13] or avion[7] or 0
            
            # Tendance verticale
            if v_rate > 0.5: trend = "⬆️ Montée"
            elif v_rate < -0.5: trend = "⬇️ Descente"
            else: trend = "➡️ Stable"
            
            pos_str = f"({lat:.4f}, {lon:.4f})"
            
            deja_vu = False
            if not df_existant.empty:
                today_str = now.strftime("%d/%m/%Y")
                match = df_existant[
                    (df_existant["Identifiant Appareil (ICAO24)"] == icao24) &
                    (df_existant["Date"] == today_str)
                ]
                for idx in match.index:
                    try:
                        row_heure = df_existant.at[idx, "Heure"]
                        delta = abs((datetime.strptime(row_heure, "%H:%M") - datetime.strptime(now.strftime("%H:%M"), "%H:%M")).total_seconds() / 60)
                        
                        if delta < 15:
                            deja_vu = True
                            # Mise à jour des positions (on ajoute si pas déjà là)
                            current_positions = str(df_existant.at[idx, "Positions"])
                            if pos_str not in current_positions:
                                df_existant.at[idx, "Positions"] = (current_positions + " | " + pos_str).strip(" | ")
                            
                            # Mise à jour altitude et tendance
                            df_existant.at[idx, "Altitude (m)"] = int(altitude)
                            df_existant.at[idx, "Evolution Verticale"] = trend
                            updated_any = True
                            print(f"  [Update]    {callsign} ({icao24}) - nouvelle position ajoutee")
                            break
                    except Exception: pass

            if not deja_vu:
                # On stocke l'avion avec ses infos de position pour l'enrichissement
                vols_a_enrichir.append((avion, pos_str, trend))

        if not vols_a_enrichir and not updated_any:
            print("Aucune nouvelle donnee a enregistrer.")
            return

        # -- FR24 : un seul appel groupe, seulement si necessaire -------------
        fr24_flights_cache: list | None = None
        def get_fr24_lazy():
            nonlocal fr24_flights_cache
            if fr24_flights_cache is None:
                fr24_flights_cache = get_fr24_flights_in_area()
            return fr24_flights_cache

        # -- Enrichissement des nouveaux vols ----------------------------------
        nouveaux_vols = []
        for avion, pos_str, trend in vols_a_enrichir:
            icao24   = avion[0]
            callsign = str(avion[1]).strip() or "Inconnu"
            altitude = avion[13] or avion[7] or 0

            print(f"\n  [NOUVEAU] {callsign} ({icao24}) - {int(altitude)} m")

            # -- Cascade de fallbacks pour l'appareil --
            raw_make, raw_model, raw_reg = get_aircraft_info(icao24)
            make, model, reg = clean(raw_make), clean(raw_model), clean(raw_reg)

            if not make or not model or not reg:
                hx_make, hx_model, hx_reg = get_aircraft_hexdb(icao24)
                if not make: make = clean(hx_make)
                if not model: model = clean(hx_model)
                if not reg: reg = clean(hx_reg)
                
                if not make or not model:
                    ps_make, ps_model = get_aircraft_planespotters(icao24)
                    if not make: make = clean(ps_make)
                    if not model: model = clean(ps_model)

            # -- Route --
            hexdb_result = get_route_hexdb(callsign)
            if hexdb_result:
                dep, arr, h_dep, h_arr, source = hexdb_result[0], hexdb_result[1], "--:--", "--:--", "hexdb"
            else:
                dep, arr, h_dep, h_arr = "Inconnu", "Inconnu", "--:--", "--:--"
                source = "OpenSky (Live)"

            nouveaux_vols.append({
                "Date":                          now.strftime("%d/%m/%Y"),
                "Heure":                         now.strftime("%H:%M"),
                "Identifiant Vol (Callsign)":    callsign,
                "Compagnie":                     make,
                "Modele Avion":                  model,
                "Immatriculation":               reg,
                "Identifiant Appareil (ICAO24)": icao24,
                "Altitude (m)":                  int(altitude),
                "Evolution Verticale":           trend,
                "De":                            resolve_airport(dep),
                "A":                             resolve_airport(arr),
                "Dep_H":                         h_dep,
                "Arr_H":                         h_arr,
                "Source":                        source,
                "Planespotters":                 f'=HYPERLINK("https://www.planespotters.net/hex/{icao24.upper()}","{icao24.upper()}")',
                "Positions":                     pos_str,
            })

        # -- Sauvegarde finale ------------------------------------------------
        df_final = pd.concat([df_existant, pd.DataFrame(nouveaux_vols)], ignore_index=True)
        df_final = df_final.drop_duplicates(subset=["Date", "Heure", "Identifiant Vol (Callsign)"], keep="last").tail(2000).fillna("")
        
        print(f"\n[GSheets]       mise à jour de la base...")
        conn.update(worksheet="Vols_Joinville", data=df_final)
        print(f"[GSheets]       Terminé.")

    except Exception as e:
        print(f"Erreur fatale : {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()