# -*- coding: utf-8 -*-
"""The rules of the cleaning, checked against a second, independent statement of them (tests/helpers.py): the order
moisture, speed, dry yield; mean +/- 3 SD recomputed on the readings still alive before every step; a second dry-yield
pass only when the first one cut the SD by more than 25 %; one unit per combine (or per task, or one for the field)."""
from __future__ import annotations

import oced_clean
from tests import helpers
from tests.conftest import quiet


def test_one_combine_is_one_unit_and_follows_the_method(field_run):
    S = field_run.summary
    assert S["cleaning"]["groups"] == ["field"] and S["cleaning"]["group_by"] == "field"
    helpers.assert_group_follows_the_method(helpers.table(field_run.flagged_shp), S, "field")


def test_second_yield_pass_only_after_the_sd_fell_by_more_than_25_pct(field_run):
    rows = [r for r in field_run.summary["cleaning"]["steps"] if r["attribute"] == "dry yield"]
    assert len(rows) >= 2                                   # the synthetic field has gross yield errors: two passes
    for r in rows[:-1]:
        assert r["sd_drop_pct"] > 25.0                      # every pass but the last one earned the next
    assert not rows[-1]["sd_drop_pct"] > 25.0               # the last one did not: the cleaning stopped there
    assert field_run.summary["cleaning"]["yield_passes"] == {"field": len(rows)}


def test_every_combine_is_its_own_unit(combines_run):
    S = combines_run.summary
    d = helpers.table(combines_run.flagged_shp)
    machine = S["columns"]["harvester_id"]["column"]
    groups = sorted(d[machine].unique())
    assert len(groups) == 2 and sorted(S["cleaning"]["groups"]) == groups and S["cleaning"]["group_by"] == "machine"
    for g in groups:
        helpers.assert_group_follows_the_method(d[d[machine] == g], S, g)
    # the two combines were filtered with their own means: the limits differ
    a, b = (helpers.step_rows(S, "S4", g)[0] for g in groups)
    assert (a["lower"], a["upper"]) != (b["lower"], b["upper"])
    # the clip and the swath step run once, over the whole operation
    assert [r["group"] for r in S["cleaning"]["steps"][:3]] == [None, None, None]
    assert helpers.step_rows(S, "S1")[0]["removed"] > 0
    assert sorted(c["combine"] for c in S["cleaning"]["combines"]) == groups
    assert sum(c["kept"] for c in S["cleaning"]["combines"]) == S["cleaning"]["readings_kept"]


def test_group_by_field_and_by_task(combines, tmp_path):
    one = oced_clean.clean_yield([combines["shp"]], combines["boundary"], str(tmp_path / "field"), group_by="field",
                                 log=quiet)
    assert one.summary["cleaning"]["groups"] == ["field"]
    helpers.assert_group_follows_the_method(helpers.table(one.flagged_shp), one.summary, "field")
    task = oced_clean.clean_yield([combines["shp"]], combines["boundary"], str(tmp_path / "task"), group_by="task",
                                  log=quiet)
    T = task.summary["cleaning"]
    assert T["group_by"] == "task" and len(T["groups"]) == 4          # two combines x two harvest evenings
    assert sum(r["readings_before"] for r in helpers.step_rows(task.summary, "S3")) == \
        helpers.step_rows(task.summary, "S2")[0]["remaining"]


def test_a_reading_without_a_value_is_not_removed_by_that_filter(combines_run):
    S = combines_run.summary
    d = helpers.table(combines_run.flagged_shp)
    moisture = S["columns"]["moisture"]["column"]
    empty = d[d[moisture].isna()]
    assert len(empty) > 0 and not (empty["Rem_By"] == "S3").any()
    assert sum(r.get("no_value", 0) for r in helpers.step_rows(S, "S3")) > 0
    assert any("no moisture value" in w for w in S["warnings"])
