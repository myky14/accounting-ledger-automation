import pandas as pd

from ledger_flattener import OUTPUT_COLUMNS
from utils import RunLog, read_workbook


DEFAULT_REQUIRED_COLUMNS = [
    "Vendor Name",
    "Description",
    "Amount",
    "Trans Date",
]

DEFAULT_RECOMMENDED_COLUMNS = [
    "Account",
    "Account Name",
    "Vendor ID",
    "Currency",
]


def load_preflattened_ledger(path_value, config):
    """Read and validate a manually prepared formatted ledger."""
    # Nếu client có ledger format quá lạ, user có thể tự clean thành flattened ledger rồi upload vào đây.
    # Khi dùng Pre-Flattened Ledger Mode thì pipeline sẽ bỏ qua bước flatten.
    df = read_workbook(path_value)
    log = RunLog()
    warnings = validate_formatted_ledger(df, config)
    formatted_df = normalize_formatted_ledger(df)
    reconciliation_df = build_preflattened_reconciliation_summary(formatted_df, warnings)
    suspicious_df = pd.DataFrame(columns=["Source Row", "Reason", "Raw Row"])

    log.add("INFO", "Input Mode", "Input Mode = Pre-Flattened Ledger")
    log.add("INFO", "Flatten Step", "Flatten Step = Skipped")
    for warning in warnings:
        log.add("WARNING", "Formatted Ledger Validation", warning)

    return formatted_df, suspicious_df, reconciliation_df, log


def validate_formatted_ledger(df, config):
    """Raise on missing required columns, warn on missing recommended columns."""
    validation_config = config.get("formatted_ledger_validation", {})
    required_columns = validation_config.get(
        "required_columns",
        DEFAULT_REQUIRED_COLUMNS,
    )
    recommended_columns = validation_config.get(
        "recommended_columns",
        DEFAULT_RECOMMENDED_COLUMNS,
    )

    missing_required = [column for column in required_columns if column not in df.columns]
    if missing_required:
        available = ", ".join(str(column) for column in df.columns)
        raise ValueError(
            "Pre-flattened ledger is missing required columns: "
            + ", ".join(missing_required)
            + f". Available columns: {available}"
        )

    # Vẫn phải validate các cột tối thiểu để tránh enrich sai dữ liệu.
    warnings = []
    missing_recommended = [
        column for column in recommended_columns if column not in df.columns
    ]
    if missing_recommended:
        warnings.append(
            "Pre-flattened ledger is missing recommended columns: "
            + ", ".join(missing_recommended)
            + ". The pipeline will continue with blank values where needed."
        )
    return warnings


def normalize_formatted_ledger(df):
    formatted_df = df.copy()
    for column in OUTPUT_COLUMNS:
        if column not in formatted_df.columns:
            formatted_df[column] = ""
    return formatted_df


def build_preflattened_reconciliation_summary(formatted_df, warnings):
    rows = [
        {"Check": "Input mode", "Value": "Pre-Flattened Ledger"},
        {"Check": "Flatten step", "Value": "Skipped"},
        {
            "Check": "Raw reconciliation",
            "Value": "Raw reconciliation not available because flattened ledger was provided manually",
        },
        {"Check": "Formatted rows provided", "Value": len(formatted_df)},
        {
            "Check": "Formatted amount total",
            "Value": pd.to_numeric(formatted_df["Amount"], errors="coerce").fillna(0).sum(),
        },
        {
            "Check": "Validation warnings",
            "Value": " | ".join(warnings) if warnings else "",
        },
    ]
    return pd.DataFrame(rows, columns=["Check", "Value"])
