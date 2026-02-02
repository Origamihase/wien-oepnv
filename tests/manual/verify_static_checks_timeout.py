import sys
import os
import subprocess
from unittest.mock import MagicMock, patch

# Ensure script is importable
sys.path.insert(0, os.path.join(os.getcwd(), 'scripts'))

import run_static_checks

def test_pip_audit_timeout():
    print("Testing pip-audit timeout configuration...")
    with patch('subprocess.run') as mock_run:
        mock_run.return_value.returncode = 0

        # Test 1: _run passes timeout
        run_static_checks._run(["test_cmd"], timeout=999)

        # Check if subprocess.run was called with timeout=999
        # call_args returns (args, kwargs)
        # args[0] is command list
        # kwargs has timeout
        call_args = mock_run.call_args
        if call_args:
            _, kwargs = call_args
            if kwargs.get('timeout') == 999:
                 print("SUCCESS: _run passed timeout=999 to subprocess.run")
            else:
                 print(f"FAIL: _run passed timeout={kwargs.get('timeout')}")
                 sys.exit(1)

        # Test 2: main calls pip-audit with 1200
        mock_run.reset_mock()
        mock_run.return_value.returncode = 0

        # Mock sys.argv
        with patch.object(sys, 'argv', ["run_static_checks.py"]):
             run_static_checks.main()

             # Find pip-audit call
             pip_audit_found = False
             for call in mock_run.call_args_list:
                 args, kwargs = call
                 cmd = args[0]
                 if cmd == ["pip-audit"]:
                     pip_audit_found = True
                     if kwargs.get('timeout') == 1200:
                         print("SUCCESS: pip-audit called with timeout=1200")
                     else:
                         print(f"FAIL: pip-audit called with timeout={kwargs.get('timeout')}")
                         sys.exit(1)

             if not pip_audit_found:
                 print("FAIL: pip-audit was not called")
                 sys.exit(1)

if __name__ == "__main__":
    try:
        test_pip_audit_timeout()
    except Exception as e:
        print(f"FAIL: Exception: {e}")
        sys.exit(1)
