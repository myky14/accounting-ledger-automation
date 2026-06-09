import pandas as pd

from utils import (
    apply_review_workbook_formatting,
    normalize_name,
    read_workbook,
    resolve_column_map,
    safe_value,
    validate_master_columns,
    write_workbook_safely,
)


REVIEW_COLUMNS = [
    "Ledger Vendor Name",
    "Suggested Vendor Name",
    "Match Score",
    "Match Status",
    "Approved Vendor Name",
    "Approval Status",
    "Reviewer Notes",
]


def get_vendor_review_path(config):
    return config["paths"].get("vendor_match_review", "output/Vendor Match Review.xlsx")


def generate_vendor_match_review(formatted_ledger, config, output_file=None):
    """Create one clean review row per unique ledger vendor name."""
    try:
        from rapidfuzz import fuzz, process
    except ImportError as exc:
        raise ImportError(
            "rapidfuzz is required for vendor review generation. Run with .venv "
            "or install rapidfuzz in the active Python environment."
        ) from exc

    ledger_df = _read_dataframe(formatted_ledger)
    vendor_df, vendor_columns = _load_vendor_master(config)
    vendor_name_column = vendor_columns["vendor_name"]

    # Normalized values chi dung noi bo de fuzzy matching.
    # Khong dua cac cot debug nay vao Excel review vi reviewer can file gon,
    # de doc, va tap trung vao quyet dinh approve/reject.
    vendor_df["_normalized_vendor"] = vendor_df[vendor_name_column].apply(normalize_name)
    vendor_df = vendor_df[vendor_df["_normalized_vendor"] != ""].copy()
    vendor_lookup = vendor_df.drop_duplicates("_normalized_vendor").set_index(
        "_normalized_vendor"
    )
    vendor_name_list = vendor_df["_normalized_vendor"].tolist()

    thresholds = config["matching"]["thresholds"]
    payroll_vendors = _normalized_payroll_vendors(config)
    unique_vendor_names = (
        ledger_df["Vendor Name"].fillna("").astype(str).str.strip().drop_duplicates()
    )

    review_rows = []
    for ledger_vendor_name in unique_vendor_names:
        normalized_ledger_vendor = normalize_name(ledger_vendor_name)
        if normalized_ledger_vendor in payroll_vendors:
            continue

        if normalized_ledger_vendor == "" or not vendor_name_list:
            review_rows.append(_blank_review_row(ledger_vendor_name, "Vendor Not Found"))
            continue

        match = process.extractOne(
            normalized_ledger_vendor,
            vendor_name_list,
            scorer=fuzz.ratio,
        )
        if match is None:
            review_rows.append(_blank_review_row(ledger_vendor_name, "Vendor Not Found"))
            continue

        suggested_normalized_name, score, _ = match
        suggested_vendor_row = vendor_lookup.loc[suggested_normalized_name]
        review_rows.append(
            {
                "Ledger Vendor Name": ledger_vendor_name,
                "Suggested Vendor Name": safe_value(
                    suggested_vendor_row[vendor_name_column]
                ),
                "Match Score": score,
                # Đây là status do hệ thống tự suggest dựa trên fuzzy matching.
                # Chưa có nghĩa là match đúng hoàn toàn.
                "Match Status": _match_status(score, thresholds),
                # MANUAL CHECK: Kiểm tra Suggested Vendor Name trước khi approve.
                "Approved Vendor Name": "",
                # Approval Status là quyết định cuối cùng của reviewer.
                # Chỉ khi Approval Status = Approved thì mới được fill Tax ID/address.
                "Approval Status": "Needs Review",
                # MANUAL CHECK: Không approve nếu vendor giống tên nhưng Tax ID/address không đúng.
                # Reviewer Notes dùng để ghi lý do approve/reject.
                # Sau này rất hữu ích để audit hoặc debug workflow.
                "Reviewer Notes": "",
            }
        )

    review_df = pd.DataFrame(review_rows, columns=REVIEW_COLUMNS)

    output_path = output_file or get_vendor_review_path(config)
    write_workbook_safely(output_path, {"Vendor Match Review": review_df})
    apply_review_workbook_formatting(output_path, ["Vendor Match Review"])

    return review_df, output_path


def _read_dataframe(data):
    if isinstance(data, pd.DataFrame):
        return data.copy()
    return read_workbook(data)


def _load_vendor_master(config):
    vendor_config = config["vendor_master"]
    vendor_df = read_workbook(
        config["paths"]["vendor_master"], vendor_config.get("sheet_name", 0), dtype=str
    )
    validation_config = config.get("master_validation", {})
    validate_master_columns(
        vendor_df,
        validation_config.get("vendor_master_required_columns", []),
        validation_config.get("vendor_master_optional_columns", []),
        "Vendor Master",
    )
    vendor_columns = resolve_column_map(
        vendor_df,
        vendor_config["columns"],
        "vendor_master.columns",
        allow_missing=True,
    )
    return vendor_df, vendor_columns


def _blank_review_row(ledger_vendor_name, approval_status):
    return {
        "Ledger Vendor Name": ledger_vendor_name,
        "Suggested Vendor Name": "",
        "Match Score": 0.0,
        "Match Status": "NO MATCH",
        # MANUAL CHECK: Chi nhap Approved Vendor Name khi chac chan vendor trong
        # ledger va vendor master la cung mot entity.
        "Approved Vendor Name": "",
        "Approval Status": approval_status,
        "Reviewer Notes": "",
    }


def _match_status(score, thresholds):
    if score >= thresholds["auto_match"]:
        return "AUTO MATCH"
    if score >= thresholds["low_confidence"]:
        return "LOW CONFIDENCE"
    return "NO MATCH"


def _normalized_payroll_vendors(config):
    return {
        normalize_name(vendor_name)
        for vendor_name in config.get("payroll_vendors", [])
        if normalize_name(vendor_name)
    }
