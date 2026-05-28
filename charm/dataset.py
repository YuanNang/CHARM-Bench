from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .types import Problem


def find_image(images_dir: Path, problem_id: int, index: int) -> Path:
    filename = f"W{problem_id}-{index}.png"
    matches = sorted(images_dir.rglob(filename))
    if not matches:
        raise FileNotFoundError(f"missing image for problem {problem_id}: {filename}")
    if len(matches) > 1:
        raise ValueError(f"multiple images found for problem {problem_id}: {filename}")
    return matches[0]


def read_manifest(path: Path) -> Iterable[Problem]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield Problem.from_json(json.loads(line))
