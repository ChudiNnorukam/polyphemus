import json
from pathlib import Path

from polyphemus.tools import quant_experiment_scaffold as qes


def test_slugify_normalizes_titles():
    assert qes.slugify("BTC Cheap Side v4") == "btc-cheap-side-v4"
    assert qes.slugify("XRP / 5m,15m FAK") == "xrp-5m-15m-fak"


def test_render_template_replaces_tokens(tmp_path, monkeypatch):
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    (template_dir / "quant_hypothesis.md").write_text("Asset <asset> windows <windows>\n", encoding="utf-8")
    monkeypatch.setattr(qes, "TEMPLATES_DIR", template_dir)

    rendered = qes.render_template("quant_hypothesis.md", {"asset": "BTC", "windows": "5m"})

    assert rendered == "Asset BTC windows 5m\n"


def test_scaffold_creates_expected_files(tmp_path, monkeypatch):
    templates_dir = tmp_path / "templates"
    templates_dir.mkdir()
    for name in ["quant_hypothesis.md", "quant_evidence_log.md", "quant_promotion_review.md"]:
        (templates_dir / name).write_text(f"{name} <slug> <asset> <windows> <entry_family>\n", encoding="utf-8")

    experiments_dir = tmp_path / "experiments"
    monkeypatch.setattr(qes, "TEMPLATES_DIR", templates_dir)
    monkeypatch.setattr(qes, "EXPERIMENTS_DIR", experiments_dir)
    monkeypatch.setattr(
        "sys.argv",
        [
            "quant_experiment_scaffold.py",
            "--slug",
            "BTC Cheap Side v4",
            "--asset",
            "BTC",
            "--windows",
            "5m",
            "--entry-family",
            "cheap_side",
        ],
    )

    rc = qes.main()

    assert rc == 0
    experiment_dir = experiments_dir / "btc-cheap-side-v4"
    assert experiment_dir.exists()
    assert (experiment_dir / "hypothesis.md").exists()
    assert (experiment_dir / "evidence_log.md").exists()
    assert (experiment_dir / "promotion_review.md").exists()
    meta = json.loads((experiment_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["asset"] == "BTC"
    assert meta["entry_family"] == "cheap_side"
