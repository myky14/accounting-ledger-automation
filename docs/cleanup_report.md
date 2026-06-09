# Legacy Cleanup Report

Date: 2026-06-09

## Summary

The current workflow is config-driven and uses `main.py`, `app.py`, `config_loader.py`, `ledger_flattener.py`, `vendor_review.py`, `employee_review.py`, `vendor_matcher.py`, `utils.py`, and `configs/vice.yaml`.

Không xóa thẳng legacy files, chỉ archive để rollback nếu cần.
Workflow hiện tại dùng config-driven engine, không dùng script format_ledger cũ nữa.

## Files Checked

| File | Status | Action Taken | Why It Is Safe |
| --- | --- | --- | --- |
| `format_ledger.py` | Legacy | Moved to `archive/legacy/format_ledger.py` | No imports, calls, README instructions, debug scripts, or current workflow references were found. Its standalone flattening logic is handled by `ledger_flattener.py`. |
| `format_ledger2.py` | Legacy | Moved to `archive/legacy/format_ledger2.py` | No imports, calls, README instructions, debug scripts, or current workflow references were found. Its standalone flattening logic is handled by `ledger_flattener.py`. |
| `configs/itv.yaml` | Legacy | Moved to `archive/legacy/configs/itv.yaml` | It was only referenced by stale CLI defaults/docs. `main.py` now defaults to `configs/vice.yaml`, and docs were updated. |
| `configs/HA2.yaml` | Legacy | Moved to `archive/legacy/configs/HA2.yaml` | No current imports, app selection requirements, README instructions, main workflow requirements, or debug script requirements were found. |
| `configs/sample.yaml` | Legacy demo config | Moved to `archive/legacy/configs/sample.yaml` | It was not referenced by current code or docs and would otherwise appear in the Streamlit config dropdown. `configs/vice.yaml` remains the active config. |
| `configs/vice.yaml` | Active | Kept in `configs/vice.yaml` | This is the active VICE config for the current config-driven workflow. |

## Reference Audit

The project was searched for references to:

- `format_ledger.py`
- `format_ledger2.py`
- `configs/itv.yaml`
- `configs/HA2.yaml`
- `itv.yaml`
- `HA2.yaml`

Findings:

- `format_ledger.py` and `format_ledger2.py` were not imported or called by active modules.
- `app.py` imports and uses `ledger_flattener.flatten_ledger`.
- `main.py` imports and uses `ledger_flattener.flatten_ledger`.
- `README.md` already documented `configs/vice.yaml` as the active VICE config.
- `howtorun.md` and the old `main.py` CLI default referenced `configs/itv.yaml`; both were updated to `configs/vice.yaml`.
- `config_loader.py` had a compatibility comment mentioning `configs/itv.yaml`; the comment was generalized.

## Restore Instructions

To restore archived files, move them back from `archive/legacy/` to their original locations:

```powershell
Move-Item archive\legacy\format_ledger.py format_ledger.py
Move-Item archive\legacy\format_ledger2.py format_ledger2.py
Move-Item archive\legacy\configs\itv.yaml configs\itv.yaml
Move-Item archive\legacy\configs\HA2.yaml configs\HA2.yaml
Move-Item archive\legacy\configs\sample.yaml configs\sample.yaml
```

If restoring `configs/itv.yaml` as an active default, also update `main.py`, README docs, and Streamlit config expectations accordingly.

## Verification

Completed checks:

```powershell
.\.venv\Scripts\python.exe -m py_compile main.py app.py config_loader.py ledger_flattener.py vendor_review.py employee_review.py vendor_matcher.py utils.py
.\.venv\Scripts\python.exe main.py --config configs/vice.yaml --help
.\.venv\Scripts\python.exe -c "from config_loader import load_config; c=load_config('configs/vice.yaml'); print(c.get('company') or c.get('client')); print(c['paths']['raw_ledger'])"
```

Results:

- Python compile check passed.
- CLI help check passed and shows `Default: configs/vice.yaml`.
- `configs/vice.yaml` loads successfully.
- No full pipeline run was performed.

## Pre-Commit Root Cleanup Audit

Date: 2026-06-09

Vietnamese note:

Không xóa thẳng legacy/debug files trước khi push GitHub.
Chỉ archive để repo sạch hơn nhưng vẫn có thể rollback nếu cần.

Files checked:

| File | Status | Action Taken | Why |
| --- | --- | --- | --- |
| `add_vendor_info.py` | Legacy | Moved to `archive/legacy/add_vendor_info.py` | No active imports, calls, CLI usage, Streamlit usage, config references, or README/howtorun/docs instructions were found. It duplicates older fuzzy enrichment behavior now handled by the review-first workflow in `vendor_review.py`, `employee_review.py`, and `vendor_matcher.py`. |
| `formatted_ledger.py` | Active | Kept at root | `main.py` and `app.py` import `load_preflattened_ledger` from this module for Pre-Flattened Ledger Mode. Moving it would break the current workflow. |
| `debug_payroll_extraction.py` | Debug tool | Moved to `tools/debug_payroll_extraction.py` | It is not part of the app or CLI workflow, but remains useful as a focused smoke-test script for payroll employee-token extraction examples. |

Reference audit:

- Searched for `add_vendor_info`, `formatted_ledger`, and `debug_payroll_extraction` across the repository, excluding `sample_data/`.
- `add_vendor_info.py` was only self-contained legacy code.
- `formatted_ledger.py` is active and required by both CLI and Streamlit Pre-Flattened Ledger Mode.
- `debug_payroll_extraction.py` imports `extract_employee_from_payroll_description` from `vendor_matcher.py` and contains assertion examples only.

Restore or run instructions:

```powershell
Move-Item archive\legacy\add_vendor_info.py add_vendor_info.py
.\.venv\Scripts\python.exe tools\debug_payroll_extraction.py
```

Verification after this cleanup:

```powershell
.\.venv\Scripts\python.exe -m py_compile main.py app.py config_loader.py ledger_flattener.py vendor_review.py employee_review.py vendor_matcher.py utils.py
.\.venv\Scripts\python.exe main.py --config configs/vice.yaml --help
```

Results:

- Requested active-module compile check passed.
- CLI help check passed and shows `Default: configs/vice.yaml`.
- Extra compile check for `formatted_ledger.py` and `tools/debug_payroll_extraction.py` passed.
- `tools/debug_payroll_extraction.py` ran successfully using hard-coded examples only.
