import os
import json
import gspread
from google.oauth2.service_account import Credentials
import re

def load_config():
    config = {}
    secrets_path = os.path.join(".streamlit", "secrets.toml")
    if os.path.exists(secrets_path):
        try:
            with open(secrets_path, "r") as f:
                current_section = None
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"): continue
                    if line.startswith("[") and line.endswith("]"):
                        current_section = line[1:-1].strip()
                        continue
                    if "=" in line:
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip().strip('"').strip("'")
                        if not current_section or current_section == "connections.gsheets":
                            config[key] = val
        except: pass
    return config

def get_worksheet():
    config = load_config()
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if os.path.exists("service_account.json"):
        creds = Credentials.from_service_account_file("service_account.json", scopes=scope)
        client = gspread.authorize(creds)
        sheet_url = config.get("spreadsheet")
        sh = client.open_by_url(sheet_url) if sheet_url else client.open(config.get("GOOGLE_SHEET_NAME", "Radar_Joinville"))
        return sh.worksheet("Vols_Joinville")
    return None

def main():
    print("🚀 DÉMARRAGE DU FIXER DE FORMATTAGE (RETOUR VIRGULES)...")
    ws = get_worksheet()
    if not ws:
        print("❌ Impossible d'accéder à la feuille.")
        return

    data = ws.get_all_values()
    if len(data) <= 1:
        print("Base vide.")
        return

    header = data[0]
    rows = data[1:]
    col_map = {name: i for i, name in enumerate(header)}
    
    updates = []
    fixed_count = 0

    for i, row in enumerate(rows):
        row_num = i + 2
        new_row = list(row)
        changed = False

        # 1. Correction Lat/Lon via la PREMIÈRE position de la trace (avec protection virgule)
        positions_str = row[col_map["Positions"]]
        match = re.search(r"\((\d+\.\d+),\s*(\d+\.\d+),", positions_str)
        if match:
            # On récupère les valeurs brutes du JSON/Trace (qui sont en points)
            first_lat_val = float(match.group(1))
            first_lon_val = float(match.group(2))
            
            # On formate en texte avec virgule et une apostrophe pour forcer le texte dans GSheets
            # ou on s'assure que GSheets comprenne bien le format.
            new_lat = f"{first_lat_val:.4f}".replace(".", ",")
            new_lon = f"{first_lon_val:.4f}".replace(".", ",")
            
            if row[col_map["Lat"]] != new_lat:
                new_row[col_map["Lat"]] = new_lat
                changed = True
            if row[col_map["Lon"]] != new_lon:
                new_row[col_map["Lon"]] = new_lon
                changed = True

        # 2. Correction Altitude via OpenSky
        os_info_raw = row[col_map["OpenSky State Info"]]
        if os_info_raw and os_info_raw.startswith("["):
            try:
                os_data = json.loads(os_info_raw)
                alt_val = os_data[13] if (len(os_data) > 13 and os_data[13]) else os_data[7]
                if alt_val:
                    new_alt = str(int(alt_val))
                    if row[col_map["Altitude (m)"]] != new_alt:
                        new_row[col_map["Altitude (m)"]] = new_alt
                        changed = True
            except: pass

        if changed:
            updates.append({'range': f'A{row_num}', 'values': [new_row]})
            fixed_count += 1

    if updates:
        print(f"💾 Application de {len(updates)} corrections de formatage...")
        # On utilise RAW pour éviter que GSheets n'interprète mal les nombres
        ws.batch_update(updates, value_input_option='USER_ENTERED')
        print(f"✅ Terminé ! {fixed_count} lignes ont été réparées.")
    else:
        print("✨ Aucun problème de formatage détecté.")

if __name__ == "__main__":
    main()
