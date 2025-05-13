# NO-CODE-ARCHITECTS-TOOLKIT/routes/v1/video/composite_video_processor.py

import logging
import uuid
from flask import Blueprint, request, jsonify

# --- Configuration Imports ---
# Assuming you have a config.py for GCS bucket names, etc.
# from config import TEMP_GCS_BUCKET, STAGING_GCS_BUCKET
# For demonstration, let's define them here or ensure they are loaded from environment
# In a real app, use a proper config loading mechanism.
try:
    from config import TEMP_GCS_BUCKET, STAGING_GCS_BUCKET
except ImportError:
    # Fallback or raise error if essential config is missing
    logging.warning("config.py not found or GCS buckets not defined. Using placeholder values.")
    TEMP_GCS_BUCKET = "your-temp-bucket-name" # REPLACE with actual or from env
    STAGING_GCS_BUCKET = "your-staging-bucket-name" # REPLACE with actual or from env


# --- Service Imports ---
# These paths assume the file locations we discussed previously.
# 1. FFprobe Analyzer Service
try:
    from services.v1.ffmpeg.ffprobe_analyzer import run_ffprobe_on_gcs_blob, extract_relevant_metadata
except ImportError as e:
    logging.error(f"Could not import ffprobe_analyzer service: {e}. Ensure it's in services/v1/ffmpeg/")
    run_ffprobe_on_gcs_blob = None
    extract_relevant_metadata = None

# 2. Image to Video Conversion Service
try:
    from services.v1.image.convert.image_to_video import convert_image_to_video_gcs_stream
except ImportError as e:
    logging.error(f"Could not import image_to_video service: {e}. Ensure it's in services/v1/image/convert/")
    convert_image_to_video_gcs_stream = None

# 3. Video Standardization Service (You will create this service next)
try:
    from services.v1.video.video_standardizer import standardize_video_gcs_stream
except ImportError as e:
    logging.warning(f"Could not import video_standardizer service: {e}. This service needs to be implemented.")
    standardize_video_gcs_stream = None # Placeholder

# --- Utility Imports ---
# from app_utils import parse_gcs_uri # If you have this helper

# --- Setup ---
composite_video_bp = Blueprint('composite_video_processor', __name__)
logger = logging.getLogger(__name__)

# --- Helper Function for Target Resolution ---
def get_target_resolution(aspect_ratio_str: str, base_width: int = 1920, base_height: int = 1080) -> dict | None:
    """
    Determines target output resolution based on aspect ratio string.
    Adjusts one dimension to maintain a common reference (e.g., 1080p height for 16:9).
    Ensures dimensions are even.
    """
    try:
        if ':' not in aspect_ratio_str:
            logger.error(f"Invalid aspect_ratio_str format: {aspect_ratio_str}. Expected 'W:H'.")
            return None
        
        ar_w, ar_h = map(float, aspect_ratio_str.split(':'))
        if ar_w <= 0 or ar_h <= 0:
            logger.error(f"Aspect ratio dimensions must be positive: {aspect_ratio_str}")
            return None

        target_w, target_h = None, None

        # Common aspect ratios and target resolutions (can be expanded)
        if ar_w / ar_h == 16 / 9:      # 16:9 (Landscape HD)
            target_h = base_height
            target_w = int(target_h * (ar_w / ar_h))
        elif ar_w / ar_h == 9 / 16:    # 9:16 (Portrait HD)
            target_w = base_height # Use base_height for the shorter dimension (width in portrait)
            target_h = int(target_w * (ar_h / ar_w))
        elif ar_w / ar_h == 1 / 1:     # 1:1 (Square)
            target_w = base_height
            target_h = base_height
        elif ar_w / ar_h == 4 / 5:     # 4:5 (Portrait common for social)
            target_w = base_height # Use base_height for the shorter dimension
            target_h = int(target_w * (ar_h / ar_w))
        elif ar_w / ar_h == 4 / 3:     # 4:3 (Older SD/tablet)
            target_h = base_height
            target_w = int(target_h * (ar_w / ar_h))
        else: # Generic calculation, might need refinement for less common ARs
            logger.warning(f"Non-standard aspect ratio {aspect_ratio_str}. Calculating based on base_height={base_height}.")
            target_h = base_height
            target_w = int(target_h * (ar_w / ar_h))

        # Ensure dimensions are even for yuv420p compatibility
        if target_w % 2 != 0: target_w += 1
        if target_h % 2 != 0: target_h += 1
        
        return {"width": target_w, "height": target_h}

    except ValueError:
        logger.error(f"Could not parse aspect ratio: {aspect_ratio_str}")
        return None
    except Exception as e:
        logger.error(f"Error in get_target_resolution for {aspect_ratio_str}: {e}")
        return None


# --- Main Processing Function ---
def _process_video_editing_request(data: dict) -> tuple[dict, int]:
    """
    Internal function to handle the core logic of processing the video editing request.
    """
    # Validate essential services are available
    if not all([run_ffprobe_on_gcs_blob, extract_relevant_metadata, convert_image_to_video_gcs_stream, standardize_video_gcs_stream]):
        logger.critical("One or more critical services (FFprobe, ImageToVideo, Standardizer) are not available. Aborting.")
        return {"error": "Internal server configuration error: Critical services missing."}, 500

    job_id = data.get("job_id") or str(uuid.uuid4())
    asset_gcs_uris = data.get("asset_gcs_uris", [])
    output_preferences = data.get("output_preferences", {})
    image_settings = data.get("image_settings", {})
    # webhook_url = data.get("webhook_url") # For future async processing

    logger.info(f"Job {job_id}: Starting composite video processing.")
    logger.info(f"Job {job_id}: Input asset URIs: {asset_gcs_uris}")
    logger.info(f"Job {job_id}: Output preferences: {output_preferences}")
    logger.info(f"Job {job_id}: Image settings: {image_settings}")

    if not asset_gcs_uris:
        return {"error": "No 'asset_gcs_uris' provided.", "job_id": job_id}, 400
    if not output_preferences.get("target_aspect_ratio_str") or not output_preferences.get("target_fps"):
        return {"error": "Missing 'target_aspect_ratio_str' or 'target_fps' in 'output_preferences'.", "job_id": job_id}, 400

    target_aspect_ratio_str = output_preferences["target_aspect_ratio_str"]
    target_fps = int(output_preferences["target_fps"])
    target_resolution = get_target_resolution(target_aspect_ratio_str)

    if not target_resolution:
        return {"error": f"Could not determine target resolution for aspect ratio '{target_aspect_ratio_str}'.", "job_id": job_id}, 400

    logger.info(f"Job {job_id}: Target AR: {target_aspect_ratio_str}, Target Res: {target_resolution}, Target FPS: {target_fps}")

    default_image_duration = image_settings.get("default_duration", 5)
    default_image_effect = image_settings.get("default_effect", "static")
    default_image_zoom_factor = image_settings.get("default_zoom_factor", 1.0) # 1.0 for static, >1 for zoom

    assets_for_standardization = [] # List of dicts: {"gcs_uri": ..., "metadata": ...}

    # --- Step 1 & 2: Asset Analysis and Image-to-Video Conversion ---
    for index, asset_uri in enumerate(asset_gcs_uris):
        logger.info(f"Job {job_id}: Processing asset {index + 1}/{len(asset_gcs_uris)}: {asset_uri}")
        
        # Analyze original asset
        raw_probe_data = run_ffprobe_on_gcs_blob(asset_uri)
        if not raw_probe_data:
            logger.error(f"Job {job_id}: Failed to ffprobe original asset {asset_uri}. Skipping.")
            continue
        
        asset_metadata = extract_relevant_metadata(raw_probe_data, asset_uri)
        if "error" in asset_metadata or not asset_metadata.get("width"): # Basic check
            logger.error(f"Job {job_id}: Invalid metadata for {asset_uri} after ffprobe. Skipping. Data: {asset_metadata}")
            continue

        current_asset_gcs_uri = asset_uri # This will be updated if image is converted

        if asset_metadata.get("is_image"):
            logger.info(f"Job {job_id}: Asset {asset_uri} is an image. Converting to video.")
            
            img_converted_blob_name = f"{job_id}/converted_images/image_{index}_{uuid.uuid4()}.mp4"
            temp_video_gcs_uri = f"gs://{TEMP_GCS_BUCKET}/{img_converted_blob_name}"

            conversion_success = convert_image_to_video_gcs_stream(
                original_image_gcs_uri=asset_uri,
                image_width=asset_metadata["width"],
                image_height=asset_metadata["height"],
                duration_seconds=default_image_duration, # Or use per-asset settings if provided
                frame_rate=target_fps, # Use final target FPS for consistency
                effect_type=default_image_effect, # Or use per-asset settings
                zoom_speed_factor=default_image_zoom_factor, # Or use per-asset settings
                output_gcs_uri=temp_video_gcs_uri
            )

            if not conversion_success:
                logger.error(f"Job {job_id}: Failed to convert image {asset_uri} to video. Skipping.")
                continue
            
            logger.info(f"Job {job_id}: Image {asset_uri} converted to video: {temp_video_gcs_uri}. Re-analyzing.")
            current_asset_gcs_uri = temp_video_gcs_uri # Update URI to the new video

            # IMPORTANT: Analyze the newly created video
            raw_converted_video_probe_data = run_ffprobe_on_gcs_blob(current_asset_gcs_uri)
            if not raw_converted_video_probe_data:
                logger.error(f"Job {job_id}: Failed to ffprobe converted video {current_asset_gcs_uri}. Skipping.")
                continue
            
            asset_metadata = extract_relevant_metadata(raw_converted_video_probe_data, current_asset_gcs_uri)
            if "error" in asset_metadata or not asset_metadata.get("width"):
                logger.error(f"Job {job_id}: Invalid metadata for converted video {current_asset_gcs_uri}. Skipping.")
                continue
            logger.info(f"Job {job_id}: Metadata for converted video {current_asset_gcs_uri} obtained.")
        
        assets_for_standardization.append({
            "gcs_uri": current_asset_gcs_uri,
            "metadata": asset_metadata
        })

    if not assets_for_standardization:
        logger.warning(f"Job {job_id}: No assets were successfully processed for standardization.")
        return {"error": "No assets could be prepared for video generation.", "job_id": job_id}, 400

    # --- Step 3: Standardization ---
    standardized_segment_gcs_uris = []
    for index, asset_data in enumerate(assets_for_standardization):
        input_video_gcs_uri = asset_data["gcs_uri"]
        current_video_metadata = asset_data["metadata"]
        
        logger.info(f"Job {job_id}: Standardizing video {index + 1}/{len(assets_for_standardization)}: {input_video_gcs_uri}")

        std_blob_name = f"{job_id}/standardized_segments/segment_{index}_{uuid.uuid4()}.mp4"
        standardized_video_gcs_uri = f"gs://{STAGING_GCS_BUCKET}/{std_blob_name}"
        
        standardization_success = standardize_video_gcs_stream(
            input_gcs_uri=input_video_gcs_uri,
            current_metadata=current_video_metadata,
            target_aspect_ratio_str=target_aspect_ratio_str,
            target_resolution=target_resolution, # The dict with {"width": W, "height": H}
            target_fps=target_fps,
            output_gcs_uri=standardized_video_gcs_uri
        )

        if standardization_success:
            standardized_segment_gcs_uris.append(standardized_video_gcs_uri)
            logger.info(f"Job {job_id}: Successfully standardized {input_video_gcs_uri} to {standardized_video_gcs_uri}")
        else:
            logger.error(f"Job {job_id}: Failed to standardize video {input_video_gcs_uri}. Skipping segment.")
            # Potentially fail entire job here if one segment fails, or collect errors
            
    if not standardized_segment_gcs_uris:
        logger.error(f"Job {job_id}: No segments were successfully standardized.")
        return {"error": "Video generation failed as no segments could be standardized.", "job_id": job_id}, 500

    logger.info(f"Job {job_id}: All standardization attempts complete.")
    logger.info(f"Job {job_id}: Standardized segments for concatenation: {standardized_segment_gcs_uris}")

    # The GCS URIs in standardized_segment_gcs_uris are ready for the concat demuxer step.
    # This step (downloading to /tmp, creating concat_list.txt, running concat FFmpeg)
    # would typically happen next, possibly in another function or service.
    # For now, this route will return the list of standardized segments.

    return {
        "job_id": job_id,
        "message": "Assets processed and standardized successfully.",
        "standardized_segments_gcs_uris": standardized_segment_gcs_uris,
        "next_step": "Use these URIs with FFmpeg concat demuxer."
    }, 200


# --- Flask Route Definition ---
@composite_video_bp.route('/process-composite-video', methods=['POST'])
# @authenticate # If you have an authentication decorator
# @validate_payload({...}) # If you have a payload validation decorator
def process_composite_video_route():
    """
    Main API endpoint to process a list of assets (images/videos) from GCS,
    standardize them, and prepare them for final concatenation.
    """
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 415
    
    data = request.get_json()
    
    # --- Basic Payload Validation ---
    required_keys = ["asset_gcs_uris", "output_preferences", "image_settings"]
    if not all(key in data for key in required_keys):
        return jsonify({"error": f"Missing one or more required keys: {', '.join(required_keys)}"}), 400
    if not isinstance(data["asset_gcs_uris"], list):
        return jsonify({"error": "'asset_gcs_uris' must be a list"}), 400
    if not isinstance(data["output_preferences"], dict):
        return jsonify({"error": "'output_preferences' must be a dict"}), 400
    if not isinstance(data["image_settings"], dict):
        return jsonify({"error": "'image_settings' must be a dict"}), 400
    if not data["output_preferences"].get("target_aspect_ratio_str") or not data["output_preferences"].get("target_fps"):
         return jsonify({"error": "Missing 'target_aspect_ratio_str' or 'target_fps' in 'output_preferences'."}), 400

    # Call the internal processing function
    response, status_code = _process_video_editing_request(data)
    return jsonify(response), status_code

# Remember to register this blueprint in your main app.py:
# from routes.v1.video.composite_video_processor import composite_video_bp
# app.register_blueprint(composite_video_bp, url_prefix='/api/v1/video') # Or your desired prefix