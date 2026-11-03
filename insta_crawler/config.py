import os
from pathlib import Path


def load_dotenv_file(dotenv_path: Path = Path(".env")) -> None:
    if not dotenv_path.exists():
        return
    for raw_line in dotenv_path.read_text().splitlines():
        stripped_line = raw_line.strip()
        if not stripped_line or stripped_line.startswith("#") or "=" not in stripped_line:
            continue
        variable_name, variable_value = stripped_line.split("=", 1)
        os.environ.setdefault(variable_name.strip(), variable_value.strip().strip('"').strip("'"))
