import argparse
import json
import streamlit as st
# On importe les deux fonctions de l'infinite_collector pour être ISO-code
from infinite_collector import get_flight_airlabs, get_flight_flightaware

def main():
    parser = argparse.ArgumentParser(description="Test AirLabs (via HEX) et FlightAware (via Callsign)")
    parser.add_argument("--hex", help="L'adresse ICAO24 hex de l'appareil (ex: 4AC94B)")
    parser.add_argument("--callsign", help="L'indicatif de vol / Callsign (ex: AFR123)")
    args = parser.parse_args()

    if not args.hex and not args.callsign:
        print("❌ Erreur : Vous devez fournir au moins --hex ou --callsign (ou les deux).")
        return

    print(f"\n{'='*60}")
    print(f"🧪 TEST MULTI-SOURCES : {args.callsign or '?' } / {args.hex or '?'}")
    print(f"{'='*60}")

    # --- 1. TEST AIRLABS (via HEX) ---
    if args.hex:
        print("\n--- SOURCE 1 : AIRLABS (Recherche via HEX) ---")
        airlabs_data = get_flight_airlabs(args.hex.strip().lower())
        if airlabs_data:
            print("✅ AIRLABS RÉPONSE :")
            print(json.dumps(airlabs_data, indent=2, ensure_ascii=False))
        else:
            print("❌ AIRLABS : Aucun résultat live pour cet hex.")
    else:
        print("\n--- SOURCE 1 : AIRLABS ---")
        print("⏩ Sautée (pas de --hex fourni)")

    # --- 2. TEST FLIGHTAWARE (via CALLSIGN) ---
    if args.callsign:
        print("\n--- SOURCE 2 : FLIGHTAWARE (Recherche via CALLSIGN) ---")
        fa_data = get_flight_flightaware(args.callsign.strip().upper())
        if fa_data:
            print("✅ FLIGHTAWARE RÉPONSE :")
            # fa_data["raw"] contient le JSON complet de l'AeroAPI
            print(json.dumps(fa_data, indent=2, ensure_ascii=False))
        else:
            print("❌ FLIGHTAWARE : Aucun résultat pour ce callsign.")
    else:
        print("\n--- SOURCE 2 : FLIGHTAWARE ---")
        print("⏩ Sautée (pas de --callsign fourni)")

    # --- RÉSUMÉ COMPARATIF ---
    print(f"\n{'='*60}")
    print("📊 BILAN DU TEST")
    print(f"{'='*60}")
    
    if args.hex and args.callsign:
        print(f"Test effectué pour l'appareil {args.hex.upper()} sur le vol {args.callsign.upper()}")
        print("Utilisez ces résultats pour vérifier laquelle des deux sources est la plus complète.")

if __name__ == "__main__":
    main()
