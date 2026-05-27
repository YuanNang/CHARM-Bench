from __future__ import annotations

from collections import Counter

from .pinyin import normalize_pinyin_syllable, pinyin_for_text
from .types import Feedback, Problem, TokenFeedback


def positional_feedback(guess_items: list[str], gold_items: list[str]) -> list[TokenFeedback]:
    result: list[TokenFeedback | None] = [None] * len(guess_items)
    remaining: Counter[str] = Counter()

    for index, guess_item in enumerate(guess_items):
        if index < len(gold_items) and guess_item == gold_items[index]:
            result[index] = TokenFeedback(guess_item, "green")
        elif index < len(gold_items):
            remaining[gold_items[index]] += 1

    for index, guess_item in enumerate(guess_items):
        if result[index] is not None:
            continue
        if remaining[guess_item] > 0:
            result[index] = TokenFeedback(guess_item, "yellow")
            remaining[guess_item] -= 1
        else:
            result[index] = TokenFeedback(guess_item, "gray")

    return [item for item in result if item is not None]


class CharmEnvironment:
    def __init__(self, problem: Problem):
        self.problem = problem

    def submit(self, answer: str) -> Feedback:
        guess = answer.strip()
        if len(guess) != self.problem.answer_length:
            return Feedback(correct=False, guess=guess, message="length_mismatch")
        correct = guess == self.problem.answer
        if correct:
            return Feedback(correct=True, guess=guess, message="correct")

        guess_chars = list(guess)
        gold_chars = list(self.problem.answer)
        if any(char.isspace() for char in guess):
            guess_pinyin = [normalize_pinyin_syllable(item) for item in guess.split()]
        else:
            guess_pinyin = pinyin_for_text(guess)

        return Feedback(
            correct=False,
            guess=guess,
            character_feedback=positional_feedback(guess_chars, gold_chars),
            pinyin_feedback=positional_feedback(guess_pinyin, self.problem.pinyin_syllables),
            message="incorrect",
        )
