"""John Deere Ops Center section-element harvest export -> one reading per combine and second.

Ops Center can export a harvest as one point per header element (section) and second: narrow elements, many a
second across the header (millions of elements for a field). Cleaned as it is, every
element would count as a reading: consecutive records lie side by side across the header, so passes, steps, swept
strips and overlap would be measured between neighbouring elements instead of along the travel, and the map strips
are a few pixels wide. collapse() rebuilds the per-second readings that the per-second Ops Center export (and the
GreenStar log) holds:

    width      the widths of the elements with an area, added up, capped at the header: the widest single record
               when one spans the header (> 90 % of the per-second sums' 99th percentile), else that percentile
    distance   the area-weighted mean of the elements' distances (area = distance x width): the area is kept
    per area   the yields, the wet mass, the moisture and the dry matter: area-weighted means, so the field totals are
               kept (PER_AREA, case-insensitive)
    position   the mean of the elements with an area (the header centre); the elevation too
    the rest   the first element's value (heading, speed, time, crop, variety, machine ...)

Elements without an area (distance or width 0) carry no grain; a second with none is dropped, as SMS drops a
zero-area record. The columns keep the export's names (SECTIONID is dropped, N_Elem counts the elements of each
reading), so detection and cleaning read the result as a per-second Ops Center export. Checked against a collapse
made by hand and imported into SMS: the same readings and area. The export's '<stem>-Deere-Metadata.json'
(crop, units) is copied beside the result as '<result stem>-Deere-Metadata.json', so that the collapsed file keeps
its crop; collapse() returns it as "crop" and "monitor_product" (common.jd_monitor_product: what SMS imports it with).

    from oced_clean._engine.formats import ops_sections as ops
    if ops.is_section_export(path):
        info = ops.collapse(path, out_shp)        # {"n_in", "n_out", "dropped", "header_ft", "area_ac", "crop", ...}
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

SECTION_COLS = ("sectionid", "section_id", "section")
TIME_COLS = ("isotime", "time")                       # IsoTime first: Time may be a local string without seconds
MACHINE_COLS = ("machine", "machine_id", "vehicle")
PER_AREA = ("vryieldvol", "vryieldmas", "wetmass", "drymatter", "moisture", "yld_vol_dr", "yld_vol_we", "yld_mass_w",
            "yld_mass_d", "moisture_p")
MEAN_COLS = ("elevation", "elevation_")
SQFT_PER_AC = 43560.0
SAMPLE_N = 20000                # records drawn at random to tell a section export from a per-second one
SAMPLE_SHARED = 0.05            # ... a section export when more than this share of them shares its machine and second


def _col(columns, names):
    low = {c.lower(): c for c in columns}
    return next((low[n] for n in names if n in low), None)


def is_section_export(path: str) -> bool:
    """A point export with a section column in which more than SAMPLE_SHARED of SAMPLE_N records drawn at random
    share their machine and second with another drawn record. Random, not the first records: an export can be
    ordered by section (each section's whole track, then the next), where neighbouring records never
    share a second. A per-second export shares none."""
    import pyogrio
    info = pyogrio.read_info(path)
    cols = list(info["fields"])
    scol, tcol = _col(cols, SECTION_COLS), _col(cols, TIME_COLS)
    n = int(info["features"])
    if not scol or not tcol or n < 2:
        return False
    keys = [c for c in (_col(cols, MACHINE_COLS), tcol) if c]
    fids = None if n <= SAMPLE_N else np.sort(np.random.default_rng(0).choice(n, SAMPLE_N, replace=False))
    d = pyogrio.read_dataframe(path, columns=keys, read_geometry=False, fids=fids)
    return bool(d.duplicated(keys, keep=False).mean() > SAMPLE_SHARED)


def collapse(path: str, out_shp: str, log=print) -> dict:
    """Write out_shp: one point per machine and second from the section-element export at path (module doc), in
    the order machine, then time. Two reads keep the memory low on a large export (millions of elements): the
    keys, the numbers and the positions of every element, then every column of the first element of each second.
    Returns {"n_in", "n_out", "dropped", "header_ft", "area_ac", "machine_col", "time_col", "crop",
    "monitor_product", "machine_serials", "machines_renamed"} (crop and monitor_product None, machine_serials {}
    without the export's Deere metadata; machine_serials = {Machine column value: serial number (VIN)},
    detect.machine_serials). The Machine column of the result carries those serials in place of the export's index
    ('1', '2' ...: machines_renamed), as SMS names the datasets of the export, so that the per-combine cleaning, the
    clean file and the ISOXML devices name the combines."""
    import geopandas as gpd
    import pyogrio
    cols = list(pyogrio.read_info(path)["fields"])
    scol, tcol, mcol = _col(cols, SECTION_COLS), _col(cols, TIME_COLS), _col(cols, MACHINE_COLS)
    dcol, wcol = _col(cols, ("distance", "distance_f")), _col(cols, ("swathwidth", "swth_wdth_"))
    if not (tcol and dcol and wcol):
        raise ValueError(f"{os.path.basename(path)}: a section export needs time, distance and width columns")
    keys = [c for c in (mcol, tcol) if c]
    numeric = [c for c in cols if c.lower() in PER_AREA or c.lower() in MEAN_COLS]
    g = pyogrio.read_dataframe(path, columns=list(dict.fromkeys(keys + [dcol, wcol] + numeric)))
    crs = g.crs
    num = lambda c: pd.to_numeric(g[c], errors="coerce").to_numpy(float)  # noqa: E731
    dist, wid = num(dcol), num(wcol)
    area = np.where(np.isfinite(dist) & np.isfinite(wid), dist * wid, 0.0)
    act = area > 0
    gid = g.groupby(keys, sort=False, dropna=False).ngroup().to_numpy()   # numbered in the order first seen
    n_grp = int(gid.max()) + 1 if len(gid) else 0
    a_sum = np.bincount(gid, weights=area, minlength=n_grp)
    w_sum = np.bincount(gid, weights=np.where(act, wid, 0.0), minlength=n_grp)
    w_max = float(np.nanmax(wid)) if len(wid) else 0.0
    p99 = float(np.percentile(w_sum[a_sum > 0], 99)) if (a_sum > 0).any() else 0.0
    header = w_max if w_max > 0.9 * p99 else p99
    ok = a_sum > 0
    first = pd.Series(np.arange(len(g))).groupby(gid).first().to_numpy()    # increasing, as the groups are numbered
    out = pyogrio.read_dataframe(path, fids=first, read_geometry=False)
    out = out.drop(columns=[c for c in (scol,) if c]).reset_index(drop=True)
    na = np.full(n_grp, np.nan)

    def wmean(v):
        s = np.bincount(gid, weights=np.where(act & np.isfinite(v), v * area, 0.0), minlength=n_grp)
        a = np.bincount(gid, weights=np.where(act & np.isfinite(v), area, 0.0), minlength=n_grp)
        return np.divide(s, a, out=na.copy(), where=a > 0)

    def amean(v):
        s = np.bincount(gid, weights=np.where(act & np.isfinite(v), v, 0.0), minlength=n_grp)
        n = np.bincount(gid, weights=(act & np.isfinite(v)).astype(float), minlength=n_grp)
        return np.divide(s, n, out=na.copy(), where=n > 0)

    out[dcol] = np.divide(a_sum, w_sum, out=na.copy(), where=w_sum > 0)
    out[wcol] = np.minimum(w_sum, header)
    for c in numeric:
        out[c] = wmean(num(c)) if c.lower() in PER_AREA else amean(num(c))
    x = amean(g.geometry.x.to_numpy(float))
    y = amean(g.geometry.y.to_numpy(float))
    out["N_Elem"] = np.bincount(gid, minlength=n_grp).astype(int)
    n_in = len(g)
    del g
    out = gpd.GeoDataFrame(out, geometry=gpd.points_from_xy(x, y), crs=crs)[ok]
    # machine, then time: an export ordered by section first sees the seconds of its first section's track
    t = pd.to_datetime(out[tcol], errors="coerce", utc=True, format="ISO8601" if tcol.lower() == "isotime" else None)
    order = (np.lexsort((t.dt.tz_localize(None).to_numpy(), out[mcol].to_numpy() if mcol else np.zeros(len(out))))
             if len(out) and t.notna().all() else np.arange(len(out)))
    out = out.iloc[order].reset_index(drop=True)
    # the combines by the serial numbers the metadata gives their index ('1', '2' ... in the Machine column), as SMS
    # names the datasets it reads from the export; an index without a serial keeps its value
    meta_src = os.path.splitext(path)[0] + "-Deere-Metadata.json"
    serials = {}
    if mcol and os.path.isfile(meta_src):
        from oced_clean._engine.formats import detect as _detect
        serials = dict((_detect._deere_metadata(path) or {}).get("machines") or {})
        if serials:
            v = out[mcol].astype(str).str.strip()
            out[mcol] = v.map(lambda s: serials.get(s, s)).astype(object)
    os.makedirs(os.path.dirname(out_shp) or ".", exist_ok=True)
    pyogrio.write_dataframe(out, out_shp)
    res = {"n_in": int(n_in), "n_out": int(len(out)), "dropped": int((~ok).sum()), "header_ft": round(header, 2),
           "area_ac": float((out[dcol] * out[wcol]).sum() / SQFT_PER_AC), "machine_col": mcol, "time_col": tcol,
           "crop": None, "monitor_product": None, "machine_serials": {},
           "machines_renamed": {k: s for k, s in serials.items()} if serials else {}}
    if os.path.isfile(meta_src):
        import shutil
        from oced_clean._engine.formats import common, detect
        shutil.copyfile(meta_src, os.path.splitext(out_shp)[0] + "-Deere-Metadata.json")
        meta = detect._deere_metadata(path) or {}
        res["crop"] = meta.get("crop")
        res["machine_serials"] = dict(meta.get("machines") or {})
        if meta.get("crop") or meta.get("crop_id") is not None:
            res["monitor_product"] = common.jd_monitor_product(
                meta.get("crop"), f"CropName / CropId of {os.path.basename(meta_src)} (Operations Center)",
                crop_id=meta.get("crop_id"))
    log(f"{os.path.basename(path)}: {res['n_in']:,} section elements -> {res['n_out']:,} readings (one per "
        f"{'machine and ' if mcol else ''}second; header {header:g} ft; {res['dropped']:,} seconds without an area "
        f"dropped)")
    return res
