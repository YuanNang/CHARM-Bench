from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Iterable

from .adapters import ModelAdapter, build_initial_messages, build_tools
from .environment import CharmEnvironment
from .types import Feedback, Problem, TokenFeedback


def tool_message(tool_call_id: str, feedback: dict) -> dict:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "name": "submit_answer",
        "content": json.dumps(feedback, ensure_ascii=False),
    }


def _feedback_from_json(payload: dict) -> Feedback:
    return Feedback(
        correct=bool(payload.get("correct")),
        guess=str(payload.get("guess", "")),
        character_feedback=[
            TokenFeedback(token=item.get("token", ""), mark=item.get("mark", "gray"))
            for item in payload.get("character_feedback", [])
        ],
        pinyin_feedback=[
            TokenFeedback(token=item.get("token", ""), mark=item.get("mark", "gray"))
            for item in payload.get("pinyin_feedback", [])
        ],
        message=payload.get("message"),
    )




def _json_safe_message(message: dict) -> dict:
    return {key: value for key, value in message.items() if not key.startswith("_google_")}


def _json_safe_messages(messages: list[dict]) -> list[dict]:
    return [_json_safe_message(message) for message in messages]

def _build_record(
    problem: Problem,
    success: bool,
    attempts: int,
    max_attempts: int | None,
    duration_seconds: float,
    history: list[Feedback],
    tools: list[dict],
    messages: list[dict],
) -> dict:
    return {
        "id": problem.id,
        "success": success,
        "attempts_used": attempts,
        "max_attempts": max_attempts,
        "duration_seconds": duration_seconds,
        "final_answer": history[-1].guess if history else None,
        "gold_answer": problem.answer,
        "category": problem.category,
        "turns": [item.to_json() for item in history],
        "tools": tools,
        "messages": _json_safe_messages(messages),
    }


def _load_completed_ids(out_path: Path) -> set[int]:
    if not out_path.exists():
        return set()
    ids: set[int] = set()
    with out_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if "id" in record:
                ids.add(int(record["id"]))
    return ids


def _load_checkpoint_messages(path: Path) -> list[dict]:
    if not path.exists():
        return []
    messages: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                messages.append(json.loads(line))
    return messages


def _write_checkpoint(path: Path, message: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(message, ensure_ascii=False) + "\n")


def _history_from_messages(messages: list[dict]) -> list[Feedback]:
    history: list[Feedback] = []
    for message in messages:
        if message.get("role") != "tool":
            continue
        content = message.get("content")
        if not content:
            continue
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            continue
        history.append(_feedback_from_json(payload))
    return history


async def run_problem(
    problem: Problem,
    adapter: ModelAdapter,
    max_attempts: int | None,
    checkpoint_path: Path | None = None,
    checkpoint_messages: list[dict] | None = None,
) -> dict:
    start = time.perf_counter()
    env = CharmEnvironment(problem)
    tools = build_tools()
    elapsed_seconds = 0.0

    if checkpoint_messages:
        messages = checkpoint_messages
        history = _history_from_messages(messages)
        attempts = len(history)
    else:
        history = []
        messages = build_initial_messages(problem)
        attempts = 0
        if checkpoint_path:
            for message in messages:
                _write_checkpoint(checkpoint_path, _json_safe_message(message))

    if history and history[-1].correct:
        record = _build_record(
            problem,
            True,
            attempts,
            max_attempts,
            elapsed_seconds,
            history,
            tools,
            messages,
        )
        return record

    while max_attempts is None or attempts < max_attempts:
        attempts += 1
        generation = await adapter.generate(messages, attempts)
        messages.append(generation.message)
        if checkpoint_path:
            _write_checkpoint(checkpoint_path, _json_safe_message(generation.message))
        tool_calls = generation.message.get("tool_calls") or []
        if tool_calls:
            feedback = env.submit(generation.answer)
            history.append(feedback)
            tool_call = tool_calls[0]
            tool_msg = tool_message(tool_call["id"], feedback.to_json())
            messages.append(tool_msg)
            if checkpoint_path:
                _write_checkpoint(checkpoint_path, tool_msg)
        else:
            feedback = Feedback(
                correct=False,
                guess=generation.answer,
                message="missing_tool_call",
            )
            history.append(feedback)
        duration_seconds = elapsed_seconds + (time.perf_counter() - start)
        record = _build_record(
            problem,
            bool(history and history[-1].correct),
            attempts,
            max_attempts,
            duration_seconds,
            history,
            tools,
            messages,
        )
        if feedback.correct:
            break

    success = bool(history and history[-1].correct)
    duration_seconds = elapsed_seconds + (time.perf_counter() - start)
    record = _build_record(
        problem,
        success,
        attempts,
        max_attempts,
        duration_seconds,
        history,
        tools,
        messages,
    )
    return record


async def run_eval_async(
    problems: list[Problem],
    adapter: ModelAdapter,
    max_attempts: int | None,
    out_path: Path,
    concurrency: int,
) -> dict:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_dir = out_path.parent / "checkpoints"
    semaphore = asyncio.Semaphore(concurrency)
    completed_ids = _load_completed_ids(out_path)
    total = len(problems)
    if completed_ids:
        print(f"skipping {len(completed_ids)} completed problems from {out_path}")

    async def run_one(
        problem: Problem,
        checkpoint_path: Path,
        checkpoint_messages: list[dict] | None,
    ) -> dict:
        async with semaphore:
            try:
                return await run_problem(
                    problem,
                    adapter,
                    max_attempts,
                    checkpoint_path,
                    checkpoint_messages,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"error problem {problem.id}: {exc}", flush=True)
                return {
                    "id": problem.id,
                    "success": False,
                    "attempts_used": 0,
                    "max_attempts": max_attempts,
                    "duration_seconds": 0.0,
                    "final_answer": None,
                    "gold_answer": problem.answer,
                    "category": problem.category,
                    "turns": [],
                    "tools": build_tools(),
                    "messages": checkpoint_messages or build_initial_messages(problem),
                    "error": str(exc),
                }

    pending: list[tuple[Problem, Path, list[dict] | None]] = []
    for problem in problems:
        if problem.id in completed_ids:
            continue
        checkpoint_path = checkpoint_dir / f"{problem.id}.jsonl"
        checkpoint_messages = _load_checkpoint_messages(checkpoint_path)
        pending.append((problem, checkpoint_path, checkpoint_messages or None))

    tasks = [
        asyncio.create_task(run_one(problem, checkpoint_path, checkpoint_messages))
        for problem, checkpoint_path, checkpoint_messages in pending
    ]

    processed = 0
    success = 0
    duration = 0.0
    done = len(completed_ids)
    mode = "a" if out_path.exists() else "w"
    with out_path.open(mode, encoding="utf-8") as handle:
        for task in asyncio.as_completed(tasks):
            record = await task
            status = "ok" if record.get("success") else "fail"
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            processed += 1
            done += 1
            success += int(bool(record.get("success")))
            duration += float(record.get("duration_seconds", 0.0))
            print(
                f"[{done}/{total}] problem {record.get('id')} {status}",
                flush=True,
            )
            if record.get("error"):
                print(
                    f"[{done}/{total}] problem {record.get('id')} error={record['error']}",
                    flush=True,
                )
    return {
        "count": processed,
        "success": success,
        "success_rate": success / processed if processed else 0.0,
        "avg_duration_seconds": duration / processed if processed else 0.0,
        "total": total,
        "skipped": len(completed_ids),
    }


def run_eval(
    problems: Iterable[Problem],
    adapter: ModelAdapter,
    max_attempts: int | None,
    out_path: Path,
    concurrency: int = 1,
) -> dict:
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")
    problem_list = list(problems)
    return asyncio.run(
        run_eval_async(problem_list, adapter, max_attempts, out_path, concurrency)
    )


def summarize_run(path: Path) -> dict:
    total = 0
    success = 0
    attempts = 0
    duration = 0.0
    by_category: dict[str, dict[str, int]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            total += 1
            attempts += int(record["attempts_used"])
            success += int(bool(record["success"]))
            duration += float(record.get("duration_seconds", 0.0))
            category = record.get("category") or ""
            bucket = by_category.setdefault(category, {"total": 0, "success": 0})
            bucket["total"] += 1
            bucket["success"] += int(bool(record["success"]))
    return {
        "total": total,
        "success": success,
        "success_rate": success / total if total else 0.0,
        "avg_attempts": attempts / total if total else 0.0,
        "avg_duration_seconds": duration / total if total else 0.0,
        "by_category": {
            category: {
                **values,
                "success_rate": values["success"] / values["total"] if values["total"] else 0.0,
            }
            for category, values in sorted(by_category.items())
        },
    }
