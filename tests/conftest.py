# -*- coding: utf-8 -*-
"""Shared fixtures: synthetic inputs (OCED's own generators, made-up numbers only) and one cleaning run of each."""
from __future__ import annotations

import os

import numpy as np
import pytest

import oced_clean
from tests import helpers
from tests.oced_synthetic import gs3_card, ops_exports, synthetic, synthetic_cn1, synthetic_combines, synthetic_jdl


_JDL_TRACK = synthetic_jdl.track


def quiet(_message):
    return None


@pytest.fixture(scope="session")
def field(tmp_path_factory):
    """A point export in the SMS column names (one combine) and its boundary, which stops before the passes end."""
    return synthetic.make_field(str(tmp_path_factory.mktemp("field")))


@pytest.fixture(scope="session")
def field_run(field, tmp_path_factory):
    return oced_clean.clean_yield([field["shp"]], field["boundary"], str(tmp_path_factory.mktemp("field_run")),
                                  log=quiet)


@pytest.fixture(scope="session")
def combines(tmp_path_factory):
    """Two combines over two evenings (a file shaped like decoded John Deere logs) and a boundary that cuts the east
    end of the passes."""
    d = str(tmp_path_factory.mktemp("combines"))
    F = synthetic_combines.make_two_combines(d)
    F["boundary"] = helpers.box_around_points(os.path.join(d, "boundary.geojson"), F["shp"], cut_east_m=25.0)
    return F


@pytest.fixture(scope="session")
def combines_run(combines, tmp_path_factory):
    return oced_clean.clean_yield([combines["shp"]], combines["boundary"],
                                  str(tmp_path_factory.mktemp("combines_run")), log=quiet)


def _box(folder, lon0, lat0):
    return helpers.write_box(os.path.join(folder, "boundary.geojson"), lon0, lat0, -3000.0, 3000.0, -3000.0, 3000.0)


@pytest.fixture(scope="session")
def cn1_card(tmp_path_factory):
    """A Case IH / New Holland card with two harvest tasks, and a boundary around its field."""
    d = str(tmp_path_factory.mktemp("cn1"))
    C = synthetic_cn1.make_card(os.path.join(d, "upload"))
    C["boundary"] = _box(d, synthetic_cn1.LON0, synthetic_cn1.LAT0)
    return C


def _jdl_track_with_changing_speed(*args, **kwargs):
    """OCED's synthetic Gen4 track drives at exactly one speed, so its speed has no spread to filter on. Here the
    combine speeds up and slows down along the pass (2.5 m back and forth every 38 s: about 2 m/s +/- 0.4)."""
    tr = _JDL_TRACK(*args, **kwargs)
    s = (tr["et"] - tr["et"][0]) / 1000.0
    wobble = 2.5 * np.sin(s / 6.0) / synthetic_jdl.M_PER_DEG_LAT
    for key in ("lat1", "lat2", "glat"):
        tr[key] = tr[key] + wobble
    return tr


@pytest.fixture(scope="session")
def jdl_card(tmp_path_factory):
    """A John Deere Gen4 card (JD-Data/log/.../*.jdl) and a boundary around its field."""
    d = str(tmp_path_factory.mktemp("jdl"))
    synthetic_jdl.track = _jdl_track_with_changing_speed
    try:
        C = synthetic_jdl.make_card(os.path.join(d, "upload"))
    finally:
        synthetic_jdl.track = _JDL_TRACK
    C["boundary"] = _box(d, synthetic_jdl.LON0, synthetic_jdl.LAT0)
    return C


@pytest.fixture(scope="session")
def gs3(tmp_path_factory):
    """A John Deere GreenStar 3 card (RCD/EIC/.../*.fdd + .fdl) and a boundary around its field."""
    d = str(tmp_path_factory.mktemp("gs3"))
    C = gs3_card.make_card(os.path.join(d, "upload"), n=400)
    C["boundary"] = _box(d, C["lon0"], C["lat0"])
    return C


@pytest.fixture(scope="session")
def ops_sections(tmp_path_factory):
    """A John Deere Operations Center export with one point per header section and second (two combines)."""
    d = str(tmp_path_factory.mktemp("ops"))
    shp, seconds, area, mass = ops_exports._section_export(os.path.join(d, "upload"))
    return {"shp": shp, "root": os.path.join(d, "upload"), "seconds": seconds,
            "boundary": _box(d, ops_exports.LON0, ops_exports.LAT0)}
