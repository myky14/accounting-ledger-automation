import re

import pandas as pd

from utils import (
    RunLog,
    amount_to_number,
    contains_any_keyword,
    get_cell,
    is_blank,
    is_transaction_date,
    read_workbook,
    resolve_column,
    resolve_column_map,
    row_snapshot,
    safe_excel_text,
    safe_value,
)


OUTPUT_COLUMNS = [
    "Account",
    "Account Name",
    "Trans Date",
    "Vendor ID",
    "Vendor Name",
    "Src",
    "Trans Ref",
    "Description",
    "Additional Description",
    "Our Reference",
    "Currency",
    "USD",
    "Amt",
    "CAD",
    "Amount",
    "Ep",
    "Flatten Warnings",
]


def flatten_ledger(config):
    """Flatten one hierarchical ledger according to the client config."""
    log = RunLog()
    ledger_config = config["ledger"]
    paths = config["paths"]

    df = read_workbook(paths["raw_ledger"], ledger_config.get("sheet_name", 0))
    columns = resolve_column_map(df, ledger_config["columns"], "ledger.columns")

    account_header_config = ledger_config["account_header"]
    account_columns = {
        "marker_column": resolve_column(
            df, account_header_config["marker_column"], "ledger.account_header.marker_column"
        ),
        "account_id_column": resolve_column(
            df,
            account_header_config["account_id_column"],
            "ledger.account_header.account_id_column",
        ),
        "account_name_column": resolve_column(
            df,
            account_header_config["account_name_column"],
            "ledger.account_header.account_name_column",
        ),
    }

    additional_config = ledger_config["additional_description"]
    additional_description_column = resolve_column(
        df,
        additional_config["description_column"],
        "ledger.additional_description.description_column",
    )
    required_blank_columns = [
        resolve_column(df, col, "ledger.additional_description.required_blank_columns")
        for col in additional_config.get("required_blank_columns", [])
    ]
    exclude_keywords = additional_config.get("exclude_keywords", ["total", "subtotal"])
    total_keywords = ledger_config.get("total_keywords", ["total", "subtotal"])

    final_rows = []
    suspicious_rows = []
    counters = {
        "raw_rows": len(df),
        "blank_rows": 0,
        "account_headers": 0,
        "additional_descriptions": 0,
        "transactions": 0,
        "skipped_totals": 0,
        "suspicious_rows": 0,
    }
    raw_transaction_amount_total = 0.0

    current_account = ""
    current_account_name = ""
    current_additional_description = ""

    for index, row in df.iterrows():
        source_row = index + 2
        snapshot = row_snapshot(row)

        if row.isna().all():
            counters["blank_rows"] += 1
            continue

        if _is_account_header(row, account_header_config, account_columns):
            current_account = str(safe_value(row[account_columns["account_id_column"]])).strip()
            current_account_name = str(
                safe_value(row[account_columns["account_name_column"]])
            ).strip()
            current_additional_description = ""
            counters["account_headers"] += 1
            continue

        if _is_additional_description(
            row,
            additional_description_column,
            required_blank_columns,
            exclude_keywords,
            current_account,
        ):
            current_additional_description = str(
                safe_value(row[additional_description_column])
            ).strip()
            counters["additional_descriptions"] += 1
            continue

        if is_transaction_date(get_cell(row, columns, "trans_date")):
            output_row, warnings = _build_transaction_row(
                row,
                columns,
                current_account,
                current_account_name,
                current_additional_description,
            )
            if warnings:
                output_row["Flatten Warnings"] = "; ".join(warnings)
                log.add(
                    "WARNING",
                    "Transaction Validation",
                    output_row["Flatten Warnings"],
                    source_row,
                    snapshot,
                )

            amount_number = amount_to_number(output_row["Amount"])
            if amount_number is not None:
                raw_transaction_amount_total += amount_number

            final_rows.append(output_row)
            counters["transactions"] += 1
            continue

        if contains_any_keyword(row, total_keywords):
            counters["skipped_totals"] += 1
            log.add(
                "INFO",
                "Skipped Total/Subtotal",
                "Row was not exported because it appears to be a total/subtotal row.",
                source_row,
                snapshot,
            )
            continue

        counters["suspicious_rows"] += 1
        message = "Non-blank row was not classified as header, description, transaction, or total."
        suspicious_rows.append(
            {
                "Source Row": source_row,
                "Reason": message,
                "Raw Row": snapshot,
            }
        )
        log.add("WARNING", "Unclassified Row", message, source_row, snapshot)

    result_df = pd.DataFrame(final_rows, columns=OUTPUT_COLUMNS)
    if not result_df.empty:
        result_df["Trans Date"] = pd.to_datetime(
            result_df["Trans Date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")

    formatted_amount_total = _sum_amount_column(result_df, "Amount")
    reconciliation_df = _build_reconciliation_summary(
        counters,
        raw_transaction_amount_total,
        formatted_amount_total,
        result_df,
    )

    if abs(raw_transaction_amount_total - formatted_amount_total) > 0.01:
        log.add(
            "ERROR",
            "Reconciliation",
            "Raw candidate transaction amount total does not equal formatted amount total.",
        )
    else:
        log.add(
            "INFO",
            "Reconciliation",
            "Raw candidate transaction amount total equals formatted amount total.",
        )

    suspicious_df = pd.DataFrame(
        suspicious_rows, columns=["Source Row", "Reason", "Raw Row"]
    )
    return result_df, suspicious_df, reconciliation_df, log


def _is_account_header(row, account_header_config, account_columns):
    marker_value = str(safe_value(row[account_columns["marker_column"]])).strip()
    expected_marker = str(account_header_config["marker_value"]).strip()
    if marker_value.lower() != expected_marker.lower():
        return False

    account_id = str(safe_value(row[account_columns["account_id_column"]])).strip()
    account_pattern = account_header_config.get("account_id_pattern")
    if account_pattern and not re.match(account_pattern, account_id):
        return False
    return True


def _is_additional_description(
    row,
    description_column,
    required_blank_columns,
    exclude_keywords,
    current_account,
):
    if not current_account:
        return False
    if any(not is_blank(row[column]) for column in required_blank_columns):
        return False

    description = str(safe_value(row[description_column])).strip()
    if description == "":
        return False

    description_lower = description.lower()
    return not any(str(keyword).lower() in description_lower for keyword in exclude_keywords)


def _build_transaction_row(
    row,
    columns,
    current_account,
    current_account_name,
    current_additional_description,
):
    warnings = []
    amount = get_cell(row, columns, "amount")
    cad = get_cell(row, columns, "cad")
    usd = get_cell(row, columns, "usd")
    vendor_id = get_cell(row, columns, "vendor_id")
    vendor_name = get_cell(row, columns, "vendor_name")
    our_reference = get_cell(row, columns, "our_reference")
    trans_ref = get_cell(row, columns, "trans_ref")

    if isinstance(our_reference, float) and our_reference.is_integer():
        our_reference = str(int(our_reference))

    if not current_account:
        warnings.append("Transaction has no inherited account.")
    if is_blank(vendor_id) and is_blank(vendor_name):
        warnings.append("Transaction has no vendor ID or vendor name.")
    if amount_to_number(amount) is None and amount_to_number(cad) is None:
        warnings.append("Transaction has no numeric amount or CAD value.")

    return (
        {
            "Account": current_account,
            "Account Name": current_account_name,
            "Trans Date": get_cell(row, columns, "trans_date"),
            "Vendor ID": safe_excel_text(vendor_id),
            "Vendor Name": vendor_name,
            "Src": get_cell(row, columns, "src"),
            "Trans Ref": safe_excel_text(trans_ref),
            "Description": get_cell(row, columns, "description"),
            "Additional Description": current_additional_description,
            "Our Reference": safe_excel_text(our_reference),
            "Currency": get_cell(row, columns, "currency"),
            "USD": usd,
            "Amt": cad,
            "CAD": cad,
            "Amount": amount,
            "Ep": get_cell(row, columns, "ep"),
            "Flatten Warnings": "",
        },
        warnings,
    )


def _sum_amount_column(df, column):
    if df.empty or column not in df:
        return 0.0
    return pd.to_numeric(df[column], errors="coerce").fillna(0).sum()


def _build_reconciliation_summary(
    counters,
    raw_transaction_amount_total,
    formatted_amount_total,
    result_df,
):
    rows = [
        {"Check": "Raw rows read", "Value": counters["raw_rows"]},
        {"Check": "Blank rows skipped", "Value": counters["blank_rows"]},
        {"Check": "Account headers detected", "Value": counters["account_headers"]},
        {
            "Check": "Additional descriptions detected",
            "Value": counters["additional_descriptions"],
        },
        {"Check": "Transactions exported", "Value": counters["transactions"]},
        {"Check": "Total/subtotal rows skipped", "Value": counters["skipped_totals"]},
        {"Check": "Suspicious unclassified rows", "Value": counters["suspicious_rows"]},
        {
            "Check": "Raw candidate transaction amount total",
            "Value": raw_transaction_amount_total,
        },
        {"Check": "Formatted amount total", "Value": formatted_amount_total},
        {
            "Check": "Amount total difference",
            "Value": raw_transaction_amount_total - formatted_amount_total,
        },
    ]

    if not result_df.empty:
        rows.append(
            {
                "Check": "Formatted rows with flatten warnings",
                "Value": int((result_df["Flatten Warnings"] != "").sum()),
            }
        )

    return pd.DataFrame(rows, columns=["Check", "Value"])
