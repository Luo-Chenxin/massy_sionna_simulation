from pathlib import Path
import pandas as pd

# ==========================================
# 1. PATH CONFIGURATION & INITIALIZATION
# ==========================================

base_dir = Path('data')
output_dir = base_dir / 'transimitters'
file_antenna = base_dir / 'bs_antenna_verify - Copy.csv'

# Ensure the output directory exists
output_dir.mkdir(parents=True, exist_ok=True)

# ==========================================
# 2. DATA LOADING & INITIAL CLEANING
# ==========================================

# Read data using CP1252 encoding and filter out ghost 'Unnamed' columns
df_antenna = pd.read_csv(file_antenna, encoding='cp1252', sep=',', usecols=lambda x: 'Unnamed' not in str(x))

# Strip leading and trailing whitespaces from structural column headers
df_antenna.columns = df_antenna.columns.str.strip()

# ==========================================
# 3. COORDINATE & HEIGHT PROCESSING
# ==========================================

# Parse the compound 'Lat-Lon' string column into separate Geodetic axes
df_antenna[['Latitude', 'Longitude']] = df_antenna['Lat-Lon'].str.split(expand=True)

# Cast coordinates and heights to floating-point numerics
df_antenna['Latitude'] = pd.to_numeric(df_antenna['Latitude'], errors='coerce')
df_antenna['Longitude'] = pd.to_numeric(df_antenna['Longitude'], errors='coerce')
df_antenna['height'] = pd.to_numeric(df_antenna['height'], errors='coerce')

# Cast the fallback field to floating-point numerics
df_antenna['Hauteur en m'] = pd.to_numeric(df_antenna['Hauteur en m'], errors='coerce')

# Fall back to origin height field ('Hauteur en m') where 'height' is NaN, 
df_antenna['height'] = df_antenna['height'].fillna(df_antenna['Hauteur en m'])

# ==========================================
# 4. FREQUENCY CLEANING & FILTERING
# ==========================================

# Ensure frequency is treated as string and handle NaN values safely
df_antenna["frequency"] = df_antenna["frequency"].astype(str)
df_antenna["frequency_clean"] = df_antenna["frequency"].str.strip().str.lower()
df_antenna["frequency_num_part"] = df_antenna["frequency_clean"].str.extract(r"(\d+)\s*$", expand=False)

# Add a boolean column "special_antenna"
# df_antenna["special_antenna"] = (df_antenna["frequency_clean"].str.contains("5g")) & (df_antenna["frequency_num_part"] == "3500")

# ==========================================
# 5. EXPORTING SUB-DATASETS FOR SIONNA
# ==========================================

# Define columns needed for sionna simulation (excluding ID for now)
sionna_cols = [
    'Numéro de Station', 
    'Numéro d\'antenne',
    'Latitude', 
    'Longitude', 
    'height', 
    'Azimut', 
    'frequency',
    # 'special_antenna',
]

# Iterate through each group based on the numeric part
for frequency_num, group in df_antenna.groupby("frequency_num_part"):
    
    # Extract columns and create a clean copy
    result_df = group[sionna_cols].copy()

    # Inject a 1-based auto-incrementing ID column at the first position (index 0)
    result_df.insert(0, 'ID', range(1, len(result_df) + 1))

    # Assign structured filenames matching categorized bands
    filename = f"{frequency_num}_mhz.csv"
    output_path = output_dir / filename
    
    # Save partitioned dataset with UTF-8-BOM to retain French diacritics natively in Excel/Pandas
    result_df.to_csv(output_path, index=False, encoding='utf-8-sig')
    print(f"The file has been saved: {output_path} (containing {len(result_df)} data entries).")

print("\nAll files have been processed.")