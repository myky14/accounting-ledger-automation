# Legacy Archive

These files are old one-off flattening/enrichment scripts and non-active configs kept for reference only.

The active workflow uses the config-driven engine:

- `ledger_flattener.py` for ledger flattening
- `main.py` for CLI runs
- `app.py` for the Streamlit UI
- `configs/vice.yaml` as the active config

Archived files can be restored by moving them back to their original paths if an old workflow needs to be inspected or rerun.

## Archived Root Scripts

- `format_ledger.py` and `format_ledger2.py`: old standalone flattening scripts. The active flattening workflow uses `ledger_flattener.py`.
- `add_vendor_info.py`: old standalone fuzzy enrichment script. The active workflow uses review-first matching through `vendor_review.py`, `employee_review.py`, and `vendor_matcher.py`.

Restore an archived script only for historical comparison or one-off debugging:

```powershell
Move-Item archive\legacy\add_vendor_info.py add_vendor_info.py
```
