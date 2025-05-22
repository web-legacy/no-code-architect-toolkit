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
import uuid
import requests
from urllib.parse import urlparse, parse_qs
import mimetypes
from google.cloud import storage
import json # Added import for parsing JSON credentials
from google.oauth2 import service_account # Added import for service account credentials

# Assuming a local storage path is configured (still needed for temp files if any)
LOCAL_STORAGE_PATH = os.environ.get('LOCAL_STORAGE_PATH', '/tmp')
GCP_SA_CREDENTIALS_JSON = os.environ.get('GCP_SA_CREDENTIALS', '{}') # Get credentials JSON from env var

def get_extension_from_url(url):
    """Extract file extension from URL or content type.
    
    Args:
        url (str): The URL to extract the extension from
        
    Returns:
        str: The file extension including the dot (e.g., '.jpg')
        
    Raises:
        ValueError: If no valid extension can be determined from the URL or content type
    """
    # First try to get extension from URL
    parsed_url = urlparse(url)
    path = parsed_url.path
    if path:
        ext = os.path.splitext(path)[1].lower()
        if ext:
            return ext

    # If no extension in URL, try to determine from content type
    try:
        response = requests.head(url, allow_redirects=True)
        content_type = response.headers.get('content-type', '').split(';')[0]
        ext = mimetypes.guess_extension(content_type)
        if ext:
            return ext.lower()
    except:
        pass

    # If we can't determine the extension, raise an error
    raise ValueError(f"Could not determine file extension from URL: {url}")


def download_file(gcs_url):
    """Streams a file from GCS.  Does NOT download to local disk."""
    try:
        # Parse the GCS URL to extract bucket and blob names
        parsed_url = urlparse(gcs_url)
        
        if parsed_url.scheme == 'gs':
            # Handle gs:// format
            bucket_name = parsed_url.netloc
            blob_name = parsed_url.path.lstrip('/')
        elif parsed_url.scheme == 'https' and parsed_url.netloc == 'storage.googleapis.com':
            # Handle https://storage.googleapis.com/bucket-name/object-name format
            path_parts = parsed_url.path.lstrip('/').split('/', 1)
            if len(path_parts) < 2:
                 raise ValueError(f"Invalid GCS HTTPS URL format: {gcs_url}")
            bucket_name = path_parts[0]
            blob_name = path_parts[1]
        else:
            raise ValueError(f"Unsupported GCS URL format: {gcs_url}")

        # Explicitly load credentials from the environment variable
        credentials = None
        if GCP_SA_CREDENTIALS_JSON:
            try:
                credentials_info = json.loads(GCP_SA_CREDENTIALS_JSON)
                credentials = service_account.Credentials.from_service_account_info(credentials_info)
            except Exception as e:
                print(f"Error loading GCP service account credentials from environment variable: {e}")
                # Fallback to default credential discovery if explicit loading fails
                credentials = None 

        # Initialize storage client with explicit credentials if loaded, otherwise use default discovery
        storage_client = storage.Client(credentials=credentials)

        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        # Return a file-like object streaming the data; the actual data is not downloaded here!
        return blob.open("rb")  # 'rb' is crucial for reading binary data

    except Exception as e:
        print(f"Error downloading from GCS: {e}")  # Replace with proper logging
        return None # Better error handling is required in a production-ready setting
