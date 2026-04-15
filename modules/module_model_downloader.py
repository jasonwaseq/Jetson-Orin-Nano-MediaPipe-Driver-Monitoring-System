import os
import urllib.request

def download_model(url, path):
    """Download model if not exists"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        print(f"Downloading {os.path.basename(path)} (~5MB)...")
        urllib.request.urlretrieve(url, path)
        print("Download complete!")
    else:
        print(f"Model found at {path}")
