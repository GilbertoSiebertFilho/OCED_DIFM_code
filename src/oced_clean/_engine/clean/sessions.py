# -*- coding: utf-8 -*-
"""Harvest sessions (machine x day) and the moisture basis of the mapped yield: diagnostics, never a correction.

    from oced_clean._engine.clean import sessions
    moist = sessions.standard_moisture(S["moisture"], source, crop, M, wet, dry)      # Ms, its source, shrink
    dry_sym = sessions.dry_symmetric(wet, M, moist["standard_pct"])                    # Yld_Dry_Sy
    day, key, info = sessions.session_keys(g, C, time_ok, t, machine)                  # Harv_Day, Session
    table, field = sessions.session_table(...); offsets = sessions.session_offsets(...)

Problem. A field harvested over several days (or by several combines on one day) is a set of sessions with different
grain moisture, different sensor state and sometimes different calibration. The mapped "dry" yield of an SMS or Ops
Center export is the wet yield shrunk to a standard moisture Ms, but HOW it was shrunk matters when the grain is drier
than Ms: SMS and the Ag Leader displays shrink one-sided (the factor (100 - M) / (100 - Ms) is applied only when
M > Ms, never above 1), so a session drier than Ms is mapped at its wet volume while a wetter one is shrunk, and the
two sessions differ in yield for the same dry matter. The agronomic dry-matter basis is the
symmetric shrink (both ways, Luck & Fulton 2014: dry yield = wet x (100 - M) / (100 - Ms)); engine/formats/common.py
offers both for decoded raw data. A step in moisture between sessions can also be the sensor and not the grain
(Section 4.2 of the literature review: the capacitance sensor's fill artefact). Nothing here is corrected: the module measures and reports, and recommends which column to map.

Standard moisture Ms, in order: settings.moisture.standard_pct > the decoder report (source: options.std_moisture_pct
of a decoded raw dataset) > inferred from the export (records where dry / wet < 0.999: Ms = 100 - (100 - M) / ratio,
the median over >= 200 such records) > the crop table (engine.formats.common.CROPS,
the house standard per crop). Shrink detection ("auto"): if dry == wet on >= 95 % of the records with M < Ms the
mapped dry yield was shrunk one-sided, else symmetric (settings.moisture.shrink can fix it; a decoded dataset carries
its own option). R["moisture"] = {standard_pct, source, shrink, n_below_standard, share_below_standard}.

Yld_Dry_Sy (N 15.4) = wet yield x (100 - M) / (100 - Ms), the symmetric dry basis, when a wet yield (or wet mass +
test weight) and moisture exist. Under symmetric shrink it equals the mapped dry yield; under one-sided shrink it is
ABOVE the mapped yield wherever M < Ms (drier grain holds more dry matter per wet bushel: (100 - M) / (100 - Ms) > 1),
so a one-sided map under-states the dry sessions against the wet ones.

Sessions. Session = (machine, calendar day of the record time); without a time of day (the SMS 'Time' is a date) the
dataset (task) is the session, its day read from the date in the dataset name ('YY/MM/DD-HH:MM:SS' of an SMS task,
'M/D/YYYY' of an Ops Center task name such as 'TaskName 1/2/2000 (GreenStar 4 Monitor)') or, failing that, from the
date-only time column (clean() passes it as date_col). The calendar day of UTC records is taken in local time: the
decoder's time-zone offset only when the decoder reports time_basis 'UTC' (the John Deere and CNH decoders shift the
timestamps themselves when tz_offset_h is given and then report 'local'), else round(longitude / 15) h.
Columns Harv_Day (C 10) and Session (C 32) go to the flagged and clean files so that the maps can use the day as a
block. R["sessions"]["table"] has one row per session (machine, day, datasets, start, end, n_readings, n_kept, area_ac,
wet_bu, mean_moisture, sd_moisture, share_below_standard_kept, mean_wet_yield, mean_dry_yield as mapped, mean_dry_sym;
the means and the share over the KEPT points, while R["moisture"]["share_below_standard"] is over every processed
record) and R["sessions"]["field"] the field mean of mass per area over ALL processed readings
(the integral, what a scale ticket measures) against the cleaned-map mean (the mean of the kept points, which is not
the field total: the cleaning removes low-yield headland and overlap records, so the cleaned-map mean is higher).

Discontinuity check (diagnostic): for consecutive sessions of the same machine, cells of 30 m holding >= 5 kept points
of both sessions (adjacent passes; same plot when a plan exists; the records with <= 2 % overlap first, every kept
record when those are too few for the gate, the row's "pairs" says which) give the yield ratio session 2 / session 1
as a
geometric mean with a block-bootstrap CI over pass pairs (engine/clean/machines.py) and the moisture difference.
Recommendation: when any pair of sessions differs by more than 2 moisture points, or more than 20 % of the readings
fall below Ms under one-sided shrink (house rules), map Yld_Dry_Sy as the yield and keep Harv_Day as a block.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from oced_clean._engine.clean import machines

MIN_INFER_RECORDS = 200          # records with dry / wet < 0.999 needed to infer Ms from the export
ONE_SIDED_SHARE = 0.95           # dry == wet on >= 95 % of the records below Ms -> one-sided shrink
MOISTURE_STEP_PTS = 2.0          # recommendation: sessions differ by > 2 moisture points (house rule)
BELOW_STANDARD_SHARE = 0.20      # recommendation: > 20 % of the readings below Ms under one-sided shrink (house rule)
MIN_OFFSET_CELLS = 10            # discontinuity check needs >= 10 paired cells and >= 3 pass pairs
MIN_OFFSET_PASS_PAIRS = 3
SESSION_WIDTH, DAY_WIDTH = 32, 10      # Session = "<machine>|<YYYY-MM-DD>": a 17-character VIN + the day = 28

DEFAULTS = {"standard_pct": None, "shrink": "auto", "add_symmetric_column": True}


# ===================================================================================================== moisture
def parse_date_in_name(s) -> str | None:
    """'YYYY-MM-DD' from a dataset name: the SMS task 'YY/MM/DD-HH:MM:SS', a 'YYYY-MM-DD', or the Ops Center task name
    'M/D/YYYY' (e.g. 'TaskName 1/2/2000 (GreenStar 4 Monitor)'; 'D/M/YYYY' when the first number cannot be a month);
    else None."""
    s = str(s)
    m = re.search(r"(\d\d)/(\d\d)/(\d\d)-\d\d:\d\d:\d\d", s)
    if m:
        return f"20{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"(\d{4})-(\d\d)-(\d\d)", s)
    if m:
        return m.group(0)
    m = re.search(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)", s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        mo, d = (a, b) if a <= 12 else (b, a)                 # 25/1/2000 can only be day/month
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{y:04d}-{mo:02d}-{d:02d}"
    return None


def _source_std_moisture(source) -> float | None:
    """The decoder's standard moisture from a clean(source=...) dict, when it is a single number."""
    if not isinstance(source, dict):
        return None
    cands = []
    opts = source.get("options")
    if isinstance(opts, dict):
        cands.append(opts.get("std_moisture_pct"))
    cal = source.get("calibration")
    if isinstance(cal, dict):
        cands.append(cal.get("std_moisture_pct"))
    cands.append(source.get("std_moisture_pct"))
    summ = source.get("calibration_summary")
    if isinstance(summ, str):
        m = re.search(r"standard moisture ([0-9.]+) %", summ)
        if m:
            cands.append(m.group(1))
    for v in cands:
        if isinstance(v, dict):
            v = list(v.values())
        if isinstance(v, (list, tuple, set)):
            vals = {float(x) for x in v if x is not None and str(x).replace(".", "", 1).isdigit()}
            if len(vals) == 1:
                return vals.pop()
            continue
        if v is None or isinstance(v, bool):
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if 0 < f < 60:
            return f
    return None


def _source_shrink(source) -> str | None:
    if isinstance(source, dict):
        opts = source.get("options")
        if isinstance(opts, dict) and opts.get("shrink") in ("one_sided", "symmetric"):
            return opts["shrink"]
        summ = source.get("calibration_summary")
        if isinstance(summ, str):
            if "shrink one-sided" in summ:
                return "one_sided"
            if "shrink symmetric" in summ:
                return "symmetric"
    return None


def standard_moisture(Mset: dict, source, crop: str | None, M, wet=None, dry=None, crop_table=None) -> dict:
    """Resolve the standard moisture Ms and how the mapped dry yield was shrunk. M, wet, dry: arrays (wet / dry may be
    None). Returns {standard_pct, source, shrink, shrink_source, n_below_standard, share_below_standard,
    n_inferred, inferred_pct, notes, warnings}."""
    P = {**DEFAULTS, **(Mset or {})}
    notes, warnings = [], []
    M = np.asarray(M, float) if M is not None else None
    ratio = None
    if wet is not None and dry is not None:
        wet_, dry_ = np.asarray(wet, float), np.asarray(dry, float)
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where((wet_ > 0) & np.isfinite(wet_) & np.isfinite(dry_), dry_ / wet_, np.nan)
    # ---- inferred from the export
    inferred, n_inf = None, 0
    if ratio is not None and M is not None:
        sel = np.isfinite(ratio) & (ratio < 0.999) & (ratio > 0.5) & np.isfinite(M)
        n_inf = int(sel.sum())
        if n_inf >= MIN_INFER_RECORDS:
            ms = 100 - (100 - M[sel]) / ratio[sel]
            inferred = float(np.median(ms))
            spread = float(np.quantile(ms, 0.95) - np.quantile(ms, 0.05))
            if spread > 0.5:
                warnings.append(f"the standard moisture inferred from dry / wet varies ({spread:.2f} points between "
                                "the 5th and 95th percentile): the export may mix crops or standards")
    # ---- Ms in order
    Ms, src = None, None
    if P.get("standard_pct") is not None:
        Ms, src = float(P["standard_pct"]), "settings"
    elif _source_std_moisture(source) is not None:
        Ms, src = _source_std_moisture(source), "decoder report"
    elif inferred is not None:
        Ms, src = round(inferred, 2), f"inferred from dry / wet yield of the export ({n_inf:,} records)"
    elif crop and crop_table and crop in crop_table:
        Ms, src = float(crop_table[crop][1]), f"crop table ({crop})"
    if Ms is None:
        warnings.append("standard moisture unknown (no settings.moisture.standard_pct, no decoder value, fewer than "
                        f"{MIN_INFER_RECORDS} shrunk records in the export, no known crop): no Yld_Dry_Sy column and no "
                        "share below the standard")
    elif inferred is not None and abs(inferred - Ms) > 0.3 and src != "inferred":
        warnings.append(f"the standard moisture {Ms:g} % ({src}) differs from the one the export implies "
                        f"({inferred:.2f} % from dry / wet on {n_inf:,} records)")
    # ---- shrink
    shrink, shrink_src = P.get("shrink", "auto"), "settings"
    if shrink == "auto":
        s_src = _source_shrink(source)
        if s_src is not None:
            shrink, shrink_src = s_src, "decoder report"
        elif ratio is not None and M is not None and Ms is not None:
            below = np.isfinite(ratio) & np.isfinite(M) & (M < Ms)
            if below.sum() >= 30:
                share_eq = float((np.abs(ratio[below] - 1) < 1e-6).mean())
                shrink = "one_sided" if share_eq >= ONE_SIDED_SHARE else "symmetric"
                shrink_src = f"detected: dry == wet on {100 * share_eq:.2f} % of the {int(below.sum()):,} records below Ms"
            else:
                shrink, shrink_src = "unknown", "too few records below the standard to tell"
        else:
            shrink, shrink_src = "unknown", "no wet and dry yield columns to compare"
    n_below = share_below = None
    if Ms is not None and M is not None:
        fin = np.isfinite(M)
        n_below = int((M[fin] < Ms).sum())
        share_below = float(n_below / fin.sum()) if fin.any() else None
    return {"standard_pct": Ms, "source": src, "shrink": shrink, "shrink_source": shrink_src,
            "n_below_standard": n_below, "share_below_standard": share_below, "n_inferred": n_inf,
            "inferred_pct": inferred, "notes": notes, "warnings": warnings}


def dry_symmetric(wet, M, Ms: float) -> np.ndarray:
    """wet x (100 - M) / (100 - Ms): the symmetric (dry-matter) basis; NaN where wet or M is missing."""
    wet, M = np.asarray(wet, float), np.asarray(M, float)
    with np.errstate(invalid="ignore"):
        out = wet * (100.0 - M) / (100.0 - float(Ms))
    return np.where(np.isfinite(out), out, np.nan)


# ===================================================================================================== keys
def local_day_offset_h(source, lon_deg) -> tuple:
    """Hours to add to the record times before taking the calendar day: (offset, note). The decoders of the John
    Deere formats write UTC (source['time_basis'] == 'UTC'); a harvest afternoon in Alberta (UTC-6) then crosses the
    UTC midnight and a calendar day in UTC would cut one session in two. Without a time-zone offset in the decoder
    options, the solar offset round(longitude / 15) is used (civil time differs from it by at most 1-2 h, so the day
    changes within that distance of local midnight; house rule). Local or unknown times are left as they are."""
    src = source if isinstance(source, dict) else {}
    if str(src.get("time_basis") or "").upper() != "UTC":
        return 0.0, ""                        # local or unknown times: the decoder already applied any offset
    tz = src.get("tz_offset_h")
    if tz is not None:
        return float(tz), f"UTC {float(tz):+g} h (decoder time-zone offset)"
    lon = float(lon_deg) if lon_deg is not None and np.isfinite(lon_deg) else None
    if lon is None or abs(lon) > 180:
        return 0.0, ("UTC (no longitude to derive the local day)" if lon is None else
                     f"UTC (longitude {lon:.2f} is not in degrees: no local day derived)")
    off = float(round(lon / 15.0))
    return off, f"UTC {off:+g} h (solar time at longitude {lon:.2f}; the records are in UTC)"


def _day_from_column(v: pd.Series) -> np.ndarray:
    """'YYYY-MM-DD' per record from a date (or datetime) column; '' where it does not parse."""
    if pd.api.types.is_datetime64_any_dtype(v):
        tt = pd.Series(v).reset_index(drop=True)
    else:
        sv = pd.Series(v).astype(str).str.strip().reset_index(drop=True)
        try:
            tt = pd.to_datetime(sv, errors="coerce", format="ISO8601")
        except (ValueError, TypeError):
            tt = pd.to_datetime(sv, errors="coerce", format="mixed")
        if tt.notna().sum() < 0.5 * len(sv):
            tt = pd.to_datetime(sv, errors="coerce", format="mixed")
    return tt.dt.strftime("%Y-%m-%d").fillna("").to_numpy(dtype=object)


def _join_key(machine: str, part: str) -> str:
    """'<machine>|<part>' within SESSION_WIDTH characters: a too long machine id loses its head, never the part."""
    part = str(part)
    room = SESSION_WIDTH - len(part) - 1
    m = str(machine)
    if len(m) > room > 0:
        m = m[-room:]
    return f"{m}|{part}"


def session_keys(g, C: dict, time_ok: bool, t: pd.Series | None, machine: pd.Series, source=None, lon_deg=None,
                 date_col: str | None = None):
    """(harv_day, session, info) per record. With a time of day: day = the record's calendar day (local: UTC records
    are shifted by local_day_offset_h), session = machine + day. Without one: session = the dataset (its key), day
    from the dataset name's date, else the date column (`date_col`, or C['time']: clean() nulls C['time'] when the
    column has no time of day, so it passes the detected column as date_col), else "". One dataset and no date: day
    "" and session "all". A session key longer than SESSION_WIDTH keeps the day and the tail of the machine id (the
    VIN's serial end), never a truncated day."""
    n = len(g)
    dcol = date_col or C.get("time")
    dcol = dcol if (dcol and dcol in g) else None
    mach = machine.astype(str).to_numpy()
    several = len(set(mach)) > 1
    day = np.array([""] * n, dtype=object)
    if time_ok and t is not None:
        tt = pd.to_datetime(pd.Series(t).reset_index(drop=True), errors="coerce")
        off_h, off_note = local_day_offset_h(source, lon_deg)
        if off_h:
            tt = tt + pd.Timedelta(hours=off_h)
        day = tt.dt.strftime("%Y-%m-%d").fillna("").to_numpy(dtype=object)
        key = day.copy()
        basis = "machine x calendar day of the record time" + (f" ({off_note})" if off_note else "")
    elif "_ds_key" in g:
        ds = g["_ds_key"].astype(str).to_numpy()
        key = ds.copy()
        dates = {k: parse_date_in_name(k) for k in set(ds)}
        if all(v is None for v in dates.values()) and dcol:
            day = _day_from_column(g[dcol])
        else:
            day = np.array([dates.get(k) or "" for k in ds], dtype=object)
        basis = "dataset (no time of day in the records); day from the dataset name's date" + \
            ("" if any(dates.values()) else (f" / the date column {dcol!r}" if dcol else " (none found)"))
    else:
        key = np.array(["all"] * n, dtype=object)
        if dcol:
            day = _day_from_column(g[dcol])
        basis = "the whole dataset (no time, no dataset column)" + (f"; day from the date column {dcol!r}" if dcol
                                                                     else "")
    if several:
        # '<machine>|<key>', unless the key already names the machine (the combines told apart by the source at the
        # end of the dataset names, 'Task 1 (Maker Inc.)', or by dataset names that are VINs)
        key = np.array([k if (f"({m})" in str(k) or str(k).strip().upper() == str(m)) else _join_key(m, k)
                        for m, k in zip(mach, key)], dtype=object)
    key = np.array([str(k)[:SESSION_WIDTH] for k in key], dtype=object)
    day = np.array([str(d)[:DAY_WIDTH] for d in day], dtype=object)
    return day, key, {"basis": basis, "n_sessions": int(len(set(key)))}


# ===================================================================================================== table
def session_table(g, alive, session, day, machine, C: dict, y, ywet, moi, dry_sym, area_ac, Ms, t=None) -> tuple:
    """One row per session + the field summary. Means and share_below_standard_kept are over the kept points (all
    readings when none is kept); R['moisture']['share_below_standard'] is the same share over every processed record."""
    alive = np.asarray(alive, bool)
    df = pd.DataFrame({"session": np.asarray(session, dtype=object), "day": np.asarray(day, dtype=object),
                       "machine": machine.astype(str).to_numpy(), "alive": alive,
                       "y": np.asarray(y, float), "area": np.nan_to_num(np.asarray(area_ac, float)),
                       "wet": (np.asarray(ywet, float) if ywet is not None else np.nan),
                       "moi": (np.asarray(moi, float) if moi is not None else np.nan),
                       "dsym": (np.asarray(dry_sym, float) if dry_sym is not None else np.nan)})
    df["ds"] = g[C["dataset"]].astype(str).to_numpy() if C.get("dataset") else ""
    if t is not None:
        df["t"] = pd.to_datetime(pd.Series(t).reset_index(drop=True), errors="coerce").to_numpy()
    rows = []
    for key, d in df.groupby("session", sort=False):
        k = d[d.alive] if d.alive.any() else d
        wet_bu = float((np.nan_to_num(d.wet) * d.area).sum()) if ywet is not None else None
        start = end = None
        if "t" in d and d.t.notna().any():
            start, end = str(d.t.min()), str(d.t.max())
        rows.append({"session": key, "machine": str(d.machine.iloc[0]), "day": str(d.day.iloc[0]),
                     "datasets": sorted(set(d.ds)) if C.get("dataset") else [], "start": start, "end": end,
                     "n_readings": int(len(d)), "n_kept": int(d.alive.sum()), "area_ac": float(d.area.sum()),
                     "wet_bu": wet_bu,
                     "mean_moisture": (float(np.nanmean(k.moi)) if np.isfinite(k.moi).any() else None),
                     "sd_moisture": (float(np.nanstd(k.moi, ddof=1)) if np.isfinite(k.moi).sum() > 1 else None),
                     "share_below_standard_kept": (float((k.moi[np.isfinite(k.moi)] < Ms).mean())
                                                   if Ms is not None and np.isfinite(k.moi).any() else None),
                     "mean_wet_yield": (float(np.nanmean(k.wet)) if np.isfinite(k.wet).any() else None),
                     "mean_dry_yield": (float(np.nanmean(k.y)) if np.isfinite(k.y).any() else None),
                     "mean_dry_sym": (float(np.nanmean(k.dsym)) if np.isfinite(k.dsym).any() else None),
                     "means_over": "kept points" if d.alive.any() else "all readings (none kept)"})
    rows.sort(key=lambda r: ((r["start"] or ""), r["day"], r["session"]))
    area = float(df.area.sum())
    fld = {"mean_mass_per_area_all": (float((np.nan_to_num(df.wet) * df.area).sum() / area)
                                      if ywet is not None and area > 0 else None),
           "mean_mass_per_area_basis": "wet yield" if ywet is not None else None,
           "mapped_yield_per_area_all": (float((np.nan_to_num(df.y) * df.area).sum() / area) if area > 0 else None),
           "cleaned_map_mean": (float(np.nanmean(df.y[alive])) if alive.any() else None),
           "cleaned_map_mean_dry_sym": (float(np.nanmean(df.dsym[alive]))
                                        if dry_sym is not None and np.isfinite(df.dsym[alive]).any() else None),
           "area_ac": area,
           "note": "the cleaned-map mean is not the field total: it is the mean of the kept points, while the field "
                   "mean of mass per area integrates every processed reading over its swept area"}
    return rows, fld


# ===================================================================================================== offsets
def session_offsets(g, alive, session, machine, y, moi, table: list, Mset: dict | None = None) -> list:
    """Yield ratio and moisture difference between consecutive sessions of the same machine on paired 30 m cells
    (>= 5 kept points of both sessions; same plot when Plot_Id exists). Diagnostic only."""
    P = {**machines.DEFAULTS, **(Mset or {})}
    alive = np.asarray(alive, bool)
    kk = g.loc[alive, ["_x", "_y", "Pass_Id"] + [c for c in ("Plot_Id", "Overlap_f") if c in g]].copy()
    kk["_s"] = np.asarray(session, dtype=object)[alive]
    max_ovl = P.get("pair_max_overlap")
    max_ovl = float(max_ovl) if (max_ovl is not None and "Overlap_f" in kk) else None
    kk["_yv"] = np.asarray(y, float)[alive]
    if moi is not None:
        kk["_mv"] = np.asarray(moi, float)[alive]
    plot_col = "Plot_Id" if ("Plot_Id" in kk and (kk["Plot_Id"].astype(str) != "").any()) else None
    out = []
    by_m = {}
    for r in table:
        by_m.setdefault(r["machine"], []).append(r["session"])
    for m, keys in by_m.items():
        for s1, s2 in zip(keys[:-1], keys[1:]):
            # pairs on the <= 2 % overlap records first (the later session follows the earlier one and carries the
            # sub-threshold overlap, which reads low); when they are too few for the gate, every kept record is
            # paired and the row says so (a diagnostic, never a correction)
            cells = machines.paired_cells(kk, "_s", s2, s1, 30, plot_col=plot_col, max_overlap=max_ovl)
            e = machines.ratio_estimate(cells, reps=int(P["bootstrap_reps"]), seed=int(P["seed"]), ci=float(P["ci"]))
            enough = e["n_cells"] >= MIN_OFFSET_CELLS and e["n_pass_pairs"] >= MIN_OFFSET_PASS_PAIRS
            pairs = f"kept records with <= {100 * max_ovl:g} % overlap" if max_ovl is not None else "all kept records"
            if not enough and max_ovl is not None:
                cells = machines.paired_cells(kk, "_s", s2, s1, 30, plot_col=plot_col, max_overlap=None)
                e = machines.ratio_estimate(cells, reps=int(P["bootstrap_reps"]), seed=int(P["seed"]), ci=float(P["ci"]))
                enough = e["n_cells"] >= MIN_OFFSET_CELLS and e["n_pass_pairs"] >= MIN_OFFSET_PASS_PAIRS
                pairs = ("all kept records (too few cells with <= {0:g} % overlap; the later session's records carry "
                         "the overlap below the F8 threshold and may read low)".format(100 * max_ovl))
            out.append({"machine": m, "session_1": s1, "session_2": s2, "cell_m": 30, "n_cells": e["n_cells"],
                        "n_pass_pairs": e["n_pass_pairs"], "pairs": pairs,
                        "yield_ratio_2_over_1": e["factor"] if enough else None,
                        "ci_lo": e["ci_lo"] if enough else None, "ci_hi": e["ci_hi"] if enough else None,
                        "moisture_diff_2_minus_1": (-e["moisture_diff"] if (enough and e["moisture_diff"] is not None)
                                                    else None),
                        "status": ("ok" if enough else f"too few adjacent cells ({e['n_cells']} cells, "
                                                      f"{e['n_pass_pairs']} pass pairs)")})
    return out


def recommendation(moist: dict, table: list, offsets: list, dry_sym_available: bool = True) -> dict:
    """Which yield yield column to map, from the session moistures and the shrink. dry_sym_available False (no
    Yld_Dry_Sy could be computed: standard moisture unknown, or no wet yield / moisture) -> no column is recommended,
    the text says what is missing, and Harv_Day stays the block."""
    reasons = []
    mm = [r["mean_moisture"] for r in table if r.get("mean_moisture") is not None]
    if len(mm) >= 2 and (max(mm) - min(mm)) > MOISTURE_STEP_PTS:
        reasons.append(f"the session mean moistures span {max(mm) - min(mm):.2f} points (> {MOISTURE_STEP_PTS:g})")
    for o in offsets:
        md = o.get("moisture_diff_2_minus_1")
        if md is not None and abs(md) > MOISTURE_STEP_PTS:
            reasons.append(f"sessions {o['session_1']} -> {o['session_2']} differ by {md:+.2f} moisture points on "
                           "adjacent passes")
    share = moist.get("share_below_standard")
    if moist.get("shrink") == "one_sided" and share is not None and share > BELOW_STANDARD_SHARE:
        reasons.append(f"{100 * share:.0f} % of the readings are below the standard moisture "
                       f"{moist['standard_pct']:g} % and the mapped dry yield was shrunk one-sided (not shrunk there)")
    if reasons and not dry_sym_available:
        why = ("standard moisture unknown: give the crop or moisture.standard_pct" if moist.get("standard_pct") is None
               else "no wet yield (or wet mass + test weight) or no moisture column")
        txt = (f"no Yld_Dry_Sy column could be computed ({why}); keep Harv_Day as a block: " + "; ".join(reasons))
        return {"map_column": None, "block": "Harv_Day", "text": txt, "reasons": reasons}
    if reasons:
        txt = ("map Yld_Dry_Sy as the yield and keep Harv_Day as a block: " + "; ".join(reasons))
        return {"map_column": "Yld_Dry_Sy", "block": "Harv_Day", "text": txt, "reasons": reasons}
    return {"map_column": None, "block": "Harv_Day" if len(table) > 1 else None,
            "text": "the mapped dry yield can be used as exported (sessions within 2 moisture points"
                    + (", symmetric shrink" if moist.get("shrink") == "symmetric" else "") + ")"
                    + ("; keep Harv_Day as a block" if len(table) > 1 else ""),
            "reasons": []}


# ===================================================================================================== figure
def figure(g, alive, session, day, moi, y, dry_sym, Ms, t, path, tag, palette, style_fn=None):
    """fig_clean_sessions.png: moisture and dry yield against time (or record order), coloured by session, Ms as a
    line. `palette` = (INK2, MUTED, BASE, BLUE, ORANGE, GRID, SEQ)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    if style_fn:
        style_fn(plt)
    INK2, MUTED, BASE, BLUE, ORANGE, GRID, SEQ = palette
    alive = np.asarray(alive, bool)
    sess = np.asarray(session, dtype=object)
    keys = list(dict.fromkeys(sess))
    colours = [BLUE, ORANGE, "#2a9d8f", "#8a4fff", "#c9a227", "#d64550", "#4c7a3a", "#7f8c8d"]
    if t is not None:
        tt = pd.to_datetime(pd.Series(t).reset_index(drop=True), errors="coerce").to_numpy()
        xs, xlab = tt, "time"
    else:
        xs, xlab = np.arange(len(sess)), "record (processing order)"
    fig, axs = plt.subplots(2, 1, figsize=(13, 7.2), sharex=True, gridspec_kw={"hspace": 0.12})
    moi_a = np.asarray(moi, float) if moi is not None else None
    y_a = np.asarray(y, float)
    ds_a = np.asarray(dry_sym, float) if dry_sym is not None else None
    for i, k in enumerate(keys):
        m = (sess == k) & alive
        c = colours[i % len(colours)]
        if moi_a is not None:
            axs[0].scatter(xs[m], moi_a[m], s=2, c=c, linewidths=0, rasterized=True, label=f"{k} ({int(m.sum()):,})")
        axs[1].scatter(xs[m], y_a[m], s=2, c=c, linewidths=0, rasterized=True, alpha=0.8)
        if ds_a is not None:
            axs[1].scatter(xs[m], ds_a[m], s=2, c=c, linewidths=0, rasterized=True, alpha=0.25, marker="x")
    if Ms is not None and moi_a is not None:
        axs[0].axhline(Ms, color=MUTED, lw=1.2, ls="--")
        axs[0].text(0.005, Ms, f"standard moisture {Ms:g} %", transform=axs[0].get_yaxis_transform(), color=MUTED,
                    va="bottom", fontsize=8.5)
    axs[0].set_ylabel("Moisture (%)")
    axs[0].set_title(f"{tag}: harvest sessions - moisture (top) and dry yield (bottom; dots = as mapped, crosses = "
                     "symmetric shrink Yld_Dry_Sy) of the kept points", loc="left")
    if moi_a is not None:
        axs[0].legend(frameon=False, fontsize=8, markerscale=4, loc="upper right", ncol=2, title="session (kept points)",
                      title_fontsize=8)
    axs[1].set_ylabel("Dry yield (bu/ac)")
    axs[1].set_xlabel(xlab)
    if np.isfinite(y_a[alive]).any():
        lo, hi = np.nanpercentile(y_a[alive], [1, 99])
        axs[1].set_ylim(max(0, lo - 0.1 * (hi - lo)), hi + 0.1 * (hi - lo))
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path
