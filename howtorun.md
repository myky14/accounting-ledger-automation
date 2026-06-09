CACH CHAY STREAMLIT

Mo terminal o thu muc chua app.py:

```powershell
.\.venv\Scripts\Activate.ps1
python -m streamlit run app.py
```

Raw Ledger Mode:
- Upload raw hierarchical ledger.
- Click `1. Flatten Ledger`.
- Generate the two review files.
- Upload approved review files.
- Click `4. Enrich Using Approved Reviews`.

Pre-Flattened Ledger Mode:
- Upload an already flattened ledger.
- The app skips flattening and validates required columns.
- Generate the two review files.
- Upload approved review files.
- Click `4. Enrich Using Approved Reviews`.

CLI raw ledger:

```powershell
.\.venv\Scripts\python.exe main.py --config configs/vice.yaml
```

CLI pre-flattened ledger:

```powershell
.\.venv\Scripts\python.exe main.py --config configs/vice.yaml --formatted-ledger "raw/client_flattened.xlsx"
```

CLI final enrichment:

```powershell
.\.venv\Scripts\python.exe main.py --config configs/vice.yaml --approved-review "output/Vendor Match Review.xlsx" --approved-employee-review "output/Employee Extraction Review.xlsx"
```
