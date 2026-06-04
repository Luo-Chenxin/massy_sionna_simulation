from pathlib import Path
import pandas as pd
from config import PRE_TRANSMITTER_FILENAME, TRANSMITTER_DIRECTORY, PRE_TRANSMITTER_FILE_ENCODE, FILE_ENCODE

def preprocess_massy_antenna_data(
    input_file: Path = PRE_TRANSMITTER_FILENAME,
    output_dir: Path = TRANSMITTER_DIRECTORY,
) -> None:
    """Clean antenna data and split it into files by frequency."""

    # Columns to change to numbers
    num_cols = ["Latitude", "Longitude", "height", "Hauteur en m"]

    # Target columns for the output Sionna files
    output_cols = [
        "Numéro de Station",
        "Numéro d'antenne",
        "Latitude",
        "Longitude",
        "height",
        "Azimut",
        "frequency",
    ]

    # ==========================================
    # 1. INITIALIZATION
    # ==========================================
    # Check if input file exists
    if not input_file.exists():
        print(f"Error: Cannot find file {input_file}")
        return

    # Create output folder if it does not exist
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Start processing: {input_file}")

    # ==========================================
    # 2. LOAD DATA & CLEAN HEADERS
    # ==========================================
    # Read CSV and drop 'Unnamed' columns
    df = pd.read_csv(
        input_file, encoding=PRE_TRANSMITTER_FILE_ENCODE, 
        usecols=lambda x: "Unnamed" not in str(x)
    )

    # Remove spaces from column names
    df.columns = df.columns.str.strip()

    # ==========================================
    # 3. PROCESS COORDINATES & HEIGHTS
    # ==========================================
    # Split 'Lat-Lon' column into 'Latitude' and 'Longitude'
    df[["Latitude", "Longitude"]] = df["Lat-Lon"].str.split(expand=True)

    # Convert columns to numbers
    for col in num_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # If 'height' is missing, use value from 'Hauteur en m'
    df["height"] = df["height"].fillna(df["Hauteur en m"])

    # ==========================================
    # 4. CLEAN FREQUENCY
    # ==========================================
    # Convert to string and lowercase
    df["frequency"] = df["frequency"].astype(str)
    freq_clean = df["frequency"].str.strip().str.lower()

    # Get only the numbers from the end of the string
    df["frequency_num_part"] = (
        freq_clean.str.extract(r"(\d+)\s*$", expand=False)
    ).fillna("unknown")

    # ==========================================
    # 5. GROUP BY FREQUENCY AND EXPORT
    # ==========================================
    # Keep only columns that exist in the dataframe
    output_cols = [col for col in output_cols if col in df.columns]

    # Group data by the frequency number
    for freq_num, group in df.groupby("frequency_num_part"):

        # Make a copy of the group data
        result_df = group[output_cols].copy()

        # Add 'ID' column at the beginning (starts from 1)
        result_df.insert(0, "ID", range(1, len(result_df) + 1))

        # Save to a new CSV file
        file_name = f"{freq_num}_mhz.csv"
        path_out = TRANSMITTER_DIRECTORY / file_name

        result_df.to_csv(path_out, index=False, encoding=FILE_ENCODE)
        print(f"Saved: {path_out} ({len(result_df)} rows)")

    print("\nAll done!")