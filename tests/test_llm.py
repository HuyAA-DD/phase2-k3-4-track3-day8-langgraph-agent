"""Tests for LLM environment configuration."""

from __future__ import annotations

import importlib
import os
import sys
from io import StringIO

import dotenv
from pytest import MonkeyPatch


def test_has_llm_api_key(monkeypatch: MonkeyPatch) -> None:
    """Recognize supported keys without exposing their values."""
    from langgraph_agent_lab.llm import has_llm_api_key

    for variable in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(variable, raising=False)
    assert not has_llm_api_key()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    assert has_llm_api_key()


def test_llm_module_loads_dotenv_once(monkeypatch: MonkeyPatch) -> None:
    """The factory loads .env once without overriding the process environment."""
    calls = 0
    real_load_dotenv = dotenv.load_dotenv
    monkeypatch.setenv("LLM_CONFIG_PRIORITY", "from-process")
    monkeypatch.delenv("LLM_CONFIG_FROM_DOTENV", raising=False)

    def fake_load_dotenv(*args: object, **kwargs: object) -> bool:
        nonlocal calls
        calls += 1
        env_stream = StringIO(
            "LLM_CONFIG_FROM_DOTENV=loaded\nLLM_CONFIG_PRIORITY=from-dotenv\n"
        )
        return real_load_dotenv(stream=env_stream)

    monkeypatch.setattr(dotenv, "load_dotenv", fake_load_dotenv)
    sys.modules.pop("langgraph_agent_lab.llm", None)

    try:
        first_import = importlib.import_module("langgraph_agent_lab.llm")
        second_import = importlib.import_module("langgraph_agent_lab.llm")

        assert first_import is second_import
        assert calls == 1
        assert os.environ["LLM_CONFIG_FROM_DOTENV"] == "loaded"
        assert os.environ["LLM_CONFIG_PRIORITY"] == "from-process"
    finally:
        sys.modules.pop("langgraph_agent_lab.llm", None)
        os.environ.pop("LLM_CONFIG_FROM_DOTENV", None)
