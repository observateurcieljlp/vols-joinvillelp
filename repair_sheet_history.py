import os
import json
import gspread
import re
import sqlite3
from google.oauth2.service_account import Credentials
from utils_aircraft import get_aircraft_info

CONFIG = {}
def load_config():
    global CONFIG
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
                            CONFIG[key] = val
        except Exception as e:
            print(f"⚠️ Erreur lecture secrets.toml : {e}")
    if os.path.exists("config.json"):
        try:
            with open("config.json", "r") as f:
                CONFIG.update(json.load(f))
        except: pass
    for key in ["GOOGLE_SHEET_NAME", "spreadsheet"]:
        if key not in CONFIG:
            CONFIG[key] = os.environ.get(key, "")

def get_gsheet_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    if os.path.exists("service_account.json"):
        creds = Credentials.from_service_account_file("service_account.json", scopes=scope)
        return gspread.authorize(creds)
    return None

def format_coord(val, col_name):
    if val is None or str(val).strip() == "":
        return ""
    val_str = str(val).strip()
    
    is_neg = False
    if val_str.startswith("-"):
        is_neg = True
        val_str = val_str[1:]
        
    if val_str.isdigit() and len(val_str) >= 5:
        if col_name == "Lat":
            val_str = val_str[:2] + "," + val_str[2:]
        elif col_name == "Lon":
            if val_str.startswith("2") or val_str.startswith("0"):
                val_str = val_str[0] + "," + val_str[1:]
            else:
                val_str = val_str[:2] + "," + val_str[2:]
                
    if is_neg:
        val_str = "-" + val_str
        
    try:
        f_val = float(val_str.replace(",", "."))
        return f"{f_val:.4f}".replace(".", ",")
    except:
        return val_str

def get_registration_fallback(icao24, row, col_map):
    # 1. Tenter via utils_aircraft
    try:
        _, _, real_reg, _ = get_aircraft_info(icao24)
        if real_reg and real_reg != "Inconnu" and "E+" not in real_reg.upper():
            return real_reg
    except: pass

    # 2. Tenter via Hexdb Aircraft Info (JSON dans la ligne)
    hex_info_idx = col_map.get("Hexdb Aircraft Info")
    if hex_info_idx is not None and hex_info_idx < len(row):
        hex_raw = row[hex_info_idx]
        if hex_raw and hex_raw.strip().startswith("{"):
            try:
                data = json.loads(hex_raw)
                reg = data.get("Registration")
                if reg and "E+" not in reg.upper():
                    return reg
            except: pass

    # 3. Tenter via Planespotters Info
    ps_info_idx = col_map.get("Planespotters Info")
    if ps_info_idx is not None and ps_info_idx < len(row):
        ps_raw = row[ps_info_idx]
        if ps_raw and ps_raw.strip().startswith("{"):
            try:
                data = json.loads(ps_raw)
                if data.get("photos"):
                    reg = data["photos"][0].get("registration")
                    if reg and "E+" not in reg.upper():
                        return reg
            except: pass
            
    # 4. Tenter via Aircraft DB Info
    ac_db_idx = col_map.get("Aircraft DB Info")
    if ac_db_idx is not None and ac_db_idx < len(row):
        ac_raw = row[ac_db_idx]
        if ac_raw and ac_raw.strip().startswith("{"):
            try:
                data = json.loads(ac_raw)
                reg = data.get("registration")
                if reg and "E+" not in reg.upper():
                    return reg
            except: pass

    return None

def process_worksheet(sh, worksheet_name):
    print(f"\n--- ⏳ Traitement de l'onglet : {worksheet_name} ---")
    try:
        ws = sh.worksheet(worksheet_name)
    except Exception as e:
        print(f"⚠️ Impossible de trouver l'onglet {worksheet_name} : {e}")
        return
        
    data = ws.get_all_values()
    if len(data) <= 1:
        print("   Onglet vide.")
        return
        
    header = data[0]
    rows = data[1:]
    col_map = {name: i for i, name in enumerate(header)}
    
    updates = []
    fixed_coords = 0
    fixed_immats = 0
    protected_fields = 0
    
    for i, row in enumerate(rows):
        row_num = i + 2
        new_row = list(row)
        changed = False
        
        # 1. Correction Lat/Lon
        for col_name in ["Lat", "Lon"]:
            idx = col_map.get(col_name)
            if idx is not None and idx < len(row):
                orig_val = row[idx]
                new_val = format_coord(orig_val, col_name)
                if orig_val != new_val:
                    new_row[idx] = new_val
                    changed = True
                    fixed_coords += 1
                    
        # 2. Correction Immatriculation si corrompue (contenant E+)
        immat_idx = col_map.get("Immatriculation")
        icao_idx = col_map.get("Identifiant Appareil (ICAO24)")
        if immat_idx is not None and immat_idx < len(row):
            orig_immat = row[immat_idx]
            if "E+" in orig_immat.upper() or "E-" in orig_immat.upper() or not orig_immat:
                icao = row[icao_idx] if (icao_idx is not None and icao_idx < len(row)) else ""
                if icao:
                    real_immat = get_registration_fallback(icao, row, col_map)
                    if real_immat:
                        new_row[immat_idx] = real_immat
                        changed = True
                        fixed_immats += 1
                        print(f"  [Ligne {row_num}] Immatriculation corrigée pour {icao} : {orig_immat} -> {real_immat}")
                        
        # 3. Protection Texte des colonnes identifiantes (Apostrophe devant pour éviter toute notation scientifique ultérieure)
        for col_name in ["Identifiant Vol (Callsign)", "Immatriculation", "Identifiant Appareil (ICAO24)"]:
            idx = col_map.get(col_name)
            if idx is not None and idx < len(new_row):
                val_str = str(new_row[idx]).strip()
                if val_str and not val_str.startswith("'"):
                    new_row[idx] = "'" + val_str
                    changed = True
                    protected_fields += 1
                    
        if changed:
            updates.append({'range': f'A{row_num}', 'values': [new_row]})
            
    if updates:
        print(f"💾 Envoi de {len(updates)} corrections de lignes vers Google Sheets...")
        try:
            ws.batch_update(updates, value_input_option='USER_ENTERED')
            print(f"✅ Onglet {worksheet_name} mis à jour avec succès :")
            print(f"   - {fixed_coords} coordonnées formatées/réparées.")
            print(f"   - {fixed_immats} immatriculations corrompues restaurées.")
            print(f"   - {protected_fields} champs d'identifiants protégés comme texte.")
        except Exception as e:
            print(f"❌ Erreur lors de la mise à jour : {e}")
    else:
        print("✨ Aucun problème détecté dans cet onglet.")

def main():
    print("🚀 DÉMARRAGE DU SCRIPT DE RÉPARATION HISTORIQUE DE FORMAT...")
    load_config()
    client = get_gsheet_client()
    if not client:
        print("❌ Impossible de s'authentifier auprès de Google Sheets.")
        return
        
    sheet_url = CONFIG.get("spreadsheet")
    try:
        sh = client.open_by_url(sheet_url) if sheet_url else client.open(CONFIG.get("GOOGLE_SHEET_NAME", "Radar_Joinville"))
        print(f"📂 Classeur ouvert : {sh.title}")
    except Exception as e:
        print(f"❌ Impossible d'ouvrir le classeur Google Sheets : {e}")
        return
        
    for name in ["Vols_Joinville", "Vols_Pessac"]:
        process_worksheet(sh, name)
        
    print("\n🎉 Processus de réparation de l'historique terminé.")

if __name__ == "__main__":
    main()
