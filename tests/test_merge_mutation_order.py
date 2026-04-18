import sys
import threading
from src.feed.merge import deduplicate_fuzzy

def test_mutation_order_in_deduplicate_fuzzy():
    """
    Asserts the correct order of operations in deduplicate_fuzzy
    by tracing the execution to ensure `_identity` is set and
    `_calculated_identity` is absent on the object AT THE EXACT MOMENT
    it is placed into the result list.
    """
    items_case1 = [
        {
            "title": "S1/S2: Weichenstörung",
            "description": "Short VOR text.",
            "guid": "vor_guid_1",
            "provider": "vor",
            "source": "vor",
            "_calculated_identity": "calc_vor"
        },
        {
            "title": "S1/S2: Weichenstörung",
            "description": "Details from ÖBB.",
            "guid": "oebb_guid_1",
            "provider": "oebb",
            "source": "oebb",
            "_calculated_identity": "calc_oebb"
        }
    ]

    items_case2 = [
        {
            "title": "S1/S2: Weichenstörung",
            "description": "Details from ÖBB.",
            "guid": "oebb_guid_1",
            "provider": "oebb",
            "source": "oebb",
            "_calculated_identity": "calc_oebb"
        },
        {
            "title": "S1/S2: Weichenstörung",
            "description": "Short VOR text.",
            "guid": "vor_guid_1",
            "provider": "vor",
            "source": "vor",
            "_calculated_identity": "calc_vor"
        }
    ]

    # We will trace the execution and intercept the moment `merged_items[idx] = new_existing`
    # is executed, verifying the state of `new_existing` right before.

    def trace_case(items_to_test):
        violation_detected = []
        assignment_found = []

        def trace_lines(frame, event, arg):
            if event == 'line' and frame.f_code.co_name == 'deduplicate_fuzzy':
                # Check if this line is an assignment to merged_items
                source_line = frame.f_globals.get('__file__')
                if source_line:
                    import linecache
                    line = linecache.getline(source_line, frame.f_lineno).strip()

                    if line == "merged_items[idx] = new_existing":
                        assignment_found.append(True)
                        locals_dict = frame.f_locals
                        if "new_existing" in locals_dict:
                            obj = locals_dict["new_existing"]
                            # The requirement is that `_identity` is set and `_calculated_identity` absent
                            # on the object placed into the result list.
                            if "_calculated_identity" in obj:
                                violation_detected.append("_calculated_identity is still present")
                            if "_identity" not in obj:
                                violation_detected.append("_identity is missing")
                            elif not obj["_identity"]:
                                violation_detected.append("_identity is empty")
            return trace_lines

        # Because pytest captures stdout and trace functions can be tricky with fixtures,
        # we set it up carefully.
        old_trace = sys.gettrace()
        sys.settrace(trace_lines)
        threading.settrace(trace_lines)
        try:
            merged = deduplicate_fuzzy(items_to_test)
        finally:
            sys.settrace(old_trace)
            threading.settrace(old_trace)

        assert assignment_found, "Assignment line 'merged_items[idx] = new_existing' was never hit during trace"
        assert not violation_detected, f"Violations found: {violation_detected}"

    # Test Case 1
    trace_case(items_case1)

    # Test Case 2
    trace_case(items_case2)
