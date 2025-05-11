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
import json
import subprocess
import logging
import uuid
import tempfile
from services.file_management import download_file
from services.cloud_storage import upload_file
from config import LOCAL_STORAGE_PATH
from urllib.parse import urlparse # Added import

# Set up logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

def time_to_seconds(time_str):
    """
    Convert a time string in format HH:MM:SS[.mmm] to seconds.
    
    Args:
        time_str (str): Time string
        
    Returns:
        float: Time in seconds
    """
    logger.info(f"Starting video cut operation for {video_url}")
    
    input_filename = None # Initialize input_filename for cleanup
    temp_files = [] # Initialize temp_files list

    try:
        # Use the streaming download_file
        video_stream = download_file(video_url)  # Get a file-like object streaming from GCS

        if video_stream is None:
            logger.error(f"Job {job_id}: Failed to get stream for {video_url}")
            raise Exception("Failed to get stream for video file")

        # Create a temporary file to write the stream to for FFmpeg input
        temp_file_extension = os.path.splitext(urlparse(video_url).path)[1] or '.mp4' # Default to .mp4 if no extension
        input_filename = os.path.join(LOCAL_STORAGE_PATH, f"cut_input_{uuid.uuid4()}{temp_file_extension}")
        temp_files.append(input_filename) # Add to temp_files for cleanup
        
        # Ensure the local directory exists
        os.makedirs(LOCAL_STORAGE_PATH, exist_ok=True)

        # Write the stream to the temporary file
        with open(input_filename, 'wb') as f:
            while True:
                chunk = video_stream.read(8192) # Read in chunks
                if not chunk:
                    break
                f.write(chunk)
        video_stream.close() # Close the stream after writing to temp file

        logger.info(f"Job {job_id}: Streamed and saved video to temporary file: {input_filename}")

        # Get the file extension
        _, ext = os.path.splitext(input_filename)
        
        # Create output filename
        output_filename = os.path.join(LOCAL_STORAGE_PATH, f"{job_id}_output{ext}")
        temp_files.append(output_filename) # Add output file to temp_files

        # Get the duration of the input file
        probe_cmd = [
            'ffprobe', 
            '-v', 'error', 
            '-show_entries', 'format=duration', 
            '-of', 'default=noprint_wrappers=1:nokey=1',
            input_filename
        ]
        duration_result = subprocess.run(probe_cmd, capture_output=True, text=True)
        try:
            file_duration = float(duration_result.stdout.strip())
            logger.info(f"File duration: {file_duration} seconds")
        except (ValueError, IndexError):
            logger.warning("Could not determine file duration, using a large value")
            file_duration = 86400  # 24 hours as a fallback
        
        # Validate and process cuts
        cuts_in_seconds = []
        for cut in cuts:
            start_seconds = time_to_seconds(cut['start'])
            end_seconds = time_to_seconds(cut['end'])
            
            # Validate cut times
            if start_seconds >= end_seconds:
                raise ValueError(f"Invalid cut: start time ({cut['start']}) must be before end time ({cut['end']})")
            
            if start_seconds < 0:
                logger.warning(f"Cut start time {cut['start']} is negative, using 0 instead")
                start_seconds = 0
                
            if end_seconds > file_duration:
                logger.warning(f"Cut end time {cut['end']} exceeds file duration, using file duration instead")
                end_seconds = file_duration
                
            # Only add valid cuts
            if start_seconds < end_seconds:
                cuts_in_seconds.append((start_seconds, end_seconds))
        
        # Sort cuts by start time and merge overlapping segments
        cuts_in_seconds.sort()
        merged_cuts = []
        
        if cuts_in_seconds:
            current_start, current_end = cuts_in_seconds[0]
            for start, end in cuts_in_seconds[1:]:
                if start <= current_end:  # Overlapping segments
                    current_end = max(current_end, end)
                else:  # Non-overlapping, add the previous merged segment
                    merged_cuts.append((current_start, current_end))
                    current_start, current_end = start, end
            # Add the last segment
            merged_cuts.append((current_start, current_end))
        
        logger.info(f"Processing cuts: {merged_cuts}")
        
        if not merged_cuts:
            logger.info("No valid cuts to apply, copying the original file")
            cmd = [
                'ffmpeg',
                '-i', input_filename,
                '-c', 'copy',
                output_filename
            ]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        else:
            # Switch to a different approach: extract segments and concatenate
            segment_files = []
            
            # Create segments to keep
            last_end = 0
            for i, (start, end) in enumerate(merged_cuts):
                # If there's a gap between last segment end and current segment start, extract it
                if start > last_end:
                    segment_file = os.path.join(LOCAL_STORAGE_PATH, f"{job_id}_segment_{i}{ext}")
                    segment_files.append(segment_file)
                    temp_files.append(segment_file)
                    
                    # Extract segment from last_end to start
                    duration = start - last_end
                    cmd = [
                        'ffmpeg',
                        '-i', input_filename,
                        '-ss', str(last_end),
                        '-t', str(duration),
                        '-c:v', video_codec,
                        '-preset', video_preset,
                        '-crf', str(video_crf),
                        '-c:a', audio_codec,
                        '-b:a', audio_bitrate,
                        '-pix_fmt', 'yuv420p',
                        '-vsync', 'cfr',
                        '-r', '30',
                        '-avoid_negative_ts', 'make_zero',
                        segment_file
                    ]
                    logger.info(f"Extracting segment {i}: {' '.join(cmd)}")
                    process = subprocess.run(cmd, capture_output=True, text=True)
                    
                    if process.returncode != 0:
                        logger.error(f"Error during segment {i} extraction: {process.stderr}")
                        raise Exception(f"FFmpeg error: {process.stderr}")
                
                last_end = end
            
            # Add final segment if needed
            if last_end < file_duration:
                segment_file = os.path.join(LOCAL_STORAGE_PATH, f"{job_id}_segment_final{ext}")
                segment_files.append(segment_file)
                temp_files.append(segment_file)
                
                cmd = [
                    'ffmpeg',
                    '-i', input_filename,
                    '-ss', str(last_end),
                    '-c:v', video_codec,
                    '-preset', video_preset,
                    '-crf', str(video_crf),
                    '-c:a', audio_codec,
                    '-b:a', audio_bitrate,
                    '-pix_fmt', 'yuv420p',
                    '-vsync', 'cfr',
                    '-r', '30',
                    '-avoid_negative_ts', 'make_zero',
                    segment_file
                ]
                logger.info(f"Extracting final segment: {' '.join(cmd)}")
                process = subprocess.run(cmd, capture_output=True, text=True)
                
                if process.returncode != 0:
                    logger.error(f"Error during final segment extraction: {process.stderr}")
                    raise Exception(f"FFmpeg error: {process.stderr}")
            
            # If we have segments to concatenate
            if segment_files:
                # Create a concat file
                concat_file = os.path.join(LOCAL_STORAGE_PATH, f"{job_id}_concat.txt")
                temp_files.append(concat_file)
                
                with open(concat_file, 'w') as f:
                    for segment in segment_files:
                        f.write(f"file '{segment}'\n")
                
                # Concatenate the segments
                cmd = [
                    'ffmpeg',
                    '-f', 'concat',
                    '-safe', '0',
                    '-i', concat_file,
                    '-c:v', video_codec,
                    '-preset', video_preset,
                    '-crf', str(video_crf),
                    '-c:a', audio_codec,
                    '-b:a', audio_bitrate,
                    '-vsync', 'cfr',
                    '-r', '30',
                    '-pix_fmt', 'yuv420p',
                    '-movflags', '+faststart',
                    output_filename
                ]
                logger.info(f"Concatenating segments: {' '.join(cmd)}")
                process = subprocess.run(cmd, capture_output=True, text=True)
                
                if process.returncode != 0:
                    logger.error(f"Error during concatenation: {process.stderr}")
                    raise Exception(f"FFmpeg error: {process.stderr}")
            else:
                # No segments to keep
                with open(output_filename, 'wb') as f:
                    # Create an empty file
                    pass
        
        return output_filename, input_filename # Return output_filename and original input_filename (temp file)
        
    except Exception as e:
        logger.error(f"Video cut operation failed: {str(e)}")
        raise
    finally:
        # Clean up all temporary files if they exist
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                os.remove(temp_file)
                logger.info(f"Removed temporary file: {temp_file}")
