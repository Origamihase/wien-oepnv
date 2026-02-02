import sys
import os
import logging

# Ensure src is in path
sys.path.insert(0, os.path.join(os.getcwd(), 'src'))

from feed.logging_safe import SafeFormatter

def test_formatter_does_not_mutate_record():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="test.py",
        lineno=1,
        msg="Hello %s",
        args=("World",),
        exc_info=None
    )

    # Verify initial state
    assert record.msg == "Hello %s"
    assert record.args == ("World",)

    formatter = SafeFormatter()

    formatted = formatter.format(record)

    print(f"Formatted: {formatted}")

    # Check if record was mutated
    if record.msg == "Hello %s" and record.args == ("World",):
        print("SUCCESS: Record was not mutated")
        return True
    else:
        print(f"FAIL: Record WAS mutated! msg={record.msg}, args={record.args}")
        return False

if __name__ == "__main__":
    if test_formatter_does_not_mutate_record():
        sys.exit(0)
    else:
        sys.exit(1)
