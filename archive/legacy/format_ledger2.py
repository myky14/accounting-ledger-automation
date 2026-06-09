import pandas as pd
import re

# ============================================
# COLUMN MAPPING (Dựa trên cấu trúc file của bạn)
# ============================================
TRANS_DATE_COL = 0  # Cột A
VENDOR_ID_COL = 1   # Cột B
VENDOR_NAME_COL = 2 # Cột C
SRC_COL = 3
TRANS_REF_COL = 4
DESCRIPTION_COL = 5
OUR_REF_COL = 6
CURRENCY_COL = 7
CAD_COL = 8
AMOUNT_COL = 9
EP_COL = 14         # Cột O trong file raw

# ============================================
# UTILITY FUNCTIONS
# ============================================

def safe_value(val):
    """Xử lý giá trị NaN tránh lỗi khi xuất file."""
    if pd.isna(val):
        return ""
    return val

def is_transaction_row(value):
    """
    KIỂM TRA DATE CHẶT CHẼ:
    - Không được trống.
    - Không được là số (tránh các dòng Total/Subtotal).
    - Phải parse được thành ngày tháng hợp lệ.
    """
    if pd.isna(value) or value == "":
        return False
    
    # Nếu Excel coi ô đó là số (giá trị ngày kiểu số), ta kiểm tra xem nó có hợp lý không
    # Tuy nhiên, trong Ledger này, các dòng Total thường là số lớn, ta nên loại bỏ
    if isinstance(value, (int, float)):
        return False
        
    val_str = str(value).strip().lower()
    
    # Loại bỏ các từ khóa gây nhiễu
    if "total" in val_str or "expense" in val_str:
        return False

    try:
        # Kiểm tra xem có định dạng năm-tháng-ngày (YYYY-MM-DD) không
        parsed = pd.to_datetime(value)
        return not pd.isna(parsed)
    except:
        return False

# ============================================
# MAIN PROCESS FUNCTION
# ============================================

def process_ledger(input_file, output_file):
    print("Đang khởi động máy xay dữ liệu...")
    
    # Đọc file
    if input_file.endswith(".csv"):
        df = pd.read_csv(input_file)
    else:
        df = pd.read_excel(input_file)

    final_data = []
    
    # Biến lưu trữ ngữ cảnh (Context)
    current_account = ""
    current_account_name = ""
    current_add_desc = ""

    for index, row in df.iterrows():
        # Bỏ qua hàng trống hoàn toàn
        if row.isna().all():
            continue

        # Đọc giá trị tại các cột chính
        raw_date_val = row.iloc[TRANS_DATE_COL]
        col_a_str = str(safe_value(raw_date_val)).strip()
        col_b_str = str(safe_value(row.iloc[1])).strip()
        col_c_str = str(safe_value(row.iloc[2])).strip()

        # ---------------------------------------------------------
        # LOGIC 1: CẬP NHẬT THÔNG TIN ACCOUNT (Dòng có chữ Expense)
        # ---------------------------------------------------------
        if col_a_str.lower() == "expense":
            current_account = col_b_str
            current_account_name = col_c_str
            current_add_desc = "" # Reset mô tả phụ khi sang Account mới
            continue # KHÔNG lưu dòng này vào bảng transaction

        # ---------------------------------------------------------
        # LOGIC 2: CẬP NHẬT ADDITIONAL DESCRIPTION 
        # (Dòng cột A rỗng, cột B có chữ, không phải ngày, không phải total)
        # ---------------------------------------------------------
        if col_a_str == "" and col_b_str != "" and "total" not in col_b_str.lower():
            current_add_desc = col_b_str
            continue # KHÔNG lưu dòng này vào bảng transaction

        # ---------------------------------------------------------
        # LOGIC 3: LỌC VÀ LƯU TRANSACTION (Chỉ khi cột A là NGÀY THÁNG)
        # ---------------------------------------------------------
        if is_transaction_row(raw_date_val):
            
            # Xử lý format cho cột Our Reference (loại bỏ .0 nếu có)
            our_ref = safe_value(row.iloc[OUR_REF_COL])
            if isinstance(our_ref, float) and our_ref.is_integer():
                our_ref = str(int(our_ref))

            # Lưu vào danh sách kết quả
            final_data.append({
                "Account": current_account,
                "Account Name": current_account_name,
                "Trans Date": raw_date_val,
                "Vendor ID": safe_value(row.iloc[VENDOR_ID_COL]),
                "Vendor Name": safe_value(row.iloc[VENDOR_NAME_COL]),
                "Src": safe_value(row.iloc[SRC_COL]),
                "Trans Ref": safe_value(row.iloc[TRANS_REF_COL]),
                "Description": safe_value(row.iloc[DESCRIPTION_COL]),
                "Additional Description": current_add_desc,
                "Our Reference": our_ref,
                "Currency": safe_value(row.iloc[CURRENCY_COL]),
                "CAD": safe_value(row.iloc[CAD_COL]),
                "Amount": safe_value(row.iloc[AMOUNT_COL]),
                "Ep": safe_value(row.iloc[EP_COL])
            })
            
        # Tất cả các dòng khác (Total, dòng rác, dòng chỉ có số ở cột A...) 
        # sẽ tự động bị bỏ qua do không thỏa mãn các IF ở trên.

    # Tạo DataFrame và xuất file
    result_df = pd.DataFrame(final_data)
    
    # Định dạng lại cột ngày tháng cho đồng nhất khi xuất ra Excel
    try:
        result_df["Trans Date"] = pd.to_datetime(result_df["Trans Date"]).dt.strftime("%Y-%m-%d")
    except:
        pass

    result_df.to_excel(output_file, index=False)
    
    print("-" * 35)
    print(f"HOÀN THÀNH!")
    print(f"Số giao dịch tìm thấy: {len(result_df)}")
    print(f"File kết quả: {output_file}")
    print("-" * 35)

# ============================================
# CHẠY TOOL
# ============================================
# Thay tên file raw của bạn vào đây
if __name__ == "__main__":
    # Demo only: khong hard-code duong dan client that trong code public.
    # Workflow moi nen chay bang main.py voi configs/sample.yaml.
    process_ledger(
        "sample_data/sample_raw_ledger.xlsx",
        "output/sample_legacy_formatted_2.xlsx",
    )
