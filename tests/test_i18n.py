"""GUI language support: string tables, core-message translation, saved choice."""

import json
import re
import string
import tkinter as tk

import numpy as np
import pytest
import soundfile as sf

from loopmaker import batch, i18n, naming, pipeline, presets

CJK = re.compile(r"[一-鿿]")
SR = 8_000


def fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


# --- String table -------------------------------------------------------------

def test_every_key_has_both_languages_with_matching_placeholders():
    for key, entry in i18n.STRINGS.items():
        assert set(entry) == {i18n.EN, i18n.ZH_TW}, key
        # English may use plural helpers ({s}, {ws}); Chinese has no plurals.
        assert fields(entry[i18n.ZH_TW]) <= fields(entry[i18n.EN]), key
        assert fields(entry[i18n.EN]) - {"s", "ws"} <= fields(entry[i18n.ZH_TW]) | {"s", "ws"}, key


def test_lookup_plurals_and_fallbacks():
    en, zh = i18n.Translator(i18n.EN), i18n.Translator(i18n.ZH_TW)
    assert en.t("summary", files=1, ok=1, warn=0, bad=0) == "1 file: 1 OK, 0 warnings, 0 cannot be processed"
    assert en.t("summary", files=3, ok=1, warn=1, bad=1) == "3 files: 1 OK, 1 warning, 1 cannot be processed"
    assert zh.t("summary", files=3, ok=1, warn=1, bad=1) == "3 個檔案：1 個 OK、1 個警告、1 個無法處理"
    assert en.t("progress_done", n=4) == "done (4 files)"
    assert zh.t("process") == "開始處理"
    assert en.t("no_such_key") == "no_such_key"
    assert i18n.Translator("klingon").language == i18n.EN


# --- Core messages --------------------------------------------------------------

def make(path, seconds, channels=1):
    data = np.random.default_rng(0).standard_normal((int(seconds * SR), channels)) * 0.1
    sf.write(str(path), data if channels > 1 else data[:, 0], SR)
    return path


def produced_messages(tmp_path) -> list[str]:
    """Collect real English messages from the core modules, one of each kind."""
    long, short = make(tmp_path / "long.wav", 40), make(tmp_path / "Rain_short.wav", 2)
    wide = make(tmp_path / "wide.wav", 0.2, channels=8)
    dup = tmp_path / "long.flac"
    sf.write(str(dup), np.zeros(SR * 40), SR)
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"nope")
    out = tmp_path / "out"

    msgs: list[str] = []
    s = pipeline.LoopSettings(out_dir=out, crossfade_s=3.0, variations=5, normalize_lufs=-23)
    for v in batch.validate_batch([batch.FileJob(p) for p in (long, short, wide, dup, broken)], s):
        msgs += v.errors + v.warnings
    for v in batch.validate_batch([batch.FileJob(short), batch.FileJob(wide)], s, "clamp"):
        msgs += v.changes
    s4 = pipeline.LoopSettings(out_dir=out, crossfade_s=4.0, variations=5)
    mid = make(tmp_path / "mid.wav", 7.5)
    msgs += batch.validate_batch([batch.FileJob(mid)], s4, "clamp")[0].changes  # 5 -> fewer variations
    for override in (batch.FileJob(long, crossfade_s=-1), batch.FileJob(long, variations=7)):
        msgs += batch.validate_batch([override], pipeline.LoopSettings(out_dir=out))[0].errors
    msgs += naming.Naming("{nme}:}{", category="a|b", suffix="x*").errors()
    msgs += naming.template_errors("") + naming.template_errors("{name}.")
    msgs += naming.Naming("AMB_bed").warnings()
    msgs += presets.OutputSpec(None, "32f", "FLAC").errors()
    msgs += pipeline.LoopSettings(min_spacing=1.5).errors()
    with pytest.raises(ValueError) as reserved:
        naming.Naming("CON").render("x", 1)
    msgs.append(str(reserved.value))
    # Messages built by the GUI itself (settings form, override cells).
    msgs += ["crossfade must be a number", "crossfade must be greater than 0",
             "safety fade cannot be negative", "crossfade override 'abc' is not a number",
             "variations override '7' must be off, 3, 4 or 5"]
    return msgs


def test_every_produced_core_message_has_a_chinese_translation(tmp_path):
    messages = produced_messages(tmp_path)
    assert len(messages) >= 20  # the scenario really covers many kinds
    untranslated = [m for m in messages if not CJK.search(i18n.translate_message(m, i18n.ZH_TW))]
    assert untranslated == []


def test_translation_keeps_the_numbers():
    zh = i18n.translate_message("crossfade 3.0 s exceeds half the file length (1.00 s)", i18n.ZH_TW)
    assert zh == "crossfade 3.0 秒超過檔案長度的一半（1.00 秒）"
    assert i18n.translate_message("Rain_short.wav: crossfade 3.0 -> 1.0 s", i18n.ZH_TW) == \
        "Rain_short.wav：crossfade 3.0 → 1.0 秒"


def test_unknown_messages_and_english_pass_through():
    assert i18n.translate_message("something new happened", i18n.ZH_TW) == "something new happened"
    text = "crossfade 3.0 s exceeds half the file length (1.00 s)"
    assert i18n.translate_message(text, i18n.EN) == text


# --- Saved choice ------------------------------------------------------------------

def test_language_is_saved_and_loaded(tmp_path):
    path = tmp_path / "Loop Maker" / "settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"other": 1}), encoding="utf-8")
    i18n.save_language(i18n.ZH_TW, path)
    assert i18n.load_language(path) == i18n.ZH_TW
    assert json.loads(path.read_text(encoding="utf-8"))["other"] == 1  # other settings kept


@pytest.mark.parametrize("content", [None, "{broken", '{"language": "fr"}', "[]"])
def test_missing_or_bad_settings_fall_back_to_system_language(tmp_path, monkeypatch, content):
    path = tmp_path / "settings.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(i18n, "system_language", lambda: i18n.ZH_TW)
    assert i18n.load_language(path) == i18n.ZH_TW


# --- GUI switches in place -------------------------------------------------------------

def test_gui_switches_language_without_changing_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(i18n, "settings_path", lambda: tmp_path / "settings.json")
    from loopmaker import gui

    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display available")
    root.withdraw()
    try:
        app = gui.LoopMakerApp(root, language=i18n.EN)
        app._validate_now()
        assert app.template_hint_var.get() == "File name preview: Example Recording_loop.wav"  # no files yet
        app._add_paths([str(make(tmp_path / "Rain_short.wav", 2))])
        app.crossfade_var.set("3")
        app.variations_var.set("3")
        app.policy_var.set(app._display("policy", "clamp"))
        app.rate_var.set(app._display("rate", presets.SOURCE))
        app._validate_now()
        assert app.process_button["text"] == "Process"
        assert app.table.rows[0].status_label["text"] == "warning"

        app._set_language(i18n.ZH_TW)
        assert app.process_button["text"] == "開始處理"
        assert app.table.rows[0].status_label["text"] == "警告"
        assert app._policy() == "clamp" and app.policy_var.get() == "自動修正"
        assert app._internal("variations", app.variations_var) == "3"
        assert app._current_output_spec().sample_rate is None  # "沿用來源" still means source
        tooltip = app.table.tooltip_for(app.table.rows[0].validation)
        assert "自動修正" in tooltip and "秒" in tooltip
        assert app.summary_var.get().startswith("1 個檔案")
        # Preview shows the real first output name (3 variations -> "+2" more).
        assert app.template_hint_var.get() == "檔名預覽：Rain_short_loop_v01.wav  (+2)"
        assert i18n.load_language(tmp_path / "settings.json") == i18n.ZH_TW  # remembered

        app._set_language(i18n.EN)
        assert app.policy_var.get() == "auto-clamp" and app._policy() == "clamp"
    finally:
        root.destroy()
