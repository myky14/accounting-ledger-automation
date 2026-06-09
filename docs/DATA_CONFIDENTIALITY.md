# Data Confidentiality

This project is designed for accounting automation, so local working folders may contain sensitive client information.

Do not commit:

- real raw ledgers
- real vendor master files
- Tax IDs
- vendor addresses
- employee/vendor personal information
- generated output workbooks
- client-specific config files

Only fake demo data in `sample_data/` should be included in the public repository.

Before pushing, run:

```bash
git status
```

Confirm that `raw/`, `output/`, and real Excel/CSV files are not staged.
