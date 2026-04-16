
from src.utils.logging import sanitize_log_message

def test_otp_false_positive():
    msg = "hotpot=delicious"
    sanitized = sanitize_log_message(msg)
    print(f"'{msg}' -> '{sanitized}'")
    if "***" in sanitized:
        print("FAIL: False positive on 'hotpot'")
    else:
        print("PASS: No false positive on 'hotpot'")

if __name__ == "__main__":
    test_otp_false_positive()
