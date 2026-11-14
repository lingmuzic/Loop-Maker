"""Feature A: file naming templates."""

import numpy as np
import pytest
import soundfile as sf

from loopmaker import cli, naming, pipeline


def test_example_from_the_spec():
    n = naming.Naming("AMB_{name}_{index}_loop")
    assert n.errors() == []
    assert n.render("Desert_Wind", 1) == "AMB_Desert_Wind_01_loop"


def test_all_tokens_and_variation_suffix():
    n = naming.Naming("{category}_{name}_{index}_{suffix}", category="SFX", suffix="lp")
    assert n.render("Rain Heavy", 7, batch_size=12, variation=3) == "SFX_Rain Heavy_07_lp_v03"


def test_index_padding_grows_for_big_batches():
    n = naming.Naming("{name}_{index}")
    assert n.render("a", 5, batch_size=99) == "a_05"
    assert n.render("a", 5, batch_size=150) == "a_005"


def test_default_template_keeps_old_names():
    assert naming.Naming().render("rain", 1) == "rain_loop"


@pytest.mark.parametrize(
    "template, fragment",
    [
        ("", "empty"),
        ("{nme}_loop", "unknown token {nme}"),
        ("AMB_{name", "unbalanced"),
        ("AMB}_{name}", "unbalanced"),
        ("AMB:{name}", "not allowed"),
        ("sub/{name}", "not allowed"),
        ("{name}?", "not allowed"),
        ("{name}.", "space or a dot"),
        ("{name} ", "space or a dot"),
    ],
)
def test_invalid_templates_are_rejected(template, fragment):
    errors = naming.template_errors(template)
    assert any(fragment in e for e in errors), errors


def test_illegal_category_and_suffix_are_rejected():
    errors = naming.Naming("{category}_{name}_{suffix}", category="A|B", suffix="x*").errors()
    assert len(errors) == 2


def test_reserved_windows_name_rejected_at_render():
    with pytest.raises(ValueError, match="reserved"):
        naming.Naming("CON").render("x", 1)


def test_template_without_name_or_index_warns():
    assert naming.Naming("AMB_loop").warnings()
    assert naming.Naming("AMB_{index}").warnings() == []


def test_batch_indexing_and_collision_handling(tmp_path):
    sr = 48_000
    files = []
    for stem in ("wind", "rain", "creek"):
        path = tmp_path / f"{stem}.wav"
        sf.write(str(path), np.random.default_rng(0).standard_normal(sr * 2) * 0.1, sr)
        files.append(path)

    settings = pipeline.LoopSettings(crossfade_s=0.5, out_dir=tmp_path / "out",
                                     naming=naming.Naming("AMB_{name}_{index}_loop"))
    names = [r.outputs[0].output_path.name for _, r in pipeline.process_batch(files, settings)]
    assert names == ["AMB_wind_01_loop.wav", "AMB_rain_02_loop.wav", "AMB_creek_03_loop.wav"]

    # No {name}/{index}: still processes, later files get _2, _3.
    settings = pipeline.LoopSettings(crossfade_s=0.5, out_dir=tmp_path / "o2", naming=naming.Naming("AMB_bed"))
    names = [r.outputs[0].output_path.name for _, r in pipeline.process_batch(files, settings)]
    assert names == ["AMB_bed.wav", "AMB_bed_2.wav", "AMB_bed_3.wav"]


def test_cli_preview_and_bad_template(tmp_path, capsys):
    src = tmp_path / "Desert_Wind.wav"
    sf.write(str(src), np.random.default_rng(0).standard_normal(48_000 * 2) * 0.1, 48_000)
    out = tmp_path / "out"

    assert cli.main([str(src), "--template", "AMB_{name}_{index}_loop", "--out", str(out), "--dry-run"]) == 0
    assert "AMB_Desert_Wind_01_loop.wav" in capsys.readouterr().out
    assert not out.exists()  # dry run writes nothing

    assert cli.main([str(src), "--template", "AMB_{nme}", "--out", str(out)]) == 2
    assert "unknown token" in capsys.readouterr().err
