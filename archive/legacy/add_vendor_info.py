# ============================================
# VENDOR MATCHING + DATA ENRICHMENT TOOL
# ============================================
# Mục tiêu:
# - Đọc formatted ledger
# - Đọc vendor information master file
# - Normalize vendor names
# - Fuzzy match vendor names
# - Fill tax/address information
# - Export final enriched workbook
# ============================================

import pandas as pd
import re
from rapidfuzz import fuzz
from rapidfuzz import process


# ============================================
# NORMALIZE NAME FUNCTION
# ============================================
# Convert messy names thành format chuẩn
# ============================================

def normalize_name(name):

    # Nếu blank
    if pd.isna(name):
        return ""
    # Convert thành string + uppercase
    name = str(name).upper()
    # Remove special characters
    name = re.sub(r'[^A-Z0-9 ]', '', name)
    # Remove spaces
    name = name.replace(" ", "")
    return name

# ============================================
# SAFE VALUE FUNCTION
# ============================================
def safe_value(val):
    # Nếu NaN
    if pd.isna(val):
        return ""
    # Convert EVERYTHING thành string
    return str(val)

# ============================================
# MAIN FUNCTION
# ============================================
def enrich_vendor_information(
    formatted_ledger_file,
    vendor_info_file,
    output_file
):

    # ========================================
    # READ FILES
    # ========================================
    ledger_df = pd.read_excel(
        formatted_ledger_file
    )
    vendor_df = pd.read_excel(
        vendor_info_file
    )

    # ========================================
    # CREATE NORMALIZED NAMES
    # ========================================
    ledger_df["normalized_vendor"] = ledger_df[
        "Vendor Name"
    ].apply(normalize_name)

    vendor_df["normalized_vendor"] = vendor_df[
        "Vendor Name"
    ].apply(normalize_name)

    # ========================================
    # CREATE MATCHING LIST
    # ========================================
    vendor_name_list = vendor_df[
        "normalized_vendor"
    ].tolist()

    # ========================================
    # CREATE NEW OUTPUT COLUMNS
    # =====================================
    ledger_df["Matched Vendor"] = ""
    ledger_df["Match Score"] = 0.0
    ledger_df["Loan Out Corp"] = ""
    ledger_df["Employee"] = ""
    ledger_df["Tax ID"] = ""
    ledger_df["Address"] = ""
    ledger_df["City"] = ""
    ledger_df["Province"] = ""
    ledger_df["Country"] = ""
    ledger_df["Zip Code"] = ""
    ledger_df["Match Status"] = ""


    # ========================================
    # LOOP THROUGH LEDGER ROWS
    # ========================================
    for index, row in ledger_df.iterrows():
        # ====================================
        # GET LEDGER VENDOR NAME
        # ====================================
        ledger_vendor = row[
            "normalized_vendor"
        ]
        # Skip blank
        if ledger_vendor == "":
            continue
        # ====================================
        # FUZZY MATCH
        # ====================================
        match_result = process.extractOne(
            ledger_vendor,
            vendor_name_list,
            scorer=fuzz.ratio
        )

        # Nếu không match được
        if match_result is None:
            continue
        # ====================================
        # EXTRACT MATCH RESULT
        # ====================================
        matched_name = match_result[0]
        match_score = match_result[1]

        # ====================================
        # SAVE MATCH INFO
        # ====================================
        ledger_df.at[
            index,
            "Match Score"
        ] = match_score

        # ====================================
        # FIND MATCHED ROW
        # ====================================
        matched_vendor_row = vendor_df[
            vendor_df["normalized_vendor"]
            == matched_name
        ]

        # Nếu không tìm thấy row
        if matched_vendor_row.empty:
            continue
        # Lấy row đầu tiên
        matched_vendor_row = matched_vendor_row.iloc[0]
        # ====================================
        # SAVE MATCHED VENDOR NAME
        # ====================================
        ledger_df.at[
            index,
            "Matched Vendor"
        ] = safe_value(
            matched_vendor_row["Vendor Name"]
        )

        # ====================================
        # MATCH STATUS LOGIC
        # ====================================

        if match_score >= 95:

            match_status = "AUTO MATCH"

        elif match_score >= 85:

            match_status = "LOW CONFIDENCE"

        else:

            match_status = "LOW CONFIDENCE"

        ledger_df.at[
            index,
            "Match Status"
        ] = match_status

        # ====================================
        # FILL INFORMATION
        # ============================================

        ledger_df.at[
            index,
            "Loan Out Corp"
        ] = safe_value(
            matched_vendor_row.get(
                "Loan Out Corp",
                ""
            )
        )

        ledger_df.at[
            index,
            "Employee"
        ] = safe_value(
            matched_vendor_row.get(
                "Employee",
                ""
            )
        )

        ledger_df.at[
            index,
            "Tax ID"
        ] = safe_value(
            matched_vendor_row.get(
                "Tax ID",
                ""
            )
        )

        ledger_df.at[
            index,
            "Address"
        ] = safe_value(
            matched_vendor_row.get(
                "Address",
                ""
            )
        )

        ledger_df.at[
            index,
            "City"
        ] = safe_value(
            matched_vendor_row.get(
                "City",
                ""
            )
        )


        ledger_df.at[
            index,
            "Province"
        ] = safe_value(
            matched_vendor_row.get(
                "Province",
                ""
            )
        )


        ledger_df.at[
            index,
            "Country"
        ] = safe_value(
            matched_vendor_row.get(
                "Country",
                ""
            )
        )


        ledger_df.at[
            index,
            "Zip Code"
        ] = safe_value(
            matched_vendor_row.get(
                "Zip Code",
                ""
            )
        )


    # ========================================
    # REMOVE HELPER COLUMN
    # ========================================

    ledger_df = ledger_df.drop(
        columns=["normalized_vendor"]
    )


    # ========================================
    # EXPORT FINAL FILE
    # ========================================

    ledger_df.to_excel(

        output_file,
        index=False

    )


    # ========================================
    # SUMMARY
    # ========================================

    print("===================================")
    print("DONE BRO 😭")
    print("===================================")
    print(f"Rows Processed: {len(ledger_df)}")
    print(f"Output File: {output_file}")
    print("===================================")


# ============================================
# RUN TOOL
# ============================================

if __name__ == "__main__":
    # Demo only: khong hard-code duong dan client that trong code public.
    # Workflow an toan hon nam trong main.py + vendor review approval.
    enrich_vendor_information(
        "output/sample_legacy_formatted_2.xlsx",
        "sample_data/sample_vendor_master.xlsx",
        "output/sample_legacy_enriched.xlsx",
    )
