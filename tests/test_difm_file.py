# -*- coding: utf-8 -*-
"""The shapefile for DIFM: the kept readings in the layout of an Ag Leader SMS shapefile export of a grain harvest,
with the monitor's yield or with the adjusted one (combine factor, grower's weight). The layout is checked on the
file itself (the zip, the .dbf header and records read byte by byte, the .prj), and the readings against the full
clean file of the same run."""
from __future__ import annotations

import datetime
import os

import geopandas as gpd
import numpy as np
import pytest

import oced_clean
from tests import helpers
from tests.conftest import quiet

NOT_IN_AN_SMS_EXPORT = ("Machine", "Src_Fmt", "Yld_Dry_Sy", "Harv_Day", "Session", "Wdth_Raw", "Swath_Scl",
                        "Yld_Harm", "Yld_Level", "Yld_Lvl_Sy")
SAME_VALUES = (("yield_volume_dry", "Yld_Vol_Dr"), ("yield_mass_dry", "Yld_Mass_D"), ("moisture", "Moisture__"),
               ("swath_width", "Swth_Wdth_"), ("distance", "Distance_f"), ("speed", "Speed_mph_"))
YIELD_FIELDS = ("Yld_Vol_Dr", "Yld_Mass_D", "Yld_Mass_W", "Yld_Vol_We")     # the four the adjusted file changes
MONITOR_NAME, ADJUSTED_NAME = "_sms3s_clean_DIFM.zip", "_sms3s_clean_adjusted_DIFM.zip"


@pytest.fixture(scope="module")
def cn1_run(cn1_card, tmp_path_factory):
    return oced_clean.clean_yield([cn1_card["root"]], cn1_card["boundary"], str(tmp_path_factory.mktemp("cn1_difm")),
                                  log=quiet)


@pytest.fixture(scope="module")
def jdl_run(jdl_card, tmp_path_factory):
    return oced_clean.clean_yield([jdl_card["root"]], jdl_card["boundary"], str(tmp_path_factory.mktemp("jdl_difm")),
                                  log=quiet)


def _grower_yield(field_run) -> dict:
    """A made-up grower's weight for the synthetic field: 4 % above the monitor's own average, as a dry yield."""
    return {"grower_weight": round(1.04 * field_run.summary["result"]["average_area_weighted"], 2),
            "weight_unit": "bu/ac", "weight_basis": "dry"}


@pytest.fixture(scope="module")
def leveled(field, field_run, tmp_path_factory):
    """The point export with a grower's weight: the shapefile for DIFM is the adjusted one without asking."""
    return oced_clean.clean_yield([field["shp"]], field["boundary"], str(tmp_path_factory.mktemp("leveled")),
                                  log=quiet, **_grower_yield(field_run))


@pytest.fixture(scope="module")
def leveled_monitor(field, field_run, tmp_path_factory):
    """The same cleaning, the monitor's yield asked for."""
    return oced_clean.clean_yield([field["shp"]], field["boundary"], str(tmp_path_factory.mktemp("leveled_monitor")),
                                  log=quiet, difm_yield="monitor", **_grower_yield(field_run))


@pytest.fixture(scope="module")
def harmonised(combines, tmp_path_factory):
    """Two combines with the combine factor from their whole-field means (the method that gives a factor on a field
    the two cut in blocks, as the synthetic one is)."""
    return oced_clean.clean_yield([combines["shp"]], combines["boundary"], str(tmp_path_factory.mktemp("harmonised")),
                                  log=quiet, combine_factor="machine")


@pytest.fixture(params=["field_run", "combines_run", "cn1_run", "jdl_run", "leveled", "leveled_monitor", "harmonised"])
def run(request):
    """One run per kind of upload (a point export, an export of two combines, a Case IH card, John Deere Gen4 logs)
    and per adjustment (a grower's weight, a combine factor)."""
    return request.getfixturevalue(request.param)


def _unpacked(r, tmp_path) -> str:
    return helpers.unpack_difm(r.difm_zip, str(tmp_path / "difm"))


def _numbers(table, column):
    return table[column].to_numpy(float)


def test_the_run_hands_out_the_file(run):
    assert os.path.isfile(run.difm_zip) and run.difm_yield in oced_clean.api.DIFM_YIELDS
    assert run.summary["files"]["difm_zip"] == run.difm_zip == run.to_dict()["difm_zip"]
    assert run.summary["difm_file"]["yield"] == run.difm_yield == run.to_dict()["difm_yield"]
    assert run.difm_zip != run.clean_zip and "for DIFM" in run.text() and f"{run.difm_yield} yield" in run.text()


def test_the_name_says_which_yield(run):
    """<clean file>_DIFM.zip with the monitor's yield, <clean file>_adjusted_DIFM.zip with the adjusted one, next to
    the full clean file."""
    clean = os.path.splitext(run.clean_shp)[0]
    assert clean.endswith("_sms3s_clean")
    assert run.difm_zip == clean + ("_adjusted_DIFM.zip" if run.difm_yield == "adjusted" else "_DIFM.zip")
    assert run.difm_zip.endswith(ADJUSTED_NAME if run.difm_yield == "adjusted" else MONITOR_NAME)


def test_layout_of_an_sms_shapefile_export(run, tmp_path):
    shp = _unpacked(run, tmp_path)
    helpers.assert_difm_layout(shp)
    assert gpd.read_file(shp).crs.to_epsg() == 4326
    difm = helpers.table(shp)
    assert list(difm.columns) == [name for name, _type in helpers.DIFM_LAYOUT]
    assert not set(NOT_IN_AN_SMS_EXPORT) & set(difm.columns)


def test_it_holds_the_readings_of_the_clean_file(run, tmp_path):
    shp = _unpacked(run, tmp_path)
    h = helpers.dbf_header(os.path.splitext(shp)[0] + ".dbf")
    decimals = {f[0]: f[3] for f in h["fields"]}
    difm, clean = gpd.read_file(shp), gpd.read_file(run.clean_shp)
    assert len(difm) == len(clean) == run.summary["result"]["readings"] == h["records"]
    assert np.allclose(difm.geometry.x, clean.geometry.x, rtol=0, atol=1e-9)
    assert np.allclose(difm.geometry.y, clean.geometry.y, rtol=0, atol=1e-9)
    C = run.summary["columns"]                                   # which column of the full clean file holds what
    for key, col in SAME_VALUES:
        if run.difm_yield == "adjusted" and col in YIELD_FIELDS:
            continue                                             # test_the_adjusted_file_... below
        source = (C.get(key) or {}).get("column")
        if source not in clean.columns:
            continue
        step = 0.5 * 10.0 ** -decimals[col] + 1e-9               # the rounding of the DBF field
        assert np.allclose(_numbers(difm, col), _numbers(clean, source), rtol=0, atol=step, equal_nan=True), col
    assert np.isfinite(_numbers(difm, "Yld_Vol_Dr")).all()
    assert np.isfinite(_numbers(difm, "Y_Offset_f")).all()               # a number on every reading, 0 when none
    if "Harv_Day" in clean.columns:
        # the date of the reading: its calendar day on the computer's clock, which for a time logged in UTC is the
        # cleaning's own harvest day (the solar day of the field) or the day next to it
        dates = helpers.dbf_raw_column(os.path.splitext(shp)[0] + ".dbf", "Date")
        for mine, day in zip(dates, clean["Harv_Day"]):
            a = datetime.date(int(mine[:4]), int(mine[4:6]), int(mine[6:]))
            assert abs((a - datetime.date.fromisoformat(str(day))).days) <= 1, (mine, day)


def test_the_monitor_file_is_the_one_of_a_run_without_adjustments(field_run, leveled_monitor, tmp_path):
    """A grower's weight was given and the monitor's yield asked for: the same records, byte for byte, as the file of
    the cleaning that was given no weight."""
    assert leveled_monitor.difm_yield == "monitor" and leveled_monitor.summary["difm_file"]["yield_asked"] == "monitor"
    assert leveled_monitor.summary["adjustment"]["grower_weight"]["level_factor"] != 1.0     # the weight was used
    a = helpers.unpack_difm(field_run.difm_zip, str(tmp_path / "a"))
    b = helpers.unpack_difm(leveled_monitor.difm_zip, str(tmp_path / "b"))
    assert helpers.dbf_header(a[:-4] + ".dbf")["fields"] == helpers.dbf_header(b[:-4] + ".dbf")["fields"]
    assert helpers.dbf_records(a[:-4] + ".dbf") == helpers.dbf_records(b[:-4] + ".dbf")


@pytest.mark.parametrize("name", ["leveled", "harmonised"])
def test_the_adjusted_file_carries_the_adjusted_yield(name, request, tmp_path):
    """Yld_Vol_Dr is the adjusted yield of the full clean file; Yld_Mass_D, Yld_Mass_W and Yld_Vol_We are scaled by
    the same factor, reading by reading; the factor is the one the summary reports."""
    r = request.getfixturevalue(name)
    A = r.summary["adjustment"]
    assert r.difm_yield == "adjusted" and A["column"] == r.yield_column and A["column"] != "Yld_Vol_Dr"
    assert r.summary["difm_file"]["yield_column_of_the_clean_file"] == A["column"]
    shp = _unpacked(r, tmp_path)
    decimals = {f[0]: f[3] for f in helpers.dbf_header(shp[:-4] + ".dbf")["fields"]}
    difm, clean = helpers.table(shp), helpers.table(r.clean_shp)
    monitor = _numbers(clean, "Yld_Vol_Dr")
    assert (monitor > 0).all()
    factor = _numbers(clean, A["column"]) / monitor
    step = 0.5 * 10.0 ** -decimals["Yld_Vol_Dr"] + 1e-9
    assert np.allclose(_numbers(difm, "Yld_Vol_Dr"), _numbers(clean, A["column"]), rtol=0, atol=step)
    for col in ("Yld_Mass_D", "Yld_Mass_W", "Yld_Vol_We"):
        assert np.allclose(_numbers(difm, col), factor * _numbers(clean, col), rtol=1e-6, atol=1e-6), col
    if name == "leveled":                                        # one factor for the field: the weight / the total
        assert np.allclose(factor, A["grower_weight"]["level_factor"], rtol=1e-4)
        assert A["combine_factor"] is None and abs(A["grower_weight"]["level_factor"] - 1.0) > 0.005
    else:                                                        # one factor per combine, 1 for the reference
        F = A["combine_factor"]
        assert F["applied"] and F["factors"][F["reference"]] == 1.0 and A["grower_weight"] is None
        combine = clean[r.summary["columns"]["harvester_id"]["column"]].astype(str)
        for m, f in F["factors"].items():
            assert np.allclose(factor[(combine == m).to_numpy()], f, rtol=1e-4), m
        assert any(abs(f - 1.0) > 0.005 for f in F["factors"].values())


@pytest.mark.parametrize("adjusted, plain", [("leveled", "leveled_monitor"), ("harmonised", "combines_run")])
def test_the_adjusted_file_changes_nothing_else(adjusted, plain, request, tmp_path):
    """Every field but the four yields is, byte for byte, the one of the file with the monitor's yield."""
    a = helpers.unpack_difm(request.getfixturevalue(adjusted).difm_zip, str(tmp_path / "a"))[:-4] + ".dbf"
    m = helpers.unpack_difm(request.getfixturevalue(plain).difm_zip, str(tmp_path / "m"))[:-4] + ".dbf"
    assert helpers.dbf_header(a)["records"] == helpers.dbf_header(m)["records"]
    for column, _type in helpers.DIFM_LAYOUT:
        same = helpers.dbf_raw_column(a, column) == helpers.dbf_raw_column(m, column)
        assert same != (column in YIELD_FIELDS), column


def test_adjusted_asked_for_but_nothing_applied(combines, tmp_path):
    """The automatic method gives no factor on a field the combines cut in blocks: the file is the monitor one and
    the result says so."""
    r = oced_clean.clean_yield([combines["shp"]], combines["boundary"], str(tmp_path / "run"), log=quiet,
                               combine_factor=True, difm_yield="adjusted")
    A, D = r.summary["adjustment"], r.summary["difm_file"]
    assert A["combine_factor"]["applied"] is False and A["column"] is None and r.yield_column == "Yld_Vol_Dr"
    assert r.difm_yield == D["yield"] == "monitor" and D["yield_asked"] == "adjusted"
    assert r.difm_zip.endswith(MONITOR_NAME) and "no adjustment" in D["notes"][0] and "no adjustment" in r.text()
