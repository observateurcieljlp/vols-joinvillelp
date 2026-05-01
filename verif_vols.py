from utils_aircraft import get_aircraft_info

# L'ID technique de ton SAH59P (trouvé tout à l'heure)
TEST_ICAO = "4AC94B"

def verif():
    print(f"Vérification de l'avion {TEST_ICAO} dans la base OpenSky...")
    make, model, reg = get_aircraft_info(TEST_ICAO)
    print(f"Résultat trouvé dans la base locale :")
    print(f" - Compagnie : {make}")
    print(f" - Modèle   : {model}")
    print(f" - Immat    : {reg}")

if __name__ == "__main__":
    verif()
