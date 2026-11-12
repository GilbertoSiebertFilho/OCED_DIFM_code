# -*- coding: utf-8 -*-
"""The command line: the text report, the --json document another program reads, and the exit codes."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import oced_clean
from oced_clean import cli


def _env():
    """The environment of a child Python that must find this package the way the tests did."""
    env = dict(os.environ)
    src = os.path.dirname(os.path.dirname(os.path.abspath(oced_clean.__file__)))
    env["PYTHONPATH"] = os.pathsep.join([src] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def test_clean_prints_the_report(field, tmp_path, capsys):
    rc = cli.main(["clean", field["shp"], "--boundary", field["boundary"], "--out", str(tmp_path / "run")])
    out = capsys.readouterr().out
    assert rc == 0
    assert "S3   Moisture" in out and "S4   Speed" in out and "S5a  Dry yield, pass 1" in out
    assert "clean file" in out and "_clean.zip" in out and "summary" in out


def test_python_m_json_is_one_document_on_stdout(field, tmp_path):
    p = subprocess.run([sys.executable, "-m", "oced_clean", "clean", field["shp"], "--boundary", field["boundary"],
                        "--out", str(tmp_path / "run"), "--json"], capture_output=True, text=True, encoding="utf-8",
                       env=_env())
    assert p.returncode == 0, p.stderr
    d = json.loads(p.stdout)                               # nothing but the document on standard output
    assert d["ok"] is True and os.path.isfile(d["clean_zip"]) and d["yield_column"] == "Yld_Vol_Dr"
    assert d["summary"]["cleaning"]["readings_kept"] == d["summary"]["result"]["readings"]
    assert "S3" in p.stderr                                # the progress lines went to standard error


def test_quiet_json_says_nothing_else(field, tmp_path, capsys):
    rc = cli.main(["clean", field["shp"], "--boundary", field["boundary"], "--out", str(tmp_path / "run"), "--json",
                   "--quiet", "--cleanup", "--group-by", "field", "--tag", "job42"])
    cap = capsys.readouterr()
    d = json.loads(cap.out)
    assert rc == 0 and cap.err == ""
    assert os.path.basename(d["clean_zip"]).startswith("job42") and sorted(os.listdir(tmp_path / "run")) == ["output"]


def test_grower_weight_and_the_yield_for_difm(field, field_run, tmp_path, capsys):
    """A made-up grower's yield 10 % above the monitor's average: the adjusted file by default, the monitor one asked
    for."""
    weight = ["--grower-weight", f"{1.10 * field_run.summary['result']['average_area_weighted']:.2f}",
              "--weight-unit", "bu/ac", "--weight-basis", "dry"]
    base = ["clean", field["shp"], "--boundary", field["boundary"], "--quiet"]
    rc = cli.main([*base, "--out", str(tmp_path / "adjusted"), "--json", *weight])
    d = json.loads(capsys.readouterr().out)
    assert rc == 0 and d["difm_yield"] == "adjusted" and d["yield_column"] == "Yld_Level"
    assert d["difm_zip"].endswith("_sms3s_clean_adjusted_DIFM.zip") and os.path.isfile(d["difm_zip"])
    assert d["summary"]["adjustment"]["grower_weight"]["unit"] == "bu/ac"
    rc = cli.main([*base, "--out", str(tmp_path / "monitor"), "--difm-yield", "monitor", *weight])
    out = capsys.readouterr().out
    assert rc == 0 and "grower's weight" in out and "level factor" in out
    assert "_sms3s_clean_DIFM.zip" in out and "monitor yield (Yld_Vol_Dr of the clean file)" in out


def test_adjustment_options_are_checked(field, tmp_path, capsys):
    rc = cli.main(["clean", field["shp"], "--boundary", field["boundary"], "--out", str(tmp_path / "run"), "--json",
                   "--difm-yield", "adjusted"])
    cap = capsys.readouterr()
    assert rc == 2 and "needs an adjustment" in json.loads(cap.out)["error"] and not (tmp_path / "run").exists()
    try:
        cli.main(["clean", field["shp"], "--boundary", field["boundary"], "--out", str(tmp_path / "run"),
                  "--combine-factor", "pairs"])
    except SystemExit as e:                                # argparse: not one of the methods
        assert e.code == 2
    else:
        raise AssertionError("a combine-factor method that does not exist must be refused")
    assert "--combine-factor" in capsys.readouterr().err


def test_input_problem_is_exit_code_2(field, tmp_path, capsys):
    rc = cli.main(["clean", str(tmp_path / "missing.zip"), "--no-boundary", "--out", str(tmp_path / "run"), "--json"])
    cap = capsys.readouterr()
    d = json.loads(cap.out)
    assert rc == 2 and d["ok"] is False and "not found" in d["error"] and cap.err.startswith("error: ")


def test_several_datasets_are_listed_in_the_json_error(field, combines, tmp_path, capsys):
    rc = cli.main(["clean", field["shp"], combines["shp"], "--boundary", field["boundary"], "--out",
                   str(tmp_path / "run"), "--json", "--quiet"])
    d = json.loads(capsys.readouterr().out)
    assert rc == 2 and d["error_type"] == "SeveralDatasetsError" and len(d["datasets"]) == 2


def test_boundary_must_be_given_or_refused_on_purpose(field, tmp_path, capsys):
    try:
        cli.main(["clean", field["shp"], "--out", str(tmp_path / "run")])
    except SystemExit as e:                                # argparse: neither --boundary nor --no-boundary
        assert e.code == 2
    else:
        raise AssertionError("a run without --boundary and without --no-boundary must be refused")
    assert "--boundary" in capsys.readouterr().err


def test_inspect(cn1_card, tmp_path, capsys):
    rc = cli.main(["inspect", cn1_card["root"], "--out", str(tmp_path / "look"), "--boundary", cn1_card["boundary"],
                   "--json", "--quiet"])
    d = json.loads(capsys.readouterr().out)
    assert rc == 0 and d["ok"] is True and d["datasets"][0]["format"] == "cnh_cn1"
    assert len(d["datasets"][0]["units"]) == len(cn1_card["tasks"])
    rc = cli.main(["inspect", cn1_card["root"], "--out", str(tmp_path / "look2"), "--quiet"])
    out = capsys.readouterr().out
    assert rc == 0 and out.startswith("[1] ")


def test_version(capsys):
    assert cli.main(["version"]) == 0
    out = capsys.readouterr().out
    assert out.startswith(f"oced-clean {oced_clean.__version__}; OCED engine ")
