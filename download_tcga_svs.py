#!/usr/bin/env python3
"""
Download open-access SVS (whole slide image) files from any TCGA project via the GDC API.

Usage:
    # Download 6 SVS files from TCGA-LUAD
    python3 download_tcga_svs.py --project TCGA-LUAD

    # Download up to 20 files from TCGA-BRCA into a custom directory
    python3 download_tcga_svs.py --project TCGA-BRCA --n 20 --output ~/Downloads/BRCA_SVS

    # Only download SVS files for specific case/sample IDs (one per line in a text file)
    python3 download_tcga_svs.py --project TCGA-LUAD --cases my_sample_ids.txt

    # Check which cases have SVS files without downloading
    python3 download_tcga_svs.py --project TCGA-LUAD --cases my_sample_ids.txt --dry-run

Requirements:
    pip install requests
"""

import argparse
import requests
import sys
from pathlib import Path

GDC_API    = "https://api.gdc.cancer.gov"
CHUNK_SIZE = 1024 * 1024  # 1 MB


# ── Query ─────────────────────────────────────────────────────────────────────

def build_filters(project_id: str, case_ids: list[str] | None) -> dict:
    conditions = [
        {"op": "=", "content": {"field": "cases.project.project_id", "value": project_id}},
        {"op": "=", "content": {"field": "data_format",              "value": "SVS"}},
        {"op": "=", "content": {"field": "access",                   "value": "open"}},
    ]
    if case_ids:
        conditions.append({
            "op": "in",
            "content": {"field": "cases.submitter_id", "value": case_ids}
        })
    return {"op": "and", "content": conditions}


def query_files(project_id: str, n: int, case_ids: list[str] | None) -> list[dict]:
    label = f"up to {n}" if not case_ids else f"up to {n} (filtered to {len(case_ids)} case IDs)"
    print(f"[1/3] Querying GDC API — project: {project_id}, {label} SVS files …")

    payload = {
        "filters": build_filters(project_id, case_ids),
        "fields": "file_id,file_name,file_size,cases.submitter_id",
        "size": n,
        "format": "JSON",
    }

    resp = requests.post(f"{GDC_API}/files", json=payload, timeout=60)
    resp.raise_for_status()
    hits = resp.json()["data"]["hits"]

    print(f"   Found {len(hits)} file(s):\n")
    for h in hits:
        size_mb = h.get("file_size", 0) / 1024 / 1024
        cases   = h.get("cases", [])
        case_id = cases[0]["submitter_id"] if cases else "unknown"
        print(f"   • {h['file_name']}  ({size_mb:.0f} MB)  case={case_id}  [{h['file_id']}]")
    return hits


# ── Download ──────────────────────────────────────────────────────────────────

def download_file(file_id: str, file_name: str, file_size: int, dest_dir: Path) -> None:
    dest_path = dest_dir / file_name
    if dest_path.exists() and dest_path.stat().st_size == file_size:
        print(f"   ✓ Already downloaded: {file_name}")
        return

    url      = f"{GDC_API}/data/{file_id}"
    size_mb  = file_size / 1024 / 1024
    print(f"\n   Downloading: {file_name}  ({size_mb:.1f} MB)")

    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        downloaded = 0
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    pct = downloaded / file_size * 100 if file_size else 0
                    print(f"\r   Progress: {downloaded/1024/1024:.1f} / {size_mb:.1f} MB  ({pct:.1f}%)",
                          end="", flush=True)
        print()

    actual = dest_path.stat().st_size
    if actual == file_size:
        print(f"   ✓ Saved: {dest_path}")
    else:
        print(f"   ⚠  Size mismatch (expected {file_size}, got {actual}) — file may be corrupt")


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download open-access SVS files from any TCGA project via the GDC API."
    )
    parser.add_argument(
        "--project", "-p", required=True,
        help="GDC project ID, e.g. TCGA-LUAD, TCGA-BRCA, TCGA-COAD"
    )
    parser.add_argument(
        "--n", "-n", type=int, default=6,
        help="Max number of files to download (default: 6)"
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Directory to save files (default: ~/Downloads/<PROJECT>_SVS)"
    )
    parser.add_argument(
        "--cases", "-c", type=Path, default=None,
        help="Text file with case/sample submitter IDs (one per line) to filter downloads"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Query and list files without downloading"
    )
    return parser.parse_args()


def load_case_ids(path: Path) -> list[str]:
    ids = [line.strip() for line in path.read_text().splitlines()
           if line.strip() and not line.startswith("#")]
    print(f"   Loaded {len(ids)} case ID(s) from {path}")
    return ids


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    save_dir = args.output or (Path.home() / "Downloads" / f"{args.project}_SVS")
    case_ids = load_case_ids(args.cases) if args.cases else None

    print("=" * 60)
    print(f"  GDC SVS Downloader — {args.project}")
    print("=" * 60)

    # 1. Query
    try:
        files = query_files(args.project, args.n, case_ids)
    except Exception as e:
        print(f"\n[ERROR] Could not query GDC API: {e}")
        sys.exit(1)

    if not files:
        print("\nNo SVS files found for the given filters.")
        sys.exit(0)

    if args.dry_run:
        print("\n[dry-run] Skipping download.")
        sys.exit(0)

    # 2. Confirm
    total_gb = sum(f.get("file_size", 0) for f in files) / 1024**3
    print(f"\n[2/3] Total download size: ~{total_gb:.2f} GB")
    print(f"      Destination: {save_dir}\n")
    ans = input("Proceed with download? [y/N] ").strip().lower()
    if ans not in ("y", "yes"):
        print("Download cancelled.")
        sys.exit(0)

    # 3. Download
    save_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n[3/3] Downloading {len(files)} file(s) …")
    for i, f in enumerate(files, 1):
        print(f"\n[{i}/{len(files)}]", end=" ")
        try:
            download_file(
                file_id   = f["file_id"],
                file_name = f["file_name"],
                file_size = f.get("file_size", 0),
                dest_dir  = save_dir,
            )
        except Exception as e:
            print(f"\n   [ERROR] Failed to download {f['file_name']}: {e}")

    print("\n" + "=" * 60)
    print(f"  Done! Files saved to: {save_dir}")
    print("=" * 60)


if __name__ == "__main__":
    main()
