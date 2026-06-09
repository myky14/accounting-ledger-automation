import argparse
from pathlib import Path

import pandas as pd

from config_loader import apply_cli_overrides, load_config
from employee_review import generate_employee_extraction_review, get_employee_review_path
from formatted_ledger import load_preflattened_ledger
from ledger_flattener import flatten_ledger
from utils import write_workbook_safely
from vendor_matcher import enrich_using_approved_mapping, enrich_vendor_information
from vendor_review import generate_vendor_match_review, get_vendor_review_path


def parse_args():
    parser = argparse.ArgumentParser(
        description="Flatten hierarchical ledgers and enrich vendor information."
    )
    parser.add_argument(
        "--config",
        default="configs/vice.yaml",
        help="Client config file. Default: configs/vice.yaml",
    )
    parser.add_argument("--raw-ledger", help="Override raw ledger input path.")
    parser.add_argument(
        "--project-name",
        help="Project/run name for company-level configs such as VICE. "
        "Defaults to the raw ledger filename.",
    )
    parser.add_argument(
        "--formatted-ledger",
        help="Use an already flattened ledger file and skip the flatten step.",
    )
    parser.add_argument("--vendor-master", help="Override vendor master input path.")
    parser.add_argument("--payroll-master", help="Override payroll master input path.")
    parser.add_argument("--formatted-output", help="Override formatted ledger output path.")
    parser.add_argument(
        "--vendor-review-output",
        help="Override generated Vendor Match Review output path.",
    )
    parser.add_argument(
        "--employee-review-output",
        help="Override generated Employee Extraction Review output path.",
    )
    parser.add_argument("--final-output", help="Override final workbook output path.")
    parser.add_argument(
        "--approved-review",
        help="Approved Vendor Match Review file to use for final enrichment.",
    )
    parser.add_argument(
        "--approved-employee-review",
        help="Approved Employee Extraction Review file to use for payroll enrichment.",
    )
    parser.add_argument(
        "--legacy-auto-enrich",
        action="store_true",
        help="Run the old fuzzy auto-enrichment workflow. Use only for comparison.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config = apply_cli_overrides(load_config(args.config), args)

    if args.formatted_ledger:
        if not config.get("input_mode", {}).get("allow_pre_flattened", True):
            raise ValueError("Pre-flattened ledger input is disabled by config.")
        print("Step 1/3: Loading pre-flattened ledger...")
        formatted_df, suspicious_df, reconciliation_df, log = load_preflattened_ledger(
            args.formatted_ledger,
            config,
        )
    else:
        print("Step 1/3: Flattening ledger...")
        formatted_df, suspicious_df, reconciliation_df, log = flatten_ledger(config)

    formatted_output = config["paths"]["formatted_ledger"]
    # Không overwrite file flattened ledger user upload.
    if not (
        args.formatted_ledger
        and Path(args.formatted_ledger).resolve() == Path(formatted_output).resolve()
    ):
        write_workbook_safely(formatted_output, {"Formatted Ledger": formatted_df})

    if args.legacy_auto_enrich:
        print("Step 2/3: Running legacy fuzzy auto-enrichment...")
        enriched_df, low_confidence_df, unmatched_df = enrich_vendor_information(
            formatted_df, config, log
        )
        final_output = config["paths"]["final_enriched_workbook"]
        write_legacy_workbook(
            final_output,
            enriched_df,
            low_confidence_df,
            unmatched_df,
            reconciliation_df,
            log.to_frame(),
            suspicious_df,
        )
    elif args.approved_review:
        print("Step 2/3: Enriching with approved review files...")
        (
            enriched_df,
            decisions_df,
            not_approved_df,
            low_confidence_df,
            payroll_extraction_df,
            employee_not_found_df,
            ambiguous_employee_df,
            employee_vendor_match_df,
            loanout_mapping_df,
            non_employee_loanout_df,
            approved_missing_source_df,
            log,
        ) = enrich_using_approved_mapping(
            formatted_df,
            config,
            args.approved_review,
            log,
            args.approved_employee_review,
        )
        final_output = config["paths"]["final_enriched_workbook"]
        employee_context_candidates_df = read_employee_context_candidates(
            args.approved_employee_review
        )
        write_reviewed_final_workbook(
            final_output,
            enriched_df,
            decisions_df,
            not_approved_df,
            low_confidence_df,
            payroll_extraction_df,
            employee_not_found_df,
            ambiguous_employee_df,
            employee_vendor_match_df,
            loanout_mapping_df,
            non_employee_loanout_df,
            approved_missing_source_df,
            reconciliation_df,
            log.to_frame(),
            employee_context_candidates_df,
        )
    else:
        print("Step 2/3: Generating Vendor Match Review...")
        review_df, review_output = generate_vendor_match_review(
            formatted_df, config, get_vendor_review_path(config)
        )
        print("Step 3/3: Generating Employee Extraction Review...")
        (
            employee_review_df,
            employee_review_output,
            employee_context_candidates_df,
        ) = generate_employee_extraction_review(
            formatted_df,
            config,
            get_employee_review_path(config),
        )
        final_output = ""
        # Nếu reviewer chọn Approved thì hệ thống sẽ dùng suggested match làm nguồn mặc định.
        # Chỉ điền Approved Vendor Name / Approved Loan Out Corp khi muốn override suggestion.
        # Match Status là ý kiến của algorithm.
        # Approval Status mới là authority cuối cùng.
        # Reviewer Notes dùng để ghi lý do approve/reject để sau này audit/debug dễ hơn.
        # MANUAL CHECK: Mở file Vendor Match Review.xlsx để kiểm tra Suggested
        # Vendor Name, rồi điền Approved Vendor Name + Approval Status trước khi
        # chạy enrichment.
        print("Manual review required before final enrichment.")
        print(f"Vendor review rows: {len(review_df)}")
        print(f"Vendor match review file: {review_output}")
        print(f"Employee review rows: {len(employee_review_df)}")
        print(f"Employee extraction review file: {employee_review_output}")
        print("After approval, run:")
        print(_approval_rerun_command(args, review_output, employee_review_output))

    print("===================================")
    print("DONE")
    print("===================================")
    print(f"Formatted ledger rows: {len(formatted_df)}")
    print(f"Formatted ledger: {formatted_output}")
    if final_output:
        print(f"Final workbook: {final_output}")
    print("===================================")


def _approval_rerun_command(args, review_output, employee_review_output):
    command_parts = ["python main.py", "--config", _quote_arg(args.config)]
    if args.raw_ledger:
        command_parts.extend(["--raw-ledger", _quote_arg(args.raw_ledger)])
    if args.project_name:
        command_parts.extend(["--project-name", _quote_arg(args.project_name)])
    if args.vendor_master:
        command_parts.extend(["--vendor-master", _quote_arg(args.vendor_master)])
    if args.payroll_master:
        command_parts.extend(["--payroll-master", _quote_arg(args.payroll_master)])
    command_parts.extend(
        [
            "--approved-review",
            _quote_arg(review_output),
            "--approved-employee-review",
            _quote_arg(employee_review_output),
        ]
    )
    return " ".join(command_parts)


def _quote_arg(value):
    value = str(value)
    if not value:
        return '""'
    if any(char.isspace() for char in value):
        return f'"{value}"'
    return value


def write_reviewed_final_workbook(
    output_path,
    enriched_df,
    decisions_df,
    not_approved_df,
    low_confidence_df,
    payroll_extraction_df,
    employee_not_found_df,
    ambiguous_employee_df,
    employee_vendor_match_df,
    loanout_mapping_df,
    non_employee_loanout_df,
    approved_missing_source_df,
    reconciliation_df,
    run_log_df,
    employee_context_candidates_df=None,
):
    """Write the safer final workbook based on approved vendor decisions."""
    # MANUAL CHECK: Kiểm tra sheet Reconciliation Summary trước khi gửi
    # final workbook cho accounting/audit.
    sheets = {
            "Final Enriched Ledger": enriched_df,
            "Vendor Match Decisions": decisions_df,
            "Review - Not Approved Vendors": not_approved_df,
            "Review - Low Confidence Suggested Matches": low_confidence_df,
            "Review - Payroll Extraction": payroll_extraction_df,
            "Review - Employee Not Found": employee_not_found_df,
            "Review - Ambiguous Employee Match": ambiguous_employee_df,
            "Review - Employee Vendor Match": employee_vendor_match_df,
            "Review - Loan Out Mapping": loanout_mapping_df,
            "Review - Non Employee Loanout Values": non_employee_loanout_df,
            "Review - Approved Missing Source": approved_missing_source_df,
            "Reconciliation Summary": reconciliation_df,
            "Run Log": run_log_df,
    }
    if employee_context_candidates_df is not None and not employee_context_candidates_df.empty:
        sheets["Review - Payroll Master Aliases"] = employee_context_candidates_df
    write_workbook_safely(output_path, sheets)


def read_employee_context_candidates(approved_employee_review_file):
    if not approved_employee_review_file:
        return pd.DataFrame()
    try:
        excel_file = pd.ExcelFile(approved_employee_review_file)
        sheet_name = next(
            (
                sheet
                for sheet in excel_file.sheet_names
                if sheet.startswith("Review - Payroll Master")
                or sheet.startswith("Review - Employee Context")
            ),
            None,
        )
        if not sheet_name:
            return pd.DataFrame()
        return pd.read_excel(excel_file, sheet_name=sheet_name, dtype=str)
    except Exception:
        return pd.DataFrame()


def write_legacy_workbook(
    output_path,
    enriched_df,
    low_confidence_df,
    unmatched_df,
    reconciliation_df,
    run_log_df,
    suspicious_df,
):
    """Write the old fuzzy auto-enrichment workbook for comparison only."""
    sheets = {
        "Final Enriched Ledger": enriched_df,
        "Review - Low Confidence Matches": low_confidence_df,
        "Review - Unmatched Vendors": unmatched_df,
        "Reconciliation Summary": reconciliation_df,
        "Run Log": run_log_df,
    }
    if not suspicious_df.empty:
        sheets["Review - Suspicious Rows"] = suspicious_df
    write_workbook_safely(output_path, sheets)


if __name__ == "__main__":
    main()
