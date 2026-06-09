import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vendor_matcher import extract_employee_from_payroll_description


EXAMPLES = {
    "PE 08/10/24   DAVIS,B E:REG EARN": "DAVIS,B",
    "PE 2025-02-08 SHARP, E ADDITIONAL FEE": "SHARP, E",
    "7/1-31 Ins Earnings M.Woodley": "M.Woodley",
    "12/31 Fringes B.Davis Adjustment": "B.Davis",
    "3/1-31 WC-ON Fringes JJ Tartaglia": "JJ Tartaglia",
    "6/1-30 Ins Earnings R,Graham": "R,Graham",
}


def main():
    for description, expected_token in EXAMPLES.items():
        result = extract_employee_from_payroll_description(description)
        assert result["employee_token"] == expected_token, (
            description,
            result,
            expected_token,
        )
        print(description)
        print(result)


if __name__ == "__main__":
    main()
