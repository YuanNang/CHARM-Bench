from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .types import Problem


def read_manifest(path: Path) -> Iterable[Problem]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield Problem.from_json(json.loads(line))
