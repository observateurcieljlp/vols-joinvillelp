# Script pour télécharger la base OpenSky une fois
import requests
import pandas as pd
import zipfile
import io

def get_aircraft_db():
    url = "https://opensky-network.org/datasets/metadata/aircraftDatabase.csv"
    # C'est une base statique, on peut la charger en mémoire ou en fichier
    # Pour le collector, on peut l'avoir en local
    df = pd.read_csv(url)
    return df
