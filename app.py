from datetime import datetime
from pathlib import Path

import streamlit as st

from config_loader import (
    apply_project_context,
    config_uses_project_templates,
    infer_project_name,
    load_config,
)
from employee_review import generate_employee_extraction_review
from formatted_ledger import load_preflattened_ledger
from ledger_flattener import flatten_ledger
from main import read_employee_context_candidates, write_reviewed_final_workbook
from utils import validate_workbook, write_workbook_safely
from vendor_matcher import enrich_using_approved_mapping
from vendor_review import generate_vendor_match_review


st.set_page_config(
    page_title="Accounting Ledger Automation Pipeline",
    layout="wide",
)


def main():
    _render_page_styles()
    _render_header()
    _render_about_project()

    config_files = _active_config_files()
    if not config_files:
        st.error("Không tìm thấy config file trong thư mục configs/.")
        return

    selected_config = st.selectbox(
        "Company config",
        config_files,
        index=_default_config_index(config_files),
        format_func=_config_label,
    )
    st.info(
        "For the public demo, use sample_demo.yaml or upload your own files. "
        "Private raw/vendor/payroll files are not included in this repository."
    )
    config_preview = load_config(selected_config)
    if str(config_preview.get("company") or config_preview.get("client", "")).upper() == "VICE":
        st.info(
            "VICE uses one reusable company config for multiple project ledgers. "
            "Vendor Master and Payroll Master are company-level master data."
        )

    input_mode = st.radio(
        "Input mode",
        ["Raw Ledger Mode", "Pre-Flattened Ledger Mode"],
        horizontal=True,
    )

    raw_ledger_upload = None
    flattened_ledger_upload = None
    raw_ledger_selected = None
    flattened_ledger_selected = None
    if input_mode == "Raw Ledger Mode":
        raw_ledger_selected = _select_existing_workbook(
            "Select raw ledger workbook",
            config_preview["paths"].get("raw_ledger"),
            "raw_ledger_selected",
        )
        raw_ledger_upload = st.file_uploader(
            "Upload raw ledger workbook", type=["xlsx", "xls", "csv"]
        )
    else:
        flattened_ledger_selected = _select_existing_workbook(
            "Select pre-flattened ledger workbook",
            config_preview["paths"].get("raw_ledger"),
            "flattened_ledger_selected",
        )
        flattened_ledger_upload = st.file_uploader(
            "Upload pre-flattened ledger workbook", type=["xlsx", "xls", "csv"]
        )
    vendor_master_selected = _select_existing_workbook(
        "Select company Vendor Master",
        config_preview["paths"].get("vendor_master"),
        "vendor_master_selected",
    )
    vendor_master_upload = st.file_uploader(
        "Upload company Vendor Master", type=["xlsx", "xls", "csv"]
    )
    payroll_master_selected = _select_existing_workbook(
        "Select company Payroll Master",
        config_preview.get("payroll_master", {}).get("path"),
        "payroll_master_selected",
    )
    payroll_master_upload = st.file_uploader(
        "Upload company Payroll Master", type=["xlsx", "xls", "csv"]
    )
    project_name = st.text_input(
        "Project Name (optional)",
        placeholder="Inferred from the selected raw ledger if left blank",
    )
    approved_review_upload = st.file_uploader(
        "Upload approved Vendor Match Review file", type=["xlsx"]
    )
    approved_employee_review_upload = st.file_uploader(
        "Upload approved Employee Extraction Review file", type=["xlsx"]
    )

    _init_session()
    config = _build_runtime_config(
        selected_config,
        raw_ledger_upload,
        raw_ledger_selected,
        flattened_ledger_upload,
        flattened_ledger_selected,
        vendor_master_upload,
        vendor_master_selected,
        payroll_master_upload,
        payroll_master_selected,
        approved_review_upload,
        approved_employee_review_upload,
        project_name,
    )

    _render_workflow_guide(input_mode)

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        if input_mode == "Raw Ledger Mode":
            if st.button("1. Flatten Ledger", use_container_width=True):
                _flatten_ledger(config)
        else:
            st.button("1. Flatten Ledger", disabled=True, use_container_width=True)
            _load_preflattened_if_uploaded(config)

    with col2:
        if st.button("2. Generate Vendor Match Review", use_container_width=True):
            _generate_review(config)

    with col3:
        if st.button("3. Generate Employee Extraction Review", use_container_width=True):
            _generate_employee_review(config)

    with col4:
        if st.button("4. Enrich Using Approved Reviews", use_container_width=True):
            _enrich_with_approval(config)

    st.divider()
    _render_downloads()

    _render_review_notes()
    _render_footer()
    return
    with st.expander("Additional manual review reminders", expanded=False):
        st.markdown(
            """
- MANUAL CHECK: Mở file `Vendor Match Review.xlsx` để kiểm tra `Suggested Vendor Name` trước khi enrich.
- MANUAL CHECK: Chỉ nhập `Approved Vendor Name` khi chắc chắn vendor trong ledger và vendor master là cùng một entity.
- MANUAL CHECK: Không approve match chỉ dựa vào score nếu vendor name có vẻ giống nhưng Tax ID/address khác.
- MANUAL CHECK: Kiểm tra sheet `Reconciliation Summary` trước khi gửi final workbook.
"""
        )


def _render_page_styles():
    st.markdown(
        """
        <style>
        .app-header {
            border-bottom: 1px solid rgba(128, 128, 128, 0.28);
            padding-bottom: 1rem;
            margin-bottom: 1.25rem;
            color: inherit;
        }
        .app-kicker {
            color: var(--primary-color, inherit);
            font-size: 0.9rem;
            font-weight: 600;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }
        .app-title {
            color: inherit;
            font-size: 2rem;
            font-weight: 800;
            margin: 0.15rem 0 0.35rem 0;
            line-height: 1.15;
        }
        .app-subtitle {
            color: inherit;
            opacity: 0.78;
            max-width: 900px;
            font-size: 1rem;
            line-height: 1.5;
        }
        .workflow-band {
            background: rgba(128, 128, 128, 0.08);
            border: 1px solid rgba(128, 128, 128, 0.22);
            border-radius: 8px;
            padding: 1rem;
            margin: 1rem 0;
            color: inherit;
        }
        .footer {
            border-top: 1px solid rgba(128, 128, 128, 0.28);
            margin-top: 2rem;
            padding-top: 1rem;
            color: inherit;
            opacity: 0.78;
            font-size: 0.9rem;
            line-height: 1.5;
        }
        .footer strong {
            color: inherit;
            opacity: 1;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_header():
    st.markdown(
        """
        <div class="app-header">
            <div class="app-kicker">Internal Accounting Operations Tool</div>
            <div class="app-title">Accounting Ledger Automation Pipeline</div>
            <div class="app-subtitle">
                Config-driven workflow for ledger flattening, vendor matching,
                payroll employee review, loan-out mapping, reconciliation, and
                audit-ready Excel outputs.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_about_project():
    with st.expander("About This Project", expanded=False):
        st.subheader("Accounting Ledger Automation Pipeline")
        st.markdown(
            """
Accounting ledgers from clients often arrive in inconsistent hierarchical formats.
Vendor names, payroll processor descriptions, employee names, and tax/address
details are rarely standardized. This creates repetitive Excel cleanup work and
increases the risk of attaching the wrong Tax ID or address to a transaction.

This project automates the repetitive parts of the workflow while preserving
manual review for high-risk accounting decisions.
"""
        )
        overview_col, value_col = st.columns(2)
        with overview_col:
            st.markdown(
                """
**Core Features**
- Hierarchical ledger flattening
- Pre-flattened ledger validation
- Config-driven client mappings
- Vendor fuzzy matching
- Payroll employee extraction
- Loan-out corporation mapping
- Manual approval workbooks
- Reconciliation and run logs
"""
            )
        with value_col:
            st.markdown(
                """
**Business Value**
- Reduces manual Excel cleanup
- Reduces repetitive VLOOKUP work
- Improves consistency across clients
- Lowers risk of wrong Tax ID/address enrichment
- Creates audit-friendly review records
- Supports multiple client formats through YAML configs
"""
            )
        st.markdown(
            """
**Workflow**

`Raw or Pre-Flattened Ledger -> Vendor Match Review -> Employee Extraction Review -> Approved Mapping -> Final Enriched Workbook`

**Safety & Review Philosophy**
- High-risk matches are not auto-approved.
- Payroll rows require employee-level review before Tax ID/address enrichment.
- Low-confidence or ambiguous matches are separated into review sheets.
- Reconciliation summaries and run logs are generated for audit support.

**Technical Architecture**

Python, pandas, openpyxl, RapidFuzz, Streamlit, and YAML configuration files.

**Future Roadmap**

Alias memory, database-backed history, OCR invoice matching, approval dashboard,
AI-assisted row classification, and web deployment.
"""
        )


def _render_workflow_guide(input_mode):
    # Giải thích workflow để user biết file nào cần review trước khi enrich.
    if input_mode == "Raw Ledger Mode":
        steps = [
            "Upload raw ledger and vendor master",
            "Flatten Ledger",
            "Generate Vendor Match Review",
            "Generate Employee Extraction Review",
            "Upload approved reviews and enrich",
        ]
    else:
        steps = [
            "Upload pre-flattened ledger and vendor master",
            "Validate formatted ledger",
            "Generate Vendor Match Review",
            "Generate Employee Extraction Review",
            "Upload approved reviews and enrich",
        ]

    st.markdown('<div class="workflow-band">', unsafe_allow_html=True)
    st.markdown("**Workflow Steps**")
    columns = st.columns(len(steps))
    for index, step in enumerate(steps, start=1):
        with columns[index - 1]:
            st.caption(f"Step {index}")
            st.write(step)
    st.markdown("</div>", unsafe_allow_html=True)


def _render_review_notes():
    with st.expander("Review and Audit Notes", expanded=False):
        # Giải thích vì sao không auto-fill risky matches.
        st.markdown(
            """
- Review `Suggested Vendor Name` before approving vendor enrichment.
- To accept a suggested vendor match, only set `Approval Status = Approved`.
- Fill `Approved Vendor Name` only when you want to override the suggestion.
- Approve a vendor only when the ledger vendor and vendor master row are the same entity.
- Review `Suggested Payroll Employee`, `Suggested Match Type`, and loan-out/vendor suggestions before payroll enrichment.
- Payroll Master is the primary source for employee identity, SIN/GST, address, and position.
- To accept a suggested employee match, set `Approval Status = Approved`; the pipeline will use Payroll Master data and, when present, the matched loan-out corporation from Vendor Master.
- Fill `Approved Loan Out Corp` or `Approved Vendor Name` only when correcting the suggested source.
- Leave `Approval Status = Needs Review` if unsure.
- Do not approve initial-only employee matches unless the person is clearly identified.
- Payroll processor rows should not use the payroll company Tax ID/address.
- Check `Reconciliation Summary` and review sheets before sending the final workbook.
"""
        )


def _render_footer():
    st.markdown(
        """
        <div class="footer">
            <strong>Built and designed by Nguyen Du My Ky</strong><br>
            Accounting Ledger Automation Pipeline<br>
            Config-Driven ETL &bull; Vendor Matching &bull; Payroll Review &bull; Audit Workflow<br>
            GitHub: https://github.com/myky14 &nbsp;|&nbsp; LinkedIn: https://www.linkedin.com/in/myky14/
        </div>
        """,
        unsafe_allow_html=True,
    )


def _init_session():
    if "run_dir" not in st.session_state:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        st.session_state.run_dir = Path("output") / "streamlit_runs" / run_id
        st.session_state.run_dir.mkdir(parents=True, exist_ok=True)


def _active_config_files():
    config_dir = Path("configs")
    config_files = []
    for pattern in ("*.yaml", "*.json"):
        config_files.extend(
            path for path in config_dir.glob(pattern) if path.is_file()
        )
    return sorted(config_files)


def _default_config_index(config_files):
    for index, path in enumerate(config_files):
        if path.name == "sample_demo.yaml":
            return index
    return 0


def _config_label(path):
    return path.stem.upper()


def _select_existing_workbook(label, default_path, key):
    options = _workbook_options(default_path)
    if not options:
        st.selectbox(label, ["No workbook found"], disabled=True, key=key)
        return None
    return st.selectbox(
        label,
        options,
        format_func=lambda path: str(path),
        key=key,
    )


def _workbook_options(default_path=None):
    suffixes = {".xlsx", ".xls", ".csv"}
    options = []
    default = Path(default_path) if default_path else None
    if default and default.exists() and default.suffix.lower() in suffixes:
        options.append(default)
    raw_dir = Path("raw")
    if raw_dir.exists():
        options.extend(
            path
            for path in sorted(raw_dir.iterdir())
            if path.is_file() and path.suffix.lower() in suffixes
        )
    deduped = []
    seen = set()
    for path in options:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _build_runtime_config(
    selected_config,
    raw_ledger_upload,
    raw_ledger_selected,
    flattened_ledger_upload,
    flattened_ledger_selected,
    vendor_master_upload,
    vendor_master_selected,
    payroll_master_upload,
    payroll_master_selected,
    approved_review_upload,
    approved_employee_review_upload,
    project_name,
):
    config = load_config(selected_config)
    if raw_ledger_selected is not None:
        config["paths"]["raw_ledger"] = str(raw_ledger_selected)
    if flattened_ledger_selected is not None:
        st.session_state.preflattened_ledger_path = str(flattened_ledger_selected)
        config["paths"]["raw_ledger"] = str(flattened_ledger_selected)
    if vendor_master_selected is not None:
        config["paths"]["vendor_master"] = str(vendor_master_selected)
    if payroll_master_selected is not None:
        config.setdefault("payroll_master", {})["enabled"] = True
        config["payroll_master"]["path"] = str(payroll_master_selected)

    project_source = (
        getattr(raw_ledger_upload, "name", "")
        or getattr(flattened_ledger_upload, "name", "")
        or config["paths"].get("raw_ledger", "")
    )
    resolved_project_name = str(project_name or "").strip() or infer_project_name(
        project_source
    )

    if config_uses_project_templates(config):
        apply_project_context(config, resolved_project_name)
        run_dir = Path(config["paths"]["formatted_ledger"]).parent
        run_dir.mkdir(parents=True, exist_ok=True)
    else:
        run_dir = st.session_state.run_dir
        config["paths"]["formatted_ledger"] = str(run_dir / "Formatted Ledger.xlsx")
        config["paths"]["vendor_match_review"] = str(run_dir / "Vendor Match Review.xlsx")
        config.setdefault("employee_review", {})["output_path"] = str(
            run_dir / "Employee Extraction Review.xlsx"
        )
        config["paths"]["final_enriched_workbook"] = str(
            run_dir / "Final Enriched Workbook.xlsx"
        )

    if raw_ledger_upload is not None:
        config["paths"]["raw_ledger"] = str(
            _save_upload(raw_ledger_upload, run_dir / "inputs")
        )
    if flattened_ledger_upload is not None:
        # Không overwrite file flattened ledger user upload.
        st.session_state.preflattened_ledger_path = str(
            _save_upload(flattened_ledger_upload, run_dir / "inputs")
        )
        config["paths"]["raw_ledger"] = st.session_state.preflattened_ledger_path
    if vendor_master_upload is not None:
        config["paths"]["vendor_master"] = str(
            _save_upload(vendor_master_upload, run_dir / "inputs")
        )
    if payroll_master_upload is not None:
        config.setdefault("payroll_master", {})["enabled"] = True
        config["payroll_master"]["path"] = str(
            _save_upload(payroll_master_upload, run_dir / "inputs")
        )
    if approved_review_upload is not None:
        st.session_state.approved_review_path = str(
            _save_upload(approved_review_upload, run_dir / "approved")
        )
    if approved_employee_review_upload is not None:
        st.session_state.approved_employee_review_path = str(
            _save_upload(approved_employee_review_upload, run_dir / "approved")
        )

    return config


def _save_upload(uploaded_file, folder):
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / uploaded_file.name
    target.write_bytes(uploaded_file.getbuffer())
    return target


def _flatten_ledger(config):
    if not _has_required_uploads(config):
        return

    try:
        with st.spinner("Flattening ledger..."):
            formatted_df, suspicious_df, reconciliation_df, log = flatten_ledger(config)
            formatted_output = config["paths"]["formatted_ledger"]
            write_workbook_safely(formatted_output, {"Formatted Ledger": formatted_df})

        st.session_state.formatted_df = formatted_df
        st.session_state.suspicious_df = suspicious_df
        st.session_state.reconciliation_df = reconciliation_df
        st.session_state.run_log = log
        st.session_state.formatted_output = formatted_output
        st.success(f"Flattened {len(formatted_df)} transaction rows.")
    except Exception as exc:
        st.error(f"Flatten Ledger failed: {exc}")


def _load_preflattened_if_uploaded(config):
    if not config.get("input_mode", {}).get("allow_pre_flattened", True):
        st.error("Pre-Flattened Ledger Mode is disabled by this config.")
        return
    if "preflattened_ledger_path" not in st.session_state:
        st.info("Upload a pre-flattened ledger file to continue.")
        return
    if st.session_state.get("preflattened_loaded_path") == st.session_state.preflattened_ledger_path:
        return

    try:
        with st.spinner("Validating pre-flattened ledger..."):
            # Khi dùng Pre-Flattened Ledger Mode thì pipeline sẽ bỏ qua bước flatten.
            formatted_df, suspicious_df, reconciliation_df, log = load_preflattened_ledger(
                st.session_state.preflattened_ledger_path,
                config,
            )
            formatted_output = config["paths"]["formatted_ledger"]
            # Không overwrite file flattened ledger user upload.
            write_workbook_safely(formatted_output, {"Formatted Ledger": formatted_df})

        st.session_state.formatted_df = formatted_df
        st.session_state.suspicious_df = suspicious_df
        st.session_state.reconciliation_df = reconciliation_df
        st.session_state.run_log = log
        st.session_state.formatted_output = formatted_output
        st.session_state.preflattened_loaded_path = st.session_state.preflattened_ledger_path
        st.success(f"Validated pre-flattened ledger with {len(formatted_df)} rows.")
        warnings = log.to_frame()
        warning_rows = warnings[warnings["Level"] == "WARNING"]
        if not warning_rows.empty:
            st.warning("Pre-flattened ledger is usable, but recommended columns are missing.")
            st.dataframe(warning_rows, use_container_width=True)
    except Exception as exc:
        st.error(f"Pre-Flattened Ledger validation failed: {exc}")


def _generate_review(config):
    if not _has_required_uploads(config):
        return
    if "formatted_df" not in st.session_state:
        st.warning("Chạy bước 1 trước để tạo formatted ledger.")
        return

    try:
        with st.spinner("Generating Vendor Match Review..."):
            review_df, review_output = generate_vendor_match_review(
                st.session_state.formatted_df,
                config,
                config["paths"]["vendor_match_review"],
            )
        st.session_state.vendor_review_df = review_df
        st.session_state.vendor_review_output = review_output
        st.success(f"Generated {len(review_df)} vendor review rows.")
        # Match Status là ý kiến của algorithm.
        # Approval Status mới là authority cuối cùng để enrich Tax ID/address.
        # Reviewer Notes dùng để ghi lý do approve/reject cho audit trail.
        # MANUAL CHECK: Người dùng phải mở file review, kiểm tra suggestion,
        # rồi upload lại bản đã điền Approved Vendor Name/Approval Status.
        st.info(
            "To accept a suggested vendor, set Approval Status to Approved. "
            "Fill Approved Vendor Name only when overriding the suggestion."
        )
    except Exception as exc:
        st.error(f"Generate Vendor Match Review failed: {exc}")


def _generate_employee_review(config):
    if not _has_required_uploads(config):
        return
    if "formatted_df" not in st.session_state:
        st.warning("Chạy bước 1 trước để tạo formatted ledger.")
        return

    try:
        with st.spinner("Generating Employee Extraction Review..."):
            review_df, review_output, context_candidates_df = generate_employee_extraction_review(
                st.session_state.formatted_df,
                config,
                config["employee_review"]["output_path"],
            )
        st.session_state.employee_review_df = review_df
        st.session_state.employee_context_candidates_df = context_candidates_df
        st.session_state.employee_review_output = review_output
        st.success(f"Generated {len(review_df)} employee review rows.")
        # Nếu match bị ambiguous hoặc confidence thấp thì phải review manually.
        # Không được auto-fill payroll rows nếu employee chưa được approve.
        # MANUAL CHECK: Kiểm tra Suggested Employee và Suggested Loan Out Corp trước khi approve.
        # MANUAL CHECK: Không approve nếu chỉ trùng initial nhưng chưa chắc là cùng người.
        st.info(
            "To accept a suggested employee source, set Approval Status to Approved. "
            "Payroll Master supplies employee fields; Vendor Master supplies loan-out corporation fields."
        )
    except Exception as exc:
        st.error(f"Generate Employee Extraction Review failed: {exc}")


def _enrich_with_approval(config):
    if not _has_required_uploads(config):
        return
    if "formatted_df" not in st.session_state:
        st.warning("Chạy bước 1 trước để tạo formatted ledger.")
        return
    if "approved_review_path" not in st.session_state:
        st.warning("Upload approved Vendor Match Review file trước khi enrich.")
        return

    if "approved_employee_review_path" not in st.session_state:
        st.warning("Upload approved Employee Extraction Review file trước khi enrich.")
        return

    try:
        with st.spinner("Enriching using approved mapping..."):
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
                st.session_state.formatted_df,
                config,
                st.session_state.approved_review_path,
                st.session_state.run_log,
                st.session_state.approved_employee_review_path,
            )
            final_output = config["paths"]["final_enriched_workbook"]
            employee_context_candidates_df = read_employee_context_candidates(
                st.session_state.approved_employee_review_path
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
                st.session_state.reconciliation_df,
                log.to_frame(),
                employee_context_candidates_df,
            )

        st.session_state.final_output = final_output
        st.success(f"Final enriched workbook created with {len(enriched_df)} rows.")
    except Exception as exc:
        st.error(f"Enrich Using Approved Mapping failed: {exc}")


def _has_required_uploads(config):
    raw_exists = Path(config["paths"]["raw_ledger"]).exists()
    vendor_exists = Path(config["paths"]["vendor_master"]).exists()
    payroll_config = config.get("payroll_master", {})
    payroll_exists = (
        not payroll_config.get("enabled", False)
        or Path(payroll_config.get("path", "")).exists()
    )
    if not raw_exists:
        st.warning("Upload or select a raw ledger file first.")
        return False
    if not vendor_exists:
        st.warning("Upload or select a company Vendor Master first.")
        return False
    if not payroll_exists:
        st.warning("Upload or select a company Payroll Master before Employee Review/enrichment.")
        return False
    return True


def _render_downloads():
    _download_button("Download formatted ledger", "formatted_output")
    _download_button("Download vendor match review file", "vendor_review_output")
    _download_button("Download employee extraction review file", "employee_review_output")
    _download_button("Download final enriched workbook", "final_output")


def _download_button(label, state_key):
    path_value = st.session_state.get(state_key)
    if not path_value:
        st.button(label, disabled=True, use_container_width=True)
        return

    path = Path(path_value)
    if not path.exists():
        st.button(label, disabled=True, use_container_width=True)
        return

    try:
        validate_workbook(path)
    except Exception as exc:
        st.error(f"Workbook validation failed for {path.name}: {exc}")
        st.button(label, disabled=True, use_container_width=True)
        return

    st.download_button(
        label,
        data=path.read_bytes(),
        file_name=path.name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )


if __name__ == "__main__":
    main()
