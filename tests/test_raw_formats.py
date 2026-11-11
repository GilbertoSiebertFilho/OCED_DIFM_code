# -*- coding: utf-8 -*-
"""Raw monitor data, one synthetic upload per format: a Case IH / New Holland .cn1 card, John Deere Gen4 .jdl logs, a
John Deere GreenStar 3 card and a John Deere Operations Center export by header section. Each goes through the whole
route (detect, choose the units by the boundary, decode, import as SMS, clean) and the result follows the method."""
from __future__ import annotations

import os
import shutil

import pytest

import oced_clean
from oced_clean._engine.formats import common
from tests import helpers
from tests.conftest import quiet


def _check(r, fmt, kind="raw"):
    S = r.summary
    assert S["input"]["format"] == fmt and S["input"]["kind"] == kind
    assert S["import"]["as_sms"] is True and S["import"]["readings_imported"] > 0
    steps = [x["step"] for x in S["cleaning"]["steps"]]
    assert steps[:3] == ["S1", "S0", "S2"]
    assert {x["attribute"] for x in helpers.step_rows(S, "S3")} == {"moisture"}
    assert {x["attribute"] for x in helpers.step_rows(S, "S4")} == {"speed"}
    clean = helpers.table(r.clean_shp)
    assert len(clean) == S["result"]["readings"] >= 30
    assert r.yield_column in clean.columns
    d = helpers.table(r.flagged_shp)
    groups = S["cleaning"]["groups"]
    if len(groups) == 1:
        rows = helpers.step_rows(S, group=groups[0])
        if all(x["mean"] is not None for x in rows):            # a group whose values are all equal has no limit
            helpers.assert_group_follows_the_method(d, S, groups[0])
    return S, clean


def test_case_ih_card(cn1_card, tmp_path):
    r = oced_clean.clean_yield([cn1_card["root"]], cn1_card["boundary"], str(tmp_path / "run"), log=quiet)
    S, clean = _check(r, "cnh_cn1")
    assert list(clean.columns)[:len(common.STANDARD_NAMES)] == common.STANDARD_NAMES    # the SMS column names
    assert len(S["input"]["units"]) == len(cn1_card["tasks"])                            # both tasks lie in the boundary
    assert all(u["share_inside_boundary"] == 1.0 for u in S["input"]["units"])
    assert "inside the boundary" in S["input"]["units_chosen_by"]
    assert set(clean[S["columns"]["harvester_id"]["column"]]) == {cn1_card["vehicle"]}
    assert S["import"]["values_from"]["test_weight_lb_bu"] == "SMS product table"       # a Case IH card: SMS's table


def test_case_ih_card_written_in_capitals(tmp_path):
    """A newer display software writes every name inside the card in upper case; on a file system that tells upper
    from lower case (Linux) the card must still be read."""
    from tests.oced_synthetic import synthetic_cn1
    C = synthetic_cn1.make_card(str(tmp_path / "upload"), upper=True)
    b = helpers.write_box(str(tmp_path / "boundary.geojson"), synthetic_cn1.LON0, synthetic_cn1.LAT0,
                          -3000.0, 3000.0, -3000.0, 3000.0)
    r = oced_clean.clean_yield([C["root"]], b, str(tmp_path / "run"), log=quiet)
    _check(r, "cnh_cn1")
    assert len(r.summary["input"]["units"]) == len(C["tasks"])


def test_case_ih_card_as_a_zip_gives_the_same_clean_file(cn1_card, tmp_path):
    a = oced_clean.clean_yield([cn1_card["root"]], cn1_card["boundary"], str(tmp_path / "folder"), log=quiet)
    z = shutil.make_archive(str(tmp_path / "upload"), "zip", cn1_card["root"])
    b = oced_clean.clean_yield([z], cn1_card["boundary"], str(tmp_path / "zip"), log=quiet)
    for ext in (".shp", ".shx", ".dbf", ".prj"):
        with open(os.path.splitext(a.clean_shp)[0] + ext, "rb") as fa, \
                open(os.path.splitext(b.clean_shp)[0] + ext, "rb") as fb:
            assert fa.read() == fb.read(), ext


def test_john_deere_gen4_logs(jdl_card, tmp_path):
    r = oced_clean.clean_yield([jdl_card["root"]], jdl_card["boundary"], str(tmp_path / "run"), log=quiet)
    S, clean = _check(r, "jd_jdl")
    assert list(clean.columns)[:len(common.STANDARD_NAMES)] == common.STANDARD_NAMES
    assert S["import"]["values"]["max_yield"] == 10000.0       # John Deere files: SMS's 5 - 10,000 bu/ac
    assert S["import"]["values"]["min_yield"] == 5.0


def test_john_deere_greenstar_3_card(gs3, tmp_path):
    r = oced_clean.clean_yield([gs3["root"]], gs3["boundary"], str(tmp_path / "run"), log=quiet)
    S, clean = _check(r, "jd_gs3_rcd")
    assert list(clean.columns)[:len(common.STANDARD_NAMES)] == common.STANDARD_NAMES
    assert S["import"]["values_from"]["test_weight_lb_bu"] == "file"      # the card's own crop definition


def test_operations_center_export_by_section(ops_sections, tmp_path):
    r = oced_clean.clean_yield([ops_sections["root"]], ops_sections["boundary"], str(tmp_path / "run"), log=quiet)
    S, clean = _check(r, "jd_ops_center_sections", kind="export")
    # one reading per combine and second, not one per header section
    assert S["import"]["readings_in"] == ops_sections["seconds"]
    assert len(S["cleaning"]["groups"]) == 2                    # two combines: two units


def test_units_can_be_chosen_by_hand(cn1_card, tmp_path):
    look = oced_clean.inspect_upload([cn1_card["root"]], str(tmp_path / "look"), cn1_card["boundary"], log=quiet)
    units = look["datasets"][0]["units"]
    assert look["datasets"][0]["format"] == "cnh_cn1" and len(units) == len(cn1_card["tasks"])
    assert all(u["share_inside_boundary"] == 1.0 for u in units)
    one = units[0]["id"]
    r = oced_clean.clean_yield([cn1_card["root"]], cn1_card["boundary"], str(tmp_path / "run"), units=[one], log=quiet)
    assert [u["id"] for u in r.summary["input"]["units"]] == [one]
    with pytest.raises(ValueError):
        oced_clean.clean_yield([cn1_card["root"]], cn1_card["boundary"], str(tmp_path / "bad"), units=["no-such-unit"],
                               log=quiet)
