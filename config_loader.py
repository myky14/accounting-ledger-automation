from pathlib import Path
import json
import re


REQUIRED_LEDGER_COLUMNS = [
    "trans_date",
    "vendor_id",
    "vendor_name",
    "src",
    "trans_ref",
    "description",
    "our_reference",
    "currency",
    "cad",
    "amount",
    "ep",
]

REQUIRED_VENDOR_FIELDS = [
    "vendor_name",
    "tax_id",
    "address",
    "city",
    "province",
    "country",
    "zip_code",
]

OPTIONAL_VENDOR_FIELDS = [
    "vendor_id",
    "currency",
    "loan_out_corp",
    "loan_out_corp_2",
    "employee",
]

REQUIRED_PAYROLL_MASTER_FIELDS = [
    "sin",
    "last_name",
    "first_name",
    "loan_out_corp",
    "address",
    "city",
    "province",
    "zip_code",
    "gst_number",
]

OPTIONAL_PAYROLL_MASTER_FIELDS = [
    "union_name",
    "position_name",
    "federal_id",
]


def load_config(config_path):
    """Load a client config file and run basic structural validation."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file does not exist: {path}")

    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        config = json.loads(text)
    else:
        config = _load_yaml(text)

    normalize_config(config)
    validate_config(config)
    return config


def normalize_config(config):
    """Normalize newer config aliases into the internal legacy structure."""
    if "vendor_master_mapping" not in config:
        return config

    mapping = config.get("vendor_master_mapping") or {}
    vendor_master = config.setdefault("vendor_master", {})
    vendor_master.setdefault("sheet_name", 0)

    columns = {}
    for key in REQUIRED_VENDOR_FIELDS + OPTIONAL_VENDOR_FIELDS:
        if key in mapping:
            columns[key] = mapping.get(key)

    columns.setdefault("employee", None)
    columns.setdefault("loan_out_corp", mapping.get("loan_out_corp"))
    columns.setdefault("loan_out_corp_2", mapping.get("loan_out_corp_2"))
    vendor_master["columns"] = columns
    return config


def validate_config(config):
    """Validate required config sections before the pipeline touches data."""
    for section in ["paths", "ledger", "vendor_master", "matching"]:
        if section not in config:
            raise ValueError(f"Missing required config section: {section}")

    for key in [
        "raw_ledger",
        "vendor_master",
        "formatted_ledger",
        "final_enriched_workbook",
    ]:
        if not config["paths"].get(key):
            raise ValueError(f"Missing required paths.{key}")

    config["paths"].setdefault("vendor_match_review", "output/Vendor Match Review.xlsx")
    config.setdefault("employee_review", {}).setdefault(
        "output_path", "output/Employee Extraction Review.xlsx"
    )
    payroll_master = config.setdefault("payroll_master", {})
    payroll_master.setdefault("enabled", False)
    payroll_master.setdefault("path", "")
    payroll_master.setdefault("sheet_name", 0)
    payroll_master.setdefault("columns", {})
    # Nếu employee không có Loan Out Corp thì dùng SIN làm Tax ID.
    # Nếu employee có Loan Out Corp thì dùng G/HST Number làm Tax ID.
    tax_logic = payroll_master.setdefault("tax_logic", {})
    tax_logic.setdefault("no_loanout_tax_id_column", "SIN")
    tax_logic.setdefault("loanout_tax_id_column", "G/HST Number")
    tax_logic.setdefault("use_payroll_address_for_loanout", False)
    config.setdefault("input_mode", {}).setdefault("allow_pre_flattened", True)
    formatted_validation = config.setdefault("formatted_ledger_validation", {})
    formatted_validation.setdefault(
        "required_columns",
        ["Vendor Name", "Description", "Amount", "Trans Date"],
    )
    formatted_validation.setdefault(
        "recommended_columns",
        ["Account", "Account Name", "Vendor ID", "Currency"],
    )

    ledger_columns = config["ledger"].get("columns", {})
    for key in REQUIRED_LEDGER_COLUMNS:
        if key not in ledger_columns:
            raise ValueError(f"Missing required ledger.columns.{key}")

    account_header = config["ledger"].get("account_header", {})
    for key in [
        "marker_column",
        "marker_value",
        "account_id_column",
        "account_name_column",
    ]:
        if key not in account_header:
            raise ValueError(f"Missing required ledger.account_header.{key}")

    additional_description = config["ledger"].get("additional_description", {})
    if "description_column" not in additional_description:
        raise ValueError(
            "Missing required ledger.additional_description.description_column"
        )

    vendor_columns = config["vendor_master"].get("columns", {})
    for key in REQUIRED_VENDOR_FIELDS:
        if key not in vendor_columns:
            raise ValueError(f"Missing required vendor_master.columns.{key}")
    for key, value in vendor_columns.items():
        _validate_single_column_ref(value, f"vendor_master.columns.{key}")

    if payroll_master.get("enabled", False):
        if not payroll_master.get("path"):
            raise ValueError("Missing required payroll_master.path")
        payroll_columns = payroll_master.get("columns", {})
        for key in REQUIRED_PAYROLL_MASTER_FIELDS:
            if key not in payroll_columns:
                raise ValueError(f"Missing required payroll_master.columns.{key}")
            _validate_single_column_ref(
                payroll_columns.get(key),
                f"payroll_master.columns.{key}",
            )
        for key in OPTIONAL_PAYROLL_MASTER_FIELDS:
            if key in payroll_columns:
                _validate_single_column_ref(
                    payroll_columns.get(key),
                    f"payroll_master.columns.{key}",
                )

    loanout_mapping = config.get("loanout_mapping", {})
    if loanout_mapping.get("enabled", False):
        _validate_single_column_ref(
            loanout_mapping.get("corp_name_column"),
            "loanout_mapping.corp_name_column",
            allow_blank=False,
        )
        alias_columns = loanout_mapping.get("alias_columns", [])
        if not isinstance(alias_columns, list):
            raise ValueError(
                "loanout_mapping.alias_columns must be a list of column names."
            )
        for index, alias_column in enumerate(alias_columns):
            _validate_single_column_ref(
                alias_column,
                f"loanout_mapping.alias_columns[{index}]",
                allow_blank=False,
            )

    employee_matching = config.setdefault("employee_matching", {})
    employee_matching.setdefault("enabled", True)
    employee_matching.setdefault("vendor_name_column", vendor_columns.get("vendor_name"))
    employee_matching.setdefault("fuzzy_threshold", 92)
    employee_matching.setdefault("strict_initial_match", True)
    employee_matching.setdefault("allow_direct_vendor_name_match", True)
    employee_matching.setdefault(
        "source_priority",
        ["payroll_master", "loanout_alias", "vendor_name", "ledger_context"],
    )
    employee_matching.setdefault("direct_vendor_match_enabled", True)
    employee_matching.setdefault("ledger_context_enabled", True)
    employee_matching["allow_direct_vendor_name_match"] = employee_matching.get(
        "direct_vendor_match_enabled",
        employee_matching.get("allow_direct_vendor_name_match", True),
    )
    if employee_matching.get("enabled", False):
        _validate_single_column_ref(
            employee_matching.get("vendor_name_column"),
            "employee_matching.vendor_name_column",
            allow_blank=False,
        )

    employee_context_matching = config.setdefault("employee_context_matching", {})
    employee_context_matching.setdefault("enabled", True)
    employee_context_matching.setdefault(
        "search_columns",
        ["Description", "Additional Description"],
    )
    employee_context_matching.setdefault("candidate_vendor_column", "Vendor Name")
    employee_context_matching.setdefault("exclude_payroll_vendors", True)
    employee_context_matching.setdefault("fuzzy_threshold", 88)
    employee_context_matching.setdefault("max_candidates", 3)
    if not isinstance(employee_context_matching.get("search_columns", []), list):
        raise ValueError("employee_context_matching.search_columns must be a list.")

    thresholds = config["matching"].get("thresholds", {})
    for key in ["auto_match", "review", "low_confidence"]:
        if key not in thresholds:
            raise ValueError(f"Missing required matching.thresholds.{key}")


def _validate_single_column_ref(value, label, allow_blank=True):
    if value is None or value == "":
        if allow_blank:
            return
        raise ValueError(f"{label} must be a column name, not blank.")
    if isinstance(value, list):
        raise ValueError(
            f"{label} must be one column name, but got a list: {value}. "
            "Move multi-column values to a field that supports lists, such as "
            "loanout_mapping.alias_columns."
        )
    if not isinstance(value, (str, int)):
        raise ValueError(
            f"{label} must be a column name string or zero-based column index, "
            f"but got {type(value).__name__}: {value}"
        )


def apply_cli_overrides(config, args):
    """Let CLI arguments override paths without changing the client config file."""
    raw_ledger = getattr(args, "raw_ledger", None)
    if raw_ledger:
        config["paths"]["raw_ledger"] = raw_ledger

    vendor_master = getattr(args, "vendor_master", None)
    if vendor_master:
        config["paths"]["vendor_master"] = vendor_master

    payroll_master = getattr(args, "payroll_master", None)
    if payroll_master:
        config.setdefault("payroll_master", {})["enabled"] = True
        config["payroll_master"]["path"] = payroll_master

    apply_project_context(config, getattr(args, "project_name", None))

    output_overrides = {
        "formatted_ledger": getattr(args, "formatted_output", None),
        "vendor_match_review": getattr(args, "vendor_review_output", None),
        "final_enriched_workbook": getattr(args, "final_output", None),
    }
    for key, value in output_overrides.items():
        if value:
            config["paths"][key] = value
    employee_review_output = getattr(args, "employee_review_output", None)
    if employee_review_output:
        config.setdefault("employee_review", {})["output_path"] = employee_review_output
    return config


def apply_project_context(config, project_name=None):
    """Apply project/run-name templates for company-level configs."""
    if not project_name:
        project_name = config.get("project_name") or infer_project_name(
            config.get("paths", {}).get("raw_ledger", "")
        )

    if not project_name or not config_uses_project_templates(config):
        return config

    safe_project_name = sanitize_project_name(project_name)
    config["project_name"] = safe_project_name
    template_values = {
        "project_name": safe_project_name,
        "project": safe_project_name,
        "run_name": safe_project_name,
    }

    for key, value in list(config.get("paths", {}).items()):
        if isinstance(value, str) and "{" in value:
            config["paths"][key] = value.format(**template_values)

    employee_review = config.setdefault("employee_review", {})
    output_path = employee_review.get("output_path")
    if isinstance(output_path, str) and "{" in output_path:
        employee_review["output_path"] = output_path.format(**template_values)

    return config


def config_uses_project_templates(config):
    values = list(config.get("paths", {}).values())
    values.append(config.get("employee_review", {}).get("output_path", ""))
    return any(isinstance(value, str) and "{project_name}" in value for value in values)


def infer_project_name(path_value):
    if not path_value:
        return ""
    stem = Path(str(path_value)).stem.strip()
    if not stem:
        return ""
    cleaned = re.sub(
        r"\s*-\s*(GL|GENERAL LEDGER|RAW LEDGER|RAW WORKBOOK).*$",
        "",
        stem,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"[_\s-]+(GL|GENERAL_LEDGER|RAW_LEDGER|RAW_WORKBOOK)$",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned or stem


def sanitize_project_name(project_name):
    cleaned = str(project_name or "").strip()
    cleaned = re.sub(r'[<>:"/\\|?*]+', " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "VICE"


def _load_yaml(text):
    """Load YAML with PyYAML when available, otherwise use a small safe subset.

    The fallback supports the simple config style used by archived legacy configs:
    nested dictionaries, quoted strings, numbers, booleans, nulls, and inline
    lists like ["A", "B"]. It is intentionally small so beginners can read it.
    """
    try:
        import yaml

        return yaml.safe_load(text)
    except ImportError:
        return _parse_simple_yaml(text)


def _parse_simple_yaml(text):
    root = {}
    stack = [
        {
            "indent": -1,
            "container": root,
            "parent": None,
            "key": None,
        }
    ]

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue

        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()

        while stack and indent <= stack[-1]["indent"]:
            stack.pop()

        current = stack[-1]
        parent = current["container"]

        if line.startswith("- "):
            if not isinstance(parent, list):
                if current["parent"] is None:
                    raise ValueError(f"Unsupported YAML list line: {raw_line}")
                parent = []
                current["parent"][current["key"]] = parent
                current["container"] = parent
            parent.append(_parse_scalar(line[2:].strip()))
            continue

        if ":" not in line:
            raise ValueError(f"Unsupported YAML line: {raw_line}")

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()

        if not isinstance(parent, dict):
            raise ValueError(f"Unsupported YAML mapping line inside list: {raw_line}")

        if value == "":
            new_dict = {}
            parent[key] = new_dict
            stack.append(
                {
                    "indent": indent,
                    "container": new_dict,
                    "parent": parent,
                    "key": key,
                }
            )
        else:
            parent[key] = _parse_scalar(value)

    return root


def _parse_scalar(value):
    if value in ["null", "None", "~"]:
        return None
    if value in ["true", "True"]:
        return True
    if value in ["false", "False"]:
        return False

    if value.startswith("[") and value.endswith("]"):
        inside = value[1:-1].strip()
        if not inside:
            return []
        return [_parse_scalar(part.strip()) for part in inside.split(",")]

    if (
        (value.startswith('"') and value.endswith('"'))
        or (value.startswith("'") and value.endswith("'"))
    ):
        return value[1:-1]

    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value
