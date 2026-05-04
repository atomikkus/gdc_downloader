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
    pip install requests google-cloud-storage
"""

import argparse
import requests
import sys
import shutil
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from google.cloud import storage

GDC_API    = "https://api.gdc.cancer.gov"
CHUNK_SIZE = 1024 * 1024  # 1 MB


# ── Query ─────────────────────────────────────────────────────────────────────

def build_filters(project_id: str, case_ids: list[str] | None, file_ids: list[str] | None) -> dict:
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
    if file_ids:
        conditions.append({
            "op": "in",
            "content": {"field": "file_id", "value": file_ids}
        })
    return {"op": "and", "content": conditions}


def query_files(project_id: str, n: int, case_ids: list[str] | None, file_ids: list[str] | None) -> list[dict]:
    label = f"up to {n}"
    if case_ids:
        label = f"up to {n} (filtered to {len(case_ids)} case IDs)"
    if file_ids:
        label = f"up to {n} (filtered to {len(file_ids)} file IDs)"
    print(f"[1/3] Querying GDC API — project: {project_id}, {label} SVS files …")

    payload = {
        "filters": build_filters(project_id, case_ids, file_ids),
        "fields": "file_id,file_name,file_size,cases.submitter_id",
        "size": n,
        "format": "JSON",
    }

    resp = requests.post(f"{GDC_API}/files", json=payload, timeout=60)
    resp.raise_for_status()
    response_data = resp.json()["data"]
    hits = response_data["hits"]
    pagination = response_data.get("pagination", {})
    total_matching = pagination.get("total", len(hits))

    print(f"   Total matching SVS files: {total_matching}")
    print(f"   Returned this run: {len(hits)} (requested up to {n})\n")
    for h in hits:
        size_mb = h.get("file_size", 0) / 1024 / 1024
        cases   = h.get("cases", [])
        case_id = cases[0]["submitter_id"] if cases else "unknown"
        print(f"   • {h['file_name']}  ({size_mb:.0f} MB)  case={case_id}  [{h['file_id']}]")
    return hits


# ── Download ──────────────────────────────────────────────────────────────────

def parse_gcs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError("GCS output must start with gs://")
    remainder = uri[len("gs://"):]
    if not remainder or "/" not in remainder:
        raise ValueError("GCS output must include bucket and prefix, e.g. gs://my-bucket/my-prefix")
    bucket_name, prefix = remainder.split("/", 1)
    prefix = prefix.strip("/")
    if not bucket_name or not prefix:
        raise ValueError("GCS output must include non-empty bucket and prefix")
    return bucket_name, prefix


def download_file(file_id: str, file_name: str, file_size: int, dest_dir: Path, show_progress: bool = True) -> None:
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
                    if show_progress:
                        print(f"\r   Progress: {downloaded/1024/1024:.1f} / {size_mb:.1f} MB  ({pct:.1f}%)",
                              end="", flush=True)
        if show_progress:
            print()

    actual = dest_path.stat().st_size
    if actual == file_size:
        print(f"   ✓ Saved: {dest_path}")
    else:
        print(f"   ⚠  Size mismatch (expected {file_size}, got {actual}) — file may be corrupt")


def download_file_to_gcs(
    file_id: str,
    file_name: str,
    file_size: int,
    bucket: storage.Bucket,
    prefix: str,
    show_progress: bool = True,
) -> None:
    blob_path = f"{prefix}/{file_name}" if prefix else file_name
    blob = bucket.blob(blob_path)
    if blob.exists():
        blob.reload()
        if blob.size == file_size:
            print(f"   ✓ Already uploaded: gs://{bucket.name}/{blob_path}")
            return

    url = f"{GDC_API}/data/{file_id}"
    size_mb = file_size / 1024 / 1024
    print(f"\n   Uploading to GCS: {file_name}  ({size_mb:.1f} MB)")

    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        downloaded = 0
        with blob.open("wb", chunk_size=CHUNK_SIZE) as gcs_writer:
            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    gcs_writer.write(chunk)
                    downloaded += len(chunk)
                    pct = downloaded / file_size * 100 if file_size else 0
                    if show_progress:
                        print(
                            f"\r   Progress: {downloaded/1024/1024:.1f} / {size_mb:.1f} MB  ({pct:.1f}%)",
                            end="",
                            flush=True,
                        )
        if show_progress:
            print()

    blob.reload()
    if blob.size == file_size:
        print(f"   ✓ Uploaded: gs://{bucket.name}/{blob_path}")
    else:
        print(f"   ⚠  Size mismatch in GCS (expected {file_size}, got {blob.size})")


def sync_local_to_gcs(local_dir: Path, gcs_uri: str) -> None:
    source = str(local_dir)
    destination = gcs_uri.rstrip("/") + "/"

    gsutil_cmd = shutil.which("gsutil")
    if gsutil_cmd:
        cmd = [gsutil_cmd, "-m", "cp", "-r", source, destination]
        print(f"\n[4/4] Syncing local files to GCS with gsutil …")
        print(f"      Command: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        print("   ✓ GCS sync completed.")
        return

    gcloud_cmd = shutil.which("gcloud")
    if gcloud_cmd:
        cmd = [gcloud_cmd, "storage", "cp", "--recursive", source, destination]
        print(f"\n[4/4] Syncing local files to GCS with gcloud …")
        print(f"      Command: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        print("   ✓ GCS sync completed.")
        return

    raise RuntimeError(
        "Neither gsutil nor gcloud CLI was found in PATH. Install Google Cloud SDK tools to use local->GCS sync mode."
    )


def sync_files_to_gcs(file_paths: list[Path], gcs_uri: str) -> None:
    if not file_paths:
        return

    destination = gcs_uri.rstrip("/") + "/"
    sources = [str(path) for path in file_paths]

    gsutil_cmd = shutil.which("gsutil")
    if gsutil_cmd:
        cmd = [gsutil_cmd, "-m", "cp", "-n", *sources, destination]
        print(f"\n[4/4] Syncing batch ({len(file_paths)} files) to GCS with gsutil …")
        print(f"      Command: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        print("   ✓ Batch GCS sync completed.")
        return

    gcloud_cmd = shutil.which("gcloud")
    if gcloud_cmd:
        cmd = [gcloud_cmd, "storage", "cp", "--no-clobber", *sources, destination]
        print(f"\n[4/4] Syncing batch ({len(file_paths)} files) to GCS with gcloud …")
        print(f"      Command: {' '.join(cmd)}")
        subprocess.run(cmd, check=True)
        print("   ✓ Batch GCS sync completed.")
        return

    raise RuntimeError(
        "Neither gsutil nor gcloud CLI was found in PATH. Install Google Cloud SDK tools to use local->GCS sync mode."
    )


def cleanup_local_dir(local_dir: Path) -> None:
    if not local_dir.exists():
        return
    shutil.rmtree(local_dir)
    print(f"   ✓ Cleaned local directory: {local_dir}")


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
        "--workers", type=int, default=4,
        help="Number of parallel file transfers (default: 4)"
    )
    parser.add_argument(
        "--output", "-o", type=Path, default=None,
        help="Directory to save files (default: ~/Downloads/<PROJECT>_SVS)"
    )
    parser.add_argument(
        "--output-gcs", type=str, default=None,
        help="GCS destination prefix, e.g. gs://my-bucket/my-prefix. If combined with --output, local download happens first and then gcloud sync uploads to GCS."
    )
    parser.add_argument(
        "--keep-local", action="store_true",
        help="When using --output with --output-gcs, keep local files after successful GCS sync (default is cleanup)."
    )
    parser.add_argument(
        "--batch-gb", type=float, default=50.0,
        help="In hybrid mode (--output with --output-gcs), upload and cleanup after this much completed local data (GB). Default: 50"
    )
    parser.add_argument(
        "--cases", "-c", type=Path, default=None,
        help="Text file with case/sample submitter IDs (one per line) to filter downloads"
    )
    parser.add_argument(
        "--file-ids", type=Path, default=None,
        help="Text file with GDC file IDs (UUIDs), one per line, to download specific files"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Query and list files without downloading"
    )
    parser.add_argument(
        "--auto-yes", "-y", action="store_true",
        help="Skip confirmation prompt and start transfer immediately"
    )
    return parser.parse_args()


def load_case_ids(path: Path) -> list[str]:
    ids = [line.strip() for line in path.read_text().splitlines()
           if line.strip() and not line.startswith("#")]
    print(f"   Loaded {len(ids)} case ID(s) from {path}")
    return ids


def load_file_ids(path: Path) -> list[str]:
    ids = [line.strip() for line in path.read_text().splitlines()
           if line.strip() and not line.startswith("#")]
    print(f"   Loaded {len(ids)} file ID(s) from {path}")
    return ids


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    if args.workers < 1:
        print("[ERROR] --workers must be >= 1")
        sys.exit(1)
    if args.batch_gb <= 0:
        print("[ERROR] --batch-gb must be > 0")
        sys.exit(1)

    hybrid_sync_mode = bool(args.output and args.output_gcs)
    save_dir = args.output or (Path.home() / "Downloads" / f"{args.project}_SVS")
    gcs_bucket = None
    gcs_prefix = ""
    if args.output_gcs:
        try:
            bucket_name, gcs_prefix = parse_gcs_uri(args.output_gcs)
            if hybrid_sync_mode:
                # Validate URI early; actual transfer is via gcloud sync.
                pass
            else:
                gcs_bucket = storage.Client().bucket(bucket_name)
        except Exception as e:
            print(f"[ERROR] Invalid GCS output configuration: {e}")
            sys.exit(1)
    case_ids = load_case_ids(args.cases) if args.cases else None
    file_ids = load_file_ids(args.file_ids) if args.file_ids else None
    if file_ids and args.n < len(file_ids):
        print(f"   [INFO] Increasing --n from {args.n} to {len(file_ids)} to include all requested file IDs.")
        args.n = len(file_ids)

    print("=" * 60)
    print(f"  GDC SVS Downloader — {args.project}")
    print("=" * 60)

    # 1. Query
    try:
        files = query_files(args.project, args.n, case_ids, file_ids)
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
    if hybrid_sync_mode:
        destination = f"{save_dir} -> {args.output_gcs}"
    elif args.output_gcs:
        destination = args.output_gcs
    else:
        destination = str(save_dir)
    print(f"      Destination: {destination}\n")
    if args.auto_yes:
        print("Auto-confirm enabled (--auto-yes). Proceeding without prompt.")
    else:
        ans = input("Proceed with download? [y/N] ").strip().lower()
        if ans not in ("y", "yes"):
            print("Download cancelled.")
            sys.exit(0)

    # 3. Download
    save_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n[3/3] Downloading {len(files)} file(s) with {args.workers} worker(s) …")
    show_progress = args.workers == 1
    batch_threshold_bytes = int(args.batch_gb * (1024**3))
    pending_batch_files: list[Path] = []
    pending_batch_bytes = 0

    def transfer_one(file_info: dict) -> tuple[str, str | None]:
        file_name = file_info["file_name"]
        try:
            if gcs_bucket:
                download_file_to_gcs(
                    file_id=file_info["file_id"],
                    file_name=file_name,
                    file_size=file_info.get("file_size", 0),
                    bucket=gcs_bucket,
                    prefix=gcs_prefix,
                    show_progress=show_progress,
                )
            else:
                download_file(
                    file_id=file_info["file_id"],
                    file_name=file_name,
                    file_size=file_info.get("file_size", 0),
                    dest_dir=save_dir,
                    show_progress=show_progress,
                )
            return file_name, None
        except Exception as e:
            return file_name, str(e)

    file_info_by_name = {f["file_name"]: f for f in files}
    errors = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(transfer_one, f): f for f in files}
        for idx, future in enumerate(as_completed(futures), 1):
            file_name, error = future.result()
            if error:
                errors += 1
                print(f"\n[{idx}/{len(files)}] [ERROR] Failed: {file_name}: {error}")
                continue

            if not show_progress:
                print(f"\n[{idx}/{len(files)}] ✓ Completed: {file_name}")

            if hybrid_sync_mode:
                file_path = save_dir / file_name
                if file_path.exists():
                    pending_batch_files.append(file_path)
                    pending_batch_bytes += file_path.stat().st_size
                else:
                    expected_size = file_info_by_name[file_name].get("file_size", 0)
                    if expected_size:
                        pending_batch_bytes += expected_size

                if pending_batch_bytes >= batch_threshold_bytes and pending_batch_files:
                    batch_size_gb = pending_batch_bytes / (1024**3)
                    print(
                        f"\n   [batch] Threshold reached (~{batch_size_gb:.2f} GB). "
                        f"Syncing {len(pending_batch_files)} files to GCS …"
                    )
                    sync_files_to_gcs(pending_batch_files, args.output_gcs)
                    if args.keep_local:
                        print("   [batch] Keeping local files as requested (--keep-local).")
                    else:
                        for path in pending_batch_files:
                            if path.exists():
                                path.unlink()
                        print(f"   [batch] Cleaned {len(pending_batch_files)} local file(s).")
                    pending_batch_files = []
                    pending_batch_bytes = 0

    print("\n" + "=" * 60)
    if hybrid_sync_mode:
        print(f"  Download stage complete. Files saved to: {save_dir}")
    elif args.output_gcs:
        print(f"  Done! Files uploaded to: {args.output_gcs}")
    else:
        print(f"  Done! Files saved to: {save_dir}")
    if errors:
        print(f"  Completed with {errors} failed file(s).")
    print("=" * 60)

    if hybrid_sync_mode and not errors:
        try:
            if pending_batch_files:
                remaining_size_gb = pending_batch_bytes / (1024**3)
                print(
                    f"\n   [batch] Final flush (~{remaining_size_gb:.2f} GB, "
                    f"{len(pending_batch_files)} files) …"
                )
                sync_files_to_gcs(pending_batch_files, args.output_gcs)
                if args.keep_local:
                    print("   [batch] Keeping local files as requested (--keep-local).")
                else:
                    for path in pending_batch_files:
                        if path.exists():
                            path.unlink()
                    print(f"   [batch] Cleaned {len(pending_batch_files)} local file(s).")

            if not args.keep_local:
                cleanup_local_dir(save_dir)
        except Exception as e:
            print(f"\n[ERROR] Local download succeeded, but GCS sync failed: {e}")
            sys.exit(1)


if __name__ == "__main__":
    main()
