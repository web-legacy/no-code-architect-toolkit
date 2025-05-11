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
import ffmpeg
from services.file_management import download_file
from config import LOCAL_STORAGE_PATH
from urllib.parse import urlparse # Added import
import uuid # Added import

def extract_thumbnail(video_url, job_id, second=0):
    """
    Extract a thumbnail from a video at the specified timestamp.
    
    Args:
        video_url (str): URL of the video to extract thumbnail from
        job_id (str): Unique identifier for the job
        second (float): Timestamp in seconds to extract the thumbnail from (default: 0)
        
    Returns:
        str: Path to the extracted thumbnail image
    """
    video_path = None # Initialize video_path for cleanup
    try:
        # Use the streaming download_file
        video_stream = download_file(video_url)  # Get a file-like object streaming from GCS

        if video_stream is None:
            print(f"Job {job_id}: Failed to get stream for {video_url}") # Use print for now, replace with logger
            raise Exception("Failed to get stream for video file")

        # Create a temporary file to write the stream to for ffmpeg.input()
        temp_file_extension = os.path.splitext(urlparse(video_url).path)[1] or '.mp4' # Default to .mp4 if no extension
        video_path = os.path.join(LOCAL_STORAGE_PATH, f"thumbnail_input_{uuid.uuid4()}{temp_file_extension}")
        
        # Ensure the local directory exists
        os.makedirs(LOCAL_STORAGE_PATH, exist_ok=True)

        # Write the stream to the temporary file
        with open(video_path, 'wb') as f:
            while True:
                chunk = video_stream.read(8192) # Read in chunks
                if not chunk:
                    break
                f.write(chunk)
        video_stream.close() # Close the stream after writing to temp file

        print(f"Job {job_id}: Streamed and saved video to temporary file: {video_path}") # Use print for now, replace with logger

        # Set output path for the thumbnail
        thumbnail_path = os.path.join(LOCAL_STORAGE_PATH, f"{job_id}_thumbnail.jpg")
        
        # Extract thumbnail using ffmpeg at the specified timestamp
        (
            ffmpeg
            .input(video_path, ss=second)  # 'ss' is the seek parameter for the timestamp
            .output(thumbnail_path, vframes=1)  # vframes=1 extracts a single frame
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        
        # Ensure the thumbnail file exists
        if not os.path.exists(thumbnail_path):
            raise FileNotFoundError(f"Thumbnail file {thumbnail_path} was not created")
            
        return thumbnail_path
        
    except Exception as e:
        print(f"Thumbnail extraction failed: {str(e)}") # Use print for now, replace with logger
        raise
    finally:
        # Clean up the temporary input video file
        if video_path and os.path.exists(video_path):
            os.remove(video_path)
            print(f"Cleaned up local video file: {video_path}") # Use print for now, replace with logger
