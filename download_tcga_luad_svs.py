#!/usr/bin/env python3
"""
Download open-access SVS (whole slide image) files from TCGA-LUAD via the GDC API.

Usage:
    python3 download_tcga_luad_svs.py

Requirements:
    pip install requests   (usually already installed on macOS)

Output:
    Files are saved to ~/Downloads/TCGA_LUAD_SVS/
"""

import requests
import json
import os
import sys
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────────────
GDC_API   = "https://api.gdc.cancer.gov"
SAVE_DIR  = Path.home() / "Downloads" / "TCGA_LUAD_SVS"
N_FILES   = 6          # number of files to download (change as needed)
CHUNK_SIZE = 1024 * 1024  # 1 MB read chunks for streaming download

# ── Step 1: Query the GDC API for open-access SVS files ───────────────────────
def query_files(n: int) -> list[dict]:
    print(f"[1/3] Querying GDC API for {n} open-access SVS files from TCGA-LUAD …")
    payload = {
        "filters": {
            "op": "and",
            "content": [
                {
                    "op": "=",
                    "content": {
                        "field": "cases.project.project_id",
                        "value": "TCGA-LUAD"
                    }
                },
                {
                    "op": "=",
                    "content": {
                        "field": "data_format",
                        "value": "SVS"
                    }
                },
                {
                    "op": "=",
                    "content": {
                        "field": "access",
                        "value": "open"
                    }
                }
            ]
        },
        "fields": "file_id,file_name,file_size",
        "size": n,
        "format": "JSON"
    }

    resp = requests.post(f"{GDC_API}/files", json=payload, timeout=60)
    resp.raise_for_status()
    hits = resp.json()["data"]["hits"]
    print(f"   Found {len(hits)} file(s):\n")
    for h in hits:
        size_mb = h.get("file_size", 0) / 1024 / 1024
        print(f"   • {h['file_name']}  ({size_mb:.0f} MB)  [{h['file_id']}]")
    return hits


# ── Step 2: Download each file ────────────────────────────────────────────────
def download_file(file_id: str, file_name: str, file_size: int, dest_dir: Path) -> None:
    dest_path = dest_dir / file_name
    if dest_path.exists() and dest_path.stat().st_size == file_size:
        print(f"   ✓ Already downloaded: {file_name}")
        return

    url = f"{GDC_API}/data/{file_id}"
    print(f"\n   Downloading: {file_name}")
    print(f"   URL: {url}")
    size_mb = file_size / 1024 / 1024
    print(f"   Size: {size_mb:.1f} MB")

    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        downloaded = 0
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    pct = downloaded / file_size * 100 if file_size else 0
                    mb  = downloaded / 1024 / 1024
                    # overwrite the same line for a simple progress display
                    print(f"\r   Progress: {mb:.1f} / {size_mb:.1f} MB  ({pct:.1f}%)", end="", flush=True)
        print()  # newline after progress

    actual = dest_path.stat().st_size
    if actual == file_size:
        print(f"   ✓ Saved: {dest_path}")
    else:
        print(f"   ⚠  Size mismatch (expected {file_size}, got {actual}) — file may be corrupt")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    print("=" * 60)
    print("  TCGA-LUAD SVS Downloader  (GDC open-access data)")
    print("=" * 60)

    # 1. Query
    try:
        files = query_files(N_FILES)
    except Exception as e:
        print(f"\n[ERROR] Could not query GDC API: {e}")
        sys.exit(1)

    if not files:
        print("\nNo files returned. The query may need adjustment.")
        sys.exit(1)

    # 2. Confirm with user before downloading (files can be large)
    total_gb = sum(f.get("file_size", 0) for f in files) / 1024**3
    print(f"\n[2/3] Total download size: ~{total_gb:.2f} GB")
    print(f"      Files will be saved to: {SAVE_DIR}\n")
    ans = input("Proceed with download? [y/N] ").strip().lower()
    if ans not in ("y", "yes"):
        print("Download cancelled.")
        sys.exit(0)

    # 3. Download
    print(f"\n[3/3] Downloading {len(files)} file(s) …")
    for i, f in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}]", end="")
        try:
            download_file(
                file_id   = f["file_id"],
                file_name = f["file_name"],
                file_size = f.get("file_size", 0),
                dest_dir  = SAVE_DIR,
            )
        except Exception as e:
            print(f"\n   [ERROR] Failed to download {f['file_name']}: {e}")

    print("\n" + "=" * 60)
    print("  Done! Check your files at:")
    print(f"  {SAVE_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
