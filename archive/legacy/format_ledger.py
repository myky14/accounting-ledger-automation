# ============================================
# LEDGER FLATTENING TOOL
# ============================================
# Mục tiêu:
# - Đọc raw ledger dạng hierarchical
# - Flatten thành transactional table
# - Remove total rows
# - Export cleaned workbook
# ============================================

import pandas as pd
import re


# ============================================
# COLUMN MAPPING
# ============================================

TRANS_DATE_COL = 0
VENDOR_ID_COL = 1
VENDOR_NAME_COL = 2
SRC_COL = 3
TRANS_REF_COL = 4
DESCRIPTION_COL = 5
OUR_REF_COL = 6
CURRENCY_COL = 7
CAD_COL = 8
AMOUNT_COL = 9
EP_COL = 13


# ============================================
# SAFE VALUE FUNCTION
# ============================================
# Convert NaN thành ""
# tránh bị chữ nan trong output

def safe_value(val):

    if pd.isna(val):
        return ""

    return val


# ============================================
# CHECK ACCOUNT HEADER
# ============================================
# Ví dụ:
# Expense | T02-0100 | WRITER(S)

def is_account_header(col_a, col_b):

    # Regex detect account code
    account_pattern = r'^[A-Z]\d{2}-\d{4}$'

    return (
        col_a.lower() == "expense"
        and bool(re.match(account_pattern, col_b))
    )


# ============================================
# CHECK TRANSACTION ROW
# ============================================
# Detect transaction bằng date ở cột A

def is_transaction_row(value):

    try:

        parsed = pd.to_datetime(value)

        return not pd.isna(parsed)

    except:

        return False


# ============================================
# CHECK ADDITIONAL DESCRIPTION
# ============================================

def is_additional_description(col_a, col_b, current_account):

    return (
        col_a == ""
        and col_b != ""
        and current_account != ""
        and "total" not in col_b.lower()
    )


# ============================================
# MAIN PROCESS FUNCTION
# ============================================

def process_ledger(input_file, output_file):


    # ========================================
    # READ FILE
    # ========================================

    if input_file.endswith(".csv"):

        df = pd.read_csv(input_file)

    else:

        df = pd.read_excel(input_file)


    # ========================================
    # FINAL OUTPUT STORAGE
    # ========================================

    final_data = []


    # ========================================
    # CONTEXT VARIABLES
    # ========================================

    current_account = ""
    current_account_name = ""
    current_add_desc = ""


    # ========================================
    # LOOP ROWS
    # ========================================

    for index, row in df.iterrows():


        # ====================================
        # SKIP FULL BLANK ROWS
        # ====================================

        if row.isna().all():
            continue


        # ====================================
        # READ CORE COLUMNS
        # ====================================

        col_a = str(safe_value(row.iloc[0])).strip()
        col_b = str(safe_value(row.iloc[1])).strip()
        col_c = str(safe_value(row.iloc[2])).strip()


        # ====================================
        # RULE 1 — ACCOUNT HEADER
        # ====================================

        if is_account_header(col_a, col_b):

            current_account = col_b
            current_account_name = col_c

            # reset description khi qua account mới
            current_add_desc = ""

            continue


        # ====================================
        # RULE 2 — TRANSACTION ROW
        # ====================================

        if is_transaction_row(row.iloc[TRANS_DATE_COL]):


            # =================================
            # CLEAN REFERENCE NUMBER
            # =================================

            our_ref = safe_value(row.iloc[OUR_REF_COL])

            # Remove .0 nếu là float
            if isinstance(our_ref, float):

                if our_ref.is_integer():
                    our_ref = str(int(our_ref))


            # =================================
            # APPEND CLEAN ROW
            # =================================

            final_data.append({

                "Account": current_account,

                "Account Name": current_account_name,

                "Trans Date":
                    safe_value(row.iloc[TRANS_DATE_COL]),

                "Vendor ID":
                    safe_value(row.iloc[VENDOR_ID_COL]),

                "Vendor Name":
                    safe_value(row.iloc[VENDOR_NAME_COL]),

                "Src":
                    safe_value(row.iloc[SRC_COL]),

                "Trans Ref":
                    safe_value(row.iloc[TRANS_REF_COL]),

                "Description":
                    safe_value(row.iloc[DESCRIPTION_COL]),

                "Additional Description":
                    current_add_desc,

                "Our Reference":
                    our_ref,

                "Currency":
                    safe_value(row.iloc[CURRENCY_COL]),

                "CAD":
                    safe_value(row.iloc[CAD_COL]),

                "Amount":
                    safe_value(row.iloc[AMOUNT_COL]),

                "Ep":
                    safe_value(row.iloc[EP_COL])

            })

            continue


        # ====================================
        # RULE 3 — ADDITIONAL DESCRIPTION
        # ====================================

        if is_additional_description(
            col_a,
            col_b,
            current_account
        ):

            current_add_desc = col_b


    # ========================================
    # CREATE OUTPUT DATAFRAME
    # ========================================

    result_df = pd.DataFrame(final_data)


    # ========================================
    # FORMAT DATE COLUMN
    # ========================================

    try:

        result_df["Trans Date"] = pd.to_datetime(
            result_df["Trans Date"]
        ).dt.strftime("%Y-%m-%d")

    except:

        pass


    # ========================================
    # EXPORT OUTPUT
    # ========================================

    result_df.to_excel(
        output_file,
        index=False
    )


    # ========================================
    # SUMMARY
    # ========================================

    print("===================================")
    print("DONE!!")
    print("===================================")
    print(f"Rows Processed: {len(result_df)}")
    print(f"Output File: {output_file}")
    print("===================================")


# ============================================
# RUN TOOL
# ============================================

if __name__ == "__main__":
    # Demo only: khong hard-code duong dan client that trong code public.
    # Workflow moi nen chay bang main.py voi configs/sample.yaml.
    process_ledger(
        "sample_data/sample_raw_ledger.xlsx",
        "output/sample_legacy_formatted.xlsx",
    )
