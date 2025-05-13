# Copyright (c) 2025 Stephen G. Pope
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
import subprocess
import logging
from services.file_management import download_file  # Assumed modified for streaming
from PIL import Image # Changed import

from flask import Blueprint # Added import
from app_utils import * # Added import
from services.authentication import authenticate # Added import
from services.cloud_storage import upload_file # Added import

logger = logging.getLogger(__name__)

v1_image_convert_video_bp = Blueprint('v1_image_convert_video', __name__) # Added blueprint definition

@v1_image_convert_video_bp.route('/v1/image/convert/video', methods=['POST']) # Added route definition
@v1_image_convert_video_bp.route('/v1/image/transform/video', methods=['POST']) #depleft for backwards compatibility, do not use. # Added route definition
@authenticate # Added decorator
@validate_payload({ # Added decorator
    "type": "object",
    "properties": {
        "image_url": {"type": "string", "format": "uri"},
        "length": {"type": "number", "minimum": 0.1, "maximum": 60},
        "frame_rate": {"type": "integer", "minimum": 15, "maximum": 60},
        "zoom_speed": {"type": "number", "minimum": 0, "maximum": 100},
        "webhook_url": {"type": "string", "format": "uri"},
        "id": {"type": "string"}
    },
    "required": ["image_url"],
    "additionalProperties": False
})
@queue_task_wrapper(bypass_queue=False) # Added decorator
def image_to_video(job_id, data): # Added endpoint function
    image_url = data.get('image_url')
    length = data.get('length', 5)
    frame_rate = data.get('frame_rate', 30)
    zoom_speed = data.get('zoom_speed', 3) / 100
    webhook_url = data.get('webhook_url')
    id = data.get('id')

    logger.info(f"Job {job_id}: Received image to video request for {image_url}")

    try:
        # Process image to video conversion
        # This function now needs to call the core processing logic
        # which is in the process_image_to_video function below.
        # The output_gcs_bucket and output_gcs_blob_name need to be determined here.
        # Assuming a default output bucket and blob name structure for now.
        # This might need adjustment based on your cloud_storage service implementation.
        output_gcs_bucket = "your-output-bucket-name" # Replace with actual bucket name
        output_gcs_blob_name = f"videos/{job_id}.mp4" # Example blob name structure

        output_gcs_url = process_image_to_video(
            image_url, length, frame_rate, zoom_speed, job_id, output_gcs_bucket, output_gcs_blob_name
        )

        logger.info(f"Job {job_id}: Converted video uploaded to cloud storage: {output_gcs_url}")

        # Return the cloud URL for the uploaded file
        return output_gcs_url, "/v1/image/convert/video", 200
        
    except Exception as e:
        logger.error(f"Job {job_id}: Error processing image to video: {str(e)}", exc_info=True)
        return str(e), "/v1/image/convert/video", 500


def process_image_to_video(image_gcs_url, length, frame_rate, zoom_speed, job_id, output_gcs_bucket, output_gcs_blob_name):
    """
    Processes an image from GCS to create a video using FFmpeg, streaming directly to and from GCS.
    """
    try:
        # Use google-cloud-storage to get the image file.  download_file is now a stream
        #  Modify download_file in services.file_management to support this.

        image_stream = download_file(image_gcs_url)  # Get a file-like object streaming from GCS

        if image_stream is None:
            logger.error(f"Failed to download image from GCS: {image_gcs_url}")
            raise Exception("Failed to download image from GCS")

        # Get image dimensions using FFprobe
        ffprobe_command = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "stream=width,height",
            "-of", "json",
            "-i", "-"  # Input from stdin
        ]

        process = subprocess.Popen(
            ffprobe_command,
            stdin=image_stream,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = process.communicate()

        if stderr:
            logger.error(f"FFprobe error: {stderr.decode('utf-8')}")
            raise Exception(f"FFprobe error: {stderr.decode('utf-8')}")

        ffprobe_output = json.loads(stdout.decode("utf-8"))

        if not ffprobe_output or not ffprobe_output["streams"]:
            logger.error("FFprobe output is empty or missing streams")
            raise Exception("FFprobe output is empty or missing streams")

        width = ffprobe_output["streams"][0]["width"]
        height = ffprobe_output["streams"][0]["height"]

        logger.info(f"Original image dimensions: {width}x{height}")

        image_stream.seek(0)  # Reset the stream position after FFprobe

        # Determine orientation and set appropriate dimensions (same as before)
        if width > height:
            scale_dims = "7680:4320"
            output_dims = "1920x1080"
        else:
            scale_dims = "4320:7680"
            output_dims = "1080x1920"

        # Calculate total frames and zoom factor (same as before)
        total_frames = float(length * frame_rate)
        zoom_factor = 1 + (zoom_speed * length)

        logger.info(f"Using scale dimensions: {scale_dims}, output dimensions: {output_dims}")
        logger.info(f"Video length: {length}s, Frame rate: {frame_rate}fps, Total frames: {total_frames}")
        logger.info(f"Zoom speed: {zoom_speed}/s, Final zoom factor: {zoom_factor}")


        # Prepare FFmpeg command (modified for streaming)
        cmd = [
            'ffmpeg', '-framerate', str(frame_rate), '-loop', '1', '-i', '-', # '-' for stdin
            '-vf', f"scale={scale_dims},zoompan=z='min(1+({zoom_speed}*{length})*on/{total_frames}, {zoom_factor})':d={total_frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={output_dims}",
            '-c:v', 'libx264', '-t', str(length), '-pix_fmt', 'yuv420p', '-f', 'mp4', '-' # '-' for stdout
        ]

        logger.info(f"Running FFmpeg command: {' '.join(cmd)}")

        # Run FFmpeg command (with streaming)
        process = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        stdout, stderr = process.communicate(input=image_stream.read()) # Pass the stream to stdin

        if process.returncode != 0:
            logger.error(f"FFmpeg command failed. Error: {stderr.decode()}")
            raise subprocess.CalledProcessError(process.returncode, cmd, stdout, stderr)

        # Standardization Logic
        # Define target standardization parameters
        target_video_codec = "libx264"
        target_audio_codec = "aac"
        target_audio_channels = 2
        target_audio_sample_rate = 48000
        target_pixel_format = "yuv420p"
        target_frame_rate = 30 # Assuming a target frame rate of 30 fps

        # Determine target resolution based on user-defined final target aspect ratio
        target_aspect_ratio = data.get('target_aspect_ratio', '16:9') # Assuming target_aspect_ratio is passed in data

        if target_aspect_ratio == '16:9':
            target_resolution = "1920x1080"
        elif target_aspect_ratio == '9:16':
            target_resolution = "1080x1920"
        elif target_aspect_ratio == '1:1':
            target_resolution = "1080x1080"
        elif target_aspect_ratio == '4:5':
            target_resolution = "1080x1350"
        else:
            # Default to 16:9 if aspect ratio is not recognized
            logger.warning(f"Unrecognized target aspect ratio: {target_aspect_ratio}. Defaulting to 16:9 (1920x1080).")
            target_resolution = "1920x1080"

        # Construct FFmpeg command for standardization
        standardize_cmd = [
            'ffmpeg',
            '-i', '-',  # Input from stdin (output of previous FFmpeg process)
            '-c:v', target_video_codec,
            '-c:a', target_audio_codec,
            '-ac', str(target_audio_channels),
            '-ar', str(target_audio_sample_rate),
            '-pix_fmt', target_pixel_format,
            '-r', str(target_frame_rate),
            '-vf', f"scale={target_resolution},pad={target_resolution}:(ow-iw)/2:(oh-ih)/2", # Scale and pad to target resolution
            '-f', 'mp4',  # Output format
            '-'  # Output to stdout
        ]

        logger.info(f"Running standardization FFmpeg command: {' '.join(standardize_cmd)}")

        # Run standardization FFmpeg command (with streaming)
        standardize_process = subprocess.Popen(
            standardize_cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        standardized_stdout, standardized_stderr = standardize_process.communicate(input=stdout) # Pass the output of the previous process as input

        if standardize_process.returncode != 0:
            logger.error(f"Standardization FFmpeg command failed. Error: {standardized_stderr.decode()}")
            raise subprocess.CalledProcessError(standardize_process.returncode, standardize_cmd, standardized_stdout, standardized_stderr)


        # Upload the result to GCS
        # Use the imported upload_file utility from services.cloud_storage
        output_gcs_url = upload_file(
            bucket_name=output_gcs_bucket,
            destination_blob_name=output_gcs_blob_name,
            data=standardized_stdout,  # stdout contains the standardized video data from ffmpeg
            content_type='video/mp4'  # Assuming this based on the .mp4 extension in output_gcs_blob_name
        )
        # Assuming upload_file returns the full GCS URI e.g., "gs://bucket/path/to/file.mp4"
        logger.info(f"Video created successfully: {output_gcs_url}")

        return output_gcs_url

    except Exception as e:
        logger.error(f"Error in process_image_to_video: {str(e)}", exc_info=True)
        raise
