# -*- coding: utf-8 -*-
"""Problems the user can fix come back as ValueError with a message that says what to do; nothing is half written."""
from __future__ import annotations

import os
import shutil

import pytest

import oced_clean
from tests import helpers
from tests.conftest import quiet
from tests.oced_synthetic import synthetic, synthetic_cn1


def _far_boundary(folder):
    """A boundary about 55 km north of every synthetic field."""
    return helpers.write_box(os.path.join(folder, "far.geojson"), synthetic.LON0, synthetic.LAT0 + 0.5,
                             0.0, 500.0, 0.0, 500.0)


def test_nothing_recognised(tmp_path):
    d = tmp_path / "upload"
    d.mkdir()
    (d / "notes.txt").write_text("not monitor data", encoding="utf-8")
    with pytest.raises(oced_clean.CleaningError) as e:
        oced_clean.clean_yield([str(d)], None, str(tmp_path / "run"), log=quiet)
    assert "recognised" in str(e.value)


def test_missing_upload(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        oced_clean.clean_yield([str(tmp_path / "no_such.zip")], None, str(tmp_path / "run"), log=quiet)


def test_export_outside_the_boundary(field, tmp_path):
    with pytest.raises(ValueError, match="wrong file or wrong field"):
        oced_clean.clean_yield([field["shp"]], _far_boundary(str(tmp_path)), str(tmp_path / "run"), log=quiet)
    assert not [f for f in os.listdir(tmp_path / "run" / "output") if f.endswith(".shp")]     # no clean file


def test_raw_card_outside_the_boundary(cn1_card, tmp_path):
    with pytest.raises(oced_clean.CleaningError, match="no unit of the raw data"):
        oced_clean.clean_yield([cn1_card["root"]], _far_boundary(str(tmp_path)), str(tmp_path / "run"), log=quiet)


def test_several_datasets_need_a_choice(field, combines, tmp_path):
    with pytest.raises(oced_clean.SeveralDatasetsError) as e:
        oced_clean.clean_yield([field["shp"], combines["shp"]], field["boundary"], str(tmp_path / "run"), log=quiet)
    assert [d["index"] for d in e.value.datasets] == [1, 2]
    name = os.path.basename(field["shp"])
    pick = next(d["index"] for d in e.value.datasets if d["file"] == name)
    r = oced_clean.clean_yield([field["shp"], combines["shp"]], field["boundary"], str(tmp_path / "run2"),
                               candidate=pick, log=quiet)
    assert r.summary["input"]["dataset"].find(name) >= 0
    with pytest.raises(oced_clean.CleaningError, match="candidate must be"):
        oced_clean.clean_yield([field["shp"]], field["boundary"], str(tmp_path / "run3"), candidate=5, log=quiet)


def test_a_planter_file_is_refused(tmp_path):
    A = synthetic.make_applied(str(tmp_path / "applied"))
    with pytest.raises(oced_clean.CleaningError, match="not a harvest file"):
        oced_clean.clean_yield([A["shp"]], None, str(tmp_path / "run"), log=quiet)


def test_boundary_file_with_several_fields_needs_a_choice(field, tmp_path):
    with pytest.raises(oced_clean.CleaningError, match="boundary_feature"):
        oced_clean.clean_yield([field["shp"]], field["two_fields"], str(tmp_path / "run"), log=quiet)
    r = oced_clean.clean_yield([field["shp"]], field["two_fields"], str(tmp_path / "run2"), boundary_feature=1,
                               log=quiet)
    assert r.summary["boundary"]["polygons"] == 1


def test_wrong_options(field, tmp_path):
    with pytest.raises(oced_clean.CleaningError, match="group_by"):
        oced_clean.clean_yield([field["shp"]], field["boundary"], str(tmp_path / "a"), group_by="combine", log=quiet)
    with pytest.raises(ValueError, match="unknown product"):
        oced_clean.clean_yield([field["shp"]], field["boundary"], str(tmp_path / "b"), product="bananas", log=quiet)
    with pytest.raises(oced_clean.CleaningError, match="no input"):
        oced_clean.clean_yield([], field["boundary"], str(tmp_path / "c"), log=quiet)


def test_too_few_readings_left(tmp_path):
    """A card with a handful of readings inside the boundary: the engine refuses to write a clean file."""
    C = synthetic_cn1.make_card(str(tmp_path / "upload"))
    tiny = helpers.write_box(str(tmp_path / "tiny.geojson"), synthetic_cn1.LON0, synthetic_cn1.LAT0, -1.0, 12.0, -3.0, 3.0)
    with pytest.raises(ValueError):
        oced_clean.clean_yield([C["root"]], tiny, str(tmp_path / "run"), units="all", log=quiet)
    shutil.rmtree(tmp_path / "run", ignore_errors=True)
