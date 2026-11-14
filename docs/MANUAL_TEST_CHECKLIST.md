# Loop Maker – manual test checklist

Run these checks by hand with **your own ambience recordings** (no third-party or copyrighted
samples). Each check lists what to do, what you should see or hear, and what means **BUG**.

## 0. Prepare a test set

Put these in a folder such as `D:\LoopMakerTest\in` (all your own recordings):

| File | What it is |
|---|---|
| `Long_Forest.wav` | 45 s or longer, stereo, 48 kHz 24-bit, steady ambience |
| `Long_Hum.wav` | 30 s or longer, a steady tonal source (fridge, AC, transformer hum) |
| `Mid_Wind.wav` | about 12 s, stereo |
| `Short_Rain.wav` | about 3 s |
| `TooShort_Tick.wav` | under 1 s |
| `Mono_Room.wav` | mono, 20 s or longer |
| `Already48k.wav` | any file that is already 48 kHz (can be one of the above) |
| `Hires_96k.wav` | a 96 kHz 24-bit or 32-bit float recording, 20 s or longer |
| `Rec_44k.flac` | a 44.1 kHz FLAC, 20 s or longer |

Tools: **ffprobe** (from FFmpeg) or **Audacity** to check file properties. With ffprobe:

```bash
ffprobe -v error -show_entries stream=codec_name,sample_rate,channels,bits_per_sample -of default=nw=1 "file.wav"
```

`codec_name` shows the bit depth for WAV: `pcm_s16le` = 16-bit, `pcm_s24le` = 24-bit,
`pcm_f32le` = 32-bit float. In Audacity, the track's info panel shows rate and format.

Start the GUI by double-clicking `Loop Maker.bat`.

---

## 1. Presets: output properties

For each row, process `Hires_96k.wav` and `Rec_44k.flac` (crossfade 2 s, variations Off),
then check every output with ffprobe/Audacity.

| Preset | Expected sample rate | Expected format | ✓ |
|---|---|---|---|
| Custom, all "source" | same as input | same bit depth as input, `.wav` | ☐ |
| Wwise | 48000 | `pcm_s24le` | ☐ |
| Unreal | 48000 | `pcm_s16le` | ☐ |
| Unity | 48000 | same bit depth as input (float input → `pcm_f32le`) | ☐ |
| Godot | 48000 | same bit depth as input | ☐ |
| Custom 44100 / 16 / FLAC | 44100 | FLAC, 16-bit, `.flac` extension | ☐ |
| Custom 96000 / 24 / WAV | 96000 | `pcm_s24le` | ☐ |
| Custom source / source / OGG | input rate | `vorbis`, `.ogg` extension | ☐ |

Also check:

- ☐ Channel count always matches the input (`channels=2` for stereo, `1` for mono).
- ☐ The log line says `TPDF dither` for Unreal and for any 24/float → 16 conversion, and
  not for a 16-bit source with the Custom/auto setting.
- ☐ Selecting a named preset fills in and greys out the Custom fields. Switching back to
  Custom makes them editable again.
- ☐ Custom + Bit depth `32f` + Format `FLAC`: the summary line turns red with
  *"FLAC cannot store 32-bit float"* and Process is disabled.
- ☐ Listen to a 96k → 48k and a 44.1k → 48k output: **same pitch and speed** as the source,
  with no dull or harsh high end.

**BUG if:** wrong rate or bit depth, changed channel count, pitch/speed shift, audible
distortion or a click at the very start or end of a resampled file, or a named preset
producing anything other than its listed format.

## 2. Loop point and crossfade (standard loop)

Process `Long_Forest.wav` and `Long_Hum.wav` with curve **auto**, safety fade 5 ms.

- ☐ The log shows `curve equal_power (auto ...)` for the forest (correlation near 0) and
  `linear (auto ...)` for the hum if its correlation is above 0.41. A hum can legitimately
  get equal power when its two ends meet out of phase: check the reported correlation.
- ☐ In Test playback, select the output and click **Play loop point**. Listen across the
  wrap (end → start) at least 5 times: **no click, no gap, no level jump**.
- ☐ Click inside the **orange crossfade region** and listen: no dip (hole) and no swell in
  level, and no "phasey" doubling on the hum.
- ☐ Repeat with safety fade `0`: the wrap should still be clean (the algorithm itself makes
  it continuous).
- ☐ Seam score in the playback list and report is high (roughly 80+) for steady material.
- ☐ Output length = input length − crossfade (check in the log line `x s -> y s`).

**BUG if:** a click or gap at the wrap, an audible dip/bulge in the orange region, the
playhead not matching what you hear, or Space/click-to-play not working.

## 3. Variations

Process `Long_Forest.wav` with **Variations 4**, crossfade 2 s, template
`AMB_{name}_{index}_loop`.

- ☐ Four files: `AMB_Long_Forest_01_loop_v01.wav` … `_v04.wav` (Output column shows
  `…_v01.wav (+3)`; hover to see all four).
- ☐ The log lists four different split points, at least 12 % of the file length apart.
- ☐ For **each** variation: Play loop point → the wrap is seamless. Click in its orange
  crossfade region → no dip or bulge. (The orange region sits at a different position in
  each variation.)
- ☐ Each variation **starts at a quiet moment**, not in the middle of a bird call, gust or
  other loud event (listen to the first second of each).
- ☐ The four variations sound like the same recording started from different points.
- ☐ Process `Mid_Wind.wav` (12 s) with Variations 3: it works, the status is **warning** and
  the tooltip says *"variations may sound nearly identical"*.
- ☐ Change "warn below" to `10`: Mid_Wind's warning disappears immediately.

**BUG if:** fewer or more files than requested, two variations identical, a variation that
clicks at its own wrap, a variation starting on a loud transient while quieter options
clearly exist, or wrong `_vNN` numbering.

## 4. Engine import

### Wwise

- ☐ Process `Long_Forest.wav` with the **Wwise** preset and import the output into a
  Wwise project (Audio File Importer into the Actor-Mixer Hierarchy).
- ☐ It imports without warnings about format or sample rate.
- ☐ Enable looping on the Sound object (check the exact option in your Wwise version) and
  play it in the Transport Control for at least three cycles: seamless.
- ☐ Optional, variations: import the four `_vNN` files into a Random Container, set it to
  continuous playback with a crossfade transition between objects (check the exact option
  names in Wwise) and listen through several switches.

### Unreal

- ☐ Process `Long_Forest.wav` with the **Unreal** preset and drag the output into the
  Content Browser.
- ☐ It imports as a Sound Wave without errors.
- ☐ Enable *Looping* on the Sound Wave, play it in the editor for several cycles: seamless.

**BUG if:** the engine rejects the file, reports an unexpected sample rate or bit depth, or
the loop clicks in the engine although it is clean in Loop Maker's Test playback.

## 5. Mixed batch: status column and "On problem"

Add the whole test folder. Set crossfade **3 s**, variations **Off**.

- ☐ Status column: long files **OK**. `TooShort_Tick.wav` and `Short_Rain.wav` are **error**.
  Hovering shows e.g. *"crossfade 3.0 s exceeds half the file length (0.40 s)"*.
- ☐ The summary line next to Process matches the column, e.g.
  *"9 files: 7 OK, 0 warnings, 2 cannot be processed"*.
- ☐ The hint next to the crossfade field shows the batch maximum (*"max 0.40 s for all
  9 files"*). Tick `Long_Forest.wav` only → the hint changes to its own maximum.
- ☐ Type a value in a row's **Crossfade** cell → the cell turns yellow and the status updates
  without pressing anything. Type `abc` → the cell turns red and the row becomes error.
  Clear it → back to the global value.
- ☐ Change the global crossfade to `0.3` → all rows re-validate immediately.

Run the batch three times with crossfade 3 s:

- ☐ **skip and log**: the long files are written, the two short ones show *skipped* with the
  reason in the log and the Progress tooltip.
- ☐ **auto-clamp**: everything is written. The log has one line per change, e.g.
  `AUTO-CLAMP Short_Rain.wav: crossfade 3.0 -> 1.5 s`, and the report shows the crossfade
  actually used. Verify one clamped output: output length = input − clamped crossfade.
- ☐ **stop the batch**: processing stops at the first error row (in table order). Later rows
  show *not run*, and the report ends with *"(batch stopped)"*.
- ☐ Progress column during a run: *queued → processing... → done (N files)*.
- ☐ **Export report...** saves a `.txt` that matches the log report.
- ☐ CLI, same folder:

  ```bash
  .venv\Scripts\python.exe -m loopmaker "D:\LoopMakerTest\in" --crossfade 3 --on-problem clamp --report report.txt
  ```

  prints the same statuses, summary line and clamp messages, and writes `report.txt`.

**BUG if:** a status that disagrees with its tooltip, a warning that blocks a file, one
error stopping the whole batch under *skip*, a clamp that is not logged, the summary line
not matching the table, or the GUI freezing during a run.

## 6. Edge cases

- ☐ **Mono and stereo:** `Mono_Room.wav` → mono output (`channels=1`), plays on both
  speakers in Test playback. Stereo stays stereo.
- ☐ **Already 48 kHz** with the Wwise preset: the log shows no rate change (`48000 Hz`, not
  `x -> 48000 Hz`), and the audio is not resampled (sounds identical apart from the loop edit).
- ☐ **Crossfade too long:** set 30 s on `Mid_Wind.wav` → error with the numbers in the tooltip.
  CLI: `--crossfade 30` prints `SKIPPED ... exceeds half the file length` and exits with code 1.
- ☐ **Bad naming templates**, one at a time in the GUI (each must turn the summary red and
  disable Process; the CLI must exit with code 2):
  - ☐ `AMB_{nme}` → unknown token
  - ☐ `AMB:{name}` → characters not allowed
  - ☐ `AMB_{name` → unbalanced braces
  - ☐ `{name}.` → cannot end with a dot
  - ☐ `CON` → reserved name
- ☐ Template `AMB_bed` (no `{name}`/`{index}`) with three files → warning text next to the
  template, and outputs `AMB_bed.wav`, `AMB_bed_2.wav`, `AMB_bed_3.wav`.
- ☐ **Preset file fallback:** rename `presets.json`, restart the GUI → the log says
  *"preset file … not found; using built-in presets"*, and the presets still work. Put a
  typo in the JSON → *"is invalid … using built-in presets"*. Restore the file.
- ☐ **Inputs are never modified:** compare an input file's size and modified time before and
  after a run.
- ☐ **Normalize loudness** on a sparse, transient-heavy recording: the playback list shows
  *PEAK-LIMITED (x dB short of …)* in orange instead of clipping.

**BUG if:** any input file changes, a bad template is accepted, mono comes out as stereo
(or the reverse), or a 48 kHz file is reported as resampled.
