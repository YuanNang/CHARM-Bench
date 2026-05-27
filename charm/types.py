from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Mark = Literal["green", "yellow", "gray"]


@dataclass(frozen=True)
class Problem:
    id: int
    source_id: int
    answer: str
    ref_word: str
    category: str
    answer_length: int
    pinyin_syllables: list[str]
    image_1: Path
    image_2: Path
    explanation: str | None = None
    author: str | None = None

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "Problem":
        data = dict(payload)
        data["image_1"] = Path(data["image_1"])
        data["image_2"] = Path(data["image_2"])
        return cls(**data)

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "answer": self.answer,
            "ref_word": self.ref_word,
            "category": self.category,
            "answer_length": self.answer_length,
            "pinyin_syllables": self.pinyin_syllables,
            "image_1": str(self.image_1),
            "image_2": str(self.image_2),
            "explanation": self.explanation,
            "author": self.author,
        }


@dataclass(frozen=True)
class TokenFeedback:
    token: str
    mark: Mark


@dataclass(frozen=True)
class Feedback:
    correct: bool
    guess: str
    character_feedback: list[TokenFeedback] = field(default_factory=list)
    pinyin_feedback: list[TokenFeedback] = field(default_factory=list)
    message: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "correct": self.correct,
            "guess": self.guess,
            "character_feedback": [item.__dict__ for item in self.character_feedback],
            "pinyin_feedback": [item.__dict__ for item in self.pinyin_feedback],
            "message": self.message,
        }


@dataclass(frozen=True)
class Generation:
    answer: str
    message: dict[str, Any]
    reasoning: Any = None


@dataclass(frozen=True)
class FewShotExample:
    ref_word: str
    category: str
    answer: str
    image_1: Path
    image_2: Path
