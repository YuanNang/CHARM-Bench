from __future__ import annotations

import base64
import json
import os
import asyncio
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

import httpx

from .types import FewShotExample, Generation


class ModelAdapter(ABC):
    @abstractmethod
    async def generate(self, messages: list[dict[str, Any]], turn: int) -> Generation:
        raise NotImplementedError


class FakeAdapter(ModelAdapter):
    def __init__(self, answers: dict[int, list[str]] | None = None):
        self.answers = answers or {}
        self.indexes: dict[int, int] = {}

    async def generate(self, messages: list[dict[str, Any]], turn: int) -> Generation:
        problem_id = int(messages[1].get("problem_id", 0))
        values = self.answers.get(problem_id, [""])
        index = self.indexes.get(problem_id, 0)
        self.indexes[problem_id] = index + 1
        answer = values[min(index, len(values) - 1)]
        return Generation(
            answer=answer,
            message={
                "role": "assistant",
                "content": f"提交答案：{answer}",
                "reasoning": None,
                "tool_calls": [guess_tool_call(turn, answer)],
            },
        )


def _normalize_tool_calls(tool_calls: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not tool_calls:
        return []
    normalized = []
    for tool_call in tool_calls:
        function = dict(tool_call.get("function") or {})
        args = function.get("arguments", {})
        if isinstance(args, dict):
            function["arguments"] = json.dumps(args, ensure_ascii=False)
        normalized.append({**tool_call, "function": function})
    return normalized


def _normalize_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        current = dict(message)
        content = current.get("content")
        if content is None:
            current["content"] = ""
        elif isinstance(content, dict):
            current["content"] = json.dumps(content, ensure_ascii=False)
        if current.get("role") == "assistant":
            current["tool_calls"] = _normalize_tool_calls(current.get("tool_calls"))
        normalized.append(current)
    return normalized


def resolve_model(provider: str, model: str | None) -> str | None:
    if model:
        return model
    candidates = ["CHARM_MODEL"]
    if provider == "openai":
        candidates.append("OPENAI_MODEL")
    if provider == "anthropic":
        candidates.append("ANTHROPIC_MODEL")
    if provider == "google":
        candidates.append("GOOGLE_MODEL")
    for key in candidates:
        value = os.environ.get(key)
        if value:
            return value
    return None


def image_data_url(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def build_prompt(problem: Any) -> str:
    return "\n".join(
        [
            "现在开始正式答题。",
            f"第一张图提示文字：这是{problem.ref_word}",
            f"答案类别：{problem.category}",
            f"答案字数：{problem.answer_length}",
            "请观察两张图片，进行中文谐音、拆字、数量、动作和常识联想推理。你可以先写出分析过程；当你准备提交答案时，调用 submit_answer 工具，并把答案填入 answer 字段。",
            "submit_answer 的 answer 只能包含最终答案。",
        ]
    )


def build_system_prompt() -> str:
    return (
        "你是 CHARM benchmark 的 ReAct 答题 agent。任务是根据两张图片、第一张图提示词、"
        "答案类别和字数，推理中文谐音梗答案。你可以先分析图片中的物体、动作、数量、拆分关系和谐音关系。"
        "当你准备尝试答案时，必须调用 submit_answer 工具提交。"
        "submit_answer 的 answer 只能包含最终答案"
        "工具会返回正确与否，以及汉字和无声调拼音的绿/黄/灰位置反馈。"
        "如果工具返回错误，请根据历史反馈继续推理并再次调用工具。"
    )


def default_example() -> FewShotExample | None:
    image_1 = Path("data/refer/refer-1.png")
    image_2 = Path("data/refer/refer-2.png")
    if not image_1.exists() or not image_2.exists():
        return None
    return FewShotExample(
        ref_word="金子",
        category="成语",
        answer="半斤八两",
        image_1=image_1,
        image_2=image_2,
    )


def build_user_content(problem: Any, example: FewShotExample | None = None) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    if example:
        content.extend(
            [
                {
                    "type": "text",
                    "text": "\n".join(
                        [
                            "下面是一个示例题，用来说明游戏规则。",
                            f"示例第一张图提示文字：这是{example.ref_word}",
                            f"示例答案类别：{example.category}",
                            f"示例答案字数：{len(example.answer)}",
                            "示例两张图如下，正确答案是：半斤八两。",
                            "解题方式：第一张图给出“金子”，第二张图中金元宝被切开并出现两个“8”，提示“半”与“八两”，谐音得到“斤”。组合得到成语“半斤八两”。",
                        ]
                    ),
                },
                {"type": "image_url", "image_url": {"url": image_data_url(example.image_1)}},
                {"type": "image_url", "image_url": {"url": image_data_url(example.image_2)}},
            ]
        )
    content.extend(
        [
            {"type": "text", "text": build_prompt(problem)},
            {"type": "image_url", "image_url": {"url": image_data_url(problem.image_1)}},
            {"type": "image_url", "image_url": {"url": image_data_url(problem.image_2)}},
        ]
    )
    return content


def build_initial_messages(problem: Any) -> list[dict[str, Any]]:
    return [
        {"role": "system", "content": build_system_prompt()},
        {
            "role": "user",
            "problem_id": problem.id,
            "content": build_user_content(problem, default_example()),
        },
    ]


def submit_answer_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "submit_answer",
            "description": (
                "提交一个中文答案，环境会判断是否正确。若错误，返回汉字位置反馈和无声调拼音位置反馈："
                "green 表示当前位置的拼音或汉字正确，yellow 表示答案中存在该拼音或汉字但位置不对，gray 表示不存在该拼音或汉字。"
                "answer 仅填写最终答案，不要包含分析过程。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "description": "要提交的中文答案，长度应与题目给出的答案字数一致。",
                    }
                },
                "required": ["answer"],
                "additionalProperties": False,
            },
        },
    }


def build_tools() -> list[dict[str, Any]]:
    return [submit_answer_tool()]


def guess_tool_call(turn: int, answer: str) -> dict[str, Any]:
    return {
        "id": f"submit_answer_{turn}",
        "type": "function",
        "function": {
            "name": "submit_answer",
            "arguments": {"answer": answer},
        },
    }


def extract_answer(raw_message: dict[str, Any], fallback: str) -> str:
    tool_calls = raw_message.get("tool_calls") or []
    if tool_calls:
        args = tool_calls[0].get("function", {}).get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return fallback
        if isinstance(args, dict) and args.get("answer"):
            return str(args["answer"]).strip()
    return fallback


def _parse_data_url(url: str) -> tuple[str, str]:
    if not url.startswith("data:") or ";base64," not in url:
        raise ValueError("unsupported image url format")
    prefix, data = url.split(",", 1)
    media_type = prefix[len("data:") :].split(";", 1)[0] or "image/png"
    return media_type, data


def _convert_tool_schema(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted = []
    for tool in tools:
        function = tool.get("function") or {}
        converted.append(
            {
                "name": function.get("name", ""),
                "description": function.get("description", ""),
                "input_schema": function.get("parameters", {}),
            }
        )
    return converted


def _convert_messages_for_anthropic(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[dict[str, Any]]]:
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []

    for message in messages:
        role = message.get("role")
        if role == "system":
            content = message.get("content")
            if isinstance(content, str) and content:
                system_parts.append(content)
            continue
        if role == "tool":
            tool_call_id = message.get("tool_call_id")
            content = message.get("content") or ""
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": [{"type": "text", "text": content}],
                        }
                    ],
                }
            )
            continue

        content_blocks: list[dict[str, Any]] = []
        content = message.get("content")
        if role == "assistant" and message.get("_google_parts") is not None:
            parts = message["_google_parts"]
        elif isinstance(content, list):
            for item in content:
                item_type = item.get("type")
                if item_type == "text":
                    content_blocks.append({"type": "text", "text": item.get("text", "")})
                elif item_type == "image_url":
                    image_url = item.get("image_url", {}).get("url", "")
                    media_type, data = _parse_data_url(image_url)
                    content_blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": data,
                            },
                        }
                    )
        elif isinstance(content, str):
            content_blocks.append({"type": "text", "text": content})

        if role == "assistant" and message.get("_google_parts") is None:
            tool_calls = message.get("tool_calls") or []
            for tool_call in tool_calls:
                function = tool_call.get("function", {})
                args = function.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                content_blocks.append(
                    {
                        "type": "tool_use",
                        "id": tool_call.get("id"),
                        "name": function.get("name"),
                        "input": args,
                    }
                )
        converted.append(
            {
                "role": "assistant" if role == "assistant" else "user",
                "content": content_blocks,
            }
        )

    system_prompt = "\n".join(system_parts) if system_parts else None
    return system_prompt, converted


def _convert_messages_for_google(
    messages: list[dict[str, Any]],
) -> tuple[str | None, list[Any]]:
    from google.genai import types

    system_parts: list[str] = []
    converted: list[Any] = []

    for message in messages:
        role = message.get("role")
        if role == "system":
            content = message.get("content")
            if isinstance(content, str) and content:
                system_parts.append(content)
            continue

        parts: list[Any] = []
        content = message.get("content")
        if role == "assistant" and message.get("_google_parts") is not None:
            parts = message["_google_parts"]
        elif isinstance(content, list):
            for item in content:
                item_type = item.get("type")
                if item_type == "text":
                    parts.append(types.Part.from_text(text=item.get("text", "")))
                elif item_type == "image_url":
                    image_url = item.get("image_url", {}).get("url", "")
                    media_type, data = _parse_data_url(image_url)
                    parts.append(
                        types.Part.from_bytes(
                            data=base64.b64decode(data),
                            mime_type=media_type,
                        )
                    )
        elif isinstance(content, str):
            parts.append(types.Part.from_text(text=content))

        if role == "assistant" and message.get("_google_parts") is None:
            tool_calls = message.get("tool_calls") or []
            for tool_call in tool_calls:
                function = tool_call.get("function", {})
                args = function.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                parts.append(
                    types.Part.from_function_call(
                        name=function.get("name", ""),
                        args=args,
                    )
                )

        if role == "tool":
            tool_call_id = message.get("tool_call_id")
            tool_payload = message.get("content")
            if isinstance(tool_payload, str):
                try:
                    tool_payload = json.loads(tool_payload)
                except json.JSONDecodeError:
                    tool_payload = {"text": tool_payload}
            parts = [
                types.Part.from_function_response(
                    name="submit_answer",
                    response={
                        "tool_call_id": tool_call_id,
                        "feedback": tool_payload,
                    },
                )
            ]
            role = "user"

        if not parts:
            parts = [types.Part.from_text(text="")]

        converted.append(types.Content(role="model" if role == "assistant" else "user", parts=parts))

    system_prompt = "\n".join(system_parts) if system_parts else None
    return system_prompt, converted


def _summarize_google_response(response: Any) -> dict[str, Any]:
    candidates_summary = []
    for cand in getattr(response, "candidates", []) or []:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        function_calls = []
        for part in parts:
            function_call = getattr(part, "function_call", None)
            if function_call:
                function_calls.append(
                    {
                        "name": getattr(function_call, "name", None),
                        "args": getattr(function_call, "args", None),
                    }
                )
        candidates_summary.append(
            {
                "finish_reason": getattr(cand, "finish_reason", None),
                "safety_ratings": getattr(cand, "safety_ratings", None),
                "parts": [_google_part_to_json(part) for part in parts],
                "parts_text": [getattr(part, "text", None) for part in parts],
                "function_calls": function_calls,
            }
        )
    response_function_calls = []
    for call in getattr(response, "function_calls", []) or []:
        response_function_calls.append(
            {
                "name": getattr(call, "name", None),
                "args": getattr(call, "args", None),
            }
        )
    return {
        "text": getattr(response, "text", None),
        "prompt_feedback": getattr(response, "prompt_feedback", None),
        "function_calls": response_function_calls,
        "candidates": candidates_summary,
    }


def _google_part_to_json(part: Any) -> dict[str, Any]:
    function_call = getattr(part, "function_call", None)
    function_response = getattr(part, "function_response", None)
    payload: dict[str, Any] = {
        "text": getattr(part, "text", None),
        "thought": getattr(part, "thought", None),
        "has_thought_signature": bool(getattr(part, "thought_signature", None)),
    }
    if function_call:
        payload["function_call"] = {
            "name": getattr(function_call, "name", None),
            "args": getattr(function_call, "args", None),
            "id": getattr(function_call, "id", None),
        }
    if function_response:
        payload["function_response"] = {
            "name": getattr(function_response, "name", None),
            "id": getattr(function_response, "id", None),
            "response": getattr(function_response, "response", None),
        }
    return payload


def _extract_google_reasoning(response: Any) -> str | None:
    thoughts: list[str] = []
    candidates = getattr(response, "candidates", []) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        for part in parts:
            if getattr(part, "thought", None) and getattr(part, "text", None):
                thoughts.append(part.text)
    joined = "".join(thoughts).strip()
    return joined or None


def _extract_google_parts(response: Any) -> list[Any]:
    candidates = getattr(response, "candidates", []) or []
    if not candidates:
        return []
    content = getattr(candidates[0], "content", None)
    return list(getattr(content, "parts", None) or [])


def _extract_google_text(response: Any) -> str:
    text = getattr(response, "text", None) or ""
    if text.strip():
        return text
    candidates = getattr(response, "candidates", []) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        parts = getattr(content, "parts", None) or []
        collected = [getattr(part, "text", None) for part in parts]
        joined = "".join([item for item in collected if item])
        if joined.strip():
            return joined
    return ""


def _anthropic_messages_url(base_url: str | None) -> str:
    if not base_url:
        return "https://api.anthropic.com/v1/messages"
    base = base_url.rstrip("/")
    if base.endswith("/v1/messages"):
        return base
    if base.endswith("/v1"):
        return f"{base}/messages"
    return f"{base}/v1/messages"


async def _post_json_async(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, headers=headers, json=payload)
    if response.status_code >= 400:
        raise RuntimeError(
            f"Anthropic API error: {response.status_code} {response.text}"
        )
    return response.json()


class OpenAICompatibleAdapter(ModelAdapter):
    def __init__(self, model: str):
        from openai import AsyncOpenAI

        kwargs: dict[str, str | float] = {}
        api_key = os.environ.get("CHARM_API_KEY") or os.environ.get("OPENAI_API_KEY")
        base_url = os.environ.get("CHARM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        timeout = os.environ.get("CHARM_TIMEOUT")
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        if timeout:
            kwargs["timeout"] = float(timeout)
        self.client = AsyncOpenAI(**kwargs)
        self.model = model

    async def generate(self, messages: list[dict[str, Any]], turn: int) -> Generation:
        api_messages = [
            {key: value for key, value in message.items() if key != "problem_id"}
            for message in _normalize_messages(messages)
        ]
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=api_messages,
            tools=build_tools(),
            tool_choice="auto",
        )
        message = response.choices[0].message
        raw_message = message.model_dump(mode="json") if hasattr(message, "model_dump") else {}
        answer = extract_answer(raw_message, fallback=(message.content or "").strip())
        reasoning_content = getattr(message, "reasoning_content", None)
        return Generation(
            answer=answer,
            reasoning=reasoning_content,
            message={
                "role": "assistant",
                "content": message.content or "",
                "reasoning": reasoning_content,
                "tool_calls": raw_message.get("tool_calls") or [guess_tool_call(turn, answer)],
                "raw_message": raw_message,
            },
        )


class AnthropicAdapter(ModelAdapter):
    def __init__(self, model: str):
        self.model = model
        self.api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CHARM_API_KEY")
        self.base_url = os.environ.get("ANTHROPIC_BASE_URL") or os.environ.get("CHARM_BASE_URL") 
        self.timeout = float(os.environ.get("CHARM_TIMEOUT", "120"))
        self.max_tokens = int(os.environ.get("CHARM_MAX_TOKENS", "1024"))
        self.anthropic_version = os.environ.get("CHARM_ANTHROPIC_VERSION", "2023-06-01")
        if not self.api_key:
            raise ValueError("missing API key for provider anthropic")

    async def generate(self, messages: list[dict[str, Any]], turn: int) -> Generation:
        system_prompt, api_messages = _convert_messages_for_anthropic(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": api_messages,
            "max_tokens": self.max_tokens,
            "tools": _convert_tool_schema(build_tools()),
            "tool_choice": {"type": "auto"},
        }
        if system_prompt:
            payload["system"] = system_prompt

        url = _anthropic_messages_url(self.base_url)
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": self.anthropic_version,
            "content-type": "application/json",
        }
        raw_message = await _post_json_async(url, headers, payload, self.timeout)

        content_blocks = raw_message.get("content") or []
        tool_calls = []
        text_parts = []
        for block in content_blocks:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            if block.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id") or f"submit_answer_{turn}",
                        "type": "function",
                        "function": {
                            "name": block.get("name"),
                            "arguments": block.get("input", {}),
                        },
                    }
                )

        content = "".join(text_parts).strip() if text_parts else None
        answer = extract_answer({"tool_calls": tool_calls}, fallback=(content or ""))

        return Generation(
            answer=answer,
            message={
                "role": "assistant",
                "content": content,
                "reasoning": None,
                "tool_calls": tool_calls or [guess_tool_call(turn, answer)],
                "raw_message": raw_message,
            },
        )


class GoogleGenAIAdapter(ModelAdapter):
    def __init__(self, model: str):
        from google import genai

        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("CHARM_API_KEY")
        if not api_key:
            raise ValueError("missing API key for provider google")
        self.client = genai.Client(api_key=api_key)
        self.model = model

    async def generate(self, messages: list[dict[str, Any]], turn: int) -> Generation:
        from google.genai import types

        system_prompt, contents = _convert_messages_for_google(messages)
        function_declaration = types.FunctionDeclaration(
            name="submit_answer",
            description="Submit final answer only.",
            parameters_json_schema={
                "type": "object",
                "properties": {
                    "answer": {
                        "type": "string",
                        "description": "Final Chinese answer.",
                    }
                },
                "required": ["answer"],
                "additionalProperties": False,
            },
        )
        tools = [types.Tool(function_declarations=[function_declaration])]
        tool_config = types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(
                mode=types.FunctionCallingConfigMode.ANY,
                allowed_function_names=["submit_answer"],
            )
        )
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=tools,
            tool_config=tool_config,
            thinking_config=types.ThinkingConfig(include_thoughts=True),
        )

        def _call() -> Any:
            return self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )

        response = await asyncio.to_thread(_call)
        text = _extract_google_text(response)
        reasoning = _extract_google_reasoning(response)
        google_parts = _extract_google_parts(response)
        function_calls = getattr(response, "function_calls", None)
        summary = _summarize_google_response(response)
        if function_calls:
            call = function_calls[0]
            args = getattr(call, "args", {}) or {}
            answer = str(args.get("answer", "")).strip()
            if not answer:
                raise RuntimeError(f"empty google function call args: {summary}")
            return Generation(
                answer=answer,
                message={
                    "role": "assistant",
                    "content": text,
                    "reasoning": reasoning,
                    "_google_parts": google_parts,
                    "tool_calls": [
                        {
                            "id": f"submit_answer_{turn}",
                            "type": "function",
                            "function": {
                                "name": "submit_answer",
                                "arguments": args,
                            },
                        }
                    ],
                    "raw_message": summary,
                },
            )

        if not text.strip():
            raise RuntimeError(f"empty google response: {summary}")
        answer = text.strip()
        return Generation(
            answer=answer,
            message={
                "role": "assistant",
                "content": text,
                "reasoning": reasoning,
                "_google_parts": google_parts,
                "tool_calls": [guess_tool_call(turn, answer)],
                "raw_message": summary,
            },
        )


def make_adapter(provider: str, model: str | None) -> ModelAdapter:
    model = resolve_model(provider, model)
    if provider == "openai":
        if not model:
            raise ValueError("--model is required for provider openai")
        return OpenAICompatibleAdapter(model)
    if provider == "anthropic":
        if not model:
            raise ValueError("--model is required for provider anthropic")
        return AnthropicAdapter(model)
    if provider == "google":
        if not model:
            raise ValueError("--model is required for provider google")
        return GoogleGenAIAdapter(model)
    raise ValueError(f"unsupported provider: {provider}")
