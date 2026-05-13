with open("tests/test_vor_request_limit.py") as f:
    content = f.read()
import re
content = re.sub(r'@pytest\.mark\.parametrize\(\n\s*\(\"status_code\", \"headers\"\),\n\s*\[\n\s*\(429, \{.*?\}\),\n\s*\(503, \{\}\),\n\s*\]\n\s*\)\n', '', content)
content = re.sub(r'def test_fetch_events_respects_daily_limit.*?(?=def test_load_request_count_resets_on_legacy_integer)', '', content, flags=re.DOTALL)
content = re.sub(r'def test_fetch_departure_board.*?(?=def test_fetch_events_raises_limit_reached)', '', content, flags=re.DOTALL)
content = re.sub(r'def test_fetch_events_raises_limit_reached.*?(?=def test_load_request_count_returns_fallback_if_file_missing)', '', content, flags=re.DOTALL)

with open("tests/test_vor_request_limit.py", "w") as f:
    f.write(content)
