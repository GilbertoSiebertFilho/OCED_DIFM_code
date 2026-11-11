# -*- coding: utf-8 -*-
"""The shapefile for DIFM: the kept readings in the layout of an Ag Leader SMS shapefile export of a grain harvest.
The layout is checked on the file itself (the .dbf header and records read byte by byte, the .prj, the zip), and the
readings against the full clean file of the same run."""
from __future__ import annotations

import os

import geopandas as gpd
import numpy as np
import pytest

import oced_clean
from tests import helpers
from tests.conftest import quiet

NOT_IN_AN_SMS_EXPORT = ("Machine", "Src_Fmt", "Yld_Dry_Sy", "Harv_Day", "Session", "Wdth_Raw", "Swath_Scl")
SAME_VALUES = (("yield_volume_dry", "Yld_Vol_Dr"), ("yield_mass_dry", "Yld_Mass_D"), ("moisture", "Moisture__"),
               ("swath_width", "Swth_Wdth_"), ("distance", "Distance_f"), ("speed", "Speed_mph_"))


@pytest.fixture(scope="module")
def cn1_run(cn1_card, tmp_path_factory):
    return oced_clean.clean_yield([cn1_card["root"]], cn1_card["boundary"], str(tmp_path_factory.mktemp("cn1_difm")),
                                  log=quiet)


@pytest.fixture(scope="module")
def jdl_run(jdl_card, tmp_path_factory):
    return oced_clean.clean_yield([jdl_card["root"]], jdl_card["boundary"], str(tmp_path_factory.mktemp("jdl_difm")),
                                  log=quiet)


@pytest.fixture(params=["field_run", "combines_run", "cn1_run", "jdl_run"])
def run(request):
    """One run per kind of upload: a point export, an export of two combines, a Case IH card, John Deere Gen4 logs."""
    r = request.getfixturevalue(request.param)
    assert r.difm_shp and r.difm_zip, "no shapefile for DIFM in the engine's record (summary.DIFM_FILE_KEYS)"
    return r


def test_the_run_hands_out_the_file(run):
    assert os.path.isfile(run.difm_shp) and os.path.isfile(run.difm_zip)
    assert run.summary["files"]["difm_zip"] == run.difm_zip == run.to_dict()["difm_zip"]
    assert run.difm_zip != run.clean_zip and "for DIFM" in run.text()


def test_layout_of_an_sms_shapefile_export(run):
    helpers.assert_difm_layout(run.difm_shp, run.difm_zip)
    assert gpd.read_file(run.difm_shp).crs.to_epsg() == 4326


def test_it_holds_the_readings_of_the_clean_file(run):
    h = helpers.dbf_header(os.path.splitext(run.difm_shp)[0] + ".dbf")
    decimals = {f[0]: f[3] for f in h["fields"]}
    difm, clean = gpd.read_file(run.difm_shp), gpd.read_file(run.clean_shp)
    assert len(difm) == len(clean) == run.summary["result"]["readings"] == h["records"]
    assert np.allclose(difm.geometry.x, clean.geometry.x, rtol=0, atol=1e-9)
    assert np.allclose(difm.geometry.y, clean.geometry.y, rtol=0, atol=1e-9)
    C = run.summary["columns"]                                   # which column of the full clean file holds what
    for key, col in SAME_VALUES:
        source = (C.get(key) or {}).get("column")
        if source not in clean.columns:
            continue
        step = 0.5 * 10.0 ** -decimals[col] + 1e-9               # the rounding of the DBF field
        assert np.allclose(difm[col].to_numpy(float), clean[source].to_numpy(float), rtol=0, atol=step,
                           equal_nan=True), col
    assert np.isfinite(difm["Yld_Vol_Dr"].to_numpy(float)).all()
    assert np.isfinite(difm["Y_Offset_f"].to_numpy(float)).all()         # a number on every reading, 0 when none
    if "Harv_Day" in clean.columns:                                      # the date is the local day of harvest
        dates = helpers.dbf_raw_column(os.path.splitext(run.difm_shp)[0] + ".dbf", "Date")
        assert dates == [str(day).replace("-", "") for day in clean["Harv_Day"]]


def test_nothing_else_is_in_it(run):
    difm = helpers.table(run.difm_shp)
    assert list(difm.columns) == [name for name, _type in helpers.DIFM_LAYOUT]
    assert not set(NOT_IN_AN_SMS_EXPORT) & set(difm.columns)
