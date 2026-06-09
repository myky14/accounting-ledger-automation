import re

import pandas as pd

from utils import (
    APPROVED_STATUS,
    RunLog,
    normalize_name,
    read_workbook,
    resolve_column,
    resolve_column_list,
    resolve_column_map,
    safe_excel_text,
    safe_value,
    validate_master_columns,
)


ENRICHED_COLUMNS = [
    "Matched Vendor",
    "Match Score",
    "Second Best Vendor",
    "Second Best Score",
    "Match Status",
    "Loan Out Corp",
    "Employee",
    "Tax ID",
    "Address",
    "City",
    "Province",
    "Country",
    "Zip Code",
    "Additional Address Lines",
]


APPROVED_ENRICHED_COLUMNS = [
    "Suggested Vendor Name",
    "Match Score",
    "Match Status",
    "Approved Vendor Name",
    "Approval Status",
    "Reviewer Notes",
    "Approval Source Used",
    "Approval Resolution Method",
    "Enrichment Status",
    "Enrichment Source",
    "Loan Out Corp",
    "Employee",
    "Related Entity Info",
    "Employee Match Type",
    "Employee Token",
    "Normalized Employee",
    "Employee Extraction Pattern",
    "Employee Extraction Confidence",
    "Employee Extraction Status",
    "Employee Approval Status",
    "Employee SIN",
    "Employee GST Number",
    "Employee Address",
    "Employee City",
    "Employee Province",
    "Employee Country",
    "Employee Zip Code",
    "Position Name",
    "Vendor Corp Tax ID",
    "Vendor Corp Address",
    "Tax ID",
    "Address",
    "City",
    "Province",
    "Country",
    "Zip Code",
    "Additional Address Lines",
]


DEFAULT_FINAL_OUTPUT_COLUMNS = [
    "Account",
    "Account Name",
    "Trans Date",
    "Src",
    "Trans Ref",
    "Vendor ID",
    "Vendor Name",
    "Description",
    "Additional Description",
    "Loan out corp",
    "Employee",
    "Tax ID",
    "Address",
    "City",
    "Province",
    "Country",
    "Zip Code",
    "Our Reference",
    "Currency",
    "USD",
    "Amt",
    "Ep",
    "Amount",
]

TEXT_OUTPUT_COLUMNS = {
    "Trans Ref",
    "Vendor ID",
    "Tax ID",
    "Address",
    "City",
    "Province",
    "Country",
    "Zip Code",
    "Our Reference",
}


PAYROLL_NO_MATCH = {
    "employee_token": "",
    "normalized_employee": "",
    "extraction_pattern": "",
    "extraction_confidence": "No Match",
    "extraction_status": "No Match",
}


def enrich_vendor_information(ledger_df, config, log):
    """Match ledger vendors to the vendor master and enrich approved fields."""
    try:
        from rapidfuzz import fuzz, process
    except ImportError as exc:
        raise ImportError(
            "rapidfuzz is required for vendor matching. Install it in the active "
            "environment or run the project with the existing .venv."
        ) from exc

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
    optional_address_columns = _resolve_optional_address_columns(
        vendor_df, vendor_config.get("optional_address_lines", []), log
    )

    vendor_name_column = vendor_columns["vendor_name"]
    # Normalized vendor name chi la helper noi bo de tra Approved Vendor Name
    # trong vendor master. Khong dua cot nay vao review/final workbook vi user
    # can audit sheet de doc, khong bi nhieu cot ky thuat lam roi.
    vendor_df["_normalized_vendor"] = vendor_df[vendor_name_column].apply(normalize_name)
    vendor_df = vendor_df[vendor_df["_normalized_vendor"] != ""].copy()

    duplicate_count = int(vendor_df["_normalized_vendor"].duplicated().sum())
    if duplicate_count:
        log.add(
            "WARNING",
            "Vendor Master",
            f"Vendor master contains {duplicate_count} duplicate normalized vendor names.",
        )

    vendor_name_list = vendor_df["_normalized_vendor"].tolist()
    vendor_lookup = vendor_df.drop_duplicates("_normalized_vendor").set_index(
        "_normalized_vendor"
    )

    enriched_df = ledger_df.copy().astype(object)
    for column in ENRICHED_COLUMNS:
        if column not in enriched_df.columns:
            if column in ["Match Score", "Second Best Score"]:
                enriched_df[column] = 0.0
            else:
                enriched_df[column] = ""

    thresholds = config["matching"]["thresholds"]
    enrich_statuses = set(config["matching"].get("enrich_statuses", ["AUTO MATCH"]))
    low_confidence_rows = []
    unmatched_rows = []

    for index, row in enriched_df.iterrows():
        ledger_vendor_name = safe_value(row.get("Vendor Name", ""))
        normalized_ledger_vendor = normalize_name(ledger_vendor_name)
        source_row = index + 2

        if normalized_ledger_vendor == "":
            unmatched_rows.append(_review_record(row, "Blank ledger vendor name"))
            log.add(
                "WARNING",
                "Vendor Matching",
                "Ledger row has a blank vendor name and could not be matched.",
                source_row,
            )
            continue

        matches = process.extract(
            normalized_ledger_vendor,
            vendor_name_list,
            scorer=fuzz.ratio,
            limit=2,
        )
        if not matches:
            unmatched_rows.append(_review_record(row, "No vendor master candidates"))
            log.add("WARNING", "Vendor Matching", "No vendor master candidates.", source_row)
            continue

        best_name, best_score, _ = matches[0]
        second_name = matches[1][0] if len(matches) > 1 else ""
        second_score = matches[1][1] if len(matches) > 1 else 0
        matched_vendor_row = vendor_lookup.loc[best_name]
        status = _match_status(best_score, thresholds)

        enriched_df.at[index, "Matched Vendor"] = _text_value(
            matched_vendor_row[vendor_name_column]
        )
        enriched_df.at[index, "Match Score"] = best_score
        enriched_df.at[index, "Second Best Vendor"] = _text_value(_display_vendor_name(
            vendor_lookup, second_name, vendor_name_column
        ))
        enriched_df.at[index, "Second Best Score"] = second_score
        enriched_df.at[index, "Match Status"] = _text_value(status)

        if status in enrich_statuses:
            _copy_vendor_fields(
                enriched_df,
                index,
                matched_vendor_row,
                vendor_columns,
                optional_address_columns,
            )
        else:
            low_confidence_rows.append(
                _review_record(
                    enriched_df.loc[index],
                    "Low confidence vendor match. Enrichment fields were not auto-filled.",
                )
            )
            log.add(
                "WARNING",
                "Vendor Matching",
                f"Low confidence match for '{ledger_vendor_name}' "
                f"against '{enriched_df.at[index, 'Matched Vendor']}' ({best_score}).",
                source_row,
            )

    low_confidence_df = pd.DataFrame(low_confidence_rows)
    unmatched_df = pd.DataFrame(unmatched_rows)
    enriched_df = apply_final_output_schema(enriched_df, config)
    return enriched_df, low_confidence_df, unmatched_df


def enrich_using_approved_mapping(
    formatted_ledger,
    config,
    approved_review_file,
    log=None,
    approved_employee_review_file=None,
):
    """Enrich transactions only from a manually approved vendor review file."""
    log = log or RunLog()
    ledger_df = _read_dataframe(formatted_ledger)
    review_df = pd.read_excel(
        approved_review_file,
        sheet_name="Vendor Match Review",
        dtype=str,
    )
    employee_review_lookup = _build_employee_review_lookup(
        approved_employee_review_file,
        log,
    )

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
    optional_address_columns = _resolve_optional_address_columns(
        vendor_df, vendor_config.get("optional_address_lines", []), log
    )

    vendor_name_column = vendor_columns["vendor_name"]
    vendor_df["_normalized_vendor"] = vendor_df[vendor_name_column].apply(normalize_name)
    vendor_lookup = vendor_df.drop_duplicates("_normalized_vendor").set_index(
        "_normalized_vendor"
    )
    payroll_df, payroll_columns = load_payroll_master(config)
    payroll_mapping = build_payroll_employee_mapping(payroll_df, payroll_columns)
    payroll_vendors = _normalized_payroll_vendors(config)

    decision_lookup = _build_decision_lookup(review_df, log)
    enriched_df = ledger_df.copy().astype(object)
    for column in APPROVED_ENRICHED_COLUMNS:
        if column not in enriched_df.columns:
            if column == "Match Score":
                enriched_df[column] = 0.0
            else:
                enriched_df[column] = ""

    not_approved_rows = []
    payroll_extraction_rows = []
    employee_not_found_rows = []
    ambiguous_employee_rows = []
    employee_vendor_match_rows = []
    approved_missing_source_rows = []

    for index, row in enriched_df.iterrows():
        ledger_vendor_name = str(safe_value(row.get("Vendor Name", ""))).strip()
        decision = decision_lookup.get(ledger_vendor_name)
        source_row = index + 2

        if decision is not None:
            _copy_decision_fields(enriched_df, index, decision)

        normalized_ledger_vendor = normalize_name(ledger_vendor_name)

        if normalized_ledger_vendor in payroll_vendors:
            # Payroll rows không được lấy Tax ID/address của GREENSLATE CANADA INC.
            # MANUAL CHECK: Chỉ fill Tax ID/address khi employee review đã approve
            # đúng loan-out corporation cho token được extract từ description.
            extraction = extract_employee_from_payroll_description(
                safe_value(row.get("Description", ""))
            )
            _copy_extraction_fields(enriched_df, index, extraction)
            employee_decision = employee_review_lookup.get(
                _employee_review_lookup_key(
                    ledger_vendor_name,
                    extraction.get("employee_token", ""),
                )
            )
            if employee_decision is None:
                employee_decision = employee_review_lookup.get(
                    _employee_review_lookup_key("", extraction.get("employee_token", ""))
                )
            _handle_approved_employee_review_decision(
                enriched_df,
                index,
                extraction,
                employee_decision,
                vendor_lookup,
                payroll_mapping,
                config,
                vendor_columns,
                optional_address_columns,
                payroll_extraction_rows,
                employee_not_found_rows,
                ambiguous_employee_rows,
                employee_vendor_match_rows,
                approved_missing_source_rows,
                log,
                source_row,
            )
            continue

        if decision is None:
            _mark_not_approved(
                enriched_df,
                index,
                "No review decision found for ledger vendor.",
                not_approved_rows,
            )
            log.add(
                "WARNING",
                "Approved Vendor Enrichment",
                f"No review decision found for ledger vendor '{ledger_vendor_name}'.",
                source_row,
            )
            continue

        approval_status = str(safe_value(decision.get("Approval Status", ""))).strip()

        # MANUAL CHECK: Nếu Approval Status chưa phải Approved thì pipeline không
        # được fill Tax ID/address. Đây là lớp bảo vệ để tránh enrich sai entity.
        # Approval Status là quyết định cuối cùng của reviewer.
        # Chỉ khi Approval Status = Approved thì mới được fill Tax ID/address.
        if approval_status != APPROVED_STATUS:
            _mark_not_approved(
                enriched_df,
                index,
                f"Approval Status is '{approval_status or 'Needs Review'}'.",
                not_approved_rows,
            )
            continue

        vendor_source = resolve_approved_vendor_source(decision)
        _set_approval_resolution_fields(
            enriched_df,
            index,
            vendor_source,
            _vendor_approval_source_label(decision, vendor_source),
        )
        approved_vendor_name = vendor_source["source_name"]

        # Nếu reviewer chọn Approved thì hệ thống sẽ dùng suggested match làm nguồn mặc định.
        # Chỉ cần điền Approved Vendor Name khi muốn override suggestion.
        if vendor_source["source_type"] == "missing":
            _mark_approved_missing_source(
                enriched_df,
                index,
                "Vendor Review",
                "Approval Status is Approved but no approved or suggested vendor source was available.",
                approved_missing_source_rows,
            )
            log.add(
                "ERROR",
                "Approved Vendor Enrichment",
                f"Approved vendor source is missing for ledger vendor '{ledger_vendor_name}'.",
                source_row,
            )
            continue

        normalized_approved_vendor = normalize_name(approved_vendor_name)
        if normalized_approved_vendor not in vendor_lookup.index:
            _mark_not_approved(
                enriched_df,
                index,
                "Approved Vendor Name does not exist in vendor master.",
                not_approved_rows,
            )
            log.add(
                "ERROR",
                "Approved Vendor Enrichment",
                f"Approved Vendor Name '{approved_vendor_name}' was not found in vendor master.",
                source_row,
            )
            continue

        approved_vendor_row = vendor_lookup.loc[normalized_approved_vendor]
        _copy_vendor_fields(
            enriched_df,
            index,
            approved_vendor_row,
            vendor_columns,
            optional_address_columns,
        )
        enriched_df.at[index, "Enrichment Status"] = "Enriched from Approved Mapping"
        enriched_df.at[index, "Enrichment Source"] = "Approved Vendor Mapping"

    not_approved_df = pd.DataFrame(not_approved_rows)
    low_confidence_df = _low_confidence_decisions(review_df, config["matching"]["thresholds"])
    decisions_df = review_df.copy()
    payroll_extraction_df = pd.DataFrame(payroll_extraction_rows)
    employee_not_found_df = pd.DataFrame(employee_not_found_rows)
    ambiguous_employee_df = pd.DataFrame(ambiguous_employee_rows)
    loanout_mapping = build_loanout_employee_mapping(vendor_df, config)
    loanout_mapping_df = pd.DataFrame(loanout_mapping["review_rows"])
    non_employee_loanout_df = pd.DataFrame(loanout_mapping["non_employee_rows"])
    enriched_df = apply_final_output_schema(enriched_df, config)
    return (
        enriched_df,
        decisions_df,
        not_approved_df,
        low_confidence_df,
        payroll_extraction_df,
        employee_not_found_df,
        ambiguous_employee_df,
        pd.DataFrame(employee_vendor_match_rows),
        loanout_mapping_df,
        non_employee_loanout_df,
        pd.DataFrame(approved_missing_source_rows),
        log,
    )


def extract_employee_from_payroll_description(description: str) -> dict:
    """Extract the employee token from known Greenslate payroll descriptions."""
    text = str(safe_value(description)).strip()
    if not text:
        return PAYROLL_NO_MATCH.copy()

    pe_match = re.search(
        r"^PE\s+(?:\d{2}/\d{2}/\d{2}|\d{4}-\d{2}-\d{2})\s+"
        r"([A-Z][A-Z'\-\s]+),\s*([A-Z][A-Z'\-]*)\b",
        text,
        flags=re.IGNORECASE,
    )
    if pe_match:
        last_token = _clean_token_piece(pe_match.group(1))
        first_token = _clean_token_piece(pe_match.group(2))
        last_name = _clean_name_piece(last_token)
        first_piece = _clean_name_piece(first_token)
        separator = ", " if ", " in pe_match.group(0) else ","
        employee_token = f"{last_token}{separator}{first_token}"
        return _extraction_result(
            employee_token,
            _primary_employee_normalized_value(last_name, first_piece),
            "PE date LAST,INITIAL",
            "High",
            "Extracted",
        )

    dot_match = re.search(
        r"\b([A-Z]{1,3})\.([A-Z][A-Za-z'\-\s]+?)(?:\s+Adjustment)?$",
        text,
    )
    if dot_match:
        initials_token = _clean_token_piece(dot_match.group(1))
        last_token = _clean_token_piece(dot_match.group(2))
        initials = _clean_name_piece(initials_token)
        last_name = _clean_name_piece(last_token)
        employee_token = f"{initials_token}.{last_token}"
        return _extraction_result(
            employee_token,
            normalize_name(f"{initials} {last_name}"),
            "Initial dot last near end",
            "High",
            "Extracted",
        )

    initials_match = re.search(r"\b([A-Z]{1,3})\s+([A-Z][A-Za-z'\-]+)$", text)
    if initials_match:
        initials_token = _clean_token_piece(initials_match.group(1))
        last_token = _clean_token_piece(initials_match.group(2))
        initials = _clean_name_piece(initials_token)
        last_name = _clean_name_piece(last_token)
        employee_token = f"{initials_token} {last_token}"
        return _extraction_result(
            employee_token,
            normalize_name(f"{initials} {last_name}"),
            "Initials last at end",
            "Medium",
            "Extracted",
        )

    comma_match = re.search(r"\b([A-Z]{1,3}),([A-Z][A-Za-z'\-]+)\b", text)
    if comma_match:
        initials_token = _clean_token_piece(comma_match.group(1))
        last_token = _clean_token_piece(comma_match.group(2))
        initials = _clean_name_piece(initials_token)
        last_name = _clean_name_piece(last_token)
        employee_token = f"{initials_token},{last_token}"
        return _extraction_result(
            employee_token,
            normalize_name(f"{initials} {last_name}"),
            "Initial comma last typo",
            "Medium",
            "Extracted",
        )

    return PAYROLL_NO_MATCH.copy()


def load_payroll_master(config):
    payroll_config = config.get("payroll_master", {})
    if not payroll_config.get("enabled", False):
        return pd.DataFrame(), {}
    # Payroll Master là nguồn chính cho employee information.
    payroll_df = read_workbook(
        payroll_config["path"],
        payroll_config.get("sheet_name", 0),
        dtype=str,
    )
    validation_config = config.get("master_validation", {})
    missing_optional_columns = validate_master_columns(
        payroll_df,
        validation_config.get("payroll_master_required_columns", []),
        validation_config.get("payroll_master_optional_columns", []),
        "Payroll Master",
    )
    payroll_column_mapping = dict(payroll_config["columns"])
    for missing_column in missing_optional_columns:
        for key, column_ref in list(payroll_column_mapping.items()):
            if str(column_ref).strip() == str(missing_column).strip():
                payroll_column_mapping[key] = None
    payroll_columns = resolve_column_map(
        payroll_df,
        payroll_column_mapping,
        "payroll_master.columns",
        allow_missing=True,
    )
    return payroll_df, payroll_columns


def build_payroll_employee_mapping(payroll_df, payroll_columns):
    """Build normalized employee aliases from Payroll Master rows."""
    alias_lookup = {}
    records = []
    review_rows = []

    if payroll_df.empty or not payroll_columns:
        return {"alias_lookup": alias_lookup, "records": records, "review_rows": review_rows}

    for row_index, payroll_row in payroll_df.iterrows():
        first_name = _clean_token_piece(
            safe_value(payroll_row.get(payroll_columns.get("first_name"), ""))
        )
        last_name = _clean_token_piece(
            safe_value(payroll_row.get(payroll_columns.get("last_name"), ""))
        )
        if not first_name or not last_name:
            continue

        display_employee = f"{first_name} {last_name}".strip()
        employee_parts = {
            "first": _clean_name_piece(first_name),
            "last": _clean_name_piece(last_name),
            "initials": _clean_name_piece(first_name[:1]),
            "original": display_employee,
        }
        aliases = _employee_alias_variants(employee_parts)
        record = {
            "row_index": row_index,
            "display_employee": display_employee,
            "first_name": first_name,
            "last_name": normalize_name(last_name),
            "sin": _text_value(payroll_row.get(payroll_columns.get("sin"), "")),
            "loan_out_corp": _text_value(
                payroll_row.get(payroll_columns.get("loan_out_corp"), "")
            ),
            "address": _text_value(payroll_row.get(payroll_columns.get("address"), "")),
            "city": _text_value(payroll_row.get(payroll_columns.get("city"), "")),
            "province": _text_value(payroll_row.get(payroll_columns.get("province"), "")),
            "country": _text_value(payroll_row.get(payroll_columns.get("country"), "")),
            "zip_code": _text_value(payroll_row.get(payroll_columns.get("zip_code"), "")),
            "gst_number": _text_value(payroll_row.get(payroll_columns.get("gst_number"), "")),
            "union_name": _text_value(payroll_row.get(payroll_columns.get("union_name"), "")),
            "federal_id": _text_value(payroll_row.get(payroll_columns.get("federal_id"), "")),
            "position_name": _text_value(payroll_row.get(payroll_columns.get("position_name"), "")),
            "aliases": aliases,
            "last_normalized": normalize_name(last_name),
            "first_initial": normalize_name(first_name[:1]),
        }
        records.append(record)
        review_rows.append(
            {
                "Payroll Employee": display_employee,
                "Loan Out Corp": record["loan_out_corp"],
                "Position Name": record["position_name"],
                "Alias Variants": " | ".join(aliases),
            }
        )
        for alias in aliases:
            alias_lookup.setdefault(alias, []).append(record)

    return {"alias_lookup": alias_lookup, "records": records, "review_rows": review_rows}


def suggest_payroll_master_match(extraction, payroll_mapping, vendor_lookup, config):
    """Match extracted payroll token against Payroll Master first."""
    if not extraction.get("employee_token"):
        return _employee_match_result(
            "not_found",
            "No employee token was extracted from payroll description.",
        )
    if not payroll_mapping.get("records"):
        return _employee_match_result(
            "not_found",
            "Payroll Master is empty or disabled.",
        )

    variants = _extracted_employee_alias_variants(extraction)
    employee_parts = _employee_parts_from_token(extraction.get("employee_token", ""))
    ambiguous_records = _same_last_initial_records(employee_parts, payroll_mapping)
    if (
        _is_initial_only_employee_parts(employee_parts)
        and len({record["row_index"] for record in ambiguous_records}) > 1
    ):
        return _employee_match_result(
            "ambiguous",
            "Multiple Payroll Master employees share the same last name and initial.",
            candidates=ambiguous_records,
            match_score=100,
            match_type="AMBIGUOUS",
        )

    exact_candidates = []
    for variant in variants:
        exact_candidates.extend(payroll_mapping["alias_lookup"].get(variant, []))
    exact_candidates = _dedupe_mapping_records(exact_candidates)

    if len(exact_candidates) == 1:
        return _payroll_match_result(exact_candidates[0], vendor_lookup, 100)
    if len(exact_candidates) > 1:
        return _employee_match_result(
            "ambiguous",
            "Multiple Payroll Master employees matched the normalized employee token.",
            candidates=exact_candidates,
            match_score=100,
            match_type="AMBIGUOUS",
        )

    return _fuzzy_match_payroll_master(variants, employee_parts, payroll_mapping, vendor_lookup, config)


def _fuzzy_match_payroll_master(variants, employee_parts, payroll_mapping, vendor_lookup, config):
    if not variants or not payroll_mapping.get("alias_lookup"):
        return _employee_match_result("not_found", "No Payroll Master aliases available.")

    try:
        from rapidfuzz import fuzz, process
    except ImportError as exc:
        raise ImportError("rapidfuzz is required for Payroll Master employee matching.") from exc

    threshold = int(config.get("employee_matching", {}).get("fuzzy_threshold", 92))
    if _is_initial_only_employee_parts(employee_parts):
        threshold = max(threshold, 96)
    alias_choices = list(payroll_mapping["alias_lookup"].keys())
    matches = process.extract(
        query=variants[0],
        choices=alias_choices,
        scorer=fuzz.ratio,
        limit=5,
    )
    candidates = []
    for matched_alias, score, _ in matches:
        if score >= threshold:
            for record in payroll_mapping["alias_lookup"].get(matched_alias, []):
                candidate = dict(record)
                candidate["match_score"] = score
                candidates.append(candidate)
    candidates = _dedupe_mapping_records(candidates)
    if len(candidates) == 1:
        return _payroll_match_result(candidates[0], vendor_lookup, candidates[0].get("match_score", 0))
    if len(candidates) > 1:
        return _employee_match_result(
            "ambiguous",
            "Multiple Payroll Master employees matched by fuzzy token.",
            candidates=candidates,
            match_score=max(candidate.get("match_score", 0) for candidate in candidates),
            match_type="AMBIGUOUS",
        )
    return _employee_match_result("not_found", "No Payroll Master employee matched.")


def _payroll_match_result(payroll_record, vendor_lookup, match_score):
    record = dict(payroll_record)
    loan_out_corp = str(safe_value(record.get("loan_out_corp", ""))).strip()
    vendor_record = None
    if loan_out_corp:
        normalized_corp = normalize_name(loan_out_corp)
        if normalized_corp in vendor_lookup.index:
            vendor_record = vendor_lookup.loc[normalized_corp]
            record["suggested_vendor_corp"] = safe_value(vendor_record.get("Vendor Name", loan_out_corp))
        else:
            record["suggested_vendor_corp"] = ""
    else:
        record["suggested_vendor_corp"] = ""

    record["vendor_corp_found"] = vendor_record is not None
    match_type = "PAYROLL + LOAN OUT MATCH" if loan_out_corp and vendor_record is not None else "PAYROLL MASTER MATCH"
    return _employee_match_result(
        "matched",
        "Payroll Master employee match.",
        record=record,
        match_score=match_score,
        match_type=match_type,
    )


def build_loanout_employee_mapping(vendor_df, config):
    loanout_config = config.get("loanout_mapping", {})
    if not loanout_config.get("enabled", False):
        return {
            "records": [],
            "alias_lookup": {},
            "review_rows": [],
            "non_employee_rows": [],
            "related_info_by_row": {},
        }

    corp_name_column = resolve_column(
        vendor_df,
        loanout_config.get("corp_name_column")
        or config["vendor_master"]["columns"]["vendor_name"],
        "loanout_mapping.corp_name_column",
    )
    # Cột alias_columns là list nên phải loop từng cột, không được dùng nguyên list làm key.
    alias_columns = resolve_column_list(
        vendor_df,
        loanout_config.get("alias_columns", []),
        "loanout_mapping.alias_columns",
    )
    alias_prefixes = [
        _normalize_loanout_prefix(prefix)
        for prefix in loanout_config.get("alias_prefixes", ["FSO", "F/S/O", "ITF", "I/T/F"])
        if str(prefix).strip()
    ]

    records = []
    review_rows = []
    non_employee_rows = []
    related_info_by_row = {}
    alias_lookup = {}

    for row_index, vendor_row in vendor_df.iterrows():
        corp_name = _text_value(vendor_row.get(corp_name_column, "")).strip()
        if not corp_name:
            continue

        for alias_column in alias_columns:
            if alias_column not in vendor_df.columns:
                continue
            for original_value in _split_loanout_values(vendor_row.get(alias_column, "")):
                # FSO/F/S/O/ITF/I/T/F nghĩa là vendor/corp đại diện cho employee, nên được dùng để tạo employee alias.
                alias_part = _extract_employee_alias_part(original_value, alias_prefixes)
                if alias_part is None:
                    # DBA/C/O/location/company values không phải employee alias, nhưng vẫn giữ lại làm related entity info.
                    related_info_by_row.setdefault(row_index, []).append(original_value)
                    non_employee_rows.append(
                        {
                            "Vendor Name": corp_name,
                            "Alias Column": alias_column,
                            "Related Entity Info": original_value,
                            "Reason": "Value does not start with FSO/F/S/O/ITF/I/T/F.",
                        }
                    )
                    continue

                original_alias, person_part = alias_part
                alias_data = _loanout_alias_data(person_part)
                if not alias_data:
                    related_info_by_row.setdefault(row_index, []).append(original_value)
                    non_employee_rows.append(
                        {
                            "Vendor Name": corp_name,
                            "Alias Column": alias_column,
                            "Related Entity Info": original_value,
                            "Reason": "FSO/ITF value could not be parsed as an employee alias.",
                        }
                    )
                    continue

                record = {
                    "row_index": row_index,
                    "corp_name": corp_name,
                    "vendor_row": vendor_row,
                    "alias_column": alias_column,
                    "original_alias": original_alias,
                    "person_part": person_part,
                    "display_employee": alias_data["display_employee"],
                    "normalized_aliases": alias_data["normalized_aliases"],
                    "last_name": alias_data["last_name"],
                    "first_name": alias_data["first_name"],
                    "first_initial": alias_data["first_initial"],
                }
                records.append(record)
                for normalized_alias in alias_data["normalized_aliases"]:
                    alias_lookup.setdefault(normalized_alias, []).append(record)

                review_rows.append(
                    {
                        "Corporation Vendor Name": corp_name,
                        "Alias Column": alias_column,
                        "Original Alias": original_alias,
                        "Person Part": person_part,
                        "Display Employee": alias_data["display_employee"],
                        "Normalized Aliases": " | ".join(
                            alias_data["normalized_aliases"]
                        ),
                    }
                )

    for normalized_alias, candidates in list(alias_lookup.items()):
        alias_lookup[normalized_alias] = _dedupe_mapping_records(candidates)

    return {
        "records": records,
        "alias_lookup": alias_lookup,
        "review_rows": review_rows,
        "non_employee_rows": non_employee_rows,
        "related_info_by_row": {
            row_index: " | ".join(_ordered_unique(values))
            for row_index, values in related_info_by_row.items()
        },
    }


def suggest_employee_loanout_match(extraction, loanout_mapping, config):
    return _match_extracted_employee_to_loanout(extraction, loanout_mapping, config)


def suggest_employee_vendor_name_match(extraction, employee_vendor_mapping, config):
    return _match_extracted_employee_to_vendor_name(
        extraction,
        employee_vendor_mapping,
        config,
    )


def build_employee_vendor_name_mapping(vendor_df, config):
    employee_config = config.get("employee_matching", {})
    if not employee_config.get("enabled", True):
        return {"records": [], "alias_lookup": {}, "review_rows": []}

    vendor_name_column = resolve_column(
        vendor_df,
        employee_config.get("vendor_name_column")
        or config["vendor_master"]["columns"]["vendor_name"],
        "employee_matching.vendor_name_column",
    )
    loanout_mapping = build_loanout_employee_mapping(vendor_df, config)
    related_info_by_row = loanout_mapping.get("related_info_by_row", {})
    payroll_vendors = _normalized_payroll_vendors(config)

    records = []
    alias_lookup = {}
    review_rows = []

    for row_index, vendor_row in vendor_df.iterrows():
        vendor_name = _text_value(vendor_row.get(vendor_name_column, "")).strip()
        if not vendor_name or normalize_name(vendor_name) in payroll_vendors:
            continue

        alias_data = _vendor_name_alias_data(vendor_name)
        if not alias_data:
            continue

        record = {
            "row_index": row_index,
            "vendor_name": vendor_name,
            "display_employee": vendor_name,
            "vendor_row": vendor_row,
            "related_entity_info": related_info_by_row.get(row_index, ""),
            "normalized_aliases": alias_data["normalized_aliases"],
            "last_name": alias_data["last_name"],
            "first_name": alias_data["first_name"],
            "first_initial": alias_data["first_initial"],
        }
        records.append(record)
        for normalized_alias in alias_data["normalized_aliases"]:
            alias_lookup.setdefault(normalized_alias, []).append(record)
        review_rows.append(
            {
                "Vendor Name": vendor_name,
                "Display Employee": vendor_name,
                "Related Entity Info": related_info_by_row.get(row_index, ""),
                "Normalized Aliases": " | ".join(alias_data["normalized_aliases"]),
            }
        )

    for normalized_alias, candidates in list(alias_lookup.items()):
        alias_lookup[normalized_alias] = _dedupe_mapping_records(candidates)

    return {
        "records": records,
        "alias_lookup": alias_lookup,
        "review_rows": review_rows,
    }


def suggest_employee_match(extraction, loanout_mapping, employee_vendor_mapping, config):
    loanout_result = _match_extracted_employee_to_loanout(
        extraction,
        loanout_mapping,
        config,
    )
    if loanout_result["status"] in ["matched", "ambiguous"]:
        loanout_result["match_type"] = (
            "Loan Out Alias" if loanout_result["status"] == "matched" else "Ambiguous"
        )
        return loanout_result

    # Nếu không tìm thấy loan-out alias thì thử match employee trực tiếp với Vendor Name.
    # Không phải employee nào cũng có loan-out corp, nên không được chỉ dựa vào Loan out corp columns.
    vendor_result = _match_extracted_employee_to_vendor_name(
        extraction,
        employee_vendor_mapping,
        config,
    )
    if vendor_result["status"] == "matched":
        vendor_result["match_type"] = "Vendor Name"
    elif vendor_result["status"] == "ambiguous":
        vendor_result["match_type"] = "Ambiguous"
    else:
        vendor_result["match_type"] = "No Match"
    return vendor_result


def _match_status(score, thresholds):
    if score >= thresholds["auto_match"]:
        return "AUTO MATCH"
    if score >= thresholds["low_confidence"]:
        return "LOW CONFIDENCE"
    return "NO MATCH"


def apply_final_output_schema(enriched_df, config):
    final_columns = config.get("output", {}).get("final_columns")
    if not final_columns:
        return enriched_df

    output_df = enriched_df.copy().astype(object)
    if "Loan out corp" not in output_df.columns:
        output_df["Loan out corp"] = ""
    if "Loan Out Corp" in output_df.columns:
        blank_loanout = output_df["Loan out corp"].map(
            lambda value: str(safe_value(value)).strip() == ""
        )
        output_df.loc[blank_loanout, "Loan out corp"] = output_df.loc[
            blank_loanout,
            "Loan Out Corp",
        ]

    if "Amt" not in output_df.columns and "CAD" in output_df.columns:
        output_df["Amt"] = output_df["CAD"]
    if "Amount" not in output_df.columns and "Amt" in output_df.columns:
        output_df["Amount"] = output_df["Amt"]

    for column in final_columns:
        if column not in output_df.columns:
            output_df[column] = ""

    for column in TEXT_OUTPUT_COLUMNS.intersection(final_columns):
        output_df[column] = output_df[column].map(_text_value)
    if "Loan out corp" in final_columns:
        output_df["Loan out corp"] = output_df["Loan out corp"].map(_text_value)
    if "Employee" in final_columns:
        output_df["Employee"] = output_df["Employee"].map(_text_value)

    return output_df.reindex(columns=final_columns)


def _copy_vendor_fields(
    enriched_df,
    index,
    matched_vendor_row,
    vendor_columns,
    optional_address_columns,
):
    field_map = {
        "Loan Out Corp": "loan_out_corp",
        "Employee": "employee",
        "Tax ID": "tax_id",
        "Address": "address",
        "City": "city",
        "Province": "province",
        "Country": "country",
        "Zip Code": "zip_code",
    }

    for output_column, config_key in field_map.items():
        source_column = vendor_columns.get(config_key)
        if source_column is None:
            enriched_df.at[index, output_column] = ""
        else:
            enriched_df.at[index, output_column] = _text_value(
                matched_vendor_row[source_column]
            )

    extra_lines = []
    for source_column in optional_address_columns:
        value = _text_value(matched_vendor_row[source_column])
        if str(value).strip():
            extra_lines.append(str(value).strip())
    enriched_df.at[index, "Additional Address Lines"] = " | ".join(extra_lines)


def _copy_payroll_employee_fields(
    enriched_df,
    index,
    payroll_record,
    tax_logic=None,
    force_direct=False,
):
    # Payroll Master là nguồn chính cho employee information.
    # Nếu Payroll Master và Vendor Master conflict thì employee fields phải theo Payroll Master.
    field_map = {
        "Employee": "display_employee",
        "Employee SIN": "sin",
        "Employee GST Number": "gst_number",
        "Employee Address": "address",
        "Employee City": "city",
        "Employee Province": "province",
        "Employee Country": "country",
        "Employee Zip Code": "zip_code",
        "Position Name": "position_name",
    }
    for output_column, record_key in field_map.items():
        enriched_df.at[index, output_column] = _text_value(payroll_record.get(record_key, ""))

    tax_logic = tax_logic or {}
    loan_out_corp = _text_value(payroll_record.get("loan_out_corp", ""))
    has_loanout = bool(str(loan_out_corp).strip())
    tax_column = (
        tax_logic.get("loanout_tax_id_column", "G/HST Number")
        if has_loanout
        else tax_logic.get("no_loanout_tax_id_column", "SIN")
    )
    tax_record_key = _payroll_tax_record_key(tax_column)

    # Nếu employee không có Loan Out Corp thì dùng SIN làm Tax ID.
    # Nếu employee có Loan Out Corp thì dùng G/HST Number làm Tax ID.
    enriched_df.at[index, "Tax ID"] = _text_value(payroll_record.get(tax_record_key, ""))
    enriched_df.at[index, "Loan Out Corp"] = loan_out_corp

    use_payroll_address = (
        force_direct
        or not has_loanout
        or bool(tax_logic.get("use_payroll_address_for_loanout", False))
    )
    if use_payroll_address:
        enriched_df.at[index, "Address"] = _text_value(payroll_record.get("address", ""))
        enriched_df.at[index, "City"] = _text_value(payroll_record.get("city", ""))
        enriched_df.at[index, "Province"] = _text_value(payroll_record.get("province", ""))
        enriched_df.at[index, "Country"] = _text_value(payroll_record.get("country", ""))
        enriched_df.at[index, "Zip Code"] = _text_value(payroll_record.get("zip_code", ""))


def _payroll_tax_record_key(column_name):
    normalized_column = normalize_name(column_name)
    if normalized_column == "SIN":
        return "sin"
    if normalized_column in ["GHSTNUMBER", "GSTNUMBER", "HSTNUMBER"]:
        return "gst_number"
    if normalized_column == "FEDERALID":
        return "federal_id"
    return "gst_number"


def _copy_vendor_corp_fields(enriched_df, index, vendor_row, vendor_columns):
    # Vendor Master chỉ dùng cho corporation/vendor info.
    # Excel hay đọc Tax ID thành int/float nên không được gán trực tiếp vào cột string.
    enriched_df.at[index, "Vendor Corp Tax ID"] = _text_value(
        vendor_row.get(vendor_columns.get("tax_id"), "")
    )
    enriched_df.at[index, "Vendor Corp Address"] = _text_value(
        vendor_row.get(vendor_columns.get("address"), "")
    )


def _related_entity_info_from_vendor_row(vendor_row, vendor_columns):
    values = []
    for key in ["loan_out_corp", "loan_out_corp_2"]:
        source_column = vendor_columns.get(key)
        if source_column is None:
            continue
        for value in _split_loanout_values(vendor_row.get(source_column, "")):
            if _extract_employee_alias_part(
                value,
                [_normalize_loanout_prefix(prefix) for prefix in ["FSO", "F/S/O", "ITF", "I/T/F"]],
            ) is None:
                values.append(value)
    return " | ".join(_ordered_unique(values))


def _copy_extraction_fields(enriched_df, index, extraction):
    # Trước khi assign vào output dataframe, luôn convert giá trị enrich thành text an toàn.
    enriched_df.at[index, "Employee Token"] = _text_value(extraction.get("employee_token", ""))
    enriched_df.at[index, "Normalized Employee"] = _text_value(extraction.get(
        "normalized_employee", ""
    ))
    enriched_df.at[index, "Employee Extraction Pattern"] = _text_value(extraction.get(
        "extraction_pattern", ""
    ))
    enriched_df.at[index, "Employee Extraction Confidence"] = _text_value(extraction.get(
        "extraction_confidence", ""
    ))
    enriched_df.at[index, "Employee Extraction Status"] = _text_value(extraction.get(
        "extraction_status", ""
    ))


def _handle_approved_employee_review_decision(
    enriched_df,
    index,
    extraction,
    employee_decision,
    vendor_lookup,
    payroll_mapping,
    config,
    vendor_columns,
    optional_address_columns,
    payroll_extraction_rows,
    employee_not_found_rows,
    ambiguous_employee_rows,
    employee_vendor_match_rows,
    approved_missing_source_rows,
    log,
    source_row,
):
    if employee_decision is None:
        _mark_pending_employee_review(
            enriched_df,
            index,
            "No approved employee review decision found.",
            extraction,
            payroll_extraction_rows,
            employee_not_found_rows,
        )
        log.add(
            "WARNING",
            "Approved Employee Review",
            "No employee review decision found for payroll row.",
            source_row,
        )
        return

    approval_status = str(
        safe_value(employee_decision.get("Approval Status", ""))
    ).strip()
    enriched_df.at[index, "Employee Approval Status"] = (
        approval_status or "Needs Review"
    )

    # Không được auto-fill payroll rows nếu employee chưa được approve.
    if approval_status != APPROVED_STATUS:
        _mark_pending_employee_review(
            enriched_df,
            index,
            f"Employee Approval Status is '{approval_status or 'Needs Review'}'.",
            extraction,
            payroll_extraction_rows,
            employee_not_found_rows,
        )
        if str(safe_value(employee_decision.get("Suggested Match Type", ""))).strip() == "Ambiguous":
            ambiguous_employee_rows.append(
                _employee_review_record(
                    enriched_df.loc[index],
                    "Employee review suggestion is ambiguous.",
                    extraction,
                    {
                        "status": "ambiguous",
                        "match_type": "Ambiguous",
                        "match_score": _numeric_score(employee_decision.get("Match Score", 0)),
                        "candidate_names": " | ".join(
                            str(safe_value(employee_decision.get(column, ""))).strip()
                            for column in [
                                "Suggested Loan Out Corp",
                                "Suggested Vendor Name",
                            ]
                            if str(safe_value(employee_decision.get(column, ""))).strip()
                        ),
                    },
                )
            )
        return

    approved_employee = str(
        safe_value(employee_decision.get("Approved Employee", ""))
    ).strip()
    suggested_match_type = str(
        safe_value(employee_decision.get("Suggested Match Type", ""))
    ).strip()
    payroll_match_result = suggest_payroll_master_match(
        extraction,
        payroll_mapping,
        vendor_lookup,
        config,
    )
    tax_logic = config.get("payroll_master", {}).get("tax_logic", {})
    payroll_record = (
        payroll_match_result.get("record")
        if payroll_match_result.get("status") == "matched"
        else None
    )
    employee_source = resolve_approved_employee_source(employee_decision)
    _set_approval_resolution_fields(
        enriched_df,
        index,
        employee_source,
        _employee_approval_source_label(employee_decision, employee_source),
    )
    approved_loanout_corp = (
        employee_source["source_name"]
        if employee_source["source_type"] == "loan_out_corp"
        else ""
    )
    approved_vendor_name = (
        employee_source["source_name"]
        if employee_source["source_type"] == "vendor_name"
        else ""
    )
    use_payroll_direct = employee_source["source_type"] == "payroll_master"

    # Với Employee Review, nguồn enrich có thể đến từ loan-out alias, Vendor Name, hoặc ledger description context.
    # Chỉ cần điền Approved Vendor Name / Approved Loan Out Corp khi muốn override suggestion.
    if employee_source["source_type"] == "missing":
        _mark_approved_missing_source(
            enriched_df,
            index,
            "Employee Review",
            "Approval Status is Approved but no approved or suggested employee source was available.",
            approved_missing_source_rows,
        )
        log.add(
            "ERROR",
            "Approved Employee Review",
            "Approved employee review row is missing an approved or suggested source.",
            source_row,
        )
        return

    if use_payroll_direct:
        # Payroll Master là nguồn chính cho employee information.
        # Không dùng địa chỉ của payroll company cho employee.
        if payroll_record is None:
            _mark_approved_missing_source(
                enriched_df,
                index,
                "Employee Review",
                "Approval Status is Approved but Payroll Master employee row could not be resolved.",
                approved_missing_source_rows,
            )
            return
        loanout_name = str(safe_value(payroll_record.get("loan_out_corp", ""))).strip()
        if loanout_name and not tax_logic.get("use_payroll_address_for_loanout", False):
            normalized_loanout = normalize_name(loanout_name)
            if normalized_loanout in vendor_lookup.index:
                _copy_vendor_fields(
                    enriched_df,
                    index,
                    vendor_lookup.loc[normalized_loanout],
                    vendor_columns,
                    optional_address_columns,
                )
        _copy_payroll_employee_fields(
            enriched_df,
            index,
            payroll_record,
            tax_logic,
            force_direct=False,
        )
        enriched_df.at[index, "Employee Match Type"] = "PAYROLL MASTER MATCH"
        enriched_df.at[index, "Enrichment Status"] = "Enriched from Payroll Master"
        enriched_df.at[index, "Enrichment Source"] = "Payroll Master Direct Match"
        enriched_df.at[index, "Employee Extraction Status"] = _text_value(extraction.get(
            "extraction_status",
            "",
        ))
        payroll_extraction_rows.append(
            _employee_review_record(
                enriched_df.loc[index],
                "Payroll Master direct employee review approved.",
                extraction,
                {
                    "status": "approved",
                    "match_type": "PAYROLL MASTER MATCH",
                    "match_score": _numeric_score(
                        employee_decision.get("Match Score", 0)
                    ),
                    "candidate_names": payroll_record.get("display_employee", ""),
                },
            )
        )
        return

    if approved_loanout_corp:
        normalized_corp = normalize_name(approved_loanout_corp)
        if normalized_corp not in vendor_lookup.index:
            _mark_pending_employee_review(
                enriched_df,
                index,
                "Approved Loan Out Corp does not exist in vendor master.",
                extraction,
                payroll_extraction_rows,
                employee_not_found_rows,
            )
            log.add(
                "ERROR",
                "Approved Employee Review",
                f"Approved Loan Out Corp '{approved_loanout_corp}' was not found in vendor master.",
                source_row,
            )
            return

        approved_corp_row = vendor_lookup.loc[normalized_corp]
        _copy_vendor_fields(
            enriched_df,
            index,
            approved_corp_row,
            vendor_columns,
            optional_address_columns,
        )
        if payroll_record is not None:
            _copy_payroll_employee_fields(
                enriched_df,
                index,
                payroll_record,
                tax_logic,
            )
        _copy_vendor_corp_fields(enriched_df, index, approved_corp_row, vendor_columns)
        display_employee = (
            approved_employee
            or (payroll_record or {}).get("display_employee", "")
            or str(safe_value(employee_decision.get("Suggested Employee", ""))).strip()
            or str(safe_value(employee_decision.get("Suggested Payroll Employee", ""))).strip()
            or str(safe_value(extraction.get("employee_token", ""))).strip()
        )
        enriched_df.at[index, "Employee"] = _text_value(display_employee)
        enriched_df.at[index, "Loan Out Corp"] = _text_value(approved_loanout_corp)
        enriched_df.at[index, "Related Entity Info"] = ""
        loanout_match_type = (
            "PAYROLL + LOAN OUT MATCH"
            if payroll_record is not None
            and suggested_match_type == "PAYROLL + LOAN OUT MATCH"
            else "LOAN OUT MATCH"
        )
        loanout_source = (
            "Payroll Master -> Vendor Master Loan Out Corp"
            if loanout_match_type == "PAYROLL + LOAN OUT MATCH"
            else "Payroll Description -> Loan Out Mapping"
        )
        enriched_df.at[index, "Employee Match Type"] = loanout_match_type
        enriched_df.at[index, "Enrichment Status"] = "Enriched from Approved Employee Review"
        enriched_df.at[index, "Enrichment Source"] = loanout_source
        enriched_df.at[index, "Employee Extraction Status"] = _text_value(extraction.get(
            "extraction_status",
            "",
        ))
        payroll_extraction_rows.append(
            _employee_review_record(
                enriched_df.loc[index],
                "Employee review approved.",
                extraction,
                {
                    "status": "approved",
                    "match_type": loanout_match_type,
                    "match_score": _numeric_score(
                        employee_decision.get("Match Score", 0)
                    ),
                    "candidate_names": approved_loanout_corp,
                },
            )
        )
        return

    normalized_vendor = normalize_name(approved_vendor_name)
    if normalized_vendor not in vendor_lookup.index:
        _mark_pending_employee_review(
            enriched_df,
            index,
            "Approved Vendor Name does not exist in vendor master.",
            extraction,
            payroll_extraction_rows,
            employee_not_found_rows,
        )
        log.add(
            "ERROR",
            "Approved Employee Review",
            f"Approved Vendor Name '{approved_vendor_name}' was not found in vendor master.",
            source_row,
        )
        return

    approved_vendor_row = vendor_lookup.loc[normalized_vendor]
    related_entity_info = _related_entity_info_from_vendor_row(
        approved_vendor_row,
        vendor_columns,
    )
    _copy_vendor_fields(
        enriched_df,
        index,
        approved_vendor_row,
        vendor_columns,
        optional_address_columns,
    )
    if payroll_record is not None:
        _copy_payroll_employee_fields(
            enriched_df,
            index,
            payroll_record,
            tax_logic,
        )
    _copy_vendor_corp_fields(enriched_df, index, approved_vendor_row, vendor_columns)
    display_employee = (
        approved_employee
        or (payroll_record or {}).get("display_employee", "")
        or str(safe_value(employee_decision.get("Suggested Employee", ""))).strip()
        or str(safe_value(employee_decision.get("Suggested Payroll Employee", ""))).strip()
        or str(safe_value(extraction.get("employee_token", ""))).strip()
        or approved_vendor_name
    )
    # Context match chỉ tạo gợi ý, không tự fill Tax ID/address nếu chưa được approve.
    approved_match_type = (
        "LEDGER CONTEXT MATCH"
        if suggested_match_type in ["Ledger Description Context", "LEDGER CONTEXT MATCH"]
        else "DIRECT VENDOR MATCH"
        if suggested_match_type == "DIRECT VENDOR MATCH"
        else "Vendor Name"
    )
    approved_source = (
        "Approved Employee Review -> Ledger Description Context"
        if approved_match_type == "LEDGER CONTEXT MATCH"
        else "Payroll Description -> Vendor Master Direct Match"
        if approved_match_type == "DIRECT VENDOR MATCH"
        else "Payroll Description -> Vendor Name Match"
    )

    enriched_df.at[index, "Employee"] = _text_value(display_employee)
    enriched_df.at[index, "Loan Out Corp"] = ""
    enriched_df.at[index, "Related Entity Info"] = _text_value(related_entity_info)
    enriched_df.at[index, "Employee Match Type"] = approved_match_type
    enriched_df.at[index, "Enrichment Status"] = "Enriched from Approved Employee Review"
    enriched_df.at[index, "Enrichment Source"] = approved_source
    enriched_df.at[index, "Employee Extraction Status"] = _text_value(extraction.get(
        "extraction_status",
        "",
    ))
    payroll_extraction_rows.append(
        _employee_review_record(
            enriched_df.loc[index],
            "Employee review approved.",
            extraction,
            {
                "status": "approved",
                "match_type": approved_match_type,
                "match_score": _numeric_score(employee_decision.get("Match Score", 0)),
                "candidate_names": approved_vendor_name,
            },
        )
    )
    employee_vendor_match_rows.append(
        _employee_review_record(
            enriched_df.loc[index],
            "Employee review approved by Vendor Name.",
            extraction,
            {
                "status": "approved",
                "match_type": approved_match_type,
                "match_score": _numeric_score(employee_decision.get("Match Score", 0)),
                "candidate_names": approved_vendor_name,
            },
        )
    )


def _mark_pending_employee_review(
    enriched_df,
    index,
    reason,
    extraction,
    payroll_extraction_rows,
    employee_not_found_rows,
):
    enriched_df.at[index, "Enrichment Status"] = "Pending Employee Review"
    enriched_df.at[index, "Enrichment Source"] = ""
    enriched_df.at[index, "Employee Approval Status"] = safe_value(
        enriched_df.at[index, "Employee Approval Status"]
    ) or "Needs Review"
    # Payroll rows không được lấy Tax ID/address của GREENSLATE CANADA INC.
    for column in [
        "Loan Out Corp",
        "Employee",
        "Employee Match Type",
        "Related Entity Info",
        "Tax ID",
        "Address",
        "City",
        "Province",
        "Country",
        "Zip Code",
        "Additional Address Lines",
        "Employee SIN",
        "Employee GST Number",
        "Employee Address",
        "Employee City",
        "Employee Province",
        "Employee Country",
        "Employee Zip Code",
        "Position Name",
        "Vendor Corp Tax ID",
        "Vendor Corp Address",
    ]:
        enriched_df.at[index, column] = ""

    review_record = _employee_review_record(
        enriched_df.loc[index],
        reason,
        extraction,
        {
            "status": "pending_review",
            "match_score": 0,
            "candidate_names": "",
        },
    )
    payroll_extraction_rows.append(review_record)
    employee_not_found_rows.append(review_record)


def _handle_payroll_match_result(
    enriched_df,
    index,
    extraction,
    match_result,
    vendor_columns,
    optional_address_columns,
    payroll_extraction_rows,
    employee_not_found_rows,
    ambiguous_employee_rows,
):
    payroll_extraction_rows.append(
        _employee_review_record(
            enriched_df.loc[index],
            match_result["reason"],
            extraction,
            match_result,
        )
    )

    if match_result["status"] == "matched":
        _apply_loanout_match(
            enriched_df,
            index,
            match_result["record"],
            vendor_columns,
            optional_address_columns,
            "Payroll Description -> Loan Out Mapping",
        )
        enriched_df.at[index, "Employee Extraction Status"] = "Extracted"
        return

    # MANUAL CHECK: Initial-only employee matches rat rui ro vi nhieu nguoi co
    # cung last name va initial. Neu ambiguous, reviewer phai chon corporation
    # dung truoc khi Tax ID/address duoc fill.
    if match_result["status"] == "ambiguous":
        enriched_df.at[index, "Employee Extraction Status"] = "Needs Review"
        enriched_df.at[index, "Enrichment Status"] = match_result["reason"]
        ambiguous_employee_rows.append(
            _employee_review_record(
                enriched_df.loc[index],
                match_result["reason"],
                extraction,
                match_result,
            )
        )
        return

    if extraction.get("extraction_status") == "No Match":
        enriched_df.at[index, "Employee Extraction Status"] = "No Match"
    else:
        enriched_df.at[index, "Employee Extraction Status"] = "Needs Review"
    enriched_df.at[index, "Enrichment Status"] = match_result["reason"]
    employee_not_found_rows.append(
        _employee_review_record(
            enriched_df.loc[index],
            match_result["reason"],
            extraction,
            match_result,
        )
    )


def _apply_loanout_match(
    enriched_df,
    index,
    mapping_record,
    vendor_columns,
    optional_address_columns,
    enrichment_source,
):
    # Tax ID/address chi duoc fill tu corporation row khi mapping employee ->
    # loan-out du tin cay. Khong lay thong tin tu payroll processor vendor.
    _copy_vendor_fields(
        enriched_df,
        index,
        mapping_record["vendor_row"],
        vendor_columns,
        optional_address_columns,
    )
    enriched_df.at[index, "Employee"] = mapping_record["display_employee"]
    enriched_df.at[index, "Loan Out Corp"] = mapping_record["corp_name"]
    enriched_df.at[index, "Related Entity Info"] = ""
    enriched_df.at[index, "Enrichment Status"] = "Enriched from Loan Out Mapping"
    enriched_df.at[index, "Enrichment Source"] = enrichment_source


def _match_extracted_employee_to_loanout(extraction, loanout_mapping, config):
    if extraction.get("extraction_status") == "No Match":
        return _employee_match_result(
            "not_found",
            "No employee token was extracted from payroll description.",
        )

    variants = _extracted_employee_alias_variants(extraction)
    employee_parts = _employee_parts_from_token(extraction.get("employee_token", ""))
    initial_only = _is_initial_only_employee_parts(employee_parts)

    if initial_only:
        ambiguous_records = _same_last_initial_records(employee_parts, loanout_mapping)
        if len(ambiguous_records) > 1:
            return _employee_match_result(
                "ambiguous",
                "Multiple loan-out candidates share the same last name and initial.",
                candidates=ambiguous_records,
            )

    exact_candidates = []
    for variant in variants:
        exact_candidates.extend(loanout_mapping["alias_lookup"].get(variant, []))
    exact_candidates = _dedupe_mapping_records(exact_candidates)

    if len(exact_candidates) == 1:
        return _employee_match_result(
            "matched",
            "Exact normalized employee alias match.",
            record=exact_candidates[0],
            match_score=100,
        )
    if len(exact_candidates) > 1:
        return _employee_match_result(
            "ambiguous",
            "Multiple loan-out candidates matched the normalized employee alias.",
            candidates=exact_candidates,
        )

    return _fuzzy_match_employee_alias(
        variants,
        employee_parts,
        initial_only,
        loanout_mapping,
        config,
    )


def _match_ledger_vendor_to_loanout(ledger_vendor_name, loanout_mapping, config):
    variants, employee_parts = _ledger_vendor_employee_variants(ledger_vendor_name)
    if not variants:
        return _employee_match_result(
            "not_found",
            "Ledger vendor did not produce employee alias variants.",
        )

    initial_only = _is_initial_only_employee_parts(employee_parts)
    if initial_only:
        ambiguous_records = _same_last_initial_records(employee_parts, loanout_mapping)
        if len(ambiguous_records) > 1:
            return _employee_match_result(
                "ambiguous",
                "Multiple loan-out candidates share the same last name and initial.",
                candidates=ambiguous_records,
            )

    exact_candidates = []
    for variant in variants:
        exact_candidates.extend(loanout_mapping["alias_lookup"].get(variant, []))
    exact_candidates = _dedupe_mapping_records(exact_candidates)
    if len(exact_candidates) == 1:
        return _employee_match_result(
            "matched",
            "Exact normalized ledger vendor employee alias match.",
            record=exact_candidates[0],
            match_score=100,
        )
    if len(exact_candidates) > 1:
        return _employee_match_result(
            "ambiguous",
            "Multiple loan-out candidates matched the ledger vendor employee alias.",
            candidates=exact_candidates,
        )

    return _fuzzy_match_employee_alias(
        variants,
        employee_parts,
        initial_only,
        loanout_mapping,
        config,
    )


def _fuzzy_match_employee_alias(
    variants,
    employee_parts,
    initial_only,
    loanout_mapping,
    config,
):
    if not variants or not loanout_mapping["alias_lookup"]:
        return _employee_match_result(
            "not_found",
            "Employee was extracted but no loan-out mapping candidate was found.",
        )

    try:
        from rapidfuzz import fuzz, process
    except ImportError as exc:
        raise ImportError(
            "rapidfuzz is required for employee loan-out matching. Install it in "
            "the active environment or run the project with the existing .venv."
        ) from exc

    threshold = int(config.get("loanout_mapping", {}).get("fuzzy_threshold", 92))
    if initial_only and config.get("loanout_mapping", {}).get(
        "strict_initial_match", True
    ):
        threshold = max(threshold, 98)

    alias_choices = list(loanout_mapping["alias_lookup"].keys())
    fuzzy_candidates = []
    for variant in variants:
        match = process.extractOne(variant, alias_choices, scorer=fuzz.ratio)
        if match is None:
            continue
        matched_alias, score, _ = match
        if score >= threshold:
            for record in loanout_mapping["alias_lookup"].get(matched_alias, []):
                fuzzy_candidates.append((record, score))

    unique_records = _dedupe_mapping_records([record for record, _ in fuzzy_candidates])
    if len(unique_records) == 1:
        return _employee_match_result(
            "matched",
            "Fuzzy employee alias match above threshold.",
            record=unique_records[0],
            match_score=max(score for _, score in fuzzy_candidates),
        )
    if len(unique_records) > 1:
        return _employee_match_result(
            "ambiguous",
            "Multiple loan-out candidates matched by fuzzy employee alias.",
            candidates=unique_records,
        )

    return _employee_match_result(
        "not_found",
        "Employee was extracted but no loan-out mapping candidate met the threshold.",
    )


def _employee_match_result(
    status,
    reason,
    record=None,
    candidates=None,
    match_score=0,
    match_type=None,
):
    candidates = candidates or []
    result = {
        "status": status,
        "reason": reason,
        "record": record,
        "candidates": candidates,
        "match_score": match_score,
        "match_type": match_type or "",
        "candidate_names": " | ".join(
            _candidate_display_name(record)
            for record in _dedupe_mapping_records(candidates)
        ),
    }
    if record is not None:
        result["candidate_names"] = _candidate_display_name(record)
    return result


def _candidate_display_name(record):
    return (
        record.get("display_employee")
        or record.get("corp_name")
        or record.get("vendor_name")
        or ""
    )


def _split_loanout_values(value):
    text = _text_value(value)
    if not text.strip():
        return []
    return [
        part.strip()
        for part in re.split(r"[\n\r;|]+", text)
        if part.strip()
    ]


def _extract_employee_alias_part(value, alias_prefixes):
    cleaned = str(value).strip()
    normalized = _normalize_loanout_prefix(cleaned)
    for prefix in alias_prefixes:
        if normalized.startswith(prefix):
            if prefix == "FSO":
                match = re.match(r"^\s*F\s*/?\s*S\s*/?\s*O\b\s*(.+?)\s*$", cleaned, re.I)
            elif prefix == "ITF":
                match = re.match(r"^\s*I\s*/?\s*T\s*/?\s*F\b\s*(.+?)\s*$", cleaned, re.I)
            else:
                match = None
            if match is None:
                return None
            remainder = re.sub(r"^[\s:/\-]+", "", match.group(1)).strip()
            if remainder:
                return cleaned, remainder
    return None


def _normalize_loanout_prefix(value):
    return re.sub(r"[^A-Z]", "", str(value).upper())


def _loanout_alias_data(person_part):
    cleaned = str(person_part).strip()
    slash_match = re.match(
        r"^([A-Za-z'\-]+)\s*/\s*([A-Za-z][A-Za-z'\-]*)$", cleaned
    )
    if slash_match:
        last_name = _clean_name_piece(slash_match.group(1))
        first_name = _clean_name_piece(slash_match.group(2))
    else:
        comma_match = re.match(
            r"^([A-Za-z'\-]+)\s*,\s*([A-Za-z][A-Za-z'\-]*)$", cleaned
        )
        if not comma_match:
            return None
        last_name = _clean_name_piece(comma_match.group(1))
        first_name = _clean_name_piece(comma_match.group(2))

    first_initial = first_name[:1].upper()
    display_employee = f"{first_name.title()} {last_name.title()}"
    normalized_aliases = _ordered_unique(
        [
            normalize_name(cleaned),
            normalize_name(f"{last_name} {first_name}"),
            normalize_name(f"{first_name} {last_name}"),
            normalize_name(f"{first_initial} {last_name}"),
            normalize_name(f"{last_name} {first_initial}"),
        ]
    )
    return {
        "display_employee": display_employee,
        "normalized_aliases": normalized_aliases,
        "last_name": normalize_name(last_name),
        "first_name": normalize_name(first_name),
        "first_initial": first_initial,
    }


def _vendor_name_alias_data(vendor_name):
    employee_parts = _employee_parts_from_token(vendor_name)
    first_name = _clean_name_piece(employee_parts.get("first", ""))
    last_name = _clean_name_piece(employee_parts.get("last", ""))
    initials = _clean_name_piece(employee_parts.get("initials", ""))
    if not last_name or not (first_name or initials):
        return None

    first_for_alias = first_name or initials
    first_initial = (first_name or initials)[:1].upper()
    normalized_aliases = _ordered_unique(
        [
            normalize_name(vendor_name),
            normalize_name(f"{first_for_alias} {last_name}"),
            normalize_name(f"{last_name} {first_for_alias}"),
            normalize_name(f"{first_initial} {last_name}"),
            normalize_name(f"{last_name} {first_initial}"),
        ]
    )
    return {
        "normalized_aliases": normalized_aliases,
        "last_name": normalize_name(last_name),
        "first_name": normalize_name(first_name),
        "first_initial": first_initial,
    }


def _match_extracted_employee_to_vendor_name(extraction, employee_vendor_mapping, config):
    if extraction.get("extraction_status") == "No Match":
        return _employee_match_result(
            "not_found",
            "No employee token was extracted from payroll description.",
        )

    if not config.get("employee_matching", {}).get(
        "allow_direct_vendor_name_match",
        True,
    ):
        return _employee_match_result(
            "not_found",
            "Direct employee Vendor Name matching is disabled.",
        )

    variants = _extracted_employee_alias_variants(extraction)
    employee_parts = _employee_parts_from_token(extraction.get("employee_token", ""))
    initial_only = _is_initial_only_employee_parts(employee_parts)

    if initial_only:
        same_initial_records = _same_last_initial_records(
            employee_parts,
            employee_vendor_mapping,
        )
        if len(same_initial_records) > 1:
            # Nếu chỉ có initial và có nhiều người cùng last name/initial thì phải đưa vào review.
            return _employee_match_result(
                "ambiguous",
                "Multiple vendor-name candidates share the same last name and initial.",
                candidates=same_initial_records,
            )
        if len(same_initial_records) == 1:
            return _employee_match_result(
                "matched",
                "Single vendor-name candidate matched by last name and initial.",
                record=same_initial_records[0],
                match_score=100,
            )

    exact_candidates = []
    for variant in variants:
        exact_candidates.extend(employee_vendor_mapping["alias_lookup"].get(variant, []))
    exact_candidates = _dedupe_mapping_records(exact_candidates)

    if len(exact_candidates) == 1:
        return _employee_match_result(
            "matched",
            "Exact normalized employee Vendor Name match.",
            record=exact_candidates[0],
            match_score=100,
        )
    if len(exact_candidates) > 1:
        return _employee_match_result(
            "ambiguous",
            "Multiple vendor-name candidates matched the normalized employee alias.",
            candidates=exact_candidates,
        )

    return _fuzzy_match_vendor_name_alias(
        variants,
        initial_only,
        employee_vendor_mapping,
        config,
    )


def _fuzzy_match_vendor_name_alias(
    variants,
    initial_only,
    employee_vendor_mapping,
    config,
):
    if not variants or not employee_vendor_mapping["alias_lookup"]:
        return _employee_match_result(
            "not_found",
            "Employee was extracted but no vendor-name candidate was found.",
        )

    try:
        from rapidfuzz import fuzz, process
    except ImportError as exc:
        raise ImportError(
            "rapidfuzz is required for employee Vendor Name matching. Install it "
            "in the active environment or run the project with the existing .venv."
        ) from exc

    employee_config = config.get("employee_matching", {})
    threshold = int(employee_config.get("fuzzy_threshold", 92))
    if initial_only and employee_config.get("strict_initial_match", True):
        threshold = max(threshold, 98)

    alias_choices = list(employee_vendor_mapping["alias_lookup"].keys())
    fuzzy_candidates = []
    for variant in variants:
        match = process.extractOne(variant, alias_choices, scorer=fuzz.ratio)
        if match is None:
            continue
        matched_alias, score, _ = match
        if score >= threshold:
            for record in employee_vendor_mapping["alias_lookup"].get(matched_alias, []):
                fuzzy_candidates.append((record, score))

    unique_records = _dedupe_mapping_records([record for record, _ in fuzzy_candidates])
    if len(unique_records) == 1:
        return _employee_match_result(
            "matched",
            "Fuzzy employee Vendor Name match above threshold.",
            record=unique_records[0],
            match_score=max(score for _, score in fuzzy_candidates),
        )
    if len(unique_records) > 1:
        return _employee_match_result(
            "ambiguous",
            "Multiple vendor-name candidates matched by fuzzy employee alias.",
            candidates=unique_records,
        )

    return _employee_match_result(
        "not_found",
        "Employee was extracted but no vendor-name candidate met the threshold.",
    )


def _extracted_employee_alias_variants(extraction):
    return _employee_alias_variants_from_token(extraction.get("employee_token", ""))


def _ledger_vendor_employee_variants(ledger_vendor_name):
    employee_parts = _employee_parts_from_token(ledger_vendor_name)
    return _employee_alias_variants(employee_parts), employee_parts


def _employee_alias_variants_from_token(employee_token):
    employee_parts = _employee_parts_from_token(employee_token)
    return _employee_alias_variants(employee_parts)


def _employee_alias_variants(employee_parts):
    if not employee_parts:
        return []

    first = employee_parts.get("first", "")
    last = employee_parts.get("last", "")
    initials = employee_parts.get("initials", "")
    original = employee_parts.get("original", "")

    variants = [normalize_name(original)]
    if last and first:
        variants.extend(
            [
                normalize_name(f"{last} {first}"),
                normalize_name(f"{first} {last}"),
                normalize_name(f"{last} {first[:1]}"),
                normalize_name(f"{first[:1]} {last}"),
            ]
        )
    if last and initials:
        variants.extend(
            [
                normalize_name(f"{initials} {last}"),
                normalize_name(f"{last} {initials}"),
                normalize_name(f"{initials[:1]} {last}"),
                normalize_name(f"{last} {initials[:1]}"),
            ]
        )
    return _ordered_unique([variant for variant in variants if variant])


def _employee_parts_from_token(employee_token):
    token = str(safe_value(employee_token)).strip()
    if not token:
        return {}

    slash_match = re.match(r"^([A-Za-z'\-]+)\s*/\s*([A-Za-z][A-Za-z'\-]*)$", token)
    if slash_match:
        last_name = _clean_name_piece(slash_match.group(1))
        first_name = _clean_name_piece(slash_match.group(2))
        return {
            "original": token,
            "first": first_name,
            "last": last_name,
            "initials": first_name[:1],
        }

    initial_comma_match = re.match(
        r"^([A-Za-z]{1,3})\s*,\s*([A-Za-z][A-Za-z'\-]+)$", token
    )
    if initial_comma_match:
        initials = _clean_name_piece(initial_comma_match.group(1))
        last_name = _clean_name_piece(initial_comma_match.group(2))
        return {
            "original": token,
            "first": "",
            "last": last_name,
            "initials": initials,
        }

    last_first_match = re.match(
        r"^([A-Za-z'\-\s]+)\s*,\s*([A-Za-z][A-Za-z'\-]*)$", token
    )
    if last_first_match:
        last_name = _clean_name_piece(last_first_match.group(1))
        first_piece = _clean_name_piece(last_first_match.group(2))
        return {
            "original": token,
            "first": first_piece,
            "last": last_name,
            "initials": first_piece if len(first_piece) <= 3 else first_piece[:1],
        }

    dot_match = re.match(r"^([A-Za-z]{1,3})\.([A-Za-z][A-Za-z'\-\s]+)$", token)
    if dot_match:
        initials = _clean_name_piece(dot_match.group(1))
        last_name = _clean_name_piece(dot_match.group(2))
        return {
            "original": token,
            "first": "",
            "last": last_name,
            "initials": initials,
        }

    words = [_clean_name_piece(piece) for piece in re.split(r"\s+", token) if piece]
    if len(words) >= 2:
        first_name = words[0]
        last_name = words[-1]
        return {
            "original": token,
            "first": first_name if len(first_name) > 3 else "",
            "last": last_name,
            "initials": first_name if len(first_name) <= 3 else first_name[:1],
        }

    return {"original": token, "first": "", "last": "", "initials": ""}


def _same_last_initial_records(employee_parts, loanout_mapping):
    last_name = normalize_name(employee_parts.get("last", ""))
    initials = employee_parts.get("initials", "")
    first_initial = initials[:1].upper() if initials else ""
    if not last_name or not first_initial:
        return []

    return _dedupe_mapping_records(
        [
            record
            for record in loanout_mapping["records"]
            if record["last_name"] == last_name
            and record["first_initial"] == first_initial
        ]
    )


def _is_initial_only_employee_parts(employee_parts):
    if not employee_parts:
        return False
    first = employee_parts.get("first", "")
    return not first or len(first) <= 3


def _primary_employee_normalized_value(last_name, first_piece):
    return normalize_name(f"{last_name} {first_piece}")


def _extraction_result(
    employee_token,
    normalized_employee,
    extraction_pattern,
    extraction_confidence,
    extraction_status,
):
    return {
        "employee_token": employee_token,
        "normalized_employee": normalized_employee,
        "extraction_pattern": extraction_pattern,
        "extraction_confidence": extraction_confidence,
        "extraction_status": extraction_status,
    }


def _employee_review_record(row, reason, extraction, match_result):
    return {
        "Reason": reason,
        "Account": safe_value(row.get("Account", "")),
        "Account Name": safe_value(row.get("Account Name", "")),
        "Trans Date": safe_value(row.get("Trans Date", "")),
        "Vendor ID": safe_value(row.get("Vendor ID", "")),
        "Vendor Name": safe_value(row.get("Vendor Name", "")),
        "Description": safe_value(row.get("Description", "")),
        "Employee Token": extraction.get("employee_token", ""),
        "Normalized Employee": extraction.get("normalized_employee", ""),
        "Employee Extraction Pattern": extraction.get("extraction_pattern", ""),
        "Employee Extraction Confidence": extraction.get("extraction_confidence", ""),
        "Employee Extraction Status": extraction.get("extraction_status", ""),
        "Employee Match Type": match_result.get("match_type", ""),
        "Employee Match Status": match_result.get("status", ""),
        "Employee Match Score": match_result.get("match_score", 0),
        "Candidate Loan Out Corps": match_result.get("candidate_names", ""),
        "Candidate Vendor Names": match_result.get("candidate_names", ""),
        "Amount": safe_value(row.get("Amount", "")),
    }


def _normalized_payroll_vendors(config):
    return {
        normalize_name(vendor_name)
        for vendor_name in config.get("payroll_vendors", [])
        if normalize_name(vendor_name)
    }


def _dedupe_mapping_records(records):
    deduped = []
    seen = set()
    for record in records:
        key = (record.get("row_index"), record.get("corp_name"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(record)
    return deduped


def _ordered_unique(values):
    unique_values = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return unique_values


def _clean_name_piece(value):
    return re.sub(r"\s+", " ", str(value).strip()).upper()


def _clean_token_piece(value):
    return re.sub(r"\s+", " ", str(value).strip())


def _read_dataframe(data):
    if isinstance(data, pd.DataFrame):
        return data.copy()
    return read_workbook(data)


def _build_employee_review_lookup(approved_employee_review_file, log):
    if not approved_employee_review_file:
        return {}

    try:
        employee_review_df = pd.read_excel(
            approved_employee_review_file,
            sheet_name="Employee Extraction Review",
            dtype=str,
        )
    except ValueError:
        employee_review_df = pd.read_excel(approved_employee_review_file, dtype=str)

    required_columns = [
        "Extracted Employee Token",
        "Suggested Loan Out Corp",
        "Suggested Match Type",
        "Approved Loan Out Corp",
        "Approved Vendor Name",
        "Approval Status",
        "Reviewer Notes",
    ]
    missing_columns = [
        column for column in required_columns if column not in employee_review_df.columns
    ]
    if missing_columns:
        raise ValueError(
            "Approved employee review file is missing required columns: "
            + ", ".join(missing_columns)
        )

    keys = employee_review_df.apply(
        lambda row: _employee_review_lookup_key(
            row.get("Payroll Vendor Name", ""),
            row["Extracted Employee Token"],
        ),
        axis=1,
    )
    duplicate_count = int(keys.duplicated().sum())
    if duplicate_count:
        log.add(
            "WARNING",
            "Approved Employee Review",
            f"Employee review file contains {duplicate_count} duplicate payroll vendor/token rows.",
        )

    employee_review_df = employee_review_df.assign(_lookup_key=keys).drop_duplicates(
        "_lookup_key",
        keep="first",
    )
    lookup = {}
    for _, row in employee_review_df.iterrows():
        lookup[row["_lookup_key"]] = row
        lookup[("", str(safe_value(row["Extracted Employee Token"])).strip().upper())] = row
    return lookup


def _employee_review_lookup_key(payroll_vendor_name, employee_token):
    return (
        normalize_name(payroll_vendor_name),
        str(safe_value(employee_token)).strip().upper(),
    )


def _build_decision_lookup(review_df, log):
    required_columns = [
        "Ledger Vendor Name",
        "Suggested Vendor Name",
        "Match Score",
        "Match Status",
        "Approved Vendor Name",
        "Approval Status",
        "Reviewer Notes",
    ]
    missing_columns = [column for column in required_columns if column not in review_df.columns]
    if missing_columns:
        raise ValueError(
            "Approved review file is missing required columns: "
            + ", ".join(missing_columns)
        )

    duplicate_count = int(review_df["Ledger Vendor Name"].duplicated().sum())
    if duplicate_count:
        log.add(
            "WARNING",
            "Approved Vendor Review",
            f"Review file contains {duplicate_count} duplicate Ledger Vendor Name rows.",
        )

    review_df = review_df.drop_duplicates("Ledger Vendor Name", keep="first")
    return {
        str(safe_value(row["Ledger Vendor Name"])).strip(): row
        for _, row in review_df.iterrows()
    }


def _copy_decision_fields(enriched_df, index, decision):
    for column in [
        "Suggested Vendor Name",
        "Match Status",
        "Approved Vendor Name",
        "Approval Status",
        "Reviewer Notes",
    ]:
        enriched_df.at[index, column] = _text_value(decision.get(column, ""))
    enriched_df.at[index, "Match Score"] = _numeric_score(decision.get("Match Score", 0))


def resolve_approved_vendor_source(review_row):
    """Resolve the vendor source after a reviewer marks the row Approved."""
    manual_vendor = str(safe_value(review_row.get("Approved Vendor Name", ""))).strip()
    suggested_vendor = str(safe_value(review_row.get("Suggested Vendor Name", ""))).strip()
    if manual_vendor:
        return {
            "source_type": "vendor_name",
            "source_name": manual_vendor,
            "resolution_method": "manual_override",
        }
    if suggested_vendor:
        return {
            "source_type": "vendor_name",
            "source_name": suggested_vendor,
            "resolution_method": "suggested_fallback",
        }
    return {
        "source_type": "missing",
        "source_name": "",
        "resolution_method": "missing",
    }


def resolve_approved_employee_source(review_row):
    """Resolve the employee source after a reviewer marks the row Approved."""
    manual_loanout = str(safe_value(review_row.get("Approved Loan Out Corp", ""))).strip()
    manual_vendor = str(safe_value(review_row.get("Approved Vendor Name", ""))).strip()
    suggested_match_type = str(safe_value(review_row.get("Suggested Match Type", ""))).strip()

    if manual_loanout:
        return {
            "source_type": "loan_out_corp",
            "source_name": manual_loanout,
            "resolution_method": "manual_override",
        }
    if manual_vendor:
        return {
            "source_type": "vendor_name",
            "source_name": manual_vendor,
            "resolution_method": "manual_override",
        }

    # Nếu reviewer chọn Approved thì hệ thống sẽ dùng suggested match làm nguồn mặc định.
    if suggested_match_type in [
        "PAYROLL DIRECT MATCH",
        "PAYROLL MASTER MATCH",
        "PAYROLL + LOAN OUT MATCH",
    ]:
        return {
            "source_type": "payroll_master",
            "source_name": str(
                safe_value(review_row.get("Suggested Payroll Employee", ""))
            ).strip(),
            "resolution_method": "suggested_fallback",
        }
    if suggested_match_type in ["Loan Out Alias", "LOAN OUT MATCH"]:
        suggested_loanout = str(
            safe_value(review_row.get("Suggested Loan Out Corp", ""))
        ).strip()
        suggested_vendor_corp = str(
            safe_value(review_row.get("Suggested Vendor Corp", ""))
        ).strip()
        if suggested_vendor_corp:
            return {
                "source_type": "loan_out_corp",
                "source_name": suggested_vendor_corp,
                "resolution_method": "suggested_fallback",
            }
        if suggested_loanout:
            return {
                "source_type": "loan_out_corp",
                "source_name": suggested_loanout,
                "resolution_method": "suggested_fallback",
            }
    if suggested_match_type in ["PAYROLL DIRECT MATCH", "PAYROLL MASTER MATCH"]:
        return {
            "source_type": "payroll_master",
            "source_name": str(
                safe_value(review_row.get("Suggested Payroll Employee", ""))
            ).strip(),
            "resolution_method": "suggested_fallback",
        }
    if suggested_match_type in ["Vendor Name", "DIRECT VENDOR MATCH"]:
        suggested_vendor = str(
            safe_value(review_row.get("Suggested Vendor Name", ""))
        ).strip()
        if suggested_vendor:
            return {
                "source_type": "vendor_name",
                "source_name": suggested_vendor,
                "resolution_method": "suggested_fallback",
            }
    if suggested_match_type in ["Ledger Description Context", "LEDGER CONTEXT MATCH"]:
        suggested_source_vendor = str(
            safe_value(review_row.get("Suggested Source Vendor From Ledger", ""))
        ).strip()
        if suggested_source_vendor:
            return {
                "source_type": "vendor_name",
                "source_name": suggested_source_vendor,
                "resolution_method": "suggested_fallback",
            }

    return {
        "source_type": "missing",
        "source_name": "",
        "resolution_method": "missing",
    }


def _vendor_approval_source_label(review_row, source_resolution):
    if source_resolution["resolution_method"] == "manual_override":
        return "Manual Approved Vendor Name"
    if source_resolution["resolution_method"] == "suggested_fallback":
        return "Suggested Vendor Name"
    return "Missing Approved Source"


def _employee_approval_source_label(review_row, source_resolution):
    if source_resolution["resolution_method"] == "manual_override":
        if source_resolution["source_type"] == "loan_out_corp":
            return "Manual Approved Loan Out Corp"
        if source_resolution["source_type"] == "payroll_master":
            return "Manual Payroll Master"
        return "Manual Approved Vendor Name"

    if source_resolution["resolution_method"] == "suggested_fallback":
        suggested_match_type = str(
            safe_value(review_row.get("Suggested Match Type", ""))
        ).strip()
        if suggested_match_type in ["Loan Out Alias", "LOAN OUT MATCH"]:
            return "Suggested Loan Out Corp"
        if suggested_match_type in [
            "PAYROLL DIRECT MATCH",
            "PAYROLL MASTER MATCH",
            "PAYROLL + LOAN OUT MATCH",
        ]:
            return "Suggested Payroll Employee"
        if suggested_match_type in ["Ledger Description Context", "LEDGER CONTEXT MATCH"]:
            return "Suggested Source Vendor From Ledger"
        return "Suggested Vendor Name"

    return "Missing Approved Source"


def _set_approval_resolution_fields(
    enriched_df,
    index,
    source_resolution,
    source_used_label,
):
    enriched_df.at[index, "Approval Source Used"] = _text_value(source_used_label)
    enriched_df.at[index, "Approval Resolution Method"] = source_resolution[
        "resolution_method"
    ]


def _mark_approved_missing_source(
    enriched_df,
    index,
    review_type,
    reason,
    approved_missing_source_rows,
):
    enriched_df.at[index, "Enrichment Status"] = _text_value(reason)
    enriched_df.at[index, "Approval Source Used"] = "Missing Approved Source"
    enriched_df.at[index, "Approval Resolution Method"] = "missing"
    approved_missing_source_rows.append(
        {
            "Review Type": review_type,
            "Reason": reason,
            "Account": safe_value(enriched_df.at[index, "Account"]),
            "Account Name": safe_value(enriched_df.at[index, "Account Name"]),
            "Trans Date": safe_value(enriched_df.at[index, "Trans Date"]),
            "Vendor ID": safe_value(enriched_df.at[index, "Vendor ID"]),
            "Vendor Name": safe_value(enriched_df.at[index, "Vendor Name"]),
            "Description": safe_value(enriched_df.at[index, "Description"]),
            "Suggested Vendor Name": safe_value(
                enriched_df.at[index, "Suggested Vendor Name"]
            ),
            "Approved Vendor Name": safe_value(
                enriched_df.at[index, "Approved Vendor Name"]
            ),
            "Approval Status": safe_value(enriched_df.at[index, "Approval Status"]),
            "Approval Source Used": safe_value(
                enriched_df.at[index, "Approval Source Used"]
            ),
            "Approval Resolution Method": safe_value(
                enriched_df.at[index, "Approval Resolution Method"]
            ),
            "Employee": safe_value(enriched_df.at[index, "Employee"]),
            "Employee Token": safe_value(enriched_df.at[index, "Employee Token"]),
            "Loan Out Corp": safe_value(enriched_df.at[index, "Loan Out Corp"]),
            "Amount": safe_value(enriched_df.at[index, "Amount"]),
        }
    )


def _mark_not_approved(enriched_df, index, reason, not_approved_rows):
    enriched_df.at[index, "Enrichment Status"] = _text_value(reason)
    not_approved_rows.append(
        {
            "Reason": reason,
            "Account": safe_value(enriched_df.at[index, "Account"]),
            "Account Name": safe_value(enriched_df.at[index, "Account Name"]),
            "Trans Date": safe_value(enriched_df.at[index, "Trans Date"]),
            "Vendor ID": safe_value(enriched_df.at[index, "Vendor ID"]),
            "Vendor Name": safe_value(enriched_df.at[index, "Vendor Name"]),
            "Suggested Vendor Name": safe_value(
                enriched_df.at[index, "Suggested Vendor Name"]
            ),
            "Match Score": safe_value(enriched_df.at[index, "Match Score"]),
            "Match Status": safe_value(enriched_df.at[index, "Match Status"]),
            "Approved Vendor Name": safe_value(
                enriched_df.at[index, "Approved Vendor Name"]
            ),
            "Approval Status": safe_value(enriched_df.at[index, "Approval Status"]),
            "Reviewer Notes": safe_value(enriched_df.at[index, "Reviewer Notes"]),
            "Amount": safe_value(enriched_df.at[index, "Amount"]),
        }
    )


def _low_confidence_decisions(review_df, thresholds):
    scores = pd.to_numeric(review_df["Match Score"], errors="coerce").fillna(0)
    low_confidence_mask = scores < thresholds["auto_match"]
    return review_df[low_confidence_mask].copy()


def _resolve_optional_address_columns(vendor_df, optional_address_lines, log):
    try:
        return resolve_column_list(
            vendor_df,
            optional_address_lines,
            "vendor_master.optional_address_lines",
        )
    except ValueError as exc:
        log.add("WARNING", "Vendor Master", str(exc))
        return []


def _display_vendor_name(vendor_lookup, normalized_name, vendor_name_column):
    if not normalized_name:
        return ""
    if normalized_name not in vendor_lookup.index:
        return _text_value(normalized_name)
    return _text_value(vendor_lookup.loc[normalized_name][vendor_name_column])


def _review_record(row, reason):
    return {
        "Reason": reason,
        "Account": safe_value(row.get("Account", "")),
        "Account Name": safe_value(row.get("Account Name", "")),
        "Trans Date": safe_value(row.get("Trans Date", "")),
        "Vendor ID": safe_value(row.get("Vendor ID", "")),
        "Vendor Name": safe_value(row.get("Vendor Name", "")),
        "Description": safe_value(row.get("Description", "")),
        "Additional Description": safe_value(row.get("Additional Description", "")),
        "Amount": safe_value(row.get("Amount", "")),
        "Matched Vendor": safe_value(row.get("Matched Vendor", "")),
        "Match Score": safe_value(row.get("Match Score", "")),
        "Second Best Vendor": safe_value(row.get("Second Best Vendor", "")),
        "Second Best Score": safe_value(row.get("Second Best Score", "")),
        "Match Status": safe_value(row.get("Match Status", "")),
    }


def _text_value(value):
    return safe_excel_text(value)


def _numeric_score(value):
    score = pd.to_numeric(value, errors="coerce")
    if pd.isna(score):
        return 0.0
    return float(score)
