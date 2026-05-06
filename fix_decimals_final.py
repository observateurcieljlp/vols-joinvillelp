import os
import gspread
from google.oauth2.service_account import Credentials

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
    print("🚀 DÉMARRAGE DE LA RÉPARATION FINALE DES VIRGULES...")
    ws = get_worksheet()
    if not ws: return

    data = ws.get_all_values()
    header = data[0]
    rows = data[1:]
    col_map = {name: i for i, name in enumerate(header)}
    
    updates = []
    count = 0

    for i, row in enumerate(rows):
        row_num = i + 2
        new_row = list(row)
        changed = False

        for col_name in ["Lat", "Lon"]:
            idx = col_map[col_name]
            val = str(row[idx]).strip()
            
            # Si le nombre est un gros entier (ex: 488164 au lieu de 48,8164)
            if val.isdigit() and len(val) >= 5:
                # On insère la virgule après les 2 premiers chiffres (pour 48.xxx ou 02.xxx)
                # Note: La longitude peut commencer par un seul chiffre (ex: 2.xxx) 
                # mais ici on est sur du 2,xxxx donc souvent stocké 24567 -> 2,4567
                if col_name == "Lat": # Toujours 48.xxxx
                    new_val = val[:2] + "," + val[2:]
                else: # Lon: peut être 2.xxxx (stocké 24567) ou 02.xxxx
                    if val.startswith("2"): # Cas le plus courant à Joinville
                        new_val = val[0] + "," + val[1:]
                    else:
                        new_val = val[:2] + "," + val[2:]
                
                new_row[idx] = new_val
                changed = True
                print(f"  Ligne {row_num} [{col_name}]: {val} -> {new_val}")

        if changed:
            updates.append({'range': f'A{row_num}', 'values': [new_row]})
            count += 1

    if updates:
        print(f"💾 Application de {count} corrections...")
        ws.batch_update(updates, value_input_option='USER_ENTERED')
        print("✅ Terminé !")
    else:
        print("✨ Tout semble correct.")

if __name__ == "__main__":
    main()
