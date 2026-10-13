"""Görevler ve rutinler her dilde yazılabilir: tür tanıma, rutin dosyası alanları, yanıt dili talimatı."""

from __future__ import annotations

import pytest

from ada import agent, config, router, routines


@pytest.mark.parametrize("text,kind", [
    ("Write a Python script that renames my photos by date", "kod"),
    ("Click the Save button in the open window", "ekran"),
    ("Prepare an Excel spreadsheet of this month's invoices", "ofis"),
    ("Research the electric car market and compare prices", "arastirma"),
    ("Analyze our sales strategy step by step", "derin"),
    ("Keep this confidential and use the local model", "gizli"),
    ("Erstelle einen Bericht über die Verkaufszahlen", "ofis"),
    ("Rédige une lettre pour mon client", "ofis"),
    ("Busca noticias sobre energía solar y compara precios", "arastirma"),
    ("Создай таблицу расходов за месяц", "ofis"),
    ("Türkiye'deki e-ticaret pazarını araştır", "arastirma"),  # Türkçe eskisi gibi çalışır
])
def test_task_kind_is_recognised_in_other_languages(text, kind):
    assert router.classify(text) == kind


def test_action_requests_in_other_languages_are_not_treated_as_chat():
    # Anahtar sözcük yok ama bir iş isteniyor: sohbet modeli değil, iş yapabilen model
    assert router.classify("Delete the old backups") == "ofis"
    assert router.classify("Lösch die alten Sicherungen") == "ofis"
    assert router.classify("Удали старые копии") == "ofis"
    assert router.classify("How are you today?") == "sohbet"
    # Hiç tanınmayan bir dilde de çalışır: tür uzunluğa göre seçilir
    assert router.classify("こんにちは") == "sohbet"


def test_self_check_recognises_giving_up_in_other_languages():
    def turn(final):
        return [{"role": "user", "parts": [{"text": "Send the report by email"}]}, {"role": "model", "parts": [{"text": final}]}]

    assert router.assess(turn("I'm sorry, I was not able to do that."), 1, kind="ofis", background=False, task="Send the report by email")
    assert router.assess(turn("Das konnte nicht erledigt werden."), 1, kind="ofis", background=False, task="Sende den Bericht")
    assert router.assess(turn("Sure — what is your favourite colour?"), 1, kind="sohbet", background=False, task="hello") is None


@pytest.mark.parametrize("text,times,days", [
    ("---\nname: Daily news\ntime: 08:30\ndays: weekdays\nnotify: yes\n---\nSummarise the news.", ["08:30"], [0, 1, 2, 3, 4]),
    ("---\ntitel: Bericht\nuhrzeit: 9\ntage: Montag, Mittwoch\naktiv: ja\n---\nSchreibe den Bericht.", ["09:00"], [0, 2]),
    ("---\ntitre: Veille\nheure: 07:00\njours: lundi-vendredi\n---\nRésume l'actualité.", ["07:00"], [0, 1, 2, 3, 4]),
    ("---\nnombre: Resumen\nhora: 18:00\ndías: sábado, domingo\n---\nResume las noticias.", ["18:00"], [5, 6]),
    ("---\nnome: Riepilogo\nora: 10:00\ngiorni: ogni giorno\n---\nRiassumi le notizie.", ["10:00"], []),
    ("---\nad: Özet\nsaat: 09:00\ngunler: hafta içi\n---\nHaberleri özetle.", ["09:00"], [0, 1, 2, 3, 4]),
])
def test_routine_files_accept_other_languages(text, times, days):
    p = routines.parse(text, "r.md")
    assert p["spec"]["times"] == times and p["spec"].get("weekdays", []) == days and p["enabled"] and p["notify"]


def test_routine_booleans_in_other_languages():
    assert routines.parse("---\nname: X\nenabled: no\nnotify: nein\n---\nDo it.", "r.md")["enabled"] is False
    assert routines.parse("---\nname: X\nactive: oui\nnotify: não\n---\nDo it.", "r.md")["notify"] is False


def test_default_reply_language_follows_interface_language(monkeypatch):
    monkeypatch.setenv("ADA_LANG", "de")
    assert config.ui_language() == "de"
    hint = agent._language_hint()
    assert "varsayılan dil: Deutsch" in hint and "hangi dilde yazarsa o dilde" in hint
    assert hint in agent._context_block()["text"]
    monkeypatch.setenv("ADA_LANG", "auto")
    assert config.ui_language() in config.LANGS  # sistem dili; desteklenmiyorsa İngilizce
    monkeypatch.setenv("ADA_LANG", "xx")
    assert config.ui_language() in config.LANGS
