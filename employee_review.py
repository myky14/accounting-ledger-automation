import pandas as pd
import re

from utils import (
    apply_review_workbook_formatting,
    normalize_name,
    read_workbook,
    resolve_column_map,
    safe_value,
    validate_master_columns,
    write_workbook_safely,
)
from vendor_matcher import (
    build_employee_vendor_name_mapping,
    build_loanout_employee_mapping,
    build_payroll_employee_mapping,
    extract_employee_from_payroll_description,
    load_payroll_master,
    suggest_employee_loanout_match,
    suggest_employee_vendor_name_match,
    suggest_payroll_master_match,
)


EMPLOYEE_REVIEW_COLUMNS = [
    "Extracted Employee Token",
    "Suggested Employee",
    "Suggested Match Type",
    "Suggested Payroll Employee",
    "Suggested Loan Out Corp",
    "Suggested Vendor Name",
    "Suggested Source Vendor From Ledger",
    "Source Ledger Description",
    "Source Ledger Account",
    "Match Score",
    "Context Match Score",
    "Approved Loan Out Corp",
    "Approved Employee",
    "Approved Vendor Name",
    "Approval Status",
    "Reviewer Notes",
]


def get_employee_review_path(config):
    return config.get("employee_review", {}).get(
        "output_path",
        "output/Employee Extraction Review.xlsx",
    )


def generate_employee_extraction_review(formatted_ledger, config, output_file=None):
    """Create one review row per unique payroll employee token."""
    ledger_df = _read_dataframe(formatted_ledger)
    vendor_df = _load_vendor_master(config)
    vendor_lookup = _vendor_lookup(vendor_df, config)
    payroll_df, payroll_columns = load_payroll_master(config)
    payroll_mapping = build_payroll_employee_mapping(payroll_df, payroll_columns)
    loanout_mapping = build_loanout_employee_mapping(vendor_df, config)
    employee_vendor_mapping = build_employee_vendor_name_mapping(vendor_df, config)
    payroll_vendors = _normalized_payroll_vendors(config)
    context_index = _build_context_search_index(ledger_df, config, payroll_vendors)
    vendor_names = set(vendor_lookup.index)

    review_rows = []
    context_candidate_rows = []
    seen_keys = set()

    for _, row in ledger_df.iterrows():
        payroll_vendor_name = str(safe_value(row.get("Vendor Name", ""))).strip()
        if normalize_name(payroll_vendor_name) not in payroll_vendors:
            continue

        extraction = extract_employee_from_payroll_description(
            safe_value(row.get("Description", ""))
        )
        employee_token = extraction.get("employee_token", "")
        review_key = (
            normalize_name(payroll_vendor_name),
            str(employee_token).strip().upper(),
        )
        if review_key in seen_keys:
            continue
        seen_keys.add(review_key)

        # Payroll Master là nguồn chính cho employee information.
        # Payroll Master giúp giảm fuzzy guessing và tăng độ chính xác.
        match_result, context_rows = _suggest_employee_source(
            extraction,
            payroll_mapping,
            loanout_mapping,
            employee_vendor_mapping,
            context_index,
            vendor_names,
            vendor_lookup,
            config,
        )
        context_candidate_rows.extend(context_rows)
        approval_status = "Needs Review"
        reviewer_notes = ""
        if match_result.get("status") == "not_found":
            approval_status = "Employee Not Found"
        if match_result.get("status") == "ambiguous":
            reviewer_notes = "Multiple employee candidates matched. Review manually."

        review_rows.append(
            {
                "Extracted Employee Token": employee_token,
                "Suggested Employee": _suggested_employee(match_result),
                "Suggested Match Type": _suggested_match_type(match_result),
                "Suggested Payroll Employee": _payroll_suggested_employee(match_result),
                "Suggested Loan Out Corp": _payroll_suggested_loanout(match_result),
                "Suggested Vendor Name": _suggested_vendor_name(match_result),
                "Suggested Source Vendor From Ledger": _suggested_context_vendor(match_result),
                "Source Ledger Description": _suggested_context_description(match_result),
                "Source Ledger Account": _suggested_context_account(match_result),
                "Match Score": match_result.get("match_score", 0),
                "Context Match Score": match_result.get("context_match_score", 0),
                "Approved Loan Out Corp": "",
                "Approved Employee": "",
                "Approved Vendor Name": "",
                "Approval Status": approval_status,
                # Reviewer Notes dùng để ghi lý do approve/reject.
                # Sau này rất hữu ích để audit hoặc debug workflow.
                "Reviewer Notes": reviewer_notes,
            }
        )

    review_df = pd.DataFrame(review_rows, columns=EMPLOYEE_REVIEW_COLUMNS)
    payroll_alias_df = pd.DataFrame(payroll_mapping.get("review_rows", []))
    context_candidates_df = pd.DataFrame(context_candidate_rows)
    output_path = output_file or get_employee_review_path(config)
    write_workbook_safely(
        output_path,
        {
            "Employee Extraction Review": review_df,
            "Review - Payroll Master Aliases": payroll_alias_df,
            "Review - Employee Context Candidates": context_candidates_df,
        },
    )
    apply_review_workbook_formatting(
        output_path,
        [
            "Employee Extraction Review",
            "Review - Payroll Master Aliases",
            "Review - Employee Context Candidates",
        ],
    )
    return review_df, output_path, context_candidates_df


def _suggest_employee_source(
    extraction,
    payroll_mapping,
    loanout_mapping,
    employee_vendor_mapping,
    context_index,
    vendor_names,
    vendor_lookup,
    config,
):
    # Thứ tự ưu tiên rất quan trọng: Payroll Master đáng tin hơn Vendor Master, Vendor Master đáng tin hơn Ledger Context.
    payroll_result = suggest_payroll_master_match(
        extraction,
        payroll_mapping,
        vendor_lookup,
        config,
    )
    if payroll_result.get("status") in ["matched", "ambiguous"]:
        return payroll_result, []

    loanout_result = suggest_employee_loanout_match(extraction, loanout_mapping, config)
    if loanout_result.get("status") in ["matched", "ambiguous"]:
        if loanout_result.get("status") == "matched":
            loanout_result["match_type"] = "LOAN OUT MATCH"
        else:
            loanout_result["match_type"] = "AMBIGUOUS"
        return loanout_result, []

    vendor_result = suggest_employee_vendor_name_match(
        extraction,
        employee_vendor_mapping,
        config,
    )
    if vendor_result.get("status") in ["matched", "ambiguous"]:
        if vendor_result.get("status") == "matched":
            vendor_result["match_type"] = "DIRECT VENDOR MATCH"
        else:
            vendor_result["match_type"] = "AMBIGUOUS"
        return vendor_result, []

    # Nếu employee không tìm thấy trong master data thì mới dùng context từ ledger.
    context_result = find_employee_context_in_ledger(
        extraction.get("employee_token", ""),
        context_index,
        config,
        vendor_names,
    )
    if context_result.get("status") in ["matched", "ambiguous"]:
        return context_result, context_result.get("candidate_rows", [])

    return payroll_result, context_result.get("candidate_rows", [])


def _suggested_employee(match_result):
    record = match_result.get("record")
    if record is not None:
        return (
            record.get("display_employee")
            or record.get("suggested_employee")
            or record.get("vendor_name")
            or ""
        )
    candidates = match_result.get("candidates") or []
    return " | ".join(
        candidate.get("display_employee")
        or candidate.get("suggested_employee")
        or candidate.get("vendor_name", "")
        for candidate in candidates
    )


def _suggested_match_type(match_result):
    if match_result.get("status") == "ambiguous":
        return "AMBIGUOUS"
    if match_result.get("status") == "not_found":
        return "NO MATCH"
    return match_result.get("match_type") or "NO MATCH"


def _payroll_suggested_employee(match_result):
    record = match_result.get("record")
    if record is not None:
        return record.get("display_employee", "")
    candidates = match_result.get("candidates") or []
    return " | ".join(candidate.get("display_employee", "") for candidate in candidates)


def _payroll_suggested_loanout(match_result):
    record = match_result.get("record")
    if record is not None:
        return record.get("loan_out_corp", "") or record.get("corp_name", "")
    candidates = match_result.get("candidates") or []
    return " | ".join(
        str(candidate.get("loan_out_corp", "") or candidate.get("corp_name", ""))
        for candidate in candidates
    )


def _payroll_suggested_vendor_corp(match_result):
    record = match_result.get("record")
    if record is not None:
        return record.get("suggested_vendor_corp", "")
    return ""


def _payroll_suggested_match_type(match_result):
    if match_result.get("status") == "ambiguous":
        return "AMBIGUOUS"
    if match_result.get("status") == "not_found":
        return "NO MATCH"
    return match_result.get("match_type", "NO MATCH")


def _suggested_vendor_name(match_result):
    record = match_result.get("record")
    if record is None:
        return ""
    return (
        record.get("suggested_vendor_corp")
        or record.get("vendor_name")
        or record.get("corp_name")
        or ""
    )


def _suggested_context_vendor(match_result):
    record = match_result.get("record")
    if record is None:
        return ""
    return record.get("source_vendor", "")


def _suggested_context_description(match_result):
    record = match_result.get("record")
    if record is None:
        return ""
    return record.get("source_description", "")


def _suggested_context_account(match_result):
    record = match_result.get("record")
    if record is None:
        return ""
    return record.get("source_account", "")


def find_employee_context_in_ledger(
    employee_token,
    formatted_ledger_df,
    config,
    vendor_names=None,
):
    """Search non-payroll ledger descriptions for employee context."""
    context_config = config.get("employee_context_matching", {})
    if not context_config.get("enabled", False) or not config.get("employee_matching", {}).get(
        "ledger_context_enabled",
        True,
    ):
        return {"status": "not_found", "candidate_rows": []}

    variants = _employee_context_variants(employee_token)
    if not variants:
        return {"status": "not_found", "candidate_rows": []}

    threshold = int(context_config.get("fuzzy_threshold", 88))
    max_candidates = int(context_config.get("max_candidates", 3))
    vendor_names = vendor_names or set()
    search_rows = (
        formatted_ledger_df
        if isinstance(formatted_ledger_df, list)
        else _build_context_search_index(
            formatted_ledger_df,
            config,
            _normalized_payroll_vendors(config),
        )
    )

    candidates = []
    for search_row in search_rows:
        initial_match = match_initial_last_against_description(
            employee_token,
            search_row["search_text"],
        )
        score = max(
            _context_match_score(variants, search_row["normalized_search_text"]),
            initial_match["score"],
        )
        if score < threshold:
            continue
        matched_text = initial_match["matched_text"]
        search_variant = initial_match["search_variant"] or _best_context_variant(
            variants,
            search_row["normalized_search_text"],
        )
        reason = initial_match["reason"] or "Normalized/fuzzy employee token match."
        candidates.append(
            {
                "source_vendor": search_row["source_vendor"],
                "suggested_employee": str(employee_token).strip(),
                "display_employee": str(employee_token).strip(),
                "source_description": search_row["source_description"],
                "source_account": search_row["source_account"],
                "source_amount": search_row["source_amount"],
                "matched_text": matched_text,
                "search_variant": search_variant,
                "reason": reason,
                "context_match_score": score,
                "match_score": score,
                "vendor_in_master": normalize_name(search_row["source_vendor"]) in vendor_names,
            }
        )

    candidates = sorted(
        candidates,
        key=lambda candidate: candidate["context_match_score"],
        reverse=True,
    )[:max_candidates]
    candidate_rows = [
        {
            "Extracted Employee Token": employee_token,
            "Search Variant": candidate["search_variant"],
            "Matched Text": candidate["matched_text"],
            "Source Vendor Name": candidate["source_vendor"],
            "Source Ledger Description": candidate["source_description"],
            "Source Ledger Account": candidate["source_account"],
            "Context Match Score": candidate["context_match_score"],
            "Reason": candidate["reason"],
        }
        for index, candidate in enumerate(candidates)
    ]
    if not candidates:
        return {"status": "not_found", "candidate_rows": candidate_rows}

    top_candidate = candidates[0]
    close_candidates = [
        candidate
        for candidate in candidates
        if top_candidate["context_match_score"] - candidate["context_match_score"] <= 3
    ]
    if len({candidate["source_vendor"] for candidate in close_candidates}) > 1:
        # Nếu nhiều candidate gần giống nhau thì phải đưa vào review, không tự chọn bừa.
        return {
            "status": "ambiguous",
            "match_type": "AMBIGUOUS",
            "candidates": candidates,
            "match_score": top_candidate["context_match_score"],
            "context_match_score": top_candidate["context_match_score"],
            "candidate_rows": candidate_rows,
            "reason": "Multiple close ledger context candidates found.",
        }

    # Ledger Context chỉ là gợi ý vì nó suy luận từ description, không được tự fill nếu chưa approved.
    return {
        "status": "matched",
        "match_type": "LEDGER CONTEXT MATCH",
        "record": top_candidate,
        "candidates": candidates,
        "match_score": top_candidate["context_match_score"],
        "context_match_score": top_candidate["context_match_score"],
        "candidate_rows": candidate_rows,
    }


def _employee_context_variants(employee_token):
    token = str(safe_value(employee_token)).strip()
    if not token:
        return []
    variants = [normalize_name(token)]
    dot_parts = token.replace(",", ".").split(".")
    if len(dot_parts) == 2:
        first_piece = dot_parts[0].strip()
        last_piece = dot_parts[1].strip()
        if first_piece and last_piece:
            variants.extend(
                [
                    normalize_name(f"{first_piece} {last_piece}"),
                    normalize_name(f"{last_piece} {first_piece}"),
                ]
            )
    words = [piece for piece in token.replace(",", " ").replace(".", " ").split() if piece]
    if len(words) >= 2:
        first_piece = words[0]
        last_piece = words[-1]
        variants.extend(
            [
                normalize_name(f"{first_piece} {last_piece}"),
                normalize_name(f"{last_piece} {first_piece}"),
            ]
        )
    return _ordered_unique([variant for variant in variants if variant])


def match_initial_last_against_description(employee_token, description):
    """Match tokens like F.Naguib against full names like Fatma Naguib."""
    parts = _initial_last_parts(employee_token)
    if not parts:
        return {"score": 0, "matched_text": "", "search_variant": "", "reason": ""}

    initial = parts["initial"]
    last_name = parts["last"]
    escaped_last = re.escape(last_name)
    # Với token dạng F.Naguib, không thể chỉ search F NAGUIB.
    patterns = [
        rf"\b([A-Z][A-Za-z'\-]+)\s+({escaped_last})\b",
        rf"\b({escaped_last}),?\s+({initial})\b",
        rf"\b({initial})\.?\s+({escaped_last})\b",
    ]
    text = str(safe_value(description))
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            groups = match.groups()
            if len(groups) < 2:
                continue
            if normalize_name(groups[0]) == normalize_name(last_name):
                first_piece = groups[1]
                last_piece = groups[0]
            else:
                first_piece = groups[0]
                last_piece = groups[1]
            if normalize_name(last_piece) != normalize_name(last_name):
                continue
            if not normalize_name(first_piece).startswith(initial):
                continue
            # Nếu description có Fatma Naguib thì vẫn phải match vì Fatma bắt đầu bằng F và last name là Naguib.
            return {
                "score": 100,
                "matched_text": match.group(0),
                "search_variant": f"{initial} {last_name}",
                "reason": "Initial + exact last name matched full first-name mention.",
            }
    return {"score": 0, "matched_text": "", "search_variant": "", "reason": ""}


def _initial_last_parts(employee_token):
    token = str(safe_value(employee_token)).strip()
    if not token:
        return None
    match = re.match(
        r"^\s*([A-Za-z]{1,3})[\.,\s]+([A-Za-z][A-Za-z'\-]+)\s*$",
        token,
    )
    if not match:
        reverse_match = re.match(
            r"^\s*([A-Za-z][A-Za-z'\-]+),\s*([A-Za-z]{1,3})\s*$",
            token,
        )
        if not reverse_match:
            return None
        return {
            "initial": normalize_name(reverse_match.group(2)[:1]),
            "last": reverse_match.group(1),
        }
    first_piece = match.group(1)
    last_name = match.group(2)
    return {"initial": normalize_name(first_piece[:1]), "last": last_name}


def _context_match_score(variants, normalized_text):
    if not normalized_text:
        return 0
    if any(variant and variant in normalized_text for variant in variants):
        return 100
    try:
        from rapidfuzz import fuzz
    except ImportError as exc:
        raise ImportError("rapidfuzz is required for ledger context matching.") from exc
    return max(fuzz.partial_ratio(variant, normalized_text) for variant in variants)


def _best_context_variant(variants, normalized_text):
    for variant in variants:
        if variant and variant in normalized_text:
            return variant
    return variants[0] if variants else ""


def _build_context_search_index(ledger_df, config, payroll_vendors):
    context_config = config.get("employee_context_matching", {})
    search_columns = context_config.get("search_columns", ["Description", "Additional Description"])
    candidate_vendor_column = context_config.get("candidate_vendor_column", "Vendor Name")
    search_rows = []
    for row_index, row in ledger_df.iterrows():
        source_vendor = str(safe_value(row.get(candidate_vendor_column, ""))).strip()
        if not source_vendor:
            continue
        if (
            context_config.get("exclude_payroll_vendors", True)
            and normalize_name(source_vendor) in payroll_vendors
        ):
            continue
        source_description = _source_description(row, search_columns)
        search_text = source_description
        if context_config.get("include_vendor_name", True):
            search_text = f"{search_text} {source_vendor}"
        normalized_search_text = normalize_name(search_text)
        if not normalized_search_text:
            continue
        search_rows.append(
            {
                "source_vendor": source_vendor,
                "source_description": source_description,
                "source_account": safe_value(row.get("Account", "")),
                "source_amount": safe_value(row.get("Amount", "")),
                "search_text": search_text,
                "normalized_search_text": normalized_search_text,
            }
        )
    return search_rows


def _source_description(row, search_columns):
    pieces = []
    for column in search_columns:
        value = str(safe_value(row.get(column, ""))).strip()
        if value:
            pieces.append(value)
    return " | ".join(pieces)


def _ordered_unique(values):
    unique_values = []
    seen = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return unique_values


def _load_vendor_master(config):
    vendor_config = config["vendor_master"]
    vendor_df = read_workbook(
        config["paths"]["vendor_master"],
        vendor_config.get("sheet_name", 0),
        dtype=str,
    )
    validation_config = config.get("master_validation", {})
    validate_master_columns(
        vendor_df,
        validation_config.get("vendor_master_required_columns", []),
        validation_config.get("vendor_master_optional_columns", []),
        "Vendor Master",
    )
    resolve_column_map(
        vendor_df,
        vendor_config["columns"],
        "vendor_master.columns",
        allow_missing=True,
    )
    return vendor_df


def _vendor_lookup(vendor_df, config):
    vendor_column = config["vendor_master"]["columns"].get("vendor_name")
    if not vendor_column or vendor_column not in vendor_df.columns:
        return pd.DataFrame().set_index(pd.Index([], name="_normalized_vendor"))
    vendor_df = vendor_df.copy()
    vendor_df["_normalized_vendor"] = vendor_df[vendor_column].apply(normalize_name)
    vendor_df = vendor_df[vendor_df["_normalized_vendor"] != ""]
    return vendor_df.drop_duplicates("_normalized_vendor").set_index("_normalized_vendor")


def _normalized_payroll_vendors(config):
    return {
        normalize_name(vendor_name)
        for vendor_name in config.get("payroll_vendors", [])
        if normalize_name(vendor_name)
    }


def _read_dataframe(data):
    if isinstance(data, pd.DataFrame):
        return data.copy()
    return read_workbook(data)
