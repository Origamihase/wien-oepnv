import sys
import os
import traceback

# Ensure src is in path to simulate root execution
sys.path.insert(0, os.path.join(os.getcwd(), 'src'))

try:
    from feed import logging_safe
    print(f"Imported logging_safe: {logging_safe}")

    # Check if sanitize_log_message is the real one
    import inspect
    src = inspect.getsource(logging_safe.sanitize_log_message)

    if "re.sub" in src or "_keys" in src:
         print("SUCCESS: Using real implementation")
         sys.exit(0)
    else:
         print("FAIL: Using dummy implementation")
         sys.exit(1)

except Exception as e:
    print(f"FAIL: Exception: {e}")
    traceback.print_exc()
    sys.exit(1)
