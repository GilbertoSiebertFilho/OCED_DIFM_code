"""Synthetic data of the SMS 3-sigma tests: two combines over two harvest evenings (made-up numbers only)."""
import os

import numpy as np
import pandas as pd

from tests.oced_synthetic.synthetic import N_PASS, PASS_GAP_M, PASS_LEN_M, TEST_WEIGHT, WIDTH_FT, _lonlat


def make_two_combines(folder: str, seed: int = 5, name: str = "Synthetic_two_combines") -> dict:
    """A synthetic file shaped like a decoded John Deere log (columns Machine and Src_Fmt = 'jd_jdl', record times in
    UTC): two combines, two harvest evenings each. COMBINE-A cuts the passes 0-11 at about 4.5 mph and 14 % moisture,
    COMBINE-B the passes 12-23 at about 5.5 mph and 16 %, six passes per evening. Every evening starts at 23:45 UTC,
    so it crosses the UTC midnight while it is one local day (at this longitude the local day is UTC - 8 h): a task
    (combine x local day) is cut in two when the days are taken in UTC. 300 readings of COMBINE-A have no moisture
    and 200 of COMBINE-B no speed value (empty cells). Every number is made up.
    Returns {"shp", "n", "df", "source"}: `source` is the part of a decode report the cleaning reads."""
    import geopandas as gpd
    rng = np.random.default_rng(seed)
    os.makedirs(folder, exist_ok=True)
    rows = []
    per_evening = N_PASS // 4
    for m, (machine, speed_mph, moist) in enumerate((("COMBINE-A", 4.5, 14.0), ("COMBINE-B", 5.5, 16.0))):
        for ev in range(2):
            t0 = pd.Timestamp("2026-09-14 23:45:00") + pd.Timedelta(days=ev, minutes=3 * m)
            t = 0.0
            for q in range(per_evening):
                p = m * (N_PASS // 2) + ev * per_evening + q
                step_m = speed_mph * 0.44704
                n = int(PASS_LEN_M / step_m)
                xs = np.arange(n) * step_m
                east = p % 2 == 0
                if not east:
                    xs = xs[::-1]
                spd = np.full(n, speed_mph) + rng.normal(0, 0.15, n)
                yld = 65 + 8 * np.sin(xs / 90.0) + rng.normal(0, 5, n) + (6.0 if m else 0.0)
                moi = moist + rng.normal(0, 0.4, n)
                for i in range(n):
                    rows.append({"x": xs[i], "y": p * PASS_GAP_M, "Speed_mph_": spd[i], "Distance_f": spd[i] * 1.46667,
                                 "Track_deg_": 90.0 if east else 270.0, "Moisture__": moi[i], "Yld_Vol_Dr": yld[i],
                                 "Time": (t0 + pd.Timedelta(seconds=t)).strftime("%Y-%m-%d %H:%M:%S"),
                                 "Machine": machine, "Evening": ev})
                    t += 1.0
                t += 25.0
    df = pd.DataFrame(rows)
    n = len(df)
    for machine in ("COMBINE-A", "COMBINE-B"):                 # outliers the 3-sigma steps must remove, per combine
        idx = rng.permutation(np.flatnonzero((df["Machine"] == machine).to_numpy()))
        k = len(idx) // 100
        df.loc[idx[:k], "Speed_mph_"] = 11.0
        df.loc[idx[:k], "Distance_f"] = 11.0 * 1.46667
        df.loc[idx[k:2 * k], "Moisture__"] = 27.0
        df.loc[idx[2 * k:3 * k], "Yld_Vol_Dr"] = 190.0
    a = rng.permutation(np.flatnonzero((df["Machine"] == "COMBINE-A").to_numpy()))[:300]
    b = rng.permutation(np.flatnonzero((df["Machine"] == "COMBINE-B").to_numpy()))[:200]
    df.loc[a, "Moisture__"] = np.nan                           # empty cells: no moisture / no speed value
    df.loc[b, "Speed_mph_"] = np.nan
    df["Swth_Wdth_"] = WIDTH_FT
    df["Duration_s"] = 1.0
    df["Elevation_"] = 3400.0
    df["Yld_Vol_We"] = df["Yld_Vol_Dr"]
    df["Yld_Mass_W"] = df["Yld_Vol_We"] * TEST_WEIGHT
    df["Yld_Mass_D"] = df["Yld_Vol_Dr"] * TEST_WEIGHT
    df["Field"] = "Synthetic 2"
    df["Dataset"] = "Synthetic 2 " + df["Machine"].str[-1] + " 26/09/14-23:45:00"
    df["Product"] = "Wheat"
    df["Src_Fmt"] = "jd_jdl"
    df["Crop"] = "wheat"
    df["Obj__Id"] = np.arange(1, n + 1)
    lon, lat = _lonlat(df["x"].to_numpy(), df["y"].to_numpy())
    cols = ["Field", "Dataset", "Product", "Obj__Id", "Time", "Duration_s", "Elevation_", "Speed_mph_", "Distance_f",
            "Track_deg_", "Swth_Wdth_", "Moisture__", "Yld_Mass_W", "Yld_Mass_D", "Yld_Vol_We", "Yld_Vol_Dr",
            "Machine", "Src_Fmt", "Crop"]
    g = gpd.GeoDataFrame(df[cols].copy(), geometry=gpd.points_from_xy(lon, lat), crs="EPSG:4326")
    shp = os.path.join(folder, name + ".shp")
    g.to_file(shp, engine="pyogrio")
    source = {"kind": "decoded", "format": "jd_jdl", "time_basis": "UTC", "tz_offset_h": None, "warnings": []}
    return {"shp": shp, "n": n, "df": df, "source": source}
