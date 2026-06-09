# Streamlit Deployment Audit

Date: 2026-06-09

Goal: determine whether the app can run on Streamlit Community Cloud using only `sample_data/` and uploaded files.

## Overall Classification

**Warning**

The Streamlit app is close to deployment-ready for Streamlit Community Cloud when using the fictional demo config and/or uploaded files. The main warning is that `configs/vice.yaml` points to local `raw/` workbooks that are intentionally ignored and will not exist in a cloud deployment.

No absolute Windows paths were found in the checked runtime files.

## Files Checked

- `app.py`
- `main.py`
- `config_loader.py`
- `configs/vice.yaml`
- `configs/sample_demo.yaml`
- `requirements.txt`
- `utils.py` write helpers

## Classification Summary

| Area | Classification | Notes |
| --- | --- | --- |
| Absolute Windows paths | Ready | No `F:\...`, `C:\...`, `/Users/...`, or `/home/...` paths were found in the checked files. |
| Streamlit app startup | Ready | `app.py` discovers configs from `configs/` and does not require `raw/` or `output/` to exist at import/startup. |
| Demo/sample workflow | Ready | `configs/sample_demo.yaml` points to committed `sample_data/` files and relative `output/sample_demo/...` outputs. |
| Uploaded-file workflow | Ready | Uploaded files are saved under a per-run `output/streamlit_runs/.../inputs` folder created at runtime. |
| Output writes | Warning | Writes are repo-relative under `output/`. This is usually acceptable on Streamlit Cloud's ephemeral filesystem, but files are temporary and should be downloaded by the user. |
| `configs/vice.yaml` cloud readiness | Warning | It references `raw/VICE_Project_GL.xlsx`, `raw/VICE_Vendor_Master.xlsx`, and `raw/VICE_Payroll_Master.xlsx`; these files are ignored and will not exist on Streamlit Cloud. Users must upload files or select a demo config. |
| `main.py` cloud relevance | Warning | CLI default remains `configs/vice.yaml`, which is local-data oriented. This does not block Streamlit app deployment, but CLI examples for cloud/demo should use `configs/sample_demo.yaml`. |
| Dependencies | Ready | `requirements.txt` includes `pandas`, `openpyxl`, `rapidfuzz`, and `streamlit`. `config_loader.py` can use PyYAML when available but also has a fallback parser. |
| `raw/` assumptions | Ready | `app.py` checks whether paths exist before offering them. If `raw/` does not exist, upload controls still work. |
| `output/` assumptions | Ready | `app.py` and `utils.write_workbook_safely` create parent folders before writing. |

## Findings

### Ready

- Runtime paths are relative, not absolute Windows paths.
- `Path(...).mkdir(parents=True, exist_ok=True)` is used for Streamlit run directories and upload folders.
- `utils.write_workbook_safely()` calls `ensure_parent_dir()` before writing workbooks.
- Uploaded raw ledgers, vendor masters, payroll masters, and approved review files are copied into a runtime folder before processing.
- `sample_data/` can support a public demo workflow through `configs/sample_demo.yaml`.
- Missing local `raw/` files do not crash app startup.
- The dependency list is small and compatible with Streamlit Community Cloud.

### Warnings

- `configs/vice.yaml` is not self-contained for cloud use because its input paths point to ignored local files under `raw/`.
- The app will show all YAML configs in `configs/`. If `configs/vice.yaml` is committed, users may select it and then need to upload all required workbooks.
- The app writes generated review/enriched workbooks to `output/`. Streamlit Cloud storage is ephemeral, so generated files should be downloaded during the session.
- `main.py` defaults to `configs/vice.yaml`; this is fine for local CLI use but less ideal for public demo instructions.
- `app.py` still contains some company-specific display text when the selected config has `company/client = VICE`. This is not a deployment blocker, but it is a public-demo presentation warning.

### Must Fix

No must-fix runtime blocker was found for a Streamlit Community Cloud deployment that uses:

- `configs/sample_demo.yaml`
- committed fictional `sample_data/`
- uploaded user files

## File-Specific Notes

### `app.py`

Classification: **Warning**

Ready aspects:

- Uses relative folders.
- Creates `output/streamlit_runs/<timestamp>` at runtime.
- Saves uploads into the run folder.
- Handles missing existing workbooks by disabling the selectbox and asking for uploads.

Warnings:

- Output files are written to ephemeral cloud storage.
- It exposes every YAML/JSON config in `configs/`.
- It includes a special info message for a specific company/client name.

### `main.py`

Classification: **Warning**

Ready aspects:

- Uses relative paths through config.
- Does not affect Streamlit startup directly.

Warnings:

- CLI default is `configs/vice.yaml`.
- Running CLI on Streamlit Cloud is not the primary deployment path.

### `config_loader.py`

Classification: **Ready**

Ready aspects:

- Loads relative config paths.
- Does not require local data files during config load.
- Uses PyYAML if available and a local fallback parser otherwise.
- Applies project-name templates without platform-specific assumptions.

### `configs/vice.yaml`

Classification: **Warning**

Warnings:

- Input paths point to `raw/...`, which is ignored and absent on Streamlit Cloud.
- It can work only if users upload the raw ledger, vendor master, and payroll master through the app, or if cloud-safe demo inputs are configured separately.

### `configs/sample_demo.yaml`

Classification: **Ready**

Ready aspects:

- Uses committed fictional sample files under `sample_data/`.
- Uses relative `output/sample_demo/...` paths.
- Suitable for Streamlit Cloud demo use.

## Recommended Changes

Recommended before public Streamlit Cloud deployment:

1. Set the public demo instructions to use `configs/sample_demo.yaml`.
2. Consider hiding local/private configs from the Streamlit dropdown for public deployments.
3. Consider renaming or replacing public-facing `configs/vice.yaml` with a generic sanitized config if it will be committed.
4. Add a note in the app UI that cloud-generated files are temporary and should be downloaded.
5. Optionally write outputs to a temporary/session directory rather than repo-relative `output/`.

## Deployment Readiness Decision

The app is **ready with warnings** for Streamlit Community Cloud if deployed as a demo using:

- `configs/sample_demo.yaml`
- `sample_data/sample_raw_ledger.xlsx`
- `sample_data/sample_vendor_master.xlsx`
- `sample_data/sample_payroll_master.xlsx`
- uploaded user workbooks

It is **not self-contained with `configs/vice.yaml` alone**, because that config expects local `raw/` files that should not be committed.

## Verification Performed

Commands run:

```powershell
.\.venv\Scripts\python.exe -m py_compile app.py main.py config_loader.py
.\.venv\Scripts\python.exe -c "from config_loader import load_config; print(load_config('configs/vice.yaml')['paths']['raw_ledger']); print(load_config('configs/sample_demo.yaml')['paths']['raw_ledger'])"
rg -n "[A-Za-z]:\\|F:\\|C:\\|/Users/|/home/" app.py main.py config_loader.py configs\vice.yaml
```

Results:

- Python compile check passed for `app.py`, `main.py`, and `config_loader.py`.
- `configs/vice.yaml` loaded and reported `raw/VICE_Project_GL.xlsx`.
- `configs/sample_demo.yaml` loaded and reported `sample_data/sample_raw_ledger.xlsx`.
- Absolute-path scan found no matches.
