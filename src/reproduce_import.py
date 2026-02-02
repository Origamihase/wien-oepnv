import sys
import os

# Emulate running from root
sys.path.insert(0, os.path.join(os.getcwd(), 'src'))

try:
    from feed import logging_safe
    print(f"Imported logging_safe: {logging_safe}")

    # Check if sanitize_log_message is the real one
    # The real one has a docstring about secrets. The dummy one usually doesn't or is simple.
    # Or check if it accepts secrets
    try:
        logging_safe.sanitize_log_message("secret", secrets=["secret"])
        import inspect
        src = inspect.getsource(logging_safe.sanitize_log_message)
        print("Source of sanitize_log_message:")
        print(src)
        if "replace" in src and "secrets" not in src: # Heuristic for dummy
             print("FAIL: Using dummy implementation")
        elif "re.sub" in src or "_keys" in src:
             print("SUCCESS: Using real implementation")
        else:
             print("UNKNOWN implementation")

    except Exception as e:
        print(f"Error calling function: {e}")

except ImportError as e:
    print(f"ImportError: {e}")
