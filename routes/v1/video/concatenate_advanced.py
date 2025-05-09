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
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON payload"}), 400

        input_urls = data.get("input_urls")
        filter_complex_str = data.get("filter_complex")
        # output_options_list = data.get("output_options") # This might be more structured

        if not input_urls or not isinstance(input_urls, list) or len(input_urls) == 0:
            return jsonify({"error": "Missing or invalid 'input_urls'"}), 400
        if not filter_complex_str or not isinstance(filter_complex_str, str):
            return jsonify({"error": "Missing or invalid 'filter_complex' string"}), 400
        
        # --- Download input files ---
        local_input_paths = []
        for url in input_urls:
            try:
                logger.info(f"Job {job_id_param}: Downloading input file: {url}")
                local_path = download_file(url, LOCAL_STORAGE_PATH)
                local_input_paths.append(local_path)
                logger.info(f"Job {job_id_param}: Downloaded {url} to {local_path}")
            except Exception as e:
                logger.error(f"Job {job_id_param}: Failed to download input file {url}: {str(e)}")
                # Clean up already downloaded files for this job if one fails
                for p in local_input_paths:
                    if os.path.exists(p): os.remove(p)
                return jsonify({"error": f"Failed to download input file: {url}", "details": str(e)}), 500
        
        # --- Construct FFmpeg command ---
        # Example: ffmpeg -i input1.mp4 -i input2.mp4 -filter_complex "..." output.mp4
        command = ['ffmpeg']
        for path in local_input_paths:
            command.extend(['-i', path])
        
        command.extend(['-filter_complex', filter_complex_str])
        
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
