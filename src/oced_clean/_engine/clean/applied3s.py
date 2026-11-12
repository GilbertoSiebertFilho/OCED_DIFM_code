# -*- coding: utf-8 -*-
"""OCED: the 3-sigma cleaning of any point layer of a machine (planter and sprayer as-applied, or other points), the
rule of the SMS 3-sigma sequence applied to the attributes the user chooses.

    from oced_clean._engine.clean import applied3s as a3
    op = a3.detect_operation(columns)          # {"operation": "seeding", "rates": [...], "speed": ..., ...}
    R = a3.run("seeding.shp", "out", {"attributes": ["Rt_Apd_Ct_", "Speed_mph_"]}, boundary="field.geojson")
    R["files"]["clean_zip"]                    # the kept points with every attribute

Steps (on every reading of the file, in this order):
  S0  import check: no position, or the main attribute (the first rate) missing or <= min_value ("auto": 0 when the
      main attribute is an applied rate - no product / no seed applied: sections off, the planter raised at the
      headland -, no minimum for other point data).
  S1  Clip To Field Boundary (with a boundary file; a point within clip_tolerance_m of the edge is inside). The
      readings outside the boundary that S0 already removed stay counted under S0; the summary counts every reading
      outside (n_outside) and every reading inside (n_inside), so "kept of the readings inside the boundary" and
      "removed %" are those of the boundary.
  S2, S3 ...  one 3-sigma step per chosen attribute, the speed first when it is chosen: keep mean +/- k SD of the points
      still alive (ddof 0, the plain point statistics of SMS Edit Legend), the limits rounded half up to 2 decimals
      as typed in SMS, inclusive; an attribute with no negative value (rates, speeds) has its lower limit floored at
      0, one with negative values is not floored. A reading with no value in the attribute is not removed by that
      step (Filter Data cannot put a missing value outside its limits, the rule of the harvest steps in
      sms3sigma.py; not established against SMS: synthetic data only), counted apart (n_missing) and warned about
      once per step. The main attribute (`repeat`, default the first rate) is filtered again (S<n>b,
      c ...) in every group whose previous pass cut its SD by more than sd_drop_pct (25 %) and removed something, as
      SMS's yield passes: each group stops on its own (passes_by_group); at most max_passes.
Mean and SD per group: auto = per machine when the file has a machine column with more than one machine, else the
whole field; or "dataset", "machine", "none".

There is no Optimize Swath Widths here: it is the harvest step of SMS (overlapping grain strips). Outputs in out_dir:
<tag>_3s_clean.shp/.zip (the kept readings with every attribute of the file, in the file's coordinate system),
<tag>_3s_flagged.shp/.zip (every reading with Keep and Rem_By, the step that removed it), <tag>_3s_cleaning.json and
the report <tag>_3s_cleaning_report.pdf (engine/report/applied_report.py) with its figures.
"""
from __future__ import annotations

import json
import os
import re
import zipfile

import numpy as np
import pandas as pd

from oced_clean._engine.clean.sms3sigma import _sd, sigma_limits, round_half_up  # noqa: F401  (the SMS rule itself)

DEFAULTS = {
    "attributes": None,         # columns filtered with 3-sigma, in order (None = the rate found + the speed)
    "repeat": None,             # the attribute filtered again while its SD falls (None = the first rate chosen)
    "min_value": "auto",        # S0: the main attribute <= this is removed (None = no import check on it; "auto" =
                                # 0 when the main attribute is an applied rate, else None)
    "k_sd": 3.0,
    "round_decimals": 2,
    "max_passes": 5,
    "sd_drop_pct": 25.0,
    "group_by": "auto",         # auto | dataset | machine | none
    "clip": True,               # S1 (needs a boundary)
    "clip_tolerance_m": 0.01,
}
GROUP_MODES = ("auto", "dataset", "machine", "none")
TAG_SUFFIX = "_3s"
REPORT_SUFFIX = TAG_SUFFIX + "_cleaning_report.pdf"     # <tag> + this: the report, the longest name run() writes
PASS_LETTERS = "abcdefghij"
SHP_EXTS = (".shp", ".shx", ".dbf", ".prj", ".cpg")

# ---- what the columns of an as-applied export are (SMS, John Deere Operations Center, Climate and others)
# SMS: Rt_Apd_Ct_ (applied count, seeds/ac), Rt_Apd_Ms_ (mass, lb/ac), Rt_Apd_Vol (volume, gal/ac), Rt_Apd_Lq_;
# Rt_Tgt_* the target; Ops Center: AppliedRate / Applied_Ra, TargetRate / Target_Rat; Seed_Rate, VRAPPRATE ...
_RATE_RE = re.compile(r"(rt_?apd|appl|applied|app_?rate|apprate|seed_?rate|seeding|^rate|_rate|rate_|population|"
                      r"pop_|dose|vrapp)", re.I)
_TARGET_RE = re.compile(r"(tgt|target|trgt|prescr|rx|control)", re.I)
_SPEED_RE = re.compile(r"(speed|spd|velocity|vel_)", re.I)
_PRODUCT_RE = re.compile(r"^(product|prdct|prod_?name|product_n|variety|hybrid|crop)", re.I)
_MACHINE_RE = re.compile(r"^(machine|vehicle|implement|vin|serial)", re.I)
_DATASET_RE = re.compile(r"^(dataset|task|load|operation|log|session)", re.I)
_SEED_RE = re.compile(r"(ct_|_ct|count|seed|pop|plant|kseed|ksds)", re.I)
_LIQUID_RE = re.compile(r"(vol|lq|liq|gal|l_ha|lpha|gpa|spray)", re.I)
_MASS_RE = re.compile(r"(ms_|_ms|mass|lb|kg|dry|gran)", re.I)
_YIELD_RE = re.compile(r"(yld|yield|vryield)", re.I)
UNIT_BY_NAME = {"rt_apd_ct_": "seeds/ac", "rt_apd_ms_": "lb/ac", "rt_apd_vol": "gal/ac", "rt_apd_lq_": "gal/ac",
                "rt_tgt_ct_": "seeds/ac", "rt_tgt_ms_": "lb/ac", "rt_tgt_vol": "gal/ac", "speed_mph_": "mph",
                "speed_mph": "mph", "speed_km_h": "km/h", "speed_kmh": "km/h"}
OPERATION_LABEL = {"harvest": "Harvest (yield)", "seeding": "Seeding (planter)",
                   "application": "Application (sprayer / spreader)", "points": "Point data"}
LABEL_BY_MASS = "Application or seeding by mass (spreader / air drill)"   # a mass rate whose product is no crop


def _numeric_cols(df: pd.DataFrame, cols) -> list:
    out = []
    for c in cols:
        s = df[c] if c in df else None
        if s is None:
            continue
        if pd.api.types.is_numeric_dtype(s) or pd.to_numeric(s, errors="coerce").notna().mean() > 0.9:
            out.append(c)
    return out


def detect_operation(columns, df: pd.DataFrame | None = None) -> dict:
    """What a point layer is, from its column names (and values when df is given): {"operation": harvest | seeding |
    application | points, "label", "rates": [applied-rate columns], "targets": [target-rate columns], "speed",
    "product", "machine", "dataset", "units": {column: unit}}. A layer with a yield column is a harvest; one with an
    applied rate is seeding when the rate is a count (seeds, population) or a mass whose product column names a crop
    (a drill seeding wheat by the pound: SMS uses the same mass rate for seed and for fertilizer), else an
    application (a mass rate with no product values to read is labelled LABEL_BY_MASS); anything else is plain point
    data (every numeric attribute can still be filtered)."""
    cols = [c for c in columns if c not in ("geometry", "lon", "lat")]
    num = _numeric_cols(df, cols) if df is not None else list(cols)
    rates = [c for c in num if _RATE_RE.search(c) and not _TARGET_RE.search(c) and not _YIELD_RE.search(c)]
    targets = [c for c in num if _TARGET_RE.search(c) and (_RATE_RE.search(c) or re.match(r"rt_", c, re.I))]
    speed = next((c for c in num if _SPEED_RE.search(c)), None)
    product = next((c for c in cols if _PRODUCT_RE.search(c)), None)
    machine = next((c for c in cols if _MACHINE_RE.search(c)), None)
    dataset = next((c for c in cols if _DATASET_RE.search(c)), None)
    if any(_YIELD_RE.search(c) for c in cols) and not rates:
        op = "harvest"
    elif rates:
        r0 = rates[0]
        op = "seeding" if (_SEED_RE.search(r0) and not _LIQUID_RE.search(r0)) else "application"
    else:
        op = "points"
    label = OPERATION_LABEL[op]
    if op == "application" and _MASS_RE.search(rates[0]) and not _LIQUID_RE.search(rates[0]):
        crop = _product_crop(df, product)
        if crop:
            op, label = "seeding", OPERATION_LABEL["seeding"]
        elif crop is None:
            label = LABEL_BY_MASS
    units = {c: UNIT_BY_NAME[c.lower()] for c in cols if c.lower() in UNIT_BY_NAME}
    return {"operation": op, "label": label, "rates": rates, "targets": targets, "speed": speed,
            "product": product, "machine": machine, "dataset": dataset, "units": units}


def _product_crop(df, product):
    """The crop key (engine.sms_import.crop_key) the most common value of the product column of a mass-rate layer
    names; False when it names something else (a fertilizer); None when there are no values to read."""
    if df is None or not product or product not in df:
        return None
    v = df[product].astype(str).replace({"": np.nan, "nan": np.nan, "None": np.nan}).dropna()
    if not len(v):
        return None
    from oced_clean._engine.sms_import import crop_key
    return crop_key(str(v.mode().iloc[0])) or False


def merge_settings(user: dict | None) -> dict:
    S = dict(DEFAULTS)
    for k, v in (user or {}).items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown setting {k!r} (known: {', '.join(DEFAULTS)})")
        S[k] = v
    S["k_sd"] = float(S["k_sd"])
    if not 0.5 <= S["k_sd"] <= 10:
        raise ValueError(f"k_sd must be between 0.5 and 10, not {S['k_sd']}")
    S["max_passes"] = int(S["max_passes"])
    if not 1 <= S["max_passes"] <= len(PASS_LETTERS):
        raise ValueError(f"max_passes must be 1-{len(PASS_LETTERS)}")
    if S["group_by"] not in GROUP_MODES:
        raise ValueError(f"group_by must be one of {', '.join(GROUP_MODES)}")
    if isinstance(S["min_value"], str) and S["min_value"].strip().lower() == "auto":
        S["min_value"] = "auto"
    elif S["min_value"] is not None:
        S["min_value"] = float(S["min_value"])
    if S["attributes"] is not None and not isinstance(S["attributes"], (list, tuple)):
        raise ValueError("attributes must be a list of column names")
    return S


def _zip(paths, zpath, names=None) -> str:
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for i, p in enumerate(paths):
            if os.path.exists(p):
                z.write(p, names[i] if names else os.path.basename(p))
    return zpath


def _write(gdf, path) -> str:
    """A point shapefile (+ .cpg UTF-8) and its zip; the attribute names are kept (<= 10 characters already)."""
    for e in SHP_EXTS:
        if os.path.exists(os.path.splitext(path)[0] + e):
            os.remove(os.path.splitext(path)[0] + e)
    gdf.to_file(path, engine="pyogrio", encoding="UTF-8")
    base = os.path.splitext(path)[0]
    return _zip([base + e for e in SHP_EXTS], base + ".zip")


def _groups(df: pd.DataFrame, op: dict, mode: str) -> tuple:
    """(key per reading, {"mode", "rule", "groups"})."""
    n = len(df)
    mcol, dcol = op.get("machine"), op.get("dataset")
    if mode == "auto":
        if mcol and df[mcol].astype(str).nunique() > 1:
            mode = "machine"
        else:
            mode = "none"
    if mode == "machine" and mcol:
        key = df[mcol].astype(str).to_numpy()
        rule = f"per machine ({mcol})"
    elif mode == "dataset" and dcol:
        key = df[dcol].astype(str).to_numpy()
        rule = f"per dataset ({dcol})"
    else:
        if mode in ("machine", "dataset"):
            rule = f"one group for the field (no {mode} column)"
        else:
            rule = "one group for the field"
        mode, key = "none", np.full(n, "field", dtype=object)
    return key, {"mode": mode, "rule": rule, "groups": list(pd.unique(key))}


def run(shp: str, out_dir: str, settings: dict | None = None, boundary: str | None = None, feature=None,
        tag: str | None = None, log=print, inputs_left_out=None) -> dict:
    """Clean the point layer `shp` with the 3-sigma steps above; returns the result dict (also written as JSON).
    inputs_left_out: the inputs left out when the data was opened (engine.run.open_input: a damaged zip among the
    zips given), one sentence each: R['inputs_left_out'] (always there) and one warning each ('input: ...')."""
    import geopandas as gpd
    import pyogrio
    S = merge_settings(settings)
    shp = os.path.abspath(shp)
    tag = (tag or os.path.splitext(os.path.basename(shp))[0])
    tag3 = tag + TAG_SUFFIX
    os.makedirs(out_dir, exist_ok=True)
    g0 = pyogrio.read_dataframe(shp)
    if g0.crs is None:
        raise ValueError(f"{os.path.basename(shp)} has no coordinate system (.prj): the clip and the maps need one")
    n = len(g0)
    if n < 3:
        raise ValueError(f"{os.path.basename(shp)} holds {n} point(s): nothing to clean")
    df = pd.DataFrame(g0.drop(columns="geometry"))
    op = detect_operation(list(df.columns), df)
    warnings, notes = [], []
    left_out = []
    for w in inputs_left_out or []:
        if str(w) not in left_out:
            left_out.append(str(w))
    warnings += [f"input: {w}" for w in left_out]
    attrs = list(S["attributes"]) if S["attributes"] else (op["rates"][:1] + ([op["speed"]] if op["speed"] else []))
    missing = [a for a in attrs if a not in df.columns]
    if missing:
        raise ValueError(f"no column {', '.join(missing)} in {os.path.basename(shp)}")
    if not attrs:
        raise ValueError("no attribute to filter: choose the attributes (an applied rate, the speed ...)")
    vals = {a: pd.to_numeric(df[a], errors="coerce").to_numpy(float) for a in attrs}
    rates_chosen = [a for a in attrs if a != op["speed"]]
    main = S["repeat"] if S["repeat"] in attrs else (rates_chosen[0] if rates_chosen else attrs[0])
    order = ([op["speed"]] if op["speed"] in attrs else []) + [a for a in attrs if a not in (op["speed"], main)] + [main]
    order = list(dict.fromkeys(order))
    unit = {a: op["units"].get(a, "") for a in attrs}
    k, dec = float(S["k_sd"]), S["round_decimals"]

    alive = np.ones(n, bool)
    why = np.array([""] * n, dtype=object)
    cascade, steps = [], []

    def record(fid, name, crit, kill, on=True):
        kill = np.asarray(kill, bool) & alive
        why[kill] = fid
        alive[kill] = False
        cascade.append({"filter": fid, "name": name, "on": bool(on), "criterion": crit, "removed": int(kill.sum()),
                        "remaining": int(alive.sum())})
        log(f"  {fid:<4} removed {int(kill.sum()):>7,}  remaining {int(alive.sum()):>7,}  ({crit})")

    # ---- S0 import check
    geo = g0.geometry
    pos_bad = geo.is_empty.to_numpy() | ~np.isfinite(geo.x.to_numpy(float)) | ~np.isfinite(geo.y.to_numpy(float))
    is_rate = main in op["rates"]
    mv = S["min_value"]
    if mv == "auto":
        mv = 0.0 if is_rate else None
    S["min_value_used"] = mv
    vmain = vals[main]
    bad_main = ~np.isfinite(vmain) | ((vmain <= mv) if mv is not None else np.zeros(n, bool))
    record("S0", "Import check", "no position, or " + main + " missing"
           + ((f" or <= {mv:g} {unit[main]}".rstrip() + (" (no product applied)" if is_rate else ""))
              if mv is not None else ""),
           pos_bad | bad_main)

    # ---- S1 clip to the field boundary
    bnd_R = None
    utm = g0.estimate_utm_crs()
    if boundary and S["clip"]:
        from oced_clean._engine import run as er
        from oced_clean._engine.clean import aoi as aoi_mod
        P = er.read_plan(boundary, feature=feature)
        if P.get("needs_choice"):
            raise ValueError(f"{os.path.basename(boundary)} holds several fields: choose which one is this field")
        gu = g0.to_crs(utm)
        inside = aoi_mod.inside(P["plan"], gu.geometry.x.to_numpy(float), gu.geometry.y.to_numpy(float), utm,
                                float(S["clip_tolerance_m"]))
        outside = ~inside & ~pos_bad
        before_s0 = int((outside & ~alive).sum())            # outside, but already removed by S0
        record("S1", "Clip To Field Boundary", "outside the field boundary (a point within "
               f"{100 * float(S['clip_tolerance_m']):g} cm of the edge is inside)"
               + (f"; {before_s0:,} more outside were already removed by S0" if before_s0 else ""), outside)
        bnd_R = {"file": os.path.abspath(boundary), "area_ac": float(P["area_ac"]), "feature": P.get("feature"),
                 "n_outside": int(outside.sum()), "n_outside_s0": before_s0, "is_plan": bool(P.get("is_plan"))}
        for w in P.get("warnings") or []:
            warnings.append(f"boundary: {w}")
    else:
        cascade.append({"filter": "S1", "name": "Clip To Field Boundary", "on": False,
                        "criterion": "no boundary given: nothing clipped" if not boundary else "switched off",
                        "removed": 0, "remaining": int(alive.sum())})

    # ---- the 3-sigma steps
    gkey, ginfo = _groups(df, op, S["group_by"])
    num = 2

    # the lower limit is floored at 0 only for an attribute with no negative value (rates and speeds)
    floor = {}
    for a in attrs:
        fin = vals[a][np.isfinite(vals[a])]
        floor[a] = 0.0 if (fin.size == 0 or float(fin.min()) >= 0) else None

    def lim_txt(lo, hi):
        d = dec or 2
        return f"{lo:.{d}f} to {hi:.{d}f}" if lo < 0 else f"{lo:.{d}f}-{hi:.{d}f}"

    def sigma(fid, a, groups=None):
        kill = np.zeros(n, bool)
        mine = []
        n_miss = 0
        for gname in (ginfo["groups"] if groups is None else groups):
            sel = alive & (gkey == gname)
            lim = sigma_limits(vals[a][sel], k, floor[a], dec)
            if lim is None:
                steps.append(dict(group=gname, step=fid, attribute=a, unit=unit[a], n_before=int(sel.sum()),
                                  mean=None, sd=None, lo=None, hi=None, removed=0, n_missing=0,
                                  n_after=int(sel.sum()), sd_after=None, sd_drop_pct=None,
                                  note="fewer than 2 values or SD = 0: no limit"))
                continue
            nn, mu, sd, lo, hi = lim
            v = vals[a][sel]
            miss = ~np.isfinite(v)
            # no value: not outside the limits, kept by this filter and counted apart (as sms3sigma.sigma_step)
            out_ = ~miss & ~((v >= lo) & (v <= hi))
            kill[np.where(sel)[0][out_]] = True
            sda = _sd(v[~out_])
            drop = 100 * (1 - sda / sd) if sd > 0 and np.isfinite(sda) else None
            row = dict(group=gname, step=fid, attribute=a, unit=unit[a], n_before=int(sel.sum()), mean=mu, sd=sd,
                       lo=lo, hi=hi, removed=int(out_.sum()), n_missing=int(miss.sum()), n_after=int((~out_).sum()),
                       sd_after=sda, sd_drop_pct=drop, note=None)
            n_miss += int(miss.sum())
            steps.append(row)
            mine.append(row)
        u = f" {unit[a]}" if unit[a] else ""
        if len(mine) == 1:
            crit = f"{a} outside mean +/- {k:g} SD: {lim_txt(mine[0]['lo'], mine[0]['hi'])}{u}"
        elif mine:
            n_all = len(ginfo["groups"])
            on = f"{len(mine)} groups" if len(mine) == n_all else f"{len(mine)} of {n_all} groups"
            crit = (f"{a} outside mean +/- {k:g} SD {ginfo['rule']} ({on}, limits "
                    f"{lim_txt(min(s['lo'] for s in mine), max(s['hi'] for s in mine))}{u})")
        else:
            crit = f"{a} outside mean +/- {k:g} SD: no limit (fewer than 2 values or SD = 0)"
        if n_miss:
            crit += f" ({n_miss:,} with no value, not filtered on {a})"
            warnings.append(f"{fid}: {n_miss:,} reading(s) have no {a} value; they are not filtered on {a} (the mean "
                            "and SD are those of the readings that have one)")
        record(fid, f"{a} mean +/- k SD" + ("" if fid[-1].isdigit() else f", pass {PASS_LETTERS.index(fid[-1]) + 1}"),
               crit, kill)
        return mine

    passes = 0
    passes_by_group = {}
    for a in order:
        if a != main:
            sigma(f"S{num}", a)
            num += 1
            continue
        active = list(ginfo["groups"])
        for p in range(int(S["max_passes"])):
            fid = f"S{num}{PASS_LETTERS[p]}"
            mine = sigma(fid, a, active)
            passes += 1
            for g_ in active:
                passes_by_group[str(g_)] = p + 1
            # a group is filtered again only when its own SD fell by more than sd_drop_pct (as the harvest groups)
            active = [r["group"] for r in mine if r["sd_drop_pct"] is not None
                      and r["sd_drop_pct"] > float(S["sd_drop_pct"]) and r["removed"] and (r["sd_after"] or 0) > 0]
            if not active:
                break
        num += 1

    n_kept = int(alive.sum())
    if n_kept < 3:
        raise ValueError(f"only {n_kept} of the {n:,} readings survive the cleaning: check the attributes chosen")
    # ---- outputs
    F = {}
    clean = g0[alive].copy()
    F["clean_shp"] = os.path.join(out_dir, tag3 + "_clean.shp")
    F["clean_zip"] = _write(clean, F["clean_shp"])
    fl = g0.copy()
    fl["Keep"] = alive.astype(int)
    fl["Rem_By"] = why.astype(str)
    F["flagged_shp"] = os.path.join(out_dir, tag3 + "_flagged.shp")
    F["flagged_zip"] = _write(fl, F["flagged_shp"])
    # n_in (kept for the readers of older records) = the readings minus the S1 row; n_inside = every reading inside
    # the boundary (n_outside holds the outside ones S0 removed too): the base of "kept" and of removed_pct
    in_after_clip = int(n - sum(r["removed"] for r in cascade if r["filter"] in ("S1",)))
    n_outside = int(bnd_R["n_outside"]) if bnd_R else 0
    n_inside = n - n_outside
    floored = [a for a in order if floor[a] is not None]
    R = {"input": shp, "tag": tag3, "operation": op["operation"], "operation_label": op["label"], "columns": op,
         "attributes": order, "main": main, "n_read": n, "n_in": in_after_clip, "n_inside": n_inside,
         "n_outside": n_outside, "n_kept": n_kept,
         "removed_pct": float(100 * (1 - n_kept / max(n_inside, 1))), "cascade": cascade, "steps": steps,
         "passes": passes, "passes_by_group": passes_by_group,
         "grouping": ginfo | {"groups": [str(x) for x in ginfo["groups"]]},
         "settings": S, "boundary": bnd_R, "warnings": warnings, "notes": notes, "inputs_left_out": left_out,
         "crs": str(g0.crs),
         "rule": ("mean and SD recomputed per group on the points still alive before every step (ddof 0); limits "
                  f"mean +/- k SD rounded half up to {dec} decimals, inclusive"
                  + (f", the lower one floored at 0 for {', '.join(floored)}" if floored else "")
                  + f"; {main} filtered again in each group while that group's previous pass cut its SD by more "
                  f"than {S['sd_drop_pct']:g} %")}
    stats = {}
    for a in order:
        v0, v1 = vals[a][np.isfinite(vals[a])], vals[a][alive & np.isfinite(vals[a])]
        stats[a] = {"before": _stats(v0), "after": _stats(v1)}
    R["stats"] = stats
    if op.get("product"):
        pv = df[op["product"]].astype(str).replace({"": np.nan, "nan": np.nan, "None": np.nan}).dropna().unique()
        R["products"] = [str(v) for v in pv[:10]]
    F["figures"] = figures(g0, vals, alive, why, R, out_dir, tag3, boundary_geom=_boundary_geom(boundary, feature)
                           if bnd_R else None)
    F["json"] = os.path.join(out_dir, tag3 + "_cleaning.json")
    F["report_pdf"] = os.path.join(out_dir, tag + REPORT_SUFFIX)
    R["files"] = F
    from oced_clean._engine.report.applied_report import make_report
    make_report(R, F["report_pdf"])
    with open(F["json"], "w", encoding="utf-8") as fh:
        json.dump(R, fh, indent=1, default=_json_default)
    log(f"-> {F['clean_zip']}")
    return R


def _stats(v) -> dict:
    if not len(v):
        return {"n": 0}
    m, sd = float(np.mean(v)), float(np.std(v, ddof=0))
    return {"n": int(len(v)), "mean": m, "sd": sd, "cv_pct": (100 * sd / m if m else None), "min": float(np.min(v)),
            "median": float(np.median(v)), "max": float(np.max(v))}


def _json_default(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    if isinstance(v, np.ndarray):
        return v.tolist()
    return str(v)


def _boundary_geom(boundary, feature):
    from oced_clean._engine import run as er
    try:
        return er.plan_union_wgs84(er.read_plan(boundary, feature=feature))
    except Exception:
        return None


def figures(g0, vals, alive, why, R, out_dir, tag3, boundary_geom=None) -> dict:
    """The figures of the report: the map of the main attribute before and after, the points each step removed, and
    the histogram of the main attribute with the limits of its 3-sigma passes."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    out = {}
    utm = g0.estimate_utm_crs()
    gu = g0.to_crs(utm)
    x, y = gu.geometry.x.to_numpy(float), gu.geometry.y.to_numpy(float)
    x0, y0 = np.nanmin(x), np.nanmin(y)
    x, y = x - x0, y - y0
    ring = []
    if boundary_geom is not None:
        import geopandas as gpd
        b = gpd.GeoSeries([boundary_geom], crs="EPSG:4326").to_crs(utm).iloc[0]
        for p in getattr(b, "geoms", [b]):
            bx, by = p.exterior.xy
            ring.append((np.asarray(bx) - x0, np.asarray(by) - y0))
    main = R["main"]
    v = vals[main]
    fin = np.isfinite(v)
    lo, hi = (np.nanpercentile(v[alive & fin], [2, 98]) if (alive & fin).any() else (0, 1))
    fig, axs = plt.subplots(1, 2, figsize=(10, 4.6), sharex=True, sharey=True)
    for ax, sel, title in ((axs[0], fin, "before"), (axs[1], alive & fin, "after the cleaning")):
        sc = ax.scatter(x[sel], y[sel], c=v[sel], s=2.5, cmap="RdYlGn", vmin=lo, vmax=hi, linewidths=0)
        for bx, by in ring:
            ax.plot(bx, by, color="#009CDE", lw=1.2)
        ax.set_title(f"{main} {title}", fontsize=10, color="#00358E")
        ax.set_aspect("equal")
        ax.tick_params(labelsize=7)
    fig.colorbar(sc, ax=axs, shrink=0.8, label=main + (f" ({R['columns']['units'].get(main)})"
                                                     if R["columns"]["units"].get(main) else ""))
    out["map"] = os.path.join(out_dir, tag3 + "_fig_map.png")
    fig.savefig(out["map"], dpi=150, bbox_inches="tight")
    plt.close(fig)

    colors_ = {"S0": "#90A4AE", "S1": "#B0BEC5"}
    pal = ["#00B4D8", "#1565C0", "#FF5252", "#FF8F00", "#B388FF", "#76FF03"]
    fig, ax = plt.subplots(figsize=(10, 4.6))
    ax.scatter(x[alive], y[alive], s=1.5, color="#00B373", alpha=0.25, linewidths=0, label=f"kept ({int(alive.sum()):,})")
    ids = [r["filter"] for r in R["cascade"] if r["removed"]]
    for i, fid in enumerate(ids):
        sel = why == fid
        ax.scatter(x[sel], y[sel], s=5, color=colors_.get(fid, pal[i % len(pal)]), linewidths=0,
                   label=f"{fid} ({int(sel.sum()):,})")
    for bx, by in ring:
        ax.plot(bx, by, color="#009CDE", lw=1.2)
    ax.set_aspect("equal")
    ax.tick_params(labelsize=7)
    ax.legend(fontsize=7, markerscale=3, loc="upper left", bbox_to_anchor=(1.01, 1))
    ax.set_title("Readings removed by each step", fontsize=10, color="#00358E")
    out["removed"] = os.path.join(out_dir, tag3 + "_fig_removed.png")
    fig.savefig(out["removed"], dpi=150, bbox_inches="tight")
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 3.2))
    f = v[fin]
    if len(f):
        a, b = np.percentile(f, [0.5, 99.5])
        pad = 0.15 * (b - a if b > a else 1)
        ax.hist(np.clip(f, a - pad, b + pad), bins=60, color="#C9D8EC", label="before")
        ax.hist(np.clip(v[alive & fin], a - pad, b + pad), bins=60, color="#00358E", label="kept")
        for r in R["steps"]:
            if r["attribute"] == main and r["lo"] is not None:
                ax.axvline(r["lo"], color="#FF5252", lw=0.8)
                ax.axvline(r["hi"], color="#FF5252", lw=0.8)
    ax.set_xlabel(main, fontsize=8)
    ax.tick_params(labelsize=7)
    ax.legend(fontsize=7)
    ax.set_title(f"{main}: the values and the limits of each pass (red)", fontsize=10, color="#00358E")
    out["hist"] = os.path.join(out_dir, tag3 + "_fig_hist.png")
    fig.savefig(out["hist"], dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out
