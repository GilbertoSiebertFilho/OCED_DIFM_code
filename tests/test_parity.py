# -*- coding: utf-8 -*-
"""oced_clean.clean_yield against OCED's own command line (the copied engine's engine.run.main) on the same synthetic
inputs: the clean and the flagged shapefiles must be the same files, byte for byte. This is what keeps the API from
drifting away from OCED's route when either changes."""
from __future__ import annotations

import glob
import os

import pytest

import oced_clean
from oced_clean._engine import run as oced_cli
from tests.conftest import quiet

SHAPE_PARTS = (".shp", ".shx", ".dbf", ".prj", ".cpg")


def _bytes(shp):
    out = {}
    for ext in SHAPE_PARTS:
        with open(os.path.splitext(shp)[0] + ext, "rb") as f:
            out[ext] = f.read()
    return out


def _same_as_oceds_command_line(inputs, boundary, tmp_path, cli_extra=(), **api_options):
    r = oced_clean.clean_yield(inputs, boundary, str(tmp_path / "api"), log=quiet, **api_options)
    work = str(tmp_path / "cli")
    argv = [*inputs, "--out", work, *(["--aoi", boundary] if boundary else []), *cli_extra]
    assert oced_cli.main(argv) == 0
    for kind, mine in (("clean", r.clean_shp), ("flagged", r.flagged_shp)):
        theirs = glob.glob(os.path.join(work, "output", f"*_{kind}.shp"))
        assert len(theirs) == 1, theirs
        assert os.path.basename(theirs[0]) == os.path.basename(mine)
        a, b = _bytes(mine), _bytes(theirs[0])
        for ext in SHAPE_PARTS:
            assert a[ext] == b[ext], f"{kind}{ext} differs from OCED's command line"
    return r


def test_point_export(field, tmp_path, capsys):
    _same_as_oceds_command_line([field["shp"]], field["boundary"], tmp_path)


def test_two_combines(combines, tmp_path, capsys):
    _same_as_oceds_command_line([combines["shp"]], combines["boundary"], tmp_path)


def test_two_combines_by_task(combines, tmp_path, capsys):
    _same_as_oceds_command_line([combines["shp"]], combines["boundary"], tmp_path, cli_extra=["--sms-group", "task"],
                                group_by="task")


def test_no_boundary(field, tmp_path, capsys):
    _same_as_oceds_command_line([field["shp"]], None, tmp_path)


def test_import_settings(field, tmp_path, capsys):
    _same_as_oceds_command_line(
        [field["shp"]], field["boundary"], tmp_path,
        cli_extra=["--product", "barley", "--dry-moisture", "14", "--min-yield", "10", "--max-yield", "150"],
        product="barley", dry_moisture=14.0, min_yield=10.0, max_yield=150.0)


@pytest.mark.parametrize("card", ["cn1_card", "jdl_card", "gs3"])
def test_raw_cards(card, request, tmp_path, capsys):
    C = request.getfixturevalue(card)
    _same_as_oceds_command_line([C["root"]], C["boundary"], tmp_path)


def test_operations_center_sections(ops_sections, tmp_path, capsys):
    _same_as_oceds_command_line([ops_sections["root"]], ops_sections["boundary"], tmp_path)
