from pathlib import Path

SCENE_FILENAME = Path('data/blender/massy.xml')
TERRAIN_FILENAME = Path('data/blender/terrain.xml')  # Optional: if it is None, we use ALT_ORIGIN to get terrain height
FREQUENCY = 3500  # When use massy map, only supports: 700, 800, 900, 1800, 2100, 2600, 3500
FREQUENCY_UNIT='mhz' # Only supports: hz, mhz, ghz; when use massp map, only supports mhz
TRANSMITTER_DIRECTORY = Path('data/transmitters')
RECEIVER_FILENAME = Path('data/sensor_location.csv')
PRE_TRANSMITTER_FILENAME = Path('data/bs_antenna_verify - Copy.csv')
PRE_TRANSMITTER_FILE_ENCODE = 'cp1252'
FILE_ENCODE = 'utf-8-sig'

ANTENNES_INFO_FILENAME = Path('data/paris_whole/Antennes_Emetteurs_Bandes_Cartoradio.csv')
ANTENNES_LOC_FILENAME = Path('data/paris_whole/Sites_Cartoradio.csv')
FILTER_FREQUENCE = 2600
FILTER_POSTAL_CODE = r'^75\d{3}$'

# Define the geographical boundary of the target region
# This is for massy
# LAT_MAX, LAT_MIN = 48.7409, 48.7171
# LON_MIN, LON_MAX = 2.2451, 2.3013
# This is for Paris
# I take the max and min latitude and longitude of transmitters and extend it with 500m
# Extended Formula:
# latitude 1° is approximately equal to 111,000m
# 500 / 111 000 ≈ 0.004505
# 48.90138888888889 + 0.004505 ≈ 48.9059; 48.90138888888889 - 0.004505 ≈ 48.8969
# longitude 1° is approximately equal to $\Delta_{lon} =\Delta_{lat} \times \cos(lat)$
# 111 000 * cos((48.9014 + 48.8183)/2) ≈ 111 000 * cos(48.86) ≈ 111 000 * 0.6579 ≈ 73 000
# 500 / 73 000 ≈ 0.006849
# 2.249722222222222 - 0.006849 ≈ 2.2429 ; 2.450555555555556 + 0.006849 ≈ 2.4574
LAT_MAX, LAT_MIN = 48.9059, 48.8969
LON_MIN, LON_MAX = 2.2429, 2.4574

# Calculate the center origin point of the scene
LAT_ORIGIN=(LAT_MAX + LAT_MIN)/2
LON_ORIGIN=(LON_MIN + LON_MAX)/2
# This is the altitude of center point relative to the ground in massy map
ALT_ORIGIN=-42

DEFAULT_AZIMUTH_MUTIPLITER=1
DEFAULT_OFF_SET=0


# Material Database (Template)
# You can add your own materials here
# Note: "freshwater" does not use a,b,c,d parameters. It uses a specific function instead.
MATERIAL_DATABASE = {
    "asphalt_concrete": {
        "type": "itur_abcd",
        "a": 4.83, "b": 0.0, "c": 0.0108, "d": 1.3969,
        "f_min": 1.0, "f_max": 40.0,
        "color": (0.12, 0.12, 0.13), # Dark gray/asphalt
    },
    "freshwater": {
        "type": "itu_p527",
        "f_min": 0.1,  # Set your preferred minimum frequency in GHz
        "f_max": 1000.0, # Set your preferred maximum frequency in GHz
        "color": (0.00, 0.15, 0.75), # Deep blue water
    },
}

DEFAULT_SALINITY = 0.5   # Unit: g/kg or ppt
DEFAULT_TEMPERATURE = 20.0  # Unit: celsius