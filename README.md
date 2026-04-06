# GDC SVS Downloader

Download open-access whole slide images (SVS) from any TCGA project via the [GDC API](https://api.gdc.cancer.gov).

## Install

```bash
pip install -r requirements.txt
```

## Usage

```bash
python3 download_tcga_svs.py --project <PROJECT_ID> [options]
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--project`, `-p` | *(required)* | GDC project ID (e.g. `TCGA-LUAD`, `TCGA-BRCA`) |
| `--n`, `-n` | `6` | Max number of files to fetch |
| `--output`, `-o` | `~/Downloads/<PROJECT>_SVS` | Directory to save files |
| `--cases`, `-c` | — | Text file with case submitter IDs to filter (one per line) |
| `--dry-run` | — | List matching files without downloading |

## Examples

```bash
# Download 6 SVS files from TCGA-LUAD (default)
python3 download_tcga_svs.py --project TCGA-LUAD

# Download up to 20 files from TCGA-BRCA into a custom directory
python3 download_tcga_svs.py --project TCGA-BRCA --n 20 --output ~/data/BRCA_SVS

# Only download SVS files for specific cases
python3 download_tcga_svs.py --project TCGA-LUAD --cases examples/luad_cases.txt

# Check which cases have SVS files without downloading
python3 download_tcga_svs.py --project TCGA-LUAD --cases examples/luad_cases.txt --dry-run
```

## Case ID files

Pass a plain text file with one TCGA case submitter ID per line. Lines starting with `#` are treated as comments and ignored.

```
# my cohort
TCGA-05-4244
TCGA-05-4249
TCGA-05-4382
```

Example files for common projects are in [`examples/`](examples/).

## Notes

- Only **open-access** SVS files are queried — no GDC token required.
- Already-downloaded files are skipped (size-checked).
- Files can be 500 MB–2 GB each — check total size before confirming.
- TCGA case IDs follow the format `TCGA-XX-XXXX`. Find yours via the [GDC Data Portal](https://portal.gdc.cancer.gov).
