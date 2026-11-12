# -*- coding: utf-8 -*-
"""A synthetic yield export and field boundaries for the OCED tests (no grower data: every number is made up).

    from tests.oced_synthetic.synthetic import make_field
    F = make_field(folder)       # {"shp", "boundary", "two_fields", "n", "inside_x_m", ...}

The field: 24 east-west passes of 500 m, 9.0 m apart, a 30 ft header (9.144 m: every pass overlaps the previous one
by 0.14 m, so S2 narrows readings), one reading per second at about 4.5 mph, wheat at about 65 bu/ac and 14 %
moisture, with outliers the 3-sigma steps must remove (speed spikes, wet readings, yield spikes and near-zero
readings). The boundary stops 20 m before the east end of the passes, so the readings of the last 20 m are outside
it (S1). The coordinates are near Olds, Alberta, in a made-up position; nothing here comes from a real field.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

LAT0, LON0 = 51.80, -114.10
M_PER_DEG_LAT = 111_320.0
PASS_LEN_M, N_PASS, PASS_GAP_M, WIDTH_FT = 500.0, 24, 9.0, 30.0
BOUNDARY_X_M = 480.0                     # the boundary's east edge: 20 m before the passes end
TEST_WEIGHT, STD_MOIST = 60.0, 14.5      # wheat in the crop table


def _lonlat(x_m, y_m):
    m_per_deg_lon = M_PER_DEG_LAT * np.cos(np.radians(LAT0))
    return LON0 + np.asarray(x_m) / m_per_deg_lon, LAT0 + np.asarray(y_m) / M_PER_DEG_LAT


def _rect(x0, x1, y0, y1):
    from shapely.geometry import Polygon
    lo, la = _lonlat([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0])
    return Polygon(list(zip(lo, la)))


def make_field(folder: str, seed: int = 7) -> dict:
    import geopandas as gpd
    rng = np.random.default_rng(seed)
    os.makedirs(folder, exist_ok=True)
    rows = []
    t0 = pd.Timestamp("2026-09-14 13:00:00")
    t = 0.0
    for p in range(N_PASS):
        speed_mph = 4.5
        step_m = speed_mph * 0.44704                      # 1 s readings
        n = int(PASS_LEN_M / step_m)
        xs = np.arange(n) * step_m
        east = p % 2 == 0
        if not east:
            xs = xs[::-1]
        y = p * PASS_GAP_M
        spd = np.full(n, speed_mph) + rng.normal(0, 0.15, n)
        yld = 65 + 8 * np.sin(xs / 90.0) + 0.15 * y / 10 + rng.normal(0, 5, n)
        moi = 14.0 + rng.normal(0, 0.4, n)
        for i in range(n):
            rows.append({"x": xs[i], "y": y, "Speed_mph_": spd[i], "Distance_f": spd[i] * 1.46667,
                         "Track_deg_": 90.0 if east else 270.0, "Moisture__": moi[i], "Yld_Vol_Dr": yld[i],
                         "Time": (t0 + pd.Timedelta(seconds=t)).strftime("%Y-%m-%d %H:%M:%S"), "Pass": p})
            t += 1.0
        t += 25.0                                         # the turn
    df = pd.DataFrame(rows)
    n = len(df)
    idx = rng.permutation(n)
    k = n // 100
    df.loc[idx[:k], "Speed_mph_"] = 11.0                  # speed spikes (the speed filter: S4, S3 in the old order)
    df.loc[idx[:k], "Distance_f"] = 11.0 * 1.46667
    df.loc[idx[k:2 * k], "Moisture__"] = 27.0             # wet readings (the moisture filter: S3, S4 in the old order)
    df.loc[idx[2 * k:3 * k], "Yld_Vol_Dr"] = 190.0        # yield spikes (S5)
    df.loc[idx[3 * k:3 * k + k // 2], "Yld_Vol_Dr"] = 2.0  # near zero (S0: below the 5 bu/ac import minimum)
    df["Swth_Wdth_"] = WIDTH_FT
    df["Duration_s"] = 1.0
    df["Elevation_"] = 3400.0 + df["x"] * 0.01
    shrink = (100 - df["Moisture__"]) / (100 - STD_MOIST)
    df["Yld_Vol_We"] = df["Yld_Vol_Dr"] / shrink
    df["Yld_Mass_W"] = df["Yld_Vol_We"] * TEST_WEIGHT
    df["Yld_Mass_D"] = df["Yld_Vol_Dr"] * TEST_WEIGHT
    df["Field"] = "Synthetic 1"
    df["Dataset"] = "26/09/14-13:00:00"
    df["Product"] = "Wheat"
    df["Machine"] = "COMBINE-A"
    df["Obj__Id"] = np.arange(1, n + 1)
    lon, lat = _lonlat(df["x"].to_numpy(), df["y"].to_numpy())
    cols = ["Field", "Dataset", "Product", "Obj__Id", "Time", "Duration_s", "Elevation_", "Speed_mph_", "Distance_f",
            "Track_deg_", "Swth_Wdth_", "Moisture__", "Yld_Mass_W", "Yld_Mass_D", "Yld_Vol_We", "Yld_Vol_Dr",
            "Machine"]
    g = gpd.GeoDataFrame(df[cols].copy(), geometry=gpd.points_from_xy(lon, lat), crs="EPSG:4326")
    shp = os.path.join(folder, "Synthetic_export.shp")
    g.to_file(shp, engine="pyogrio")

    half = PASS_GAP_M / 2
    field = _rect(-2.0, BOUNDARY_X_M, -half, (N_PASS - 1) * PASS_GAP_M + half)
    bnd = os.path.join(folder, "Synthetic_boundary.geojson")
    gpd.GeoDataFrame({"Name": ["Synthetic 1"]}, geometry=[field], crs="EPSG:4326").to_file(bnd, driver="GeoJSON")
    other = _rect(0.0, 400.0, 400.0, 700.0)               # a second field north of the first
    two = os.path.join(folder, "Synthetic_two_fields.geojson")
    gpd.GeoDataFrame({"Name": ["Synthetic 1", "Synthetic 2"]}, geometry=[field, other],
                     crs="EPSG:4326").to_file(two, driver="GeoJSON")
    inside = int((df["x"] <= BOUNDARY_X_M).sum())
    return {"shp": shp, "boundary": bnd, "two_fields": two, "n": n, "n_inside": inside, "df": df}


def make_applied(folder: str, kind: str = "seeding", seed: int = 11) -> dict:
    """A synthetic as-applied export as SMS writes it, on the passes of make_field: a planter (kind "seeding":
    Rt_Apd_Ct_ seeds/ac against Rt_Tgt_Ct_) or a sprayer ("application": Rt_Apd_Vol gal/ac against Rt_Tgt_Vol), with
    what the cleaning must remove: sections off at the headlands (rate 0), rate spikes and speed spikes. The last 20 m
    of every pass are outside the boundary of make_field."""
    import geopandas as gpd
    rng = np.random.default_rng(seed)
    os.makedirs(folder, exist_ok=True)
    rate_col, tgt_col, target, sd = (("Rt_Apd_Ct_", "Rt_Tgt_Ct_", 32000.0, 600.0) if kind == "seeding" else
                                     ("Rt_Apd_Vol", "Rt_Tgt_Vol", 10.0, 0.25))
    rows = []
    t0 = pd.Timestamp("2026-05-12 09:00:00")
    t = 0.0
    for p in range(N_PASS):
        speed_mph = 5.5
        step_m = speed_mph * 0.44704
        n = int(PASS_LEN_M / step_m)
        xs = np.arange(n) * step_m
        east = p % 2 == 0
        if not east:
            xs = xs[::-1]
        rate = target + rng.normal(0, sd, n)
        rate[:3] = 0.0                                    # sections off at the start of the pass
        for i in range(n):
            rows.append({"x": xs[i], "y": p * PASS_GAP_M, "Speed_mph_": speed_mph + rng.normal(0, 0.2),
                         "Track_deg_": 90.0 if east else 270.0, rate_col: rate[i], tgt_col: target,
                         "Time": (t0 + pd.Timedelta(seconds=t)).strftime("%Y-%m-%d %H:%M:%S")})
            t += 1.0
        t += 20.0
    df = pd.DataFrame(rows)
    n = len(df)
    idx = rng.permutation(n)
    k = n // 100
    df.loc[idx[:k], "Speed_mph_"] = 12.0                  # speed spikes
    df.loc[idx[k:2 * k], rate_col] = target * 1.8         # rate spikes
    df["Swth_Wdth_"] = WIDTH_FT
    df["Product"] = "Synthetic seed" if kind == "seeding" else "Synthetic herbicide"
    df["Dataset"] = "26/05/12-09:00:00"
    df["Machine"] = "PLANTER-1" if kind == "seeding" else "SPRAYER-1"
    df["Obj__Id"] = np.arange(1, n + 1)
    lon, lat = _lonlat(df["x"].to_numpy(), df["y"].to_numpy())
    cols = ["Product", "Dataset", "Obj__Id", "Time", "Speed_mph_", "Track_deg_", "Swth_Wdth_", rate_col, tgt_col,
            "Machine"]
    g = gpd.GeoDataFrame(df[cols].copy(), geometry=gpd.points_from_xy(lon, lat), crs="EPSG:4326")
    shp = os.path.join(folder, f"Synthetic_{kind}.shp")
    g.to_file(shp, engine="pyogrio")
    return {"shp": shp, "n": n, "n_inside": int((df["x"] <= BOUNDARY_X_M).sum()), "rate": rate_col,
            "n_zero": int((df[rate_col] <= 0).sum()), "df": df}


FT2_PER_AC, FT_PER_M = 43560.0, 1.0 / 0.3048


def make_two_combines(folder: str, bias_b: float = 1.05, seed: int = 21, n_pass: int = 40, pass_len_m: float = 600.0,
                      gap_m: float = WIDTH_FT * 0.3048, narrow_gap_m: float = 23.0 * 0.3048, narrow_every: int = 8,
                      std_moist: float = STD_MOIST, test_weight: float = TEST_WEIGHT,
                      name: str = "Synthetic_two_combines") -> dict:
    """Two combines side by side with a KNOWN truth, for the grower's weight and the combine factor (every number
    is made up). COMBINE-A cuts the even passes and reads the grain as it is; COMBINE-B cuts the odd passes and reads
    it `bias_b` times too high (1.05 = +5 %). East-west passes of `pass_len_m`, a 30 ft header, `gap_m` apart (the
    header width: no ground cut twice); every `narrow_every`-th pass (always one of B's) is only `narrow_gap_m` from
    the previous one (23 ft: 7 ft of its 30 ft header, 23 %, over ground already cut), so the ground is NOT the
    swept area and B overlaps more than A. Grain: the ground carries Y wet bu/ac at moisture M (most of it below the
    standard moisture `std_moist`); a reading holds the grain of the NEW ground of its strip, so its logged yield is
    grain / (distance x full header width) x the combine's bias, as a monitor without section control logs it. The
    dry yield is one-sided (grain drier than the standard is not credited), as an SMS import with 'expand dry
    moisture' off.

    Returns {"shp", "boundary", "n", "df", "truth"}; truth: wet_lb / wet_bu (what a scale weighs, in lb and in
    bushels of `test_weight`), dry_bu (the same grain at the standard moisture, one-sided) and dry_sym_bu (symmetric),
    ground_ac (the ground, every strip once), swept_ac (distance x header width of every reading), boundary_ac,
    monitor_wet_bu / monitor_dry_bu (the totals the two monitors logged), by_machine, bias_b, test_weight,
    std_moist."""
    import geopandas as gpd
    rng = np.random.default_rng(seed)
    os.makedirs(folder, exist_ok=True)
    width_m = WIDTH_FT * 0.3048
    rows = []
    t0 = pd.Timestamp("2026-09-14 13:00:00")
    t, y_pass, x_max = 0.0, 0.0, 0.0
    for p in range(n_pass):
        gap = 0.0 if p == 0 else (narrow_gap_m if p % narrow_every == narrow_every - 1 else gap_m)
        y_pass += gap
        new_w_m = width_m if p == 0 else gap                  # the ground this pass cut for the first time
        n = int(pass_len_m / (4.5 * 0.44704))
        spd = 4.5 + rng.normal(0, 0.1, n)
        dist_m = spd * 0.44704                                # 1 s readings
        xs = np.cumsum(dist_m)
        x_max = max(x_max, float(xs[-1]))
        east = p % 2 == 0
        xpos = xs if east else float(xs[-1]) - xs + dist_m    # the point is at the front of the ground it swept
        mid = xpos - (dist_m / 2 if east else -dist_m / 2)
        y_true = 70 + 8 * np.sin(mid / 90.0) + 0.02 * y_pass + rng.normal(0, 4, n)       # wet bu/ac of the ground
        moi = 13.2 + 1.6 * np.sin(mid / 150.0 + 0.7 * p) + rng.normal(0, 0.35, n)
        machine = "COMBINE-A" if p % 2 == 0 else "COMBINE-B"
        bias = 1.0 if machine == "COMBINE-A" else float(bias_b)
        new_ac = dist_m * new_w_m * FT_PER_M ** 2 / FT2_PER_AC
        swept_ac = dist_m * width_m * FT_PER_M ** 2 / FT2_PER_AC
        true_bu = y_true * new_ac                             # the grain of the reading, wet bushels
        for i in range(n):
            rows.append({"x": xpos[i], "y": y_pass, "Speed_mph_": spd[i], "Distance_f": dist_m[i] * FT_PER_M,
                         "Track_deg_": 90.0 if east else 270.0, "Moisture__": moi[i],
                         "Yld_Vol_We": true_bu[i] / swept_ac[i] * bias, "true_bu": true_bu[i], "new_ac": new_ac[i],
                         "swept_ac": swept_ac[i], "Machine": machine, "Pass": p,
                         "Time": (t0 + pd.Timedelta(seconds=t)).strftime("%Y-%m-%d %H:%M:%S")})
            t += 1.0
        t += 25.0                                             # the turn
    df = pd.DataFrame(rows)
    n = len(df)
    one = np.minimum(1.0, (100 - df["Moisture__"]) / (100 - std_moist))
    sym = (100 - df["Moisture__"]) / (100 - std_moist)
    df["Swth_Wdth_"] = WIDTH_FT
    df["Duration_s"] = 1.0
    df["Elevation_"] = 3400.0 + df["x"] * 0.01
    df["Yld_Vol_Dr"] = df["Yld_Vol_We"] * one
    df["Yld_Mass_W"] = df["Yld_Vol_We"] * test_weight
    df["Yld_Mass_D"] = df["Yld_Vol_Dr"] * test_weight
    df["Field"] = "Synthetic 2"
    df["Dataset"] = np.where(df["Machine"] == "COMBINE-A", "26/09/14-13:00:00", "26/09/14-13:05:25")
    df["Product"] = "Wheat"
    df["Obj__Id"] = np.arange(1, n + 1)
    lon, lat = _lonlat(df["x"].to_numpy(), df["y"].to_numpy())
    cols = ["Field", "Dataset", "Product", "Obj__Id", "Time", "Duration_s", "Elevation_", "Speed_mph_", "Distance_f",
            "Track_deg_", "Swth_Wdth_", "Moisture__", "Yld_Mass_W", "Yld_Mass_D", "Yld_Vol_We", "Yld_Vol_Dr",
            "Machine"]
    g = gpd.GeoDataFrame(df[cols].copy(), geometry=gpd.points_from_xy(lon, lat), crs="EPSG:4326")
    shp = os.path.join(folder, name + ".shp")
    g.to_file(shp, engine="pyogrio")
    field = _rect(-3.0, x_max + 3.0, -width_m / 2 - 1.0, y_pass + width_m / 2 + 1.0)
    bnd = os.path.join(folder, name + "_boundary.geojson")
    gb = gpd.GeoDataFrame({"Name": ["Synthetic 2"]}, geometry=[field], crs="EPSG:4326")
    gb.to_file(bnd, driver="GeoJSON")
    boundary_ac = float(gb.to_crs(gb.estimate_utm_crs()).area.iloc[0]) * FT_PER_M ** 2 / FT2_PER_AC
    wet_bu = float(df["true_bu"].sum())
    mon_wet = df["Yld_Vol_We"] * df["swept_ac"]
    truth = {"wet_bu": wet_bu, "wet_lb": wet_bu * test_weight, "dry_bu": float((df["true_bu"] * one).sum()),
             "dry_sym_bu": float((df["true_bu"] * sym).sum()), "ground_ac": float(df["new_ac"].sum()),
             "swept_ac": float(df["swept_ac"].sum()), "boundary_ac": boundary_ac,
             "monitor_wet_bu": float(mon_wet.sum()), "monitor_dry_bu": float((mon_wet * one).sum()),
             "by_machine": {m: {"true_wet_bu": float(v["true_bu"].sum()),
                                "monitor_wet_bu": float((v["Yld_Vol_We"] * v["swept_ac"]).sum()),
                                "ground_ac": float(v["new_ac"].sum()), "swept_ac": float(v["swept_ac"].sum())}
                            for m, v in df.groupby("Machine")},
             "share_below_standard": float((df["Moisture__"] < std_moist).mean()), "bias_b": float(bias_b),
             "test_weight": float(test_weight), "std_moist": float(std_moist)}
    return {"shp": shp, "boundary": bnd, "n": n, "df": df, "truth": truth}
