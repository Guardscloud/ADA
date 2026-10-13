"""Otonomi: hedef yaşam döngüsü, tetikleyiciler, acil durdurma."""

from __future__ import annotations

import asyncio

import pytest
from conftest import call, gemini_response, last_user_text, text

from ada import goals, runtime, safety, store, triggers
from ada.tools import REGISTRY


async def settle():
    for _ in range(200):
        await asyncio.sleep(0.02)
        if not any(not r.done for r in runtime.RUNS.values()):
            return


async def drive(goal_id, runner, ticks=10):
    for _ in range(ticks):
        await runner.tick()
        await settle()
        g = goals.get(goal_id)
        if g["status"] in ("done", "failed", "paused"):
            return g
        goals.update(goal_id, lambda x: x.update(next_run=None), progress=False)
    return goals.get(goal_id)


def goal_script(verdicts, idle_ids=()):
    verdicts = list(verdicts)

    def script(contents, cfg):
        msg = last_user_text(contents)
        if cfg.system_instruction.startswith("Sen ADA'nın kalite denetçisi"):
            return gemini_response(text(verdicts.pop(0)))
        if msg.startswith("[HEDEF MODU]"):
            gid = msg.split("Hedef kimliği: ")[1].split("\n")[0]
            if gid in idle_ids:
                return gemini_response(text("hiçbir şey yapmadım"))
            if "DENETÇİ GERİ BİLDİRİMİ" in msg:
                return gemini_response(call("write_text_file", filename="o.md", content="x"), call("goal_complete", goal_id=gid, summary="düzeltildi"))
            if "(henüz plan yok)" in msg:
                return gemini_response(call("goal_set_plan", goal_id=gid, steps=["A", "B"]), call("goal_step_done", goal_id=gid, step_index=0, result="ok"))
            return gemini_response(call("goal_step_done", goal_id=gid, step_index=1, result="ok"), call("goal_complete", goal_id=gid, summary="bitti"))
        return gemini_response(text("oturum özeti"))

    return script


@pytest.mark.asyncio
async def test_goal_reject_fix_approve(fake_gemini):
    fake_gemini(goal_script(["Eksik.\nKARAR: RED", "Tamam.\nKARAR: ONAY"]))
    g = goals.create("Rapor", "yaz")
    final = await drive(g["id"], goals.Runner())
    assert final["status"] == "done"
    assert final["verifications"] == 2 and final["summary"] == "düzeltildi"
    conv = store.load(final["conv_id"])
    errors = [s for u in conv.ui if u["role"] == "assistant" for s in u["steps"] if "(hata)" in s["label"]]
    assert errors == []  # plan + adım aynı turda: sıralı yürütme sayesinde yarış yok


@pytest.mark.asyncio
async def test_idle_goal_is_paused(fake_gemini):
    g = goals.create("Takılan", "x")
    fake_gemini(goal_script([], idle_ids={g["id"]}))
    final = await drive(g["id"], goals.Runner())
    assert final["status"] == "paused" and final["sessions"] == goals.IDLE_LIMIT


@pytest.mark.asyncio
async def test_goal_session_limit(fake_gemini):
    fake_gemini(goal_script([]))
    g = goals.create("Sınırlı", "x", max_sessions=1)
    final = await drive(g["id"], goals.Runner())
    assert final["status"] == "paused" and "oturum sınırı" in final["notes"][-1]
    assert goals.set_status(g["id"], "active")["max_sessions"] > 1


@pytest.mark.asyncio
async def test_file_trigger_fires_once_for_new_files(fake_gemini, tmp_path):
    fired = []
    fake_gemini(lambda c, cfg: (fired.append(last_user_text(c)), gemini_response(text("ok")))[1])
    (tmp_path / "eski.pdf").write_text("x")
    REGISTRY["create_trigger"].handler(name="Fatura", type="file_added", prompt="işle", folder=str(tmp_path), pattern="*.pdf")
    w = triggers.Watcher()
    await w.tick(); await settle()
    assert fired == []  # mevcut dosyalar tetiklemez
    (tmp_path / "yeni.pdf").write_text("x"); (tmp_path / "not.txt").write_text("x")
    await w.tick(); await settle()
    assert len(fired) == 1 and "yeni.pdf" in fired[0] and "not.txt" not in fired[0]
    await w.tick(); await settle()
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_email_trigger_filters_sender(fake_gemini, monkeypatch):
    fired = []
    fake_gemini(lambda c, cfg: (fired.append(last_user_text(c)), gemini_response(text("ok")))[1])
    mails = [{"id": "1", "from": "a@x.com", "subject": "eski"}]
    monkeypatch.setattr(triggers, "_latest_emails", lambda count=30: list(mails))
    monkeypatch.setattr(triggers.config, "IS_MAC", True)
    REGISTRY["create_trigger"].handler(name="Müşteri", type="new_email", prompt="özetle", from_contains="musteri.com")
    w = triggers.Watcher()
    await w.tick(); await settle()
    mails[:0] = [{"id": "2", "from": "Ali <ali@musteri.com>", "subject": "Teklif"}, {"id": "3", "from": "spam@y.com", "subject": "Reklam"}]
    await w.tick(); await settle()
    assert len(fired) == 1 and "Teklif" in fired[0] and "Reklam" not in fired[0]


@pytest.mark.asyncio
async def test_emergency_stop_blocks_everything(fake_gemini):
    fake_gemini(lambda c, cfg: gemini_response(call("run_shell", command="sleep 20")))
    conv = store.create()
    run = runtime.start(conv, "uzun iş")
    g = goals.create("H", "x")
    await asyncio.sleep(0.5)
    result = await safety.emergency_stop("test")
    await asyncio.sleep(0.2)
    assert result["cancelled"] == 1 and run.done
    assert goals.get(g["id"])["status"] == "paused"
    with pytest.raises(RuntimeError):
        runtime.start(store.create(), "yeni")
    from ada.agent import _run_tool

    assert "acil durdurma" in (await _run_tool("memory_search", {})).content
    safety.resume("test")
    assert not safety.is_halted()


def test_daily_limit(monkeypatch):
    monkeypatch.setattr(safety, "DAILY_TOOL_LIMIT", 2)
    assert safety.check_limit("x") is None
    assert safety.check_limit("x") is None
    assert "sınır" in safety.check_limit("x")


def test_backup_restore_roundtrip(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("eski")
    bid = safety.backup(f)
    f.write_text("yeni")
    safety.restore(bid)
    assert f.read_text() == "eski"
    assert len(safety.list_backups()) == 2  # geri yükleme öncesi hali de yedeklendi


def test_zero_daily_limit_means_unlimited(monkeypatch):
    monkeypatch.setattr(safety, "DAILY_TOOL_LIMIT", 0)
    monkeypatch.setattr(safety, "DAILY_EMAIL_LIMIT", 0)
    assert all(safety.check_limit("mail_send") is None for _ in range(50))
