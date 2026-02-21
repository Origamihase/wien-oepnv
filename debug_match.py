from pathlib import Path

base_dir = Path("/app")
path = Path("/app/src/utils/http.py")
relative = path.relative_to(base_dir)

patterns = [
    "src/utils/*.py",
    "src/utils/*",
    "tests/**/*",
    "tests/**"
]

for p in patterns:
    print(f"'{str(relative)}' matches '{p}'? {relative.match(p)}")

path2 = Path("/app/tests/places/test_foo.py")
relative2 = path2.relative_to(base_dir)
for p in patterns:
    print(f"'{str(relative2)}' matches '{p}'? {relative2.match(p)}")
