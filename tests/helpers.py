# -*- coding: utf-8 -*-
"""Helpers of the tests: made-up boundaries, and the SMS 3-sigma rule written a second time, independently of the
engine, to check the engine's limits and removals against it (every number in the tests is synthetic)."""
from __future__ import annotations

import json
import math
import os

import numpy as np
import pandas as pd
import pyogrio

M_PER_DEG_LAT = 111_320.0
PASS_LETTERS = "abcdefghij"
SD_DROP_PCT = 25.0          # the method: another dry-yield pass only when the SD fell by more than this
K_SD = 3.0
BEFORE_THE_FILTERS = ("S0", "S1", "S2")     # import check, clip, swath: removed before the first mean and SD


def write_box(path: str, lon0: float, lat0: float, west_m: float, east_m: float, south_m: float, north_m: float) -> str:
    """A rectangular boundary (GeoJSON, WGS 84) given in metres east / north of (lon0, lat0)."""
    mx = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    xs = [lon0 + west_m / mx, lon0 + east_m / mx]
    ys = [lat0 + south_m / M_PER_DEG_LAT, lat0 + north_m / M_PER_DEG_LAT]
    ring = [[xs[0], ys[0]], [xs[1], ys[0]], [xs[1], ys[1]], [xs[0], ys[1]], [xs[0], ys[0]]]
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"name": "Synthetic boundary"},
             "geometry": {"type": "Polygon", "coordinates": [ring]}}]}, f)
    return path


def box_around_points(path: str, shp: str, margin_m: float = 30.0, cut_east_m: float = 0.0) -> str:
    """A rectangular boundary around the points of a shapefile, margin_m outside them; cut_east_m > 0 puts the east
    edge that far INSIDE the points instead, so the clip has readings to remove."""
    g = pyogrio.read_dataframe(shp, columns=[])
    lon, lat = g.geometry.x.to_numpy(float), g.geometry.y.to_numpy(float)
    lon0, lat0 = float(lon.min()), float(lat.min())
    mx = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    east = (float(lon.max()) - lon0) * mx
    north = (float(lat.max()) - lat0) * M_PER_DEG_LAT
    return write_box(path, lon0, lat0, -margin_m, (east - cut_east_m) if cut_east_m else (east + margin_m),
                     -margin_m, north + margin_m)


def table(shp: str) -> pd.DataFrame:
    return pyogrio.read_dataframe(shp, read_geometry=False)


def half_up(v: float, dec: int = 2) -> float:
    q = 10.0 ** dec
    return math.floor(v * q + 0.5) / q


def limits(values) -> tuple:
    """(mean, SD, lower, upper) as the method types them: mean +/- 3 SD of the values still alive (SD of the points,
    ddof 0), the lower limit never below 0, both rounded half up to 2 decimals."""
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    mean, sd = float(v.mean()), float(v.std(ddof=0))
    return mean, sd, half_up(max(mean - K_SD * sd, 0.0)), half_up(mean + K_SD * sd)


def step_rows(summary: dict, step: str | None = None, group=None) -> list:
    return [r for r in summary["cleaning"]["steps"]
            if (step is None or r["step"] == step) and (group is None or r["group"] == group)]


def recompute_group(d: pd.DataFrame, summary: dict) -> list:
    """The moisture, speed and dry-yield steps of one group, recomputed from the flagged file alone (which readings
    went in and their values; none of the engine's limits). Returns [(step, mean, sd, lower, upper, removed index
    set)] in the order of the method: moisture, speed, dry yield, and another dry-yield pass while the previous one
    cut the SD by more than 25 %."""
    C = summary["columns"]
    rem = d["Rem_By"].fillna("").astype(str)
    alive = (~rem.isin(BEFORE_THE_FILTERS)).to_numpy(bool)      # alive after the clip and the swath step
    moisture = pd.to_numeric(d[C["moisture"]["column"]], errors="coerce").to_numpy(float)
    dist = pd.to_numeric(d[C["distance"]["column"]], errors="coerce").to_numpy(float)
    speed = pd.to_numeric(d[C["speed"]["column"]], errors="coerce").to_numpy(float)
    # a reading shortened by the swath step: Speed = new Distance / Duration (the flagged file keeps the new distance
    # with 4 decimals, so only the shortened readings are touched)
    dnew = d["Dist_Opt"].to_numpy(float)
    short = np.isfinite(dnew) & (dist > 0) & (dnew < dist - 1e-3)
    speed = np.where(short, speed * dnew / np.where(dist > 0, dist, 1.0), speed)
    y = (pd.to_numeric(d[C["yield_volume_dry"]["column"]], errors="coerce").to_numpy(float)
         * d["Swath_Scl"].to_numpy(float))                       # the yield scaled by Optimize Swath Widths
    out = []

    def one(step, values):
        nonlocal alive
        mean, sd, lo, hi = limits(values[alive])
        bad = alive & np.isfinite(values) & ~((values >= lo) & (values <= hi))
        left = values[alive & ~bad]
        sd_after = float(np.std(left[np.isfinite(left)], ddof=0))
        out.append((step, mean, sd, lo, hi, set(d.index[bad])))
        alive = alive & ~bad
        return sd, sd_after, int(bad.sum())
    one("S3", moisture)
    one("S4", speed)
    for p in range(len(PASS_LETTERS)):
        sd, sd_after, n_bad = one("S5" + PASS_LETTERS[p], y)
        if not (sd > 0 and 100.0 * (1.0 - sd_after / sd) > SD_DROP_PCT and n_bad):
            break
    return out


def assert_group_follows_the_method(d: pd.DataFrame, summary: dict, group: str):
    """The engine's rows of one group are the method's: the same steps in the same order, the same mean, SD and
    limits, and exactly the readings outside the limits removed by that step."""
    expected = recompute_group(d, summary)
    rows = step_rows(summary, group=group)
    assert [r["step"] for r in rows] == [e[0] for e in expected], (group, [r["step"] for r in rows])
    rem = d["Rem_By"].fillna("").astype(str)
    for r, (step, mean, sd, lo, hi, bad) in zip(rows, expected):
        assert abs(r["mean"] - mean) < 1e-6 and abs(r["sd"] - sd) < 1e-6, (group, step, r["mean"], mean, r["sd"], sd)
        assert (r["lower"], r["upper"]) == (lo, hi), (group, step, r["lower"], r["upper"], lo, hi)
        assert set(d.index[rem == step]) == bad, (group, step)
        assert r["removed"] == len(bad)
    gone = set().union(*[e[5] for e in expected]) | set(d.index[rem.isin(BEFORE_THE_FILTERS)])
    assert set(d.index[d["Keep"] == 1]) == set(d.index) - gone
