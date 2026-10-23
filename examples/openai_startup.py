"""Slim OpenAI tool definitions once at agent start-up, then send them on every request.

    python examples/openai_startup.py            # offline: prints the token saving
    OPENAI_API_KEY=... python examples/openai_startup.py --call

toolism.slim_tools() returns the same shape it was given (here: Chat Completions tool dicts), is
cached by input, and with the default safe level never changes which tool calls validate.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import toolism

HERE = Path(__file__).parent


def main() -> None:
    tools = json.loads((HERE / "openai_tools.json").read_text(encoding="utf-8"))["tools"]
    slim = toolism.slim_tools(tools)

    before = toolism.measure(toolism.parse(tools), tokenizer="heuristic").total
    after = toolism.measure(toolism.parse(slim), tokenizer="heuristic").total
    print(f"tool definitions: {before} -> {after} tokens per request (heuristic count)")

    if "--call" in sys.argv:
        import openai  # pip install openai

        client = openai.OpenAI()
        response = client.chat.completions.create(
            model="gpt-5",
            tools=slim,
            messages=[{"role": "user", "content": "What's the weather in Paris in celsius?"}],
        )
        print(response.choices[0].message)


if __name__ == "__main__":
    main()
