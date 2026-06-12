"""
Main pipeline for generating radio map dataset from OSM data and transmitter information.

This script processes geographical blocks to:
1. Generate 3D scene XML files with terrain, buildings, and other features
2. Compute radio maps using Sionna ray tracing
3. Rasterize building footprints and transmitter locations
4. Store all results in HDF5 format

Usage:
    python main.py [--steps STEPS] [--log-dir LOG_DIR]
"""

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

import pandas as pd
import sionna.rt as rt

from config import FILE_ENCODE, LocalCRS
from utils.scene_utils import add_txs
from utils.geo_coords import SceneCoordinateConverter
from utils.preprocess_raw_data import preprocess_antenna_data_by_frequency_and_postal
from utils.map_splitter import TileSplitter
from utils.osm_fetcher import OSMFetcher, OSM_TAGS
from utils.osm_to_ply import OSMToPLY, generate_flat_terrain_ply
from utils.generate_xml import SionnaXMLGenerator
from utils.generate_radiomap import RadioMapGenerator
from utils.h5_manager import H5Manager
from utils.building_rasterizer import BuildingRasterizer
from utils.transmitter_mapper import TransmitterMapper


# =============================================================================
# Configuration
# =============================================================================

# Define the geographical boundary of the target region:
# Extended Formula:
# latitude 1° is approximately equal to 111,000m
# 500 / 111 000 ≈ 0.004505
# 48.90138888888889 + 0.004505 ≈ 48.9059; 48.818333333333335 - 0.004505 ≈ 48.8138
# longitude 1° is approximately equal to Δ_lon = Δ_lat × cos(lat)
# 111 000 * cos((48.9014 + 48.8183)/2) ≈ 111 000 * cos(48.86) ≈ 111 000 * 0.6579 ≈ 73 000
# 500 / 73 000 ≈ 0.006849
# 2.249722222222222 - 0.006849 ≈ 2.2429 ; 2.450555555555556 + 0.006849 ≈ 2.4574
LAT_MAX, LAT_MIN = 48.9059, 48.8138
LON_MIN, LON_MAX = 2.2429, 2.4574

# Calculate the center origin point of the scene
LAT_ORIGIN = (LAT_MAX + LAT_MIN) / 2
LON_ORIGIN = (LON_MIN + LON_MAX) / 2

# Paths
TRANSMITTER_DIRECTORY = Path('data/transmitters')
DATASET_DIR = Path('data/dataset')
XML_DIR = Path('data/xml')
LOG_DIR = Path('data/log')

# Antenna data
ANTENNES_INFO_FILENAME = Path('data/paris/Antennes_Emetteurs_Bandes_Cartoradio.csv')
ANTENNES_LOC_FILENAME = Path('data/paris/Sites_Cartoradio.csv')
FILTER_FREQUENCE = 2600
FILTER_POSTAL_CODE = r'^75\d{3}$'

# Scene parameters
TERRAIN_RESOLUTION = 10.0  # Grid cell size in meters
TERRAIN_HEIGHT = 0.0       # Constant Z height for terrain vertices
BLOCK_SIZE_M = 2560        # Block size in meters
OVERLAP_M = 150            # Overlap between blocks in meters

# Radio map parameters
TRANSMITTER_PATH = Path('data/transmitters/2600_mhz.csv')
FREQUENCY = 2.6e9          # Carrier frequency in Hz (2.6 GHz)
RM_RESOLUTION_M = 1.0      # Radio map cell size in meters

# Antenna Array (single-element cross-polarized following TR 38.901 pattern)
TX_ARRAY = rt.PlanarArray(
    num_rows=1,
    num_cols=1,
    vertical_spacing=0.5,
    horizontal_spacing=0.5,
    pattern="tr38901",
    polarization="cross",
)

# Layer Definitions
# Each layer maps an OSM tag set to its output PLY filename and default height.
# Buildings use 'drop' mode: polygons without height info are skipped.
# Other layers use default_height=0.0 (flat geometry at ground level).
LAYERS = [
    {
        "tag_name": "buildings",
        "ply_filename": "buildings.ply",
        "default_height": 0.0,       # Not used for buildings (see handle_missing_height)
        "handle_missing_height": "drop",
    },
    {
        "tag_name": "roads",
        "ply_filename": "roads.ply",
        "default_height": 0.1,
        "handle_missing_height": "use_default",
    },
    {
        "tag_name": "railways",
        "ply_filename": "railways.ply",
        "default_height": 0.5,
        "handle_missing_height": "use_default",
    },
    {
        "tag_name": "water",
        "ply_filename": "water.ply",
        "default_height": 0.2,
        "handle_missing_height": "use_default",
    },
    {
        "tag_name": "forest",
        "ply_filename": "forest.ply",
        "default_height": 2.0,
        "handle_missing_height": "use_default",
    },
]


# =============================================================================
# Logging Setup
# =============================================================================

def setup_logging(log_dir: Path) -> logging.Logger:
    """
    Configure logging to both file and console.
    
    Args:
        log_dir: Directory for log files
        
    Returns:
        Configured logger instance
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"pipeline_{timestamp}.log"
    
    # Create logger
    logger = logging.getLogger("RadioMapPipeline")
    logger.setLevel(logging.DEBUG)
    
    # File handler - detailed logging
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(file_formatter)
    
    # Console handler - info level and above
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    logger.info(f"Log file created: {log_file}")
    return logger


# =============================================================================
# OSM Data Manager
# =============================================================================

class OSMDataManager:
    """
    Manages OSM data fetching and caching to avoid redundant API calls.
    
    This class ensures that OSM data for each block is fetched only once
    and can be reused across multiple pipeline steps.
    """
    
    def __init__(self):
        """Initialize the OSM data cache."""
        self._cache: Dict[str, OSMFetcher] = {}
    
    def get_fetcher(self, block_name: str, bbox: tuple) -> OSMFetcher:
        """
        Get or create an OSMFetcher for a specific block.
        
        Args:
            block_name: Unique identifier for the block
            bbox: Bounding box tuple (lon_min, lat_min, lon_max, lat_max)
            
        Returns:
            Cached or newly created OSMFetcher instance
        """
        if block_name not in self._cache:
            self._cache[block_name] = OSMFetcher(
                bbox=bbox,
                tags=OSM_TAGS["complete"],
            )
        return self._cache[block_name]
    
    def get_buildings_gdf(self, block_name: str, bbox: tuple):
        """
        Get building footprints GeoDataFrame from cache or fetch if needed.
        
        Args:
            block_name: Unique identifier for the block
            bbox: Bounding box tuple (lon_min, lat_min, lon_max, lat_max)
            
        Returns:
            GeoDataFrame with building footprints
        """
        fetcher = self.get_fetcher(block_name, bbox)
        return fetcher.get_filtered_features(OSM_TAGS["buildings"])
    
    def clear_cache(self):
        """Clear the OSM data cache to free memory."""
        self._cache.clear()


# =============================================================================
# Pipeline Steps
# =============================================================================

def step1_preprocess_antennas(logger: logging.Logger) -> None:
    """
    Preprocess antenna data by frequency and postal code.
    """
    logger.info("=" * 60)
    logger.info("STEP 1: Preprocessing antenna data")
    logger.info("=" * 60)
    
    preprocess_antenna_data_by_frequency_and_postal(
        ANTENNES_INFO_FILENAME,
        ANTENNES_LOC_FILENAME,
        FILTER_FREQUENCE,
        FILTER_POSTAL_CODE,
        TRANSMITTER_DIRECTORY
    )
    logger.info("Antenna preprocessing completed successfully")


def step2_generate_xml_scenes(
    logger: logging.Logger,
    splitter: TileSplitter,
    all_blocks: list,
    osm_manager: OSMDataManager
) -> None:
    """
    Generate XML scene files with terrain, buildings, and other features.
    
    Args:
        logger: Logger instance
        splitter: TileSplitter instance
        all_blocks: List of block information dictionaries
        osm_manager: OSMDataManager for cached OSM data
    """
    logger.info("=" * 60)
    logger.info("STEP 2: Generating XML scene files")
    logger.info("=" * 60)
    
    for block_info in all_blocks:
        row = block_info["row"]
        col = block_info["col"]
        block_name = block_info["name"]
        
        logger.info(f"Processing {block_name} (row={row}, col={col})")
        
        # Get block metadata (with overlap for consistent coverage)
        meta = splitter.get_block_latlon_bounds(row, col)
        
        # Create output directories
        block_dir = XML_DIR / block_name
        mesh_dir = block_dir / "meshes"
        mesh_dir.mkdir(parents=True, exist_ok=True)
        
        # Get or create OSM fetcher (will be cached for later use)
        bbox = (meta.lon_min, meta.lat_min, meta.lon_max, meta.lat_max)
        fetcher = osm_manager.get_fetcher(block_name, bbox)
        
        # Generate PLY for each layer
        for layer in LAYERS:
            tag_name = layer["tag_name"]
            ply_filename = layer["ply_filename"]
            ply_path = mesh_dir / ply_filename
            
            # Filter the pre-fetched data for this specific layer
            gdf = fetcher.get_filtered_features(OSM_TAGS[tag_name])
            
            if gdf.empty:
                logger.debug(f"  [{tag_name}] No features found — writing empty PLY")
            
            # Convert OSM geometries to 3D PLY mesh
            converter = OSMToPLY(
                gdf=gdf,
                ply_path=ply_path,
                default_height=layer["default_height"],
            )
            converter._process_polygons(handle_missing_height=layer["handle_missing_height"])
            converter._collect_3d_polygons()
            converter._build_multi_polygon()
            converter.save_to_ply()
            
            logger.debug(f"  [{tag_name}] Saved to {ply_path}")
        
        # Generate terrain PLY
        terrain_path = mesh_dir / "terrain.ply"
        generate_flat_terrain_ply(
            output_path=terrain_path,
            x_min=meta.x_start,
            x_max=meta.x_end,
            y_min=meta.y_start,
            y_max=meta.y_end,
            resolution=TERRAIN_RESOLUTION,
            height=TERRAIN_HEIGHT,
        )
        logger.debug(f"  [terrain] Saved to {terrain_path}")
        
        # Generate Sionna XML scene file
        xml_path = block_dir / f"{block_name}.xml"
        xml_generator = SionnaXMLGenerator(mesh_dir=mesh_dir, output_path=xml_path)
        xml_generator.generate(validate_meshes=False)
        logger.debug(f"  [xml] Saved to {xml_path}")
        
        logger.info(f"Finished {block_name}")
    
    logger.info(f"All {len(all_blocks)} blocks processed for XML generation")


def step3_generate_radio_maps(
    logger: logging.Logger,
    splitter: TileSplitter,
    all_blocks: list
) -> None:
    """
    Generate radio maps using Sionna ray tracing.
    
    Args:
        logger: Logger instance
        splitter: TileSplitter instance
        all_blocks: List of block information dictionaries
    """
    logger.info("=" * 60)
    logger.info("STEP 3: Generating radio maps")
    logger.info("=" * 60)
    
    for block_info in all_blocks:
        row = block_info["row"]
        col = block_info["col"]
        block_name = block_info["name"]
        
        logger.info(f"Processing {block_name} (row={row}, col={col})")
        
        # Get block metadata
        meta = splitter.get_block_latlon_bounds(row, col)
        
        # Paths for this block
        block_dir = XML_DIR / block_name
        xml_path = block_dir / f"{block_name}.xml"
        
        # H5 Init
        h5_path = DATASET_DIR / f"{block_name}.h5"
        h5_path.parent.mkdir(parents=True, exist_ok=True)
        H5Manager.init_block_file(h5_path, meta, RM_RESOLUTION_M)
        
        if not xml_path.exists():
            logger.warning(f"  [skip] XML file not found: {xml_path}")
            continue
        
        # Initialize coordinate converter for this block
        lat_origin = (meta.lat_min + meta.lat_max) / 2.0
        lon_origin = (meta.lon_min + meta.lon_max) / 2.0
        converter = SceneCoordinateConverter(
            lat_origin,
            lon_origin,
            TERRAIN_HEIGHT,
            LocalCRS.OSM_STORAGE.crs,
            LocalCRS.FRANCE_LAMBERT93.crs,
        )
        
        # Generate radio map
        generator = RadioMapGenerator(converter)
        
        rss_map = generator.generate(
            xml_path=xml_path,
            csv_path=TRANSMITTER_PATH,
            block_meta=meta,
            tx_array=TX_ARRAY,
            frequency=FREQUENCY,
            resolution_m=RM_RESOLUTION_M,
        )
        
        # Save result
        if rss_map is None:
            logger.info(f"  [skip] No transmitters in core area of {block_name}")
            # Remove incomplete H5 file since radio map is essential
            if h5_path.exists():
                h5_path.unlink()
                logger.debug(f"  [h5] Removed {h5_path} (no radio map)")
        else:
            logger.info(
                f"  [done] RSS map shape: {rss_map.shape}, "
                f"dtype: {rss_map.dtype}, "
                f"min: {rss_map.min():.6e}, max: {rss_map.max():.6e}"
            )
            # Write radio map to HDF5
            H5Manager.write_dataset(
                h5_path,
                H5Manager.DATASET_RADIOMAP,
                rss_map,
                dtype='float32',
            )
            logger.debug(f"  [h5] Written radiomap to {h5_path}")
    
    logger.info(f"All {len(all_blocks)} blocks processed for radio maps")


def step4_rasterize_buildings(
    logger: logging.Logger,
    splitter: TileSplitter,
    all_blocks: list,
    osm_manager: OSMDataManager
) -> None:
    """
    Rasterize building footprints and store in HDF5.
    Uses cached OSM data from Step 2 to avoid redundant fetching.
    
    Args:
        logger: Logger instance
        splitter: TileSplitter instance
        all_blocks: List of block information dictionaries
        osm_manager: OSMDataManager with cached OSM data
    """
    logger.info("=" * 60)
    logger.info("STEP 4: Rasterizing building footprints")
    logger.info("=" * 60)
    
    for block_info in all_blocks:
        row = block_info["row"]
        col = block_info["col"]
        block_name = block_info["name"]
        
        logger.info(f"Processing {block_name} (row={row}, col={col})")
        
        # Get block metadata
        meta = splitter.get_block_latlon_bounds(row, col)
        
        # Get buildings from cache (no new API call if Step 2 already ran)
        bbox = (meta.lon_min, meta.lat_min, meta.lon_max, meta.lat_max)
        buildings_gdf = osm_manager.get_buildings_gdf(block_name, bbox)
        
        if buildings_gdf.empty:
            logger.info(f"  [buildings] No building footprints found in {block_name}")
            building_map = None
        else:
            # Rasterize building footprints
            rasterizer = BuildingRasterizer(
                gdf=buildings_gdf,
                block_meta=meta,
                resolution_m=RM_RESOLUTION_M,
            )
            building_map = rasterizer.rasterize_with_presence()
            logger.debug(f"  [buildings] Rasterized {len(buildings_gdf)} building footprints")
        
        # Write to HDF5 (only if file exists from Step 3)
        h5_path = DATASET_DIR / f"{block_name}.h5"
        
        if h5_path.exists() and building_map is not None:
            H5Manager.write_dataset(
                h5_path,
                H5Manager.DATASET_BUILDINGS,
                building_map,
                dtype='uint8',
            )
            logger.debug(f"  [h5] Written buildings to {h5_path}")
        elif not h5_path.exists():
            logger.warning(f"  [h5] Skipped — no H5 file for {block_name} (radio map missing)")
        else:
            logger.warning(f"  [h5] Skipped — no building data for {block_name}")
    
    logger.info(f"All {len(all_blocks)} blocks processed for building rasterization")


def step5_map_transmitters(
    logger: logging.Logger,
    splitter: TileSplitter,
    all_blocks: list
) -> None:
    """
    Map transmitter locations and store in HDF5.
    
    Args:
        logger: Logger instance
        splitter: TileSplitter instance
        all_blocks: List of block information dictionaries
    """
    logger.info("=" * 60)
    logger.info("STEP 5: Mapping transmitter locations")
    logger.info("=" * 60)
    
    df_tx_all = pd.read_csv(TRANSMITTER_PATH)
    
    for block_info in all_blocks:
        row = block_info["row"]
        col = block_info["col"]
        block_name = block_info["name"]
        
        logger.info(f"Processing {block_name} (row={row}, col={col})")
        
        # Get block metadata
        meta = splitter.get_block_latlon_bounds(row, col)
        
        # Initialize mapper and filter transmitters
        mapper = TransmitterMapper(
            block_meta=meta,
            resolution_m=RM_RESOLUTION_M,
        )
        
        # filter_transmitters uses core bounds (lat_min_core, etc.)
        # to match Step 3 behavior and avoid overlap duplicates
        df_tx_block = mapper.filter_transmitters(df_tx_all)
        
        if len(df_tx_block) == 0:
            logger.info(f"  [transmitters] No transmitters in core area of {block_name}")
        else:
            # Rasterize transmitter locations
            tx_map = mapper.create_presence_matrix()
            logger.debug(f"  [transmitters] Mapped {len(df_tx_block)} transmitters")
        
            # Write to HDF5 (only if file exists from Step 3)
            h5_path = DATASET_DIR / f"{block_name}.h5"
            
            if h5_path.exists():
                H5Manager.write_dataset(
                    h5_path,
                    H5Manager.DATASET_TRANSMITTERS,
                    tx_map,
                    dtype='uint8',
                )
                logger.debug(f"  [h5] Written transmitters to {h5_path}")
            else:
                logger.warning(f"  [h5] Skipped — no H5 file for {block_name} (radio map missing)")
    
    logger.info(f"All {len(all_blocks)} blocks processed for transmitter mapping")


# =============================================================================
# Main Pipeline
# =============================================================================

def run_pipeline(
    logger: logging.Logger,
    steps: Optional[list] = None
) -> None:
    """
    Execute the complete data processing pipeline.
    
    Args:
        logger: Logger instance
        steps: List of step numbers to execute (default: all steps)
    """
    if steps is None:
        steps = [1, 2, 3, 4, 5]
    
    logger.info("=" * 60)
    logger.info("Radio Map Dataset Generation Pipeline")
    logger.info(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"Steps to execute: {steps}")
    logger.info("=" * 60)
    
    # Initialize the splitter
    splitter = TileSplitter(
        lat_min=LAT_MIN,
        lat_max=LAT_MAX,
        lon_min=LON_MIN,
        lon_max=LON_MAX,
        block_size_m=BLOCK_SIZE_M,
        overlap_m=OVERLAP_M
    )
    
    # Get the task list
    all_blocks = splitter.get_all_blocks()
    logger.info(f"Total blocks to process: {len(all_blocks)}")
    
    # Initialize OSM data manager for caching
    osm_manager = OSMDataManager()
    
    # Execute requested steps
    try:
        if 1 in steps:
            step1_preprocess_antennas(logger)
        
        if 2 in steps:
            step2_generate_xml_scenes(logger, splitter, all_blocks, osm_manager)
        
        if 3 in steps:
            step3_generate_radio_maps(logger, splitter, all_blocks)
        
        if 4 in steps:
            step4_rasterize_buildings(logger, splitter, all_blocks, osm_manager)
        
        if 5 in steps:
            step5_map_transmitters(logger, splitter, all_blocks)
        
        # Clear cache after all steps complete
        osm_manager.clear_cache()
        
        logger.info("=" * 60)
        logger.info("Pipeline completed successfully!")
        logger.info(f"Finished at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Output directory: {DATASET_DIR.resolve()}")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"Pipeline failed with error: {e}", exc_info=True)
        # Clear cache even on failure
        osm_manager.clear_cache()
        raise


def main():
    """Main entry point with argument parsing."""
    parser = argparse.ArgumentParser(
        description="Generate radio map dataset from OSM data and transmitter information",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                    # Run all steps
  python main.py --steps 1 2        # Run only steps 1 and 2
  python main.py --steps 3          # Run only step 3 (radio map generation)
  python main.py --steps 2 3 4      # Run steps 2, 3, and 4 together
  python main.py --log-dir custom_logs  # Use custom log directory
        """
    )
    
    parser.add_argument(
        '--steps',
        type=int,
        nargs='+',
        choices=[1, 2, 3, 4, 5],
        help='Pipeline steps to execute (1-5). Default: all steps'
    )
    
    parser.add_argument(
        '--log-dir',
        type=Path,
        default=LOG_DIR,
        help=f'Directory for log files (default: {LOG_DIR})'
    )
    
    args = parser.parse_args()
    
    # Setup logging
    logger = setup_logging(args.log_dir)
    
    # Run pipeline
    run_pipeline(logger, args.steps)


if __name__ == "__main__":
    main()