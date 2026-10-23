"""Open Kernel: eklenti yükleme, araç ekleme/değiştirme, kancalar ve sistem talimatı."""

from __future__ import annotations

import asyncio

import pytest

from ada import agent, kernel
from ada.kernel import hooks, loader
from ada.tools import REGISTRY, ToolResult


@pytest.fixture
def plugdir(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA_KERNEL_PLUGINS", str(tmp_path))
    saved = dict(REGISTRY)
    hooks.HOOKS.clear()
    yield tmp_path
    hooks.HOOKS.clear()
    REGISTRY.clear()
    REGISTRY.update(saved)


def run(coro):
    return asyncio.run(coro)


def test_scaffold_and_example_not_loaded(plugdir):
    infos = loader.load_plugins(force=True)
    assert infos == []
    assert (plugdir / "README.txt").exists() and (plugdir / "_example.py").exists()


def test_example_plugin_works_when_copied(plugdir):
    loader.load_plugins(force=True)
    (plugdir / "my_plugin.py").write_text((plugdir / "_example.py").read_text(encoding="utf-8"), encoding="utf-8")
    infos = loader.load_plugins(force=True)
    assert infos[0].ok and infos[0].tools == ("word_count",)
    r = run(agent._run_tool("word_count", {"text": "bir iki üç"}))
    assert r.content == "3 kelime" and not r.is_error
    assert "tarih ekle" in agent._context_block()["text"]


def test_broken_plugin_is_skipped(plugdir):
    (plugdir / "a_bad.py").write_text("raise RuntimeError('bozuk')", encoding="utf-8")
    (plugdir / "b_good.py").write_text(
        "from ada import kernel\n@kernel.tool('k_ping', 'ping', {})\ndef p():\n    return 'pong'\n", encoding="utf-8"
    )
    infos = loader.load_plugins(force=True)
    assert [(i.name, i.ok) for i in infos] == [("a_bad", False), ("b_good", True)]
    assert "bozuk" in infos[0].error
    assert run(agent._run_tool("k_ping", {})).content == "pong"


def test_plugin_can_replace_builtin_tool(plugdir):
    (plugdir / "override.py").write_text(
        "from ada import kernel\n"
        "@kernel.tool('list_files', 'override', {'directory': {'type': 'string'}})\n"
        "def lf(directory=''):\n    return 'eklentiden'\n",
        encoding="utf-8",
    )
    infos = loader.load_plugins(force=True)
    assert infos[0].tools == ("list_files",)
    assert run(agent._run_tool("list_files", {})).content == "eklentiden"


def test_before_hook_rewrites_and_blocks():
    hooks.HOOKS.clear()
    calls = []

    @kernel.tool("k_echo", "echo", {"text": {"type": "string"}})
    def echo(text=""):
        calls.append(text)
        return text

    @kernel.before_tool
    async def rewrite(name, args):
        if name == "k_echo" and args.get("text") == "yasak":
            return "engellendi"
        if name == "k_echo":
            return {**args, "text": args["text"].upper()}

    @kernel.before_tool
    def broken(name, args):
        raise ValueError("kanca hatası zinciri bozmamalı")

    try:
        assert run(agent._run_tool("k_echo", {"text": "selam"})).content == "SELAM"
        r = run(agent._run_tool("k_echo", {"text": "yasak"}))
        assert r.is_error and r.content == "engellendi" and calls == ["SELAM"]
    finally:
        hooks.HOOKS.clear()
        REGISTRY.pop("k_echo", None)


def test_after_hook_replaces_result():
    hooks.HOOKS.clear()

    @kernel.tool("k_one", "one", {})
    def one():
        return "1"

    @kernel.after_tool
    def plus(name, args, result):
        if name == "k_one":
            return ToolResult(result.content + "+eklenti")

    try:
        assert run(agent._run_tool("k_one", {})).content == "1+eklenti"
    finally:
        hooks.HOOKS.clear()
        REGISTRY.pop("k_one", None)


def test_safety_still_applies_after_hooks():
    """Kanca girdiyi değiştirse bile doğrulama/limit zinciri yine çalışır."""
    hooks.HOOKS.clear()

    @kernel.before_tool
    def bad(name, args):
        return {"nonsense": 1}

    try:
        r = run(agent._run_tool("read_file", {"path": "x"}))
        assert r.is_error and "INVALID_INPUT" in r.content
    finally:
        hooks.HOOKS.clear()


def test_off_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("ADA_KERNEL_PLUGINS", "off")
    (tmp_path / "x.py").write_text("raise SystemExit", encoding="utf-8")
    assert loader.plugin_dir() is None and loader.load_plugins(force=True) == []
