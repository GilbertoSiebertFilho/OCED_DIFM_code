# -*- coding: utf-8 -*-
"""The route on a synthetic point export: the files written, the summary, the clip, the columns of the clean file."""
from __future__ import annotations

import glob
import json
import os
import zipfile

import geopandas as gpd

import oced_clean
from tests import helpers
from tests.conftest import quiet


def test_files_and_summary(field_run):
    r, S = field_run, field_run.summary
    for p in (r.clean_shp, r.clean_zip, r.flagged_shp, r.flagged_zip, r.summary_json, r.engine_json):
        assert os.path.isfile(p), p
    stem = os.path.splitext(os.path.basename(r.clean_shp))[0]
    with zipfile.ZipFile(r.clean_zip) as z:
        assert sorted(z.namelist()) == sorted(stem + e for e in (".shp", ".shx", ".dbf", ".prj", ".cpg"))
    with open(r.summary_json, encoding="utf-8") as f:
        assert json.load(f) == S                      # the summary is plain JSON, and the file is the same summary
    assert S["package"]["name"] == "oced-clean" and S["package"]["version"] == oced_clean.__version__
    assert S["input"]["kind"] == "export" and S["import"]["as_sms"] is True
    assert S["files"]["clean_zip"] == r.clean_zip
    assert "clean file" in r.text() and "S5a" in r.text()


def test_steps_run_in_the_order_of_the_method(field_run):
    S = field_run.summary
    steps = [x["step"] for x in S["cleaning"]["steps"]]
    assert steps[:6] == ["S1", "S0", "S2", "S3", "S4", "S5a"]     # the clip first, then the import check
    by = {x["step"]: x for x in S["cleaning"]["steps"]}
    assert (by["S3"]["attribute"], by["S4"]["attribute"], by["S5a"]["attribute"]) == ("moisture", "speed", "dry yield")
    assert S["cleaning"]["order"] == ["clip", "swath", "moisture", "speed", "dry yield"]
    assert S["cleaning"]["k_sd"] == 3.0 and S["cleaning"]["second_yield_pass_when_sd_drop_pct_above"] == 25.0
    # every step starts from what the previous one left
    left = S["cleaning"]["readings_in"]
    for x in S["cleaning"]["steps"]:
        assert x["readings_before"] == left and x["remaining"] == left - x["removed"], x["step"]
        left = x["remaining"]
    assert left == S["cleaning"]["readings_kept"] == S["result"]["readings"]


def test_clip_removes_the_readings_outside_the_boundary(field, field_run):
    r, S = field_run, field_run.summary
    imported = glob.glob(os.path.join(r.out_dir, "decoded", "*_imported.shp"))
    assert len(imported) == 1
    pts = gpd.read_file(imported[0])
    poly = gpd.read_file(field["boundary"]).geometry.union_all()
    outside = int((~pts.geometry.within(poly)).sum())           # counted here from the geometry alone
    assert outside > 0
    assert helpers.step_rows(S, "S1")[0]["removed"] == outside
    assert S["cleaning"]["readings_in_boundary"] == len(pts) - outside
    assert S["boundary"]["file"] == os.path.basename(field["boundary"]) and S["boundary"]["trial_design"] is False
    clean = gpd.read_file(r.clean_shp)
    assert clean.geometry.within(poly.buffer(1e-7)).all()        # nothing outside the boundary in the clean file
    assert clean.crs.to_epsg() == 4326


def test_import_as_sms_leaves_out_the_readings_below_the_minimum(field, field_run):
    P = field_run.summary["import"]
    assert P["values"]["min_yield"] == 5.0 and P["values"]["expand_dry_moisture"] is False
    assert P["readings_in"] == field["n"]
    assert P["readings_in"] - P["readings_imported"] == sum(P["left_out"].values()) > 0
    clean = helpers.table(field_run.clean_shp)
    assert clean[field_run.yield_column].min() >= 5.0


def test_clean_file_keeps_the_export_columns_and_names_what_is_what(field, field_run):
    r, S = field_run, field_run.summary
    clean, source = helpers.table(r.clean_shp), helpers.table(field["shp"])
    assert list(clean.columns)[:len(source.columns)] == list(source.columns)      # same columns, same order
    assert list(clean.columns)[len(source.columns):] == S["result"]["added_columns"]
    assert len(clean) == S["result"]["readings"]
    C = S["columns"]
    assert r.yield_column == C["yield_volume_dry"]["column"] == "Yld_Vol_Dr"
    for key in ("yield_volume_dry", "yield_mass_dry", "moisture", "swath_width", "distance", "speed", "heading",
                "time", "harvester_id"):
        assert C[key]["column"] in clean.columns, key
    flagged = helpers.table(r.flagged_shp)
    assert set(flagged["Keep"].unique()) <= {0, 1} and int(flagged["Keep"].sum()) == S["cleaning"]["readings_kept"]


def test_without_a_boundary_nothing_is_clipped(field, tmp_path):
    r = oced_clean.clean_yield(field["shp"], None, str(tmp_path / "run"), log=quiet)
    S = r.summary
    assert S["boundary"] is None
    assert helpers.step_rows(S, "S1")[0]["removed"] == 0
    assert S["cleaning"]["readings_in_boundary"] == S["import"]["readings_imported"]
    assert "none given" in r.text()


def test_cleanup_removes_the_intermediate_files(field, tmp_path):
    out = str(tmp_path / "run")
    r = oced_clean.clean_yield([field["shp"]], field["boundary"], out, cleanup=True, log=quiet)
    assert sorted(os.listdir(out)) == ["output"]
    assert os.path.isfile(r.clean_zip) and r.summary["files"]["intermediate_files_removed"] is True


def test_inspect_lists_the_upload(field, tmp_path):
    d = oced_clean.inspect_upload([field["shp"]], str(tmp_path / "look"), log=quiet)
    assert len(d["datasets"]) == 1
    ds = d["datasets"][0]
    assert ds["index"] == 1 and ds["kind"] == "export" and ds["n_points"] == field["n"] and ds["harvest"] is not False
