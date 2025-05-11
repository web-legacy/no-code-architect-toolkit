# Copyright (c) 2025 Stephen G. Pope AND Steve Webster added this endpoint May 9 2025
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import os
import uuid
import subprocess
import json
import logging
from flask import Blueprint, request, jsonify
from app_utils import validate_payload # Assuming you have this for payload validation
from services.authentication import authenticate # Assuming you have this
from services.file_management import download_file
from services.cloud_storage import upload_file # Assuming you have this
from config import LOCAL_STORAGE_PATH

v1_video_concatenate_advanced_bp = Blueprint('v1_video_concatenate_advanced', __name__)
logger = logging.getLogger(__name__)

def probe_file_for_audio_and_duration(filepath):
    """
    Probes a video file using ffprobe to check for an audio stream and get duration.
    Returns a dictionary with 'has_audio' (bool) and 'duration' (float or None).
    """
    command = [
        'ffprobe',
        '-v', 'error',
        '-select_streams', 'a:0', # Select the first audio stream
        '-show_entries', 'stream=codec_type',
        '-show_entries', 'format=duration',
        '-of', 'json',
        filepath
    ]
    try:
        process = subprocess.run(command, check=True, capture_output=True, text=True)
        probe_data = json.loads(process.stdout)

        has_audio = 'streams' in probe_data and len(probe_data['streams']) > 0
        duration = None
        if 'format' in probe_data and 'duration' in probe_data['format']:
            try:
                duration = float(probe_data['format']['duration'])
            except ValueError:
                pass # Duration not a valid float

        return {"has_audio": has_audio, "duration": duration}

    except subprocess.CalledProcessError as e:
        logger.error(f"ffprobe failed for {filepath}. Stderr: {e.stderr}")
        return {"has_audio": False, "duration": None} # Assume no audio/duration on error
    except FileNotFoundError:
        logger.error("ffprobe command not found. Is FFmpeg installed and in PATH?")
        return {"has_audio": False, "duration": None} # Assume no audio/duration if ffprobe not found
    except json.JSONDecodeError:
        logger.error(f"Failed to parse ffprobe JSON output for {filepath}.")
        return {"has_audio": False, "duration": None} # Assume no audio/duration on parse error
    except Exception as e:
        logger.error(f"An unexpected error occurred during ffprobe for {filepath}: {str(e)}")
        return {"has_audio": False, "duration": None} # Assume no audio/duration on other errors


@v1_video_concatenate_advanced_bp.route('/v1/video/concatenate/advanced', methods=['POST'])
@authenticate # Placeholder - ensure this decorator works as expected
# @validate_payload({ # Placeholder - define your payload schema here
#     "type": "object",
#     "properties": {
#         "input_urls": {
#             "type": "array",
#             "items": {"type": "string", "format": "uri"},
#             "minItems": 1
#         },
#         "filter_complex": {"type": "string"},
#         "output_options": {
#             "type": "array",
#             "items": {"type": "string"}
#         },
#         "job_id": {"type": "string"} # Optional, or generate one
#     },
#     "required": ["input_urls", "filter_complex", "output_options"]
# })
def concatenate_advanced_api():
    job_id_param = request.json.get('job_id', str(uuid.uuid4()))
    logger.info(f"Job {job_id_param}: Received advanced video concatenation request")

    try:
        local_input_paths = [] # Initialize local_input_paths
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400

        input_urls = data.get("input_urls")
        # filter_complex_str is now generated dynamically in this backend
        # output_options_list = data.get("output_options") # This might be more structured
        aspect_ratio_str = data.get("aspect_ratio") # Get aspect_ratio from payload

        if not input_urls or not isinstance(input_urls, list) or len(input_urls) == 0:
            return jsonify({"error": "Missing or invalid 'input_urls'"}), 400
        if not aspect_ratio_str or not isinstance(aspect_ratio_str, str): # Check for aspect_ratio
            return jsonify({"error": "Missing or invalid 'aspect_ratio' string"}), 400
        
        # --- Download and probe input files (using streaming download) ---
        input_details = []
        for url in input_urls:
            local_path = None # Initialize local_path for cleanup
            try:
                logger.info(f"Job {job_id_param}: Streaming download and processing input file: {url}")
                
                # Use the streaming download_file
                image_stream = download_file(url)  # Get a file-like object streaming from GCS

                if image_stream is None:
                    logger.error(f"Job {job_id_param}: Failed to get stream for {url}")
                    raise Exception("Failed to get stream for input file")

                # Create a temporary file to write the stream to for ffprobe and FFmpeg input
                # This is a compromise to avoid full download upfront but still provide a seekable file
                # for ffprobe and FFmpeg -i input.
                temp_file_extension = os.path.splitext(urlparse(url).path)[1] or '.mp4' # Default to .mp4 if no extension
                local_path = os.path.join(LOCAL_STORAGE_PATH, f"input_{uuid.uuid4()}{temp_file_extension}")
                
                # Ensure the local directory exists
                os.makedirs(LOCAL_STORAGE_PATH, exist_ok=True)

                # Write the stream to the temporary file
                with open(local_path, 'wb') as f:
                    while True:
                        chunk = image_stream.read(8192) # Read in chunks
                        if not chunk:
                            break
                        f.write(chunk)
                image_stream.close() # Close the stream after writing to temp file

                logger.info(f"Job {job_id_param}: Streamed and saved {url} to temporary file: {local_path}")

                logger.info(f"Job {job_id_param}: Probing file: {local_path}")
                probe_result = probe_file_for_audio_and_duration(local_path)
                logger.info(f"Job {job_id_param}: Probe result for {local_path}: {probe_result}")

                processed_local_path = local_path # Default to original temp file path
                
                # If no audio, add a silent audio track
                if not probe_result["has_audio"]:
                    logger.info(f"Job {job_id_param}: Input file {url} has no audio. Adding silent audio track.")
                    # Create a new temporary file path for the video with silent audio
                    silent_audio_output_path = os.path.join(LOCAL_STORAGE_PATH, f"silent_audio_{uuid.uuid4()}{os.path.splitext(local_path)[1]}")
                    
                    # Use the probed duration, or a default if probing failed
                    duration = probe_result["duration"] if probe_result["duration"] is not None else 5 

                    if not add_silent_audio_track(local_path, silent_audio_output_path, duration):
                         # Clean up original downloaded file
                        if os.path.exists(local_path): os.remove(local_path)
                        return jsonify({"error": f"Failed to add silent audio track to {url}"}), 500
                    
                    processed_local_path = silent_audio_output_path
                    logger.info(f"Job {job_id_param}: Added silent audio to {url}. New path: {processed_local_path}")
                    # Add the new temporary file to the cleanup list
                    local_input_paths.append(processed_local_path)


                input_details.append({
                    "url": url,
                    "local_path": processed_local_path, # Use the processed path (temp file)
                    "has_audio": True, # Now guaranteed to have audio (original or silent)
                    "duration": probe_result["duration"] # Keep original duration info
                })
                # Add the original temporary file to the cleanup list
                local_input_paths.append(local_path)


            except Exception as e:
                logger.error(f"Job {job_id_param}: Failed to process input file {url}: {str(e)}")
                # Clean up all local files for this job
                for p in local_input_paths:
                    if os.path.exists(p): os.remove(p)
                return jsonify({"error": f"Failed to process input file: {url}", "details": str(e)}), 500
        
        # --- Construct FFmpeg command ---
        # Example: ffmpeg -i input1.mp4 -i input2.mp4 -filter_complex "..." output.mp4
        command = ['ffmpeg']
        for detail in input_details:
            command.extend(['-i', detail["local_path"]])
        
        # --- Dynamically Construct FFmpeg filter_complex ---
        filter_complex_parts = []
        concat_video_streams = ""
        concat_audio_streams = ""
        
        # Assuming target resolution is fixed for now, or derived from payload if added later
        # For now, using a placeholder target resolution (e.g., 1920x1080 for 16:9)
        # This should ideally come from the frontend payload based on user selection.
        # Calculate target resolution based on aspect_ratio from payload
        try:
            ratio_x, ratio_y = map(int, aspect_ratio_str.split(':'))
            if ratio_y == 0: raise ValueError("Aspect ratio Y cannot be zero")
            # Using a common base height, e.g., 1080p
            base_height = 1080
            target_height = base_height
            target_width = round(base_height * (ratio_x / ratio_y))
            if target_width % 2 != 0: # Ensure even width
                target_width += 1
            logger.info(f"Job {job_id_param}: Calculated target resolution {target_width}x{target_height} for aspect ratio {aspect_ratio_str}")
        except ValueError as e:
            logger.error(f"Job {job_id_param}: Invalid aspect_ratio format: {aspect_ratio_str}. Error: {str(e)}")
            return jsonify({"error": f"Invalid aspect_ratio format: {aspect_ratio_str}", "details": str(e)}), 400


        for i, detail in enumerate(input_details):
            # Video stream processing (scaling and padding)
            filter_complex_parts.append(
                f"[{i}:v]scale='min({target_width},iw*{target_height}/ih)':'min({target_height},ih*{target_width}/iw)',"
                f"pad={target_width}:{target_height}:(ow-iw)/2:(oh-ih)/2,setpts=PTS-STARTPTS[v{i}]"
            )
            concat_video_streams += f"[v{i}]"

            # Audio stream processing (simplified as all inputs now have audio)
            filter_complex_parts.append(f"[{i}:a]asetpts=PTS-STARTPTS[a{i}]")
            concat_audio_streams += f"[a{i}]"

        # Concatenate video and audio streams with correct interleaving
        interleaved_concat_streams = ""
        for i in range(len(input_details)):
            interleaved_concat_streams += f"[v{i}][a{i}]"

        dynamic_filter_complex_str = f"{'; '.join(filter_complex_parts)}; {interleaved_concat_streams}concat=n={len(input_details)}:v=1:a=1[outv][outa]"
        logger.info(f"Job {job_id_param}: Generated dynamic filter_complex: {dynamic_filter_complex_str}")
        command.extend(['-filter_complex', dynamic_filter_complex_str])
        
        # Define output filename and add output options
        # For simplicity, assuming one output for now.
        # The output options should be passed in the request or derived.
        # This part needs to match your specific FFmpeg command structure.
        output_filename_base = f"{job_id_param}_advanced_concat_output"
        output_extension = ".mp4" # Default, can be made dynamic
        output_filepath = os.path.join(LOCAL_STORAGE_PATH, f"{output_filename_base}{output_extension}")

        # Add output options from your command:
        # -map "[outv]" -map "[outa]" -map 1:s:0? -c:v libx264 -crf 23 -preset medium -c:a aac -b:a 128k -c:s mov_text -movflags +faststart
        # These need to be added to the command list correctly.
        # The -map options are part of the filter_complex output streams, so they are handled there.
        # The rest are output encoding options.
        command.extend([
            '-map', '[outv]',
            '-map', '[outa]',
            '-c:v', 'libx264',
            '-crf', '23',
            '-preset', 'medium',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-movflags', '+faststart',
            output_filepath
        ])
        
        logger.info(f"Job {job_id_param}: Executing FFmpeg command: {' '.join(command)}")

        # --- Execute FFmpeg command ---
        try:
            process = subprocess.run(command, check=True, capture_output=True, text=True)
            logger.info(f"Job {job_id_param}: FFmpeg stdout: {process.stdout}")
            logger.info(f"Job {job_id_param}: FFmpeg stderr: {process.stderr}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Job {job_id_param}: FFmpeg command failed. Stderr: {e.stderr}")
            # Clean up local files
            for p in local_input_paths:
                if os.path.exists(p): os.remove(p)
            if os.path.exists(output_filepath): os.remove(output_filepath)
            return jsonify({"error": "FFmpeg command execution failed", "details": e.stderr}), 500

        # --- Upload output file to GCS ---
        if not os.path.exists(output_filepath):
            logger.error(f"Job {job_id_param}: Output file {output_filepath} not found after FFmpeg execution.")
            # Clean up local input files
            for p in local_input_paths:
                if os.path.exists(p): os.remove(p)
            return jsonify({"error": "Output file not found after FFmpeg execution"}), 500

        logger.info(f"Job {job_id_param}: Uploading output file {output_filepath} to GCS.")
        final_output_url = upload_file(output_filepath) # Ensure upload_file returns the GCS URL
        logger.info(f"Job {job_id_param}: Output file uploaded to {final_output_url}")

        # --- Clean up local files ---
        for p in local_input_paths:
            if os.path.exists(p): os.remove(p)
        if os.path.exists(output_filepath): os.remove(output_filepath)
        
        return jsonify({"message": "Advanced concatenation successful", "output_url": final_output_url, "job_id": job_id_param}), 200

    except Exception as e:
        logger.error(f"Job {job_id_param}: Unexpected error in advanced concatenation: {str(e)}")
        # Clean up any stray files if possible, though paths might not be defined if error was early
        return jsonify({"error": "An unexpected error occurred", "details": str(e)}), 500
