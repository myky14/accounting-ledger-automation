# GitHub Safety Audit: `.gitignore`

Date: 2026-06-09

## Current Status

The repository is structured to keep real accounting data local while allowing safe portfolio/demo assets to be committed.

Protected by `.gitignore`:

- `raw/`
- `output/`
- `.venv/`
- `venv/`
- `__pycache__/`
- `*.pyc`
- `.vscode/`
- `.idea/`
- `~$*.xlsx`
- `*.log`
- `.streamlit/`
- broad Excel/CSV patterns: `*.xlsx`, `*.xls`, `*.csv`
- `archive/legacy/configs/`
- `archive/legacy/configs/**`

Allowed for GitHub/demo use:

- `sample_data/`
- `docs/`
- `screenshots/`
- `configs/`
- `README.md`
- `requirements.txt`

Vietnamese notes:

- `sample_data` dùng cho demo GitHub nên không ignore.
- `raw` chứa dữ liệu thật nên phải ignore.
- `output` chứa file enrich/review nên phải ignore.
- Không commit Vendor Master thật hoặc Payroll Master thật.

## Repository Exposure Summary

Folders protected:

- `raw/`: local source ledgers, vendor masters, payroll masters, and other private inputs.
- `output/`: generated formatted ledgers, review workbooks, enriched workbooks, Streamlit run outputs, and smoke-test outputs.
- `.venv/`, `venv/`, `__pycache__/`: local Python environment and cache files.
- `.streamlit/`: local Streamlit settings or secrets.

Folders exposed by design:

- `sample_data/`: fictional demo Excel files for public portfolio demonstration.
- `configs/`: YAML/JSON configuration files. These should contain only safe demo or sanitized configuration.
- `docs/`: documentation and audit notes.
- `screenshots/`: future public screenshots using fictional data only.
- `tools/`: developer helper scripts.
- `archive/`: legacy code references. Archived legacy configs are ignored because they may contain old client-style names or paths.

## Risks Found

High-risk local files/folders found but protected:

- `raw/` contains ledger, payroll master, and vendor master workbook names.
- `output/` contains formatted ledger, vendor review, employee review, and final enriched workbook outputs.
- `output/streamlit_runs/` can contain uploaded inputs and approved review files.

Potential review items before public push:

- `configs/` is intentionally not ignored. Confirm every config file is safe, generic, or demo-only before committing.
- `archive/legacy/configs/` contains old archived config files and is now ignored.
- Demo data in `sample_data/` should remain fictional.

## Recommended Changes

Applied changes:

- Removed ignore rules that hid all `configs/*.yaml`, `configs/*.yml`, and `configs/*.json`.
- Added explicit `configs/`, `docs/`, `screenshots/`, `README.md`, and `requirements.txt` allow rules.
- Added missing `.idea/` ignore rule.
- Added missing `*.log` ignore rule.
- Added `archive/legacy/configs/` ignore rule for old archived configs.
- Kept `raw/`, `output/`, `.venv/`, `venv/`, `__pycache__/`, `*.pyc`, `.vscode/`, `~$*.xlsx`, and `.streamlit/` ignored.
- Kept broad `*.xlsx`, `*.xls`, and `*.csv` ignores with exceptions for `sample_data/`.

## Verification Goals

Expected behavior:

- `sample_data/` remains visible to Git and can be committed.
- `raw/` remains ignored.
- `output/` remains ignored.
- `configs/` remains visible to Git.
- Documentation remains visible to Git.
- `archive/legacy/configs/` remains ignored.

Verified behavior:

- `git status --ignored --short` shows `sample_data/`, `configs/`, `docs/`, `README.md`, and `requirements.txt` as visible/untracked.
- `git status --ignored --short` shows `raw/`, `output/`, `.venv/`, `__pycache__/`, and `tools/__pycache__/` as ignored.
- `git check-ignore -v raw` matched `.gitignore:19:raw/`.
- `git check-ignore -v output` matched `.gitignore:20:output/`.
- `git check-ignore -v sample_data\sample_vendor_master.xlsx` matched the allow rule `!sample_data/*.xlsx`.
- `git check-ignore -v configs\sample_demo.yaml` matched the allow rule `!configs/*.yaml`.
- `git check-ignore -v archive\legacy\configs\itv.yaml` matched `archive/legacy/configs/**`.

## Final Approved `.gitignore` Template

```gitignore
# Python environments and caches
.venv/
venv/
__pycache__/
*.pyc
*.pyo
*.pyd
.pytest_cache/

# Local editor and IDE settings
.vscode/
.idea/

# Secrets and local environment files
.env
.env.*

# Confidential accounting data and generated outputs
raw/
output/
logs/
*.log

# Streamlit local settings/secrets
.streamlit/

# Excel/CSV files may contain real accounting data.
# Keep sample_data exceptions below for fake demo files only.
*.xlsx
*.xls
*.csv
~$*.xlsx
~$*.xls

# Safe fake demo data for GitHub portfolio use
!sample_data/
!sample_data/*.xlsx
!sample_data/*.xls
!sample_data/*.csv

# Archived legacy configs may contain old client-style names or paths.
archive/legacy/configs/
archive/legacy/configs/**

# Public project assets and documentation
!configs/
!configs/*.yaml
!configs/*.yml
!configs/*.json
!docs/
!docs/**
!screenshots/
!screenshots/**
!README.md
!requirements.txt

# OS files
.DS_Store
Thumbs.db
```
