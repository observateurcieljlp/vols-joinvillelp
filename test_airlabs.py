import argparse
import json
import requests
import streamlit as st
from infinite_collector import get_flight_airlabs

def get_fr24_bypass(icao24):
    """Tentative de récupération via l'API interne 'clickback' de FR24."""
    url = f"https://data-live.flightradar24.com/clickback/v1/data.json?flight={icao24.lower()}"
    
    # On utilise une Session pour retenir les cookies (très important pour Cloudflare)
    session = requests.Session()
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.flightradar24.com/",
        "Origin": "https://www.flightradar24.com",
        "Sec-Ch-Ua": '"Not A(Brand";v="99", "Google Chrome";v="121", "Chromium";v="121"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site"
    }
    
    try:
        print(f"    [API FR24]      requête -> {url}")
        # Une première requête "à vide" sur la page d'accueil pour récupérer le cookie d'autorisation
        session.get("https://www.flightradar24.com", headers=headers, timeout=5)
        # Puis la vraie requête
        res = session.get(url, headers=headers, timeout=5)
        
        if res.status_code == 200:
            return res.json()
        else:
            print(f"    [API FR24]      échec HTTP {res.status_code}")
            print(f"    [API FR24]      Contenu brut (premiers 100 char) : {res.text[:100]}")
    except Exception as e:
        print(f"    [API FR24]      erreur lors de l'appel : {e}")
    return None

def main():
    parser = argparse.ArgumentParser(description="Comparaison AirLabs vs FlightRadar24 via ICAO24 (hex)")
    parser.add_argument("--hex", required=True, help="L'adresse ICAO24 hex de l'appareil (ex: 4AC94B)")
    args = parser.parse_args()

    icao24 = args.hex.strip().lower()
    
    print(f"\n{'='*60}")
    print(f"🧪 TEST MULTI-SOURCES : {icao24.upper()}")
    print(f"{'='*60}")

    # --- 1. TEST AIRLABS ---
    print("\n--- SOURCE 1 : AIRLABS ---")
    airlabs_data = get_flight_airlabs(icao24)
    if airlabs_data:
        print("✅ AIRLABS RÉPONSE :")
        print(json.dumps(airlabs_data, indent=2, ensure_ascii=False))
    else:
        print("❌ AIRLABS : Aucun résultat")

    # --- 2. TEST FR24 BYPASS ---
    print("\n--- SOURCE 2 : FLIGHTRADAR24 (BYPASS) ---")
    fr24_data = get_fr24_bypass(icao24)
    if fr24_data:
        print("✅ FR24 RÉPONSE :")
        print(json.dumps(fr24_data, indent=2, ensure_ascii=False))
    else:
        print("❌ FR24 : Aucun résultat")

    # --- RÉSUMÉ COMPARATIF ---
    print(f"\n{'='*60}")
    print("📊 RÉSUMÉ COMPARATIF")
    print(f"{'='*60}")
    
    # AirLabs Summary
    if airlabs_data:
        al_route = f"{airlabs_data.get('dep_iata','?')} -> {airlabs_data.get('arr_iata','?')}"
        al_flight = airlabs_data.get('flight_icao', '?')
    else:
        al_route, al_flight = "N/A", "N/A"

    # FR24 Summary
    if fr24_data and 'result' in fr24_data and 'request' in fr24_data['result']:
        # Note: FR24 clickback structure can be deeply nested
        fr_info = fr24_data.get('result', {}).get('response', {}).get('data', {})
        # On essaie d'extraire les aéroports si possible
        fr_route = "Structure complexe (voir JSON)"
        fr_flight = "?"
    else:
        fr_route, fr_flight = "N/A", "N/A"

    print(f"AirLabs : Vol {al_flight} | Route {al_route}")
    print(f"FR24     : Données disponibles: {'OUI' if fr24_data else 'NON'}")

if __name__ == "__main__":
    main()
