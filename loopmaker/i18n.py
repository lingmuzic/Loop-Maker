"""GUI translations: English and Traditional Chinese.

Scope (also documented in the README):
* Every visible GUI string goes through :func:`t` / :meth:`Translator.t`.
* Validation messages are produced in English by the UI-free core modules.
  :func:`translate_message` maps the known ones to Chinese with a small
  pattern catalogue, so the core stays language-free. Anything not in the
  catalogue is shown in English rather than failing.
* The log and the exported batch report stay in English on purpose: they
  match the CLI and are meant to be shared with a team.

The chosen language is remembered in ``%APPDATA%\\Loop Maker\\settings.json``.
On first launch the Windows display language decides (Chinese for zh-TW /
zh-HK / zh-MO, English otherwise).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

EN, ZH_TW = "en", "zh_TW"
LANGUAGES = {EN: "English", ZH_TW: "繁體中文"}

# --------------------------------------------------------------------------
# UI strings. Keys are stable identifiers; {placeholders} use str.format.
# --------------------------------------------------------------------------

STRINGS: dict[str, dict[str, str]] = {
    # window sections
    "files_frame": {
        EN: "Files  (hover a status for details; leave Crossfade/Var. empty to use the global setting)",
        ZH_TW: "檔案  （滑鼠移到狀態可看詳細原因；Crossfade／Var. 留空表示使用全域設定）",
    },
    "settings_frame": {EN: "Global settings", ZH_TW: "全域設定"},
    "playback_frame": {EN: "Test playback", ZH_TW: "試聽"},
    "log_frame": {EN: "Log / batch report (English)", ZH_TW: "記錄／批次報告（英文）"},
    "language": {EN: "Language / 語言", ZH_TW: "語言 / Language"},
    # file buttons
    "add_files": {EN: "Add files...", ZH_TW: "加入檔案..."},
    "add_folder": {EN: "Add folder...", ZH_TW: "加入資料夾..."},
    "remove_selected": {EN: "Remove selected", ZH_TW: "移除勾選項目"},
    "clear": {EN: "Clear", ZH_TW: "清空"},
    "browse": {EN: "Browse...", ZH_TW: "瀏覽..."},
    # settings labels
    "crossfade_s": {EN: "Crossfade (s)", ZH_TW: "Crossfade（秒）"},
    "curve": {EN: "Curve", ZH_TW: "曲線"},
    "safety_fade_ms": {EN: "Safety fade (ms)", ZH_TW: "安全淡化（ms）"},
    "variations": {EN: "Variations", ZH_TW: "Variations"},
    "min_spacing": {EN: "min spacing (% of file)", ZH_TW: "最小間距（檔案的 %）"},
    "warn_below": {EN: "warn below (s)", ZH_TW: "短於此秒數警告"},
    "on_problem": {EN: "On problem", ZH_TW: "遇到問題時"},
    "preset": {EN: "Preset", ZH_TW: "預設"},
    "sample_rate": {EN: "Sample rate", ZH_TW: "取樣率"},
    "bit_depth": {EN: "Bit depth", ZH_TW: "位元深度"},
    "format": {EN: "Format", ZH_TW: "格式"},
    "normalize_to": {EN: "Normalize loudness to", ZH_TW: "響度標準化到"},
    "lufs_ceiling": {EN: "LUFS, true-peak ceiling", ZH_TW: "LUFS，true-peak 上限"},
    "dbtp": {EN: "dBTP", ZH_TW: "dBTP"},
    "name_template": {EN: "Name template", ZH_TW: "檔名模板"},
    "output_folder": {EN: "Output folder", ZH_TW: "輸出資料夾"},
    # combobox display values (mapped back to internal values by the GUI)
    "off": {EN: "Off", ZH_TW: "關閉"},
    "source": {EN: "source", ZH_TW: "沿用來源"},
    "policy_skip": {EN: "skip and log", ZH_TW: "略過並記錄"},
    "policy_clamp": {EN: "auto-clamp", ZH_TW: "自動修正"},
    "policy_stop": {EN: "stop the batch", ZH_TW: "停止批次"},
    # run bar
    "process": {EN: "Process", ZH_TW: "開始處理"},
    "processing": {EN: "Processing...", ZH_TW: "處理中..."},
    "export_report": {EN: "Export report...", ZH_TW: "匯出報告..."},
    "add_files_to_begin": {EN: "Add files to begin.", ZH_TW: "請先加入檔案。"},
    "settings_problem": {EN: "Settings problem: {problems}", ZH_TW: "設定有問題：{problems}"},
    "summary": {
        EN: "{files} file{s}: {ok} OK, {warn} warning{ws}, {bad} cannot be processed",
        ZH_TW: "{files} 個檔案：{ok} 個 OK、{warn} 個警告、{bad} 個無法處理",
    },
    "max_for_all": {EN: "max {value:.2f} s for all {n} file{s}", ZH_TW: "全部 {n} 個檔案最多 {value:.2f} 秒"},
    "max_for_selected": {EN: "max {value:.2f} s for {n} selected", ZH_TW: "勾選的 {n} 個檔案最多 {value:.2f} 秒"},
    "name_preview": {EN: "File name preview: {name}", ZH_TW: "檔名預覽：{name}"},
    # table
    "col_file": {EN: "File", ZH_TW: "檔案"},
    "col_length": {EN: "Length", ZH_TW: "長度"},
    "col_format": {EN: "Format", ZH_TW: "格式"},
    "col_crossfade": {EN: "Crossfade", ZH_TW: "Crossfade"},
    "col_var": {EN: "Var.", ZH_TW: "Var."},
    "col_output": {EN: "Output", ZH_TW: "輸出"},
    "col_status": {EN: "Status", ZH_TW: "狀態"},
    "col_progress": {EN: "Progress", ZH_TW: "進度"},
    "table_empty": {
        EN: "Drag and drop audio files or folders here, or use Add files...",
        ZH_TW: "把音檔或資料夾拖到這裡，或按「加入檔案...」",
    },
    "table_empty_no_dnd": {
        EN: "Use Add files... (install tkinterdnd2 for drag and drop)",
        ZH_TW: "請按「加入檔案...」（安裝 tkinterdnd2 即可拖放）",
    },
    "tip_crossfade_override": {
        EN: "Per-file crossfade in seconds (empty = global setting)",
        ZH_TW: "此檔案的 crossfade 秒數（留空 = 使用全域設定）",
    },
    "tip_variations_override": {
        EN: "Per-file variations: off, 3, 4 or 5 (empty = global setting)",
        ZH_TW: "此檔案的 variations：off、3、4 或 5（留空 = 使用全域設定）",
    },
    "unreadable": {EN: "unreadable", ZH_TW: "無法讀取"},
    "status_OK": {EN: "OK", ZH_TW: "OK"},
    "status_warning": {EN: "warning", ZH_TW: "警告"},
    "status_error": {EN: "error", ZH_TW: "錯誤"},
    "tip_error": {EN: "Error: {msg}", ZH_TW: "錯誤：{msg}"},
    "tip_warning": {EN: "Warning: {msg}", ZH_TW: "警告：{msg}"},
    "tip_clamp": {EN: "Auto-clamp: {msg}", ZH_TW: "自動修正：{msg}"},
    "progress_queued": {EN: "queued", ZH_TW: "排隊中"},
    "progress_processing": {EN: "processing...", ZH_TW: "處理中..."},
    "progress_done": {EN: "done ({n} file{s})", ZH_TW: "完成（{n} 個檔案）"},
    "progress_written": {EN: "written", ZH_TW: "已寫出"},
    "progress_skipped": {EN: "skipped", ZH_TW: "已略過"},
    "progress_failed": {EN: "failed", ZH_TW: "失敗"},
    "progress_not run": {EN: "not run", ZH_TW: "未執行"},
    # playback panel
    "play": {EN: "Play  (Space)", ZH_TW: "播放（空白鍵）"},
    "stop": {EN: "Stop  (Space)", ZH_TW: "停止（空白鍵）"},
    "play_loop_point": {EN: "Play loop point", ZH_TW: "播放循環點"},
    "open_output_folder": {EN: "Open output folder", ZH_TW: "開啟輸出資料夾"},
    "playback_hint": {
        EN: "Process some files, then pick an output to audition it.",
        ZH_TW: "處理檔案後，從清單選一個輸出來試聽。",
    },
    "playback_status": {
        EN: "{name}  |  {seconds:.2f} s  |  click the waveform to play from there, Space = play/stop, orange = crossfade",
        ZH_TW: "{name}  |  {seconds:.2f} 秒  |  點波形從該處播放，空白鍵 = 播放／停止，橘色 = crossfade",
    },
    "waveform_only": {EN: "Waveform only: {error}", ZH_TW: "只能顯示波形：{error}"},
    "smooth": {EN: "SMOOTH", ZH_TW: "平順"},
    "check_by_ear": {EN: "CHECK BY EAR", ZH_TW: "請用耳朵確認"},
    "seam": {EN: "seam {score}", ZH_TW: "接縫 {score}"},
    "peak_limited": {
        EN: "PEAK-LIMITED ({short:.1f} dB short of {target:g})",
        ZH_TW: "受峰值限制（比目標 {target:g} 少 {short:.1f} dB）",
    },
    # dialogs
    "dlg_tick_rows": {EN: "Tick the checkbox of the rows you want to remove.", ZH_TW: "請先勾選要移除的檔案。"},
    "dlg_fix_settings": {EN: "Please fix the settings first:", ZH_TW: "請先修正設定："},
    "dlg_cannot_open": {EN: "Could not open {name}:\n{error}", ZH_TW: "無法開啟 {name}：\n{error}"},
    "dlg_playback_failed": {EN: "Playback failed:\n{error}", ZH_TW: "播放失敗：\n{error}"},
    "dlg_no_output_folder": {
        EN: "The output folder does not exist yet:\n{folder}",
        ZH_TW: "輸出資料夾還不存在：\n{folder}",
    },
    "dlg_add_files": {EN: "Add audio files", ZH_TW: "加入音檔"},
    "dlg_add_folder": {EN: "Add a folder of audio files", ZH_TW: "加入音檔資料夾"},
    "dlg_choose_output": {EN: "Choose output folder", ZH_TW: "選擇輸出資料夾"},
    "dlg_export_report": {EN: "Export batch report", ZH_TW: "匯出批次報告"},
    "ft_audio": {EN: "Audio files", ZH_TW: "音檔"},
    "ft_all": {EN: "All files", ZH_TW: "所有檔案"},
    "ft_text": {EN: "Text files", ZH_TW: "文字檔"},
}


class Translator:
    """Holds the current language and looks up strings."""

    def __init__(self, language: str = EN) -> None:
        self.language = language if language in LANGUAGES else EN

    def t(self, key: str, **params) -> str:
        entry = STRINGS.get(key)
        if entry is None:
            return key  # missing key: show it rather than crash
        text = entry.get(self.language) or entry[EN]
        if self.language == EN and "s" not in params:
            # English plurals: callers pass n / files / warn; derive the "s" suffixes.
            n = params.get("n", params.get("files"))
            if n is not None:
                params.setdefault("s", "" if n == 1 else "s")
            if "warn" in params:
                params.setdefault("ws", "" if params["warn"] == 1 else "s")
        params.setdefault("s", "")
        params.setdefault("ws", "")
        try:
            return text.format(**params)
        except (KeyError, IndexError, ValueError):
            return text

    def message(self, text: str) -> str:
        return translate_message(text, self.language)


# --------------------------------------------------------------------------
# Core-message catalogue (English text from batch/variations/naming/presets)
# --------------------------------------------------------------------------

_N = r"[-+]?\d+(?:\.\d+)?"  # a number as printed by the core modules

#: (regex for the English message, Chinese template using the named groups)
MESSAGE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (rf"^crossfade (?P<x>{_N}) s exceeds half the file length \((?P<h>{_N}) s\)$",
     "crossfade {x} 秒超過檔案長度的一半（{h} 秒）"),
    (rf"^too short for (?P<n>\d+) variations: needs more than 2 x (?P<c>{_N}) s crossfade \+ "
     rf"(?P<n2>\d+) x (?P<s>{_N}) s minimum spacing \((?P<p>\d+)% of the file\) = (?P<t>{_N}) s, "
     rf"but the file is (?P<d>{_N}) s$",
     "檔案太短，無法產生 {n} 個 variations：需要大於 2 × {c} 秒 crossfade + {n2} × {s} 秒最小間距"
     "（檔案的 {p}%）= {t} 秒，但檔案只有 {d} 秒"),
    (rf"^source is only (?P<d>{_N}) s \(< (?P<t>{_N}) s\): variations may sound nearly identical$",
     "來源只有 {d} 秒（短於 {t} 秒）：各個 variation 聽起來可能幾乎一樣"),
    (r"^cannot read file: (?P<e>.*)$", "無法讀取檔案：{e}"),
    (rf"^crossfade must be greater than 0 s \(got (?P<x>{_N})\)$", "crossfade 必須大於 0 秒（目前是 {x}）"),
    (r"^variations must be off, 3, 4 or 5 \(got (?P<x>.*)\)$", "variations 必須是關閉、3、4 或 5（目前是 {x}）"),
    (rf"^shorter than (?P<x>{_N}) s: loudness cannot be measured, will not be normalised$",
     "短於 {x} 秒：無法量測響度，不會做響度標準化"),
    (r"^(?P<n>\d+) channels: loudness normalisation supports up to 5\.1$",
     "{n} 聲道：響度標準化最多支援 5.1"),
    (r"^output name collides with another file in the batch; written as (?P<names>.*)$",
     "輸出檔名和批次中其他檔案相同；將改名為 {names}"),
    (r"^output name equals the input file; it would be overwritten$", "輸出檔名和輸入檔相同，會覆蓋原檔"),
    (rf"^(?P<f>.+): crossfade (?P<a>{_N}) -> (?P<b>{_N}) s$", "{f}：crossfade {a} → {b} 秒"),
    (r"^(?P<f>.+): variations (?P<a>\d+) -> off \(standard loop\) \(file too short for \d+ at this crossfade\)$",
     "{f}：variations {a} → 關閉（改為標準 loop；以目前的 crossfade 檔案太短）"),
    (r"^(?P<f>.+): variations (?P<a>\d+) -> (?P<b>\d+) \(file too short for \d+ at this crossfade\)$",
     "{f}：variations {a} → {b}（以目前的 crossfade 檔案太短）"),
    # per-file override cells (gui_table)
    (r"^crossfade override (?P<x>.*) is not a number$", "crossfade 覆寫值 {x} 不是數字"),
    (r"^variations override (?P<x>.*) must be off, 3, 4 or 5$", "variations 覆寫值 {x} 必須是 off、3、4 或 5"),
    # settings form (gui) and settings.errors()
    (r"^(?P<label>.+) must be a number$", "{label} 必須是數字"),
    (r"^crossfade must be greater than 0$", "crossfade 必須大於 0"),
    (r"^safety fade cannot be negative$", "安全淡化不能是負數"),
    (r"^template is empty$", "檔名模板是空的"),
    (r"^unknown token \{(?P<tok>[^}]*)\}; allowed: (?P<allowed>.*)$", "未知的 token {{{tok}}}；可用：{allowed}"),
    (r"^unbalanced '\{' or '\}'$", "大括號 {{ }} 沒有成對"),
    (r"^template contains characters not allowed in file names: (?P<c>.*)$", "檔名模板含有檔名不允許的字元：{c}"),
    (r"^(?P<label>category|suffix) contains characters not allowed in file names: (?P<c>.*)$",
     "{label} 含有檔名不允許的字元：{c}"),
    (r"^a file name cannot end with a space or a dot$", "檔名不能以空格或句點結尾"),
    (r"^'(?P<n>.*)' is a reserved name on Windows$", "「{n}」是 Windows 保留的名稱"),
    (r"^the template renders to an empty file name$", "檔名模板產生的檔名是空的"),
    (r"^template has neither \{name\} nor \{index\}: batch outputs will collide and get _2, _3 \.\.\.$",
     "模板沒有 {{name}} 也沒有 {{index}}：批次輸出會重名，並自動加上 _2、_3 ..."),
    (r"^FLAC cannot store 32-bit float; use WAV or 16/24-bit$", "FLAC 不能存 32-bit float；請改用 WAV 或 16/24-bit"),
    (r"^minimum spacing must be between 0 and 100 %, not (?P<x>.*)$", "最小間距必須介於 0 到 100%（目前是 {x}）"),
]
MESSAGE_PATTERNS = [(re.compile(p), z) for p, z in MESSAGE_PATTERNS]

# Labels used inside "<label> must be a number" (built by the GUI settings form).
_FIELD_LABELS_ZH = {
    "crossfade": "crossfade", "safety fade": "安全淡化", "min spacing": "最小間距",
    "warn-below": "警告秒數", "LUFS target": "LUFS 目標", "true-peak ceiling": "true-peak 上限",
}


def translate_message(text: str, language: str) -> str:
    """Translate one core message; unknown messages are returned unchanged."""
    if language == EN:
        return text
    for pattern, template in MESSAGE_PATTERNS:
        match = pattern.match(text)
        if match:
            groups = match.groupdict()
            if "label" in groups and groups["label"] in _FIELD_LABELS_ZH:
                groups["label"] = _FIELD_LABELS_ZH[groups["label"]]
            try:
                return template.format(**groups)
            except (KeyError, IndexError, ValueError):
                return text  # a broken template must never break the GUI
    return text


# --------------------------------------------------------------------------
# Remembering the choice / picking a default
# --------------------------------------------------------------------------

def settings_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / "Loop Maker" / "settings.json"


def system_language() -> str:
    """Chinese if the Windows display language is zh-TW / zh-HK / zh-MO, else English."""
    try:
        import ctypes

        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return ZH_TW if lang_id in (0x0404, 0x0C04, 0x1404) else EN
    except (AttributeError, OSError):
        return EN


def load_language(path: Path | None = None) -> str:
    """Saved language, or the system default when nothing (valid) is saved."""
    path = path or settings_path()
    try:
        saved = json.loads(path.read_text(encoding="utf-8")).get("language")
        if saved in LANGUAGES:
            return saved
    except (OSError, ValueError, AttributeError):
        pass
    return system_language()


def save_language(language: str, path: Path | None = None) -> None:
    """Remember the choice; failures (read-only profile...) are ignored."""
    path = path or settings_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data["language"] = language
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass
