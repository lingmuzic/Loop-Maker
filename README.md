# Loop Maker

> Built with AI assistance: I designed the tool and specified the algorithm from my own sound design workflow, and Claude Code wrote the code under my direction. See [Credits](#credits).

**[⬇ Download for Windows](../../releases/latest)**: no Python needed. Unzip, then run `Loop Maker.exe` ([details](#standalone-windows-app-no-python-needed)).

English | [繁體中文](README.zh-TW.md)

Turn ambience recordings into **seamlessly loopable** audio files, the same way a
sound designer does it by hand in a DAW, but in batch, with a quality check.

![Before/after waveform](docs/before_after.png)

*Synthetic example (filtered noise with a level drift; see
[`scripts/make_readme_figure.py`](scripts/make_readme_figure.py)). Top: the original
file. Middle: the Loop Maker output. Bottom: what you hear at the loop point when
each file wraps around from its end back to its start.*

## Why

Game audio engines loop ambience beds constantly. If a recording is looped as-is, its
end and start rarely match, which causes a click (a sample jump) and often an audible
change in level or texture every time the loop wraps. The classic manual fix is the
"swap halves and crossfade" edit. Loop Maker automates it.

## How it works

```
original:   [ A ......... | B ......... ]         (split at the midpoint)
swapped:    [ B ......... | A ......... ]         (old end now meets old start in the middle)
crossfade:  [ B ....  \/\/\/  .... A ]            (overlap B's tail with A's head)
```

1. Split the file in half: **A** (first half) and **B** (second half).
2. Swap them: **B | A**. The new file **starts** at the old midpoint and **ends** just
   before it. Those two samples were neighbours in the original recording, so the
   new loop point is naturally continuous: no edit happens there at all.
3. The only seam is now in the middle, where the old end meets the old start. It is
   hidden with a crossfade: the last *C* seconds of B fade out while the first *C*
   seconds of A fade in. Because those regions overlap, **the output is *C* seconds
   shorter than the input**.
4. A tiny optional safety fade (default 5 ms) is applied at the very start and end.

### Crossfade curves

| Curve | Gains | Use for |
|---|---|---|
| `auto` (default) | picks one of the two below per file | Any material; see below. |
| `equal_power` | sin / cos, `out² + in² = 1` | Uncorrelated material: ambience, room tone, wind, rain. Keeps perceived loudness constant. |
| `linear` | straight ramps, `out + in = 1` | Correlated material (e.g. steady tones/hums), where equal power would bump the level by up to +3 dB. |

#### How `auto` decides

Using the wrong curve makes the middle of the crossfade audibly **dip** (linear on
noise, about −3 dB) or **bulge** (equal power on a steady tone, about +3 dB). `auto`
measures the **zero-lag correlation *r*** between the two signals that will actually be
crossfaded: the file's end and the file's start, sample-aligned exactly as they will
overlap. The measurement is Hann-weighted toward the centre of the crossfade, where both
gains are large, and pooled across channels so every channel gets the same curve.

At the centre of the crossfade the summed power relative to the input is `1 + r` for
equal power and `(1 + r) / 2` for linear. The two errors are equal in dB when
`(1 + r)² = 2`, so the threshold is **r = √2 − 1 ≈ 0.41**: above it `linear`, below it
`equal_power`.

It measures correlation rather than asking "is this tonal?" on purpose: two hums that
meet 90° out of phase are tonal but add like noise, so equal power is the right call
for them, and the measurement gets that right. The chosen curve and *r* appear in every
report.

### Loudness normalization (batch)

With `--normalize -24` (or the GUI checkbox), every output in the batch is brought to the
same **integrated loudness** (ITU-R BS.1770-4 / EBU R128, via
[pyloudnorm](https://github.com/csteinmetz1/pyloudnorm)), so a set of ambience beds
arrives in Wwise/FMOD/engine already level-matched.

- Measured on the **finished loop**, the exact audio that will play in the game.
- One static gain per file. No limiter or compressor touches the sound.
- **True-peak ceiling** (default −1 dBTP, estimated with 4x oversampling): if reaching the
  target would exceed it, the gain stops at the ceiling and the report says how many dB
  the file fell short. To keep such a file consistent with the rest, lower the batch
  target.
- 5.1 files are measured without the LFE channel, as BS.1770 specifies. Layouts with more
  than 6 channels, silent files, and files shorter than 400 ms are written un-normalised
  with a note.
- Off by default. The −23 LUFS default target in the GUI is the EBU R128 reference.
  Pick whatever your project's asset loudness spec says.

### Loop quality report

After each file, Loop Maker prints a small report comparing the **original** (as if
it were looped untouched) with the **output**. Both are measured before loudness
normalisation, so their levels are directly comparable:

```
wind_quiet.wav -> output\wind_quiet_loop.wav
  48000 Hz, 2 ch, PCM_24 -> PCM_24, 12.000 s -> 10.000 s
  Crossfade curve: equal_power (auto; end/start correlation +0.02, threshold 0.41)
  Loudness: -31.8 -> -24.0 LUFS (gain +7.8 dB, true peak -10.4 dBTP)
  Loop-point check (window 50 ms): SMOOTH
  original  jump 0.03541 (40.19x typical step)   RMS head/tail  -32.5 /  -32.3 dBFS (diff -0.23 dB)
  loop      jump 0.00000 ( 0.00x typical step)   RMS head/tail  -32.6 /  -31.3 dBFS (diff -1.38 dB)
```

- **jump**: `|first sample - last sample|` (max over channels), and that value divided by
  the file's *typical step* (99th percentile of sample-to-sample differences). ≤ 1x means
  the wrap is no bigger than an ordinary step inside the recording.
- **RMS head/tail**: level of the first and last 50 ms (configurable). A large difference
  means an audible level jump at the wrap.
- **Verdict**: `SMOOTH` when jump ratio ≤ 1.0 and |RMS diff| ≤ 2 dB, otherwise
  `CHECK BY EAR`. These are simple heuristics, not a perceptual model.

## Install

Requires Python 3.10+ (developed on 3.12, Windows 11).

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```

```bash
pip install -r requirements.txt
```

`tkinterdnd2` and `sounddevice` are optional; without them the GUI falls back to the
*Add files* / *Add folder* buttons and a waveform-only test area. `matplotlib` and `pytest` are only needed for the figure and tests.

## Standalone Windows app (no Python needed)

For people without Python there is a ready-to-run build: `LoopMaker-<version>-win64.zip`.

1. Unzip it anywhere you can write to (Desktop, Documents; not `Program Files`).
2. Open the `Loop Maker` folder and double-click **`Loop Maker.exe`**. Keep the whole folder
   together: the exe needs the `_internal` folder next to it.
3. The app is not code-signed, so on first launch Windows SmartScreen may show *"Windows
   protected your PC"*. Click **More info → Run anyway**.

`presets.json` sits next to the exe and can be edited there. The default output folder is
`output` next to the exe. Requires Windows 10/11, 64-bit. Only the GUI is included; the CLI
still needs Python.

**Troubleshooting:** `"Loop Maker.exe" --selftest selftest.txt` checks every bundled component
(audio file I/O, resampling, loudness, playback device, drag and drop, a full batch run) and
writes the result to `selftest.txt`.

### Building it yourself

```bash
.venv\Scripts\python.exe -m pip install -r requirements-build.txt
```

```bash
.venv\Scripts\python.exe scripts\build_exe.py
```

This creates `dist\Loop Maker\` and `dist\LoopMaker-<version>-win64.zip` with PyInstaller.
It uses a one-folder build rather than one-file, because a single exe would unpack ~150 MB on
every launch (slow start) and gets flagged by antivirus tools more often. The script runs the
built exe's `--selftest` and fails if any check fails, so a broken bundle is never zipped.
The bundle is about 140 MB unpacked (58 MB zipped), mostly scipy and numpy.

## Usage

### Command line

```bash
python -m loopmaker rain.wav forest.flac --crossfade 3.0 --curve equal_power --out output
```

```bash
python -m loopmaker "D:\Recordings\Ambience"
```

| Option | Default | Meaning |
|---|---|---|
| `inputs` | (required) | Files and/or folders. Folders are scanned non-recursively for `.wav .flac .aif .aiff .ogg`. |
| `--crossfade SECONDS` | `2.0` | Crossfade length. Must be > 0 and ≤ half the file length. |
| `--curve` | `auto` | `auto`, `equal_power` or `linear`. |
| `--normalize LUFS` | off | Bring every output to this integrated loudness, e.g. `-24`. |
| `--peak-ceiling DBTP` | `-1` | True-peak ceiling for `--normalize`. |
| `--safety-fade MS` | `5` | Start/end fade in milliseconds; `0` disables it. |
| `--out FOLDER` | `output` | Output folder (created if needed). |
| `--preset NAME` | `Custom` | Output preset: `Custom`, `Wwise`, `Unreal`, `Unity`, `Godot`, or one from `presets.json`. |
| `--sample-rate`, `--bit-depth`, `--format`, `--dither` | source / source / WAV / auto | Custom preset fields. |
| `--presets JSON` | `presets.json` | Preset file to load. |
| `--template`, `--category`, `--suffix` | `{name}_{suffix}`, `AMB`, `loop` | File naming. |
| `--variations N` | `0` (off) | Write N = 3..5 rotated loop variations per input. |
| `--min-spacing PCT` | `12` | Minimum distance between variation split points, % of file length. |
| `--short-warning SECONDS` | `15` | Warn that variations may sound alike below this length. |
| `--on-problem` | `skip` | `skip` (skip and log), `clamp` (auto-clamp), `stop` (stop the batch). See *Batch validation*. |
| `--report TXT` | off | Also write the batch report to a text file. |
| `--dry-run` | off | Validate and preview output names, write nothing. |
| `--report-window MS` | `50` | Window used by the quality report. |

Before processing, the CLI prints every file's status (OK / warning / error, with reasons)
and planned output names, then a summary line such as
`12 files: 9 OK, 2 warnings, 1 cannot be processed`. After processing it prints the batch
report. Exit code: `0` when every file was written, `1` if any was skipped, failed or not
run, `2` for invalid settings (bad preset, template ...).

### GUI

On Windows, double-click **`Loop Maker.bat`** in the project folder (it starts the GUI
from the project's `.venv` without a console window). Or from a terminal:

```bash
.venv\Scripts\python.exe -m loopmaker.gui
```

Drag files or folders onto the **Files** table (or use the buttons), set the **Global
settings**, check the status column and the summary line, then press **Process**. The GUI
uses exactly the same `batch.validate_batch()` / `batch.run_batch()` as the CLI.

**Files table.** One row per file:

| Column | Meaning |
|---|---|
| ☐ | Select rows for *Remove selected* and for the max-crossfade hint |
| File, Length, Format | Read from the file header (hover the name for the full path) |
| **Crossfade**, **Var.** | Per-file overrides. Empty = global setting. An overridden cell turns **yellow**; an invalid value turns **red** and makes the row an error |
| Output | First output name from the template (`+3` = more variations; hover for all) |
| **Status** | **OK** / **warning** / **error**. Hover for the reasons, e.g. *"crossfade 3.0 s exceeds half the file length (1.00 s)"* |
| Progress | queued → processing... → done (N files) / skipped / failed / not run (hover for details) |

- **Live validation:** every change to a global setting or an override re-validates all rows
  within ~150 ms. Nothing waits for the Process button.
- **Max crossfade hint** next to the crossfade field: the longest crossfade that suits all
  files, or only the ticked ones (*"max 1.00 s for 2 selected"*). It accounts for variations.
- **Summary line** next to Process: *"12 files: 9 OK, 2 warnings, 1 cannot be processed"*.
- After the run, each row shows its result, every output appears in *Test playback*, the
  log holds the full report, and **Export report...** saves it as a text file.

**Test playback.** After processing, every output appears in the *Test playback* list
with its verdict. Select one to see its waveform; the crossfade region is highlighted
in orange.

| Action | Effect |
|---|---|
| **Space** or **Play/Stop** | Start/stop looping playback (resumes where it stopped) |
| **Left-click the waveform** | Play from that point (jumps there if already playing) |
| **Play loop point** | Start 2 s before the end, so you hear the wrap straight away |
| **Open output folder** | Show the files in Explorer |

Playback wraps from the last sample straight into the first, exactly like a game
engine's looping voice, so what you hear is a true test of the loop point. Mono files
play on both speakers; files with more channels than the output device are folded to
mono for auditioning only (the written file is untouched). Playback needs the optional
`sounddevice` package; without it the waveform still works.

**Language.** The **Language / 語言** dropdown next to Process switches the interface
between **English** and **繁體中文 (Traditional Chinese)** instantly, without a restart and
without changing any settings. The choice is remembered in
`%APPDATA%\Loop Maker\settings.json`. On first launch, Windows set to a Traditional Chinese
display language (zh-TW / zh-HK / zh-MO) starts in Chinese, anything else in English.

- Validation messages (status tooltips, the summary line, settings problems) are produced in
  English by the UI-free core and translated for display by a pattern catalogue in
  `loopmaker/i18n.py`, so the core stays language-free. A test generates every kind of core
  message and fails if one has no Chinese translation. Any message the catalogue does not
  know is shown in English rather than failing.
- The **log and the exported batch report stay in English** on purpose: they match the CLI
  output and are meant to be shared with a team.
- Technical names stay as they are in both languages: curve names (`auto`, `equal_power`,
  `linear`), preset names, *Crossfade*, *Variations*, LUFS/dBTP.

### As a library

```python
import soundfile as sf
from loopmaker.core import make_loop

audio, sr = sf.read("wind.wav")          # (frames,) or (frames, channels)
looped = make_loop(audio, sr, crossfade_s=3.0, curve="equal_power", safety_fade_s=0.005)
```

## Output presets

Choose a preset in the GUI's **Preset** dropdown or with `--preset NAME`:

| Preset | Sample rate | Bit depth | Format | Dither |
|---|---|---|---|---|
| **Custom** (default) | source / 44.1 / 48 / 96 kHz | source / 16 / 24 / 32-bit float | WAV / FLAC / OGG | auto |
| **Wwise** | 48 kHz | 24-bit | WAV | auto |
| **Unreal** | 48 kHz | 16-bit | WAV | always |
| **Unity** | 48 kHz | source | WAV | auto |
| **Godot** | 48 kHz | source | WAV | auto |

```bash
python -m loopmaker recordings\ --preset Wwise
```

```bash
python -m loopmaker rain.wav --preset Custom --sample-rate 44100 --bit-depth 16 --format FLAC
```

- **Custom with all fields on "source"** reproduces the old behaviour: same rate, same
  bit depth, WAV.
- **Unity and Godot** only specify 48 kHz WAV, so they keep the source bit depth. Check what
  your engine version imports (e.g. 24-bit WAV support) and edit the preset if needed.
- **OGG** (Vorbis) is offered only when the installed libsndfile can write it. It is lossy,
  so bit depth does not apply. **FLAC** stores 16/24-bit only; FLAC + 32-bit float is
  rejected with a clear message.
- The CLI's `--sample-rate`, `--bit-depth`, `--format` and `--dither` only apply with
  `--preset Custom`. Combining them with a named preset is an error rather than a silent
  override, so a "Wwise" file is always exactly the Wwise format.

### Resampling

When the preset changes the sample rate, the **source is resampled before looping** with a
polyphase filter (`scipy.signal.resample_poly`, exact rational ratio such as 160/147 for
44.1 → 48 kHz, Kaiser window β = 9, about 90 dB stop-band attenuation). Resampling first
matters: the filter's edge transients land on the original file start and end, which end
up inside the crossfade at near-zero gain, so the loop point stays clean. All lengths
(crossfade, output) are then in the new rate.

### Dither

Integer 16/24-bit output is quantised by Loop Maker itself (not left to libsndfile), with
optional **TPDF dither** (±1 LSB triangular noise). Dither modes:

- `auto` (default): dither only when the bit depth is **reduced**: 24 → 16, float → 24, and so on.
  A 16-bit source written as 16-bit is rounded without dither. That follows the "reduce" rule
  literally, even though crossfading and gain technically requantise too.
- `always`: dither every 16/24-bit output (used by the Unreal preset).
- `never`: plain rounding.

Dither noise is seeded from the output file name, so the same input and settings produce
byte-identical files, which avoids noisy diffs when assets live in version control.
Overloads are counted *before* dither and reported; dither itself can never wrap a sample
around.

### Preset file (`presets.json`)

The named presets are stored in [`presets.json`](presets.json) in the project folder, which
you can edit or extend:

```json
{
  "version": 1,
  "presets": {
    "Wwise": {"sample_rate": 48000, "bit_depth": "24", "format": "WAV", "dither": "auto",
              "description": "48 kHz, 24-bit WAV"}
  }
}
```

`sample_rate` and `bit_depth` may be `"source"`. If the file is missing or not valid JSON, the
built-in presets are used and a warning is shown in the GUI log or on stderr. A single invalid
entry is skipped (and named in the warning) while the rest still load. `Custom` is
reserved. Use `--presets other.json` to load a different file.

## File naming

The **Name template** field and `--template` build each output's name from tokens:

| Token | Value |
|---|---|
| `{name}` | input file name without extension |
| `{category}` | the Category field / `--category` (default `AMB`) |
| `{index}` | position in the batch: `01`, `02` ... (3 digits for 100+ files) |
| `{suffix}` | the Suffix field / `--suffix` (default `loop`) |

```bash
python -m loopmaker "Desert_Wind.wav" --template "AMB_{name}_{index}_loop"
```

That command writes `AMB_Desert_Wind_01_loop.wav`. The default template `{name}_{suffix}`
gives the familiar `<name>_loop.wav`.

- **Validation:** unknown tokens, unbalanced braces, characters Windows forbids
  (`< > : " / \ | ? *`), names ending in a space or dot, and reserved names such as `CON`
  are rejected before anything is processed. The GUI shows the reason in red under the
  template field, and the CLI exits with code 2.
- **Preview:** the GUI shows the resulting names live. The CLI prints every
  `input -> output` name before processing, and `--dry-run` stops after that preview.
- `{name}` keeps the original spelling (spaces included); only illegal characters become `_`.
- A template without `{name}` or `{index}` gets a warning. Colliding names in a batch get
  `_2`, `_3` ... appended so nothing is overwritten.

## Loop variations

**Variations: N** (GUI) or `--variations N` (CLI, N = 3 to 5) writes N loops per input
instead of one, named with `_v01`, `_v02` ... after the template
(e.g. `AMB_Desert_Wind_01_loop_v02.wav`).

```bash
python -m loopmaker "Desert_Wind.wav" --variations 4 --template "AMB_{name}_{index}_loop"
```

**What a variation is.** Instead of splitting at the midpoint, each variation splits the
source at a different point and applies the same swap + crossfade. Mathematically every
variation is the **same loop rotated** (a test checks this with `np.roll`): the crossfade
blends the same original end and start, and only the moment where the file begins and
wraps moves. Each variation loops seamlessly on its own.

**How split points are chosen** (`loopmaker/variations.py`):

1. Short-window RMS (50 ms windows, 10 ms hop) of a mono fold, smoothed over 200 ms.
2. The loudest transients are fenced off. Windows above the 95th percentile *and* 6 dB
   over the median, plus 300 ms either side, are excluded, so a variation never starts in
   the middle of a hit.
3. Candidates must leave a crossfade's length on both sides, stay off the midpoint (that is
   the standard loop), and be at least the **minimum spacing** apart: default **12 % of the
   file length**, set with `--min-spacing PCT` or the GUI field.
4. Valleys (local minima) of the smoothed energy are taken quietest first. If the spacing
   rules leave too few, other allowed positions fill in, so a file that passes the length
   check always gets its N variations.

**Length rules:**

- **Error** if the source is not longer than `2 × crossfade + N × minimum spacing`. The
  message shows the numbers, e.g. *"too short for 5 variations: needs more than 2 x 2 s
  crossfade + 5 x 1.2 s minimum spacing (12% of the file) = 10.0 s, but the file is 10.0 s"*.
- **Warning** (still processed) below **15 s** (`--short-warning SECONDS` or the GUI field):
  *"variations may sound nearly identical"*. This threshold is a rule of thumb.
- In practice, variations are most useful for sources of **roughly 30 s or longer**. That is
  also a rule of thumb, not a standard.

### Using variations in a game engine (read this)

Variations are the same material rotated, so **the end of one variation does NOT connect
naturally to the start of another**. Only each file's own wrap (its end into its own start)
is seamless. Playing `_v01` and then `_v03` back to back without a transition will jump.

In Wwise, put the variations in a **Random Container set to continuous playback, with a
crossfade transition between objects**, so each switch is covered by a crossfade. The
exact option names and where they live in the Property Editor depend on your Wwise
version: **check in Wwise** rather than relying on the wording here. The same idea applies
in other engines: switch between variations only through a crossfade.

## Batch validation and "On problem"

Every file is validated **separately and before anything is written** (from its header
only, so it is instant). Checks include: unreadable file, crossfade longer than half the
file, too short for the requested variations, short-source variation warning, files too
short or too wide for loudness normalisation, output-name collisions, an output that would
overwrite its input, and invalid per-file overrides.

| Status | Meaning |
|---|---|
| **OK** | Nothing to report |
| **warning** | Will be processed. Warnings **never block** processing |
| **error** | Cannot be processed as configured. Blocks **only that file**, never the batch (except with *stop*) |

**On problem** (GUI dropdown, `--on-problem`):

- **skip and log** (default): files with errors are skipped and the reason is logged; the rest
  of the batch runs.
- **auto-clamp**: fix what can be fixed and log **every** change, e.g.
  `Rain_short.wav: crossfade 3.0 -> 1.0 s`. The crossfade is shortened to the longest value the
  file allows. With variations, the crossfade is shortened first so the requested variations
  are kept. Only if that would leave less than 50 ms is the variation count reduced (down
  to "off", the standard loop). Clamped files show as **warning**. Things that cannot be
  clamped (unreadable file, invalid override) stay errors and are skipped.
- **stop the batch**: process files in order and stop at the first file that cannot be
  processed or that fails while processing. Later files are reported as *not run*.

A file that passes validation but still fails while processing (e.g. it was deleted
meanwhile) is reported as *failed*; under *skip* and *auto-clamp* the batch continues.

**Batch report** (log, CLI output, *Export report...*, `--report`): the settings, the
pre-run summary, and per file: status, crossfade actually used, variations, each output
path with its **seam score**, curve and loudness, clamp changes and warnings, or the
reason it was skipped/failed.

### Seam score

A single 0-100 heuristic per output (not a perceptual model), shown in reports and in the
Test playback list. It starts at 100 and subtracts:

- 25 points per unit the **wrap jump** exceeds an ordinary sample step,
- 10 points per dB of **head/tail level difference** beyond 1 dB,
- 10 points per dB of **crossfade dip/bulge** beyond 1 dB. This is the level of the centre of the
  crossfade compared with the audio just before and after it, which catches a wrong curve
  (about ±3 dB).

The 1 dB allowance covers the natural level wobble of ambience between neighbouring windows.

## Output files

- Written to the output folder with the name from the template. **The input is never
  overwritten** (this is checked explicitly before writing).
- Channel count is always preserved. Sample rate and bit depth are preserved unless a preset
  changes them.
- With bit depth on "source": PCM 8/16/24/32-bit and 32/64-bit float inputs keep their
  subtype. Inputs with no lossless equivalent in the chosen format (e.g. Ogg Vorbis → WAV,
  float → FLAC) are written as 24-bit PCM, and the report says so.

## Design decisions and defaults

Choices I made where the spec left room. They are easy to change.

- **Default output is WAV**, even for FLAC input, because WAV is what game engines and
  middleware import most directly. FLAC and OGG are available through the Custom preset.
- **Default crossfade is 2.0 s.** Long enough to hide the seam in most ambience,
  short enough to work on files of 4 s and up.
- **Crossfade must be > 0.** A 0 s crossfade would leave a hard cut in the middle,
  which defeats the purpose.
- **Odd-length files**: the extra sample goes to B, so the crossfade limit is
  `floor(frames / 2)` samples.
- **Existing outputs are overwritten** (re-running with new settings is the common
  case). Within one batch, name collisions (e.g. `rain.wav` and `rain.flac`) get
  `_loop_2`, `_loop_3`, ...
- **Folders are scanned non-recursively**, so the output folder is never picked up as
  input by accident.
- **Clipping**: an equal-power crossfade of correlated material can peak above 0 dBFS.
  For integer PCM output the samples are clipped (instead of letting them wrap around)
  and a warning is printed. Float output is written unclipped.
- **One bad file never stops a batch**; it is reported and the rest continue.

### A note on the safety fade

The safety fade guards against clicks when a file is played *once* or trimmed by an
engine. When the file is looped, it also creates a tiny dip (2 × 5 ms by default) at
the loop point, because both ends are faded to zero. That is usually inaudible under
ambience, but for a perfectly sample-continuous loop set `--safety-fade 0`: the
algorithm already makes the wrap continuous on its own. With the fade on, the report's
"jump" is always 0 (both ends are 0), so the RMS comparison is the more informative
number. The tests check continuity with the fade disabled.

## Project layout

```
loopmaker/
  core.py       pure numpy DSP: make_loop(), crossfade curves, safety fades
  quality.py    loop-point measurements and report formatting
  loudness.py   BS.1770 loudness, true peak, normalisation gain
  convert.py    resampling and TPDF-dithered quantisation (pure DSP)
  presets.py    output presets, presets.json loading with built-in fallback
  naming.py     file naming templates: validation and rendering
  variations.py split-point selection (RMS valleys, spacing, length checks)
  audio_io.py   soundfile read/write, subtype mapping, input collection
  pipeline.py   file-level processing: resample, loop(s), measure, write
  batch.py      per-file validation, On-problem policy, batch run + report
  preview.py    waveform peaks + seamless looping playback (no UI code)
  cli.py        argparse front-end   (python -m loopmaker)
  gui.py        Tkinter main window  (python -m loopmaker.gui)
  gui_table.py  file table widget: overrides, status, tooltips, progress
  gui_playback.py  Test playback panel: output list, waveform, audition
  paths.py      app folder / bundled resources, from source or as .exe
  selftest.py   self-test of all bundled components (--selftest)
  i18n.py       GUI translations (English / Traditional Chinese)
assets/         app icon (generated by scripts/make_icon.py)
tests/          pytest suite using synthetic noise and sine signals
scripts/        README figure, icon generator, exe entry point and build script
```

## Tests

```bash
python -m pytest
```

The suite uses only synthetic audio (seeded noise, sines, DC) and checks output length,
continuity at the wrap point, the too-long-crossfade error, multichannel behaviour,
format preservation, presets/dither/resampling, naming templates, the preset-file fallback,
loop variations (spacing, rotation, seamless wraps, length errors), per-file validation and
the On-problem policies, batch reports and the CLI.

## Credits

Built with AI assistance: the code was written with
[Claude Code](https://claude.com/claude-code) (Anthropic), with me directing the design,
specifying the algorithm and requirements from my own sound design workflow, and
reviewing the results.

## License

[MIT](LICENSE)
