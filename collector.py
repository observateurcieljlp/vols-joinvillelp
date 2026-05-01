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


# ─── hexdb.io : route par callsign ────────────────────────────────────────────

def get_route_hexdb(callsign: str) -> tuple[str, str] | None:
    """
    Interroge hexdb.io pour obtenir l'origine et la destination d'un vol
    à partir de son callsign ICAO (ex: AFR1234).

    Retourne (dep_icao, arr_icao) ou None si introuvable.
    """
    cs = callsign.strip()
    if not cs or cs == "Inconnu":
        return None
    try:
        r = requests.get(
            f"https://hexdb.io/api/v1/route/icao/{cs}",
            timeout=5
        )
        if r.status_code == 200:
            route = r.json().get("route", "")   # format "ORIG-DEST"
            parts = route.split("-")
            if len(parts) == 2 and all(parts):
                return parts[0], parts[1]
    except Exception:
        pass
    return None


# ─── FR24 : fallback si hexdb ne trouve rien ──────────────────────────────────

def get_fr24_flights_in_area():
    try:
        bounds = f"{BBOX['lamax']},{BBOX['lamin']},{BBOX['lomin']},{BBOX['lomax']}"
        return fr_api.get_flights(bounds=bounds)
    except Exception:
        return []


def get_route_fr24(icao24: str, fr24_flights: list) -> tuple[str, str, str, str]:
    """
    Interroge FR24 pour un icao24 donné.
    Retourne (dep, arr, h_dep, h_arr).
    """
    dep, arr, h_dep, h_arr = "Inconnu", "Inconnu", "--:--", "--:--"
    try:
        flight = next(
            (f for f in fr24_flights
             if f.icao_24bit and f.icao_24bit.lower() == icao24.lower()),
            None
        )
        if flight:
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
    except Exception:
        pass
    return dep, arr, h_dep, h_arr


# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-mode", action="store_true",
                        help="Enrichit tous les avions sans filtre altitude")
    args = parser.parse_args()

    print("--- Scan Joinville (OpenSky + hexdb.io → FR24 fallback) ---")
    try:
        USER = st.secrets["OPENSKY_USER"].lower()
        PWD  = st.secrets["OPENSKY_PWD"]

        url = (
            f"https://opensky-network.org/api/states/all"
            f"?lamin={BBOX['lamin']}&lomin={BBOX['lomin']}"
            f"&lamax={BBOX['lamax']}&lomax={BBOX['lomax']}"
        )
        response = session.get(url, auth=(USER, PWD), timeout=30)

        if response.status_code != 200:
            print(f"Erreur OpenSky : {response.status_code}")
            return

        states = response.json().get("states") or []
        print(f"--- Analyse de {len(states)} avion(s) détecté(s) ---")

        vols_potentiels = []
        for avion in states:
            altitude = avion[13] or avion[7] or 0
            au_sol   = avion[8]
            callsign = str(avion[1]).strip() or "Inconnu"

            if args.test_mode or (not au_sol and altitude < ALTITUDE_MAX):
                tag = "[TEST]" if args.test_mode else "[Candidat]"
                print(f"  {tag} {callsign} ({int(altitude)} m) — éligible")
                vols_potentiels.append(avion)
            else:
                print(f"  [Ignoré] {callsign} ({int(altitude)} m) — trop haut ou au sol")

        if not vols_potentiels:
            print("Aucun vol éligible.")
            return

        # ── Lecture GSheets + migration schéma ────────────────────────────
        conn = st.connection("gsheets", type=GSheetsConnection)
        cols = [
            "Date", "Heure", "Identifiant Vol (Callsign)", "Compagnie",
            "Modèle Avion", "Immatriculation", "Identifiant Appareil (ICAO24)",
            "Altitude (m)", "De", "A", "Dep_H", "Arr_H", "Source",
        ]
        try:
            df_existant = conn.read(worksheet="Vols_Joinville", ttl=0)
            rename_map = {
                "Avion":    "Identifiant Vol (Callsign)",
                "icao24":   "Identifiant Appareil (ICAO24)",
                "Altitude": "Altitude (m)",
            }
            df_existant = df_existant.rename(columns=rename_map)
            for c in cols:
                if c not in df_existant.columns:
                    df_existant[c] = "Inconnu"
            df_existant = df_existant[cols]
        except Exception:
            df_existant = pd.DataFrame(columns=cols)

        # ── Filtre anti-doublon (15 min) ───────────────────────────────────
        now = datetime.now()
        vols_a_enrichir = []
        for avion in vols_potentiels:
            icao24    = avion[0]
            callsign  = str(avion[1]).strip() or "Inconnu"
            deja_vu   = False
            if not df_existant.empty:
                today_str = now.strftime("%d/%m/%Y")
                match = df_existant[
                    (df_existant["Identifiant Appareil (ICAO24)"] == icao24) &
                    (df_existant["Date"] == today_str)
                ]
                for _, row in match.iterrows():
                    try:
                        delta = abs(
                            (datetime.strptime(row["Heure"], "%H:%M") -
                             datetime.strptime(now.strftime("%H:%M"), "%H:%M")
                            ).total_seconds() / 60
                        )
                        if delta < 15:
                            deja_vu = True
                            break
                    except Exception:
                        pass
            if not deja_vu:
                vols_a_enrichir.append(avion)

        if not vols_a_enrichir:
            print("Tous les vols déjà enregistrés récemment.")
            return

        # ── FR24 : un seul appel groupé, seulement si nécessaire ──────────
        # On déclenche la récup FR24 en lazy (au premier fallback)
        fr24_flights_cache: list | None = None

        def get_fr24_lazy():
            nonlocal fr24_flights_cache
            if fr24_flights_cache is None:
                print("  → Appel FR24 groupé (fallback)...")
                fr24_flights_cache = get_fr24_flights_in_area()
            return fr24_flights_cache

        # ── Enrichissement vol par vol ─────────────────────────────────────
        nouveaux_vols = []
        for avion in vols_a_enrichir:
            icao24   = avion[0]
            callsign = str(avion[1]).strip() or "Inconnu"
            altitude = avion[13] or avion[7] or 0

            print(f"\n  ✈️  {callsign} ({icao24}) — {int(altitude)} m")

            # ── Infos appareil (toujours) ──────────────────────────────────
            make, model, reg = get_aircraft_info(icao24)

            # ── Route : hexdb en priorité, FR24 en fallback ────────────────
            hexdb_result = get_route_hexdb(callsign)
            if hexdb_result:
                dep, arr     = hexdb_result
                h_dep, h_arr = "--:--", "--:--"
                source       = "hexdb"
                print(f"    hexdb ✅  {dep} → {arr}")
            else:
                # FR24 chargé en lazy : un seul appel groupé pour tous les fallbacks
                print(f"    hexdb ❌  → appel FR24...")
                dep, arr, h_dep, h_arr = get_route_fr24(icao24, get_fr24_lazy())
                source = "FR24" if dep != "Inconnu" else "OpenSky (Live)"

            nouveaux_vols.append({
                "Date":                          now.strftime("%d/%m/%Y"),
                "Heure":                         now.strftime("%H:%M"),
                "Identifiant Vol (Callsign)":    callsign,
                "Compagnie":                     make,
                "Modèle Avion":                  model,
                "Immatriculation":               reg,
                "Identifiant Appareil (ICAO24)": icao24,
                "Altitude (m)":                  int(altitude),
                "De":                            dep,
                "A":                             arr,
                "Dep_H":                         h_dep,
                "Arr_H":                         h_arr,
                "Source":                        source,
            })

        # ── Sauvegarde ─────────────────────────────────────────────────────
        if nouveaux_vols:
            df_nouveaux = pd.DataFrame(nouveaux_vols)
            df_final = (
                pd.concat([df_existant, df_nouveaux], ignore_index=True)
                  .drop_duplicates(
                      subset=["Date", "Heure", "Identifiant Vol (Callsign)"],
                      keep="last"
                  )
                  .tail(2000)
                  .fillna("")
            )
            conn.update(worksheet="Vols_Joinville", data=df_final)
            print(f"\nSuccès : {len(nouveaux_vols)} passage(s) enregistré(s).")

    except Exception as e:
        print(f"Erreur : {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()