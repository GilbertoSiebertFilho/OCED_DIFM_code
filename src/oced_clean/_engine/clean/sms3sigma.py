# -*- coding: utf-8 -*-
"""The SMS 3-sigma cleaning: the Ag Leader SMS Advanced sequence the Olds College OFPE team runs by hand, reproduced
step by step so that OCCD can clean a yield export "as in SMS" and compare it with its own cascade (F0-F11).

    settings = {"method": "sms3sigma"}                      # or "both": the OCCD cascade and this, then a comparison
    R = engine.clean.yield_clean.run(shp, out, settings)

The sequence (the project's SMS 3-sigma guide and the logs of its SMS cleaning projects, one per field).
The mean and the SD are recomputed on the points still alive before
every step, as the user reads them again in Edit Legend after each filter:

  S0  SMS import check: no plausible position, no yield, a negative dry yield, or a dry yield below the SMS import
      minimum (min_yield, 5 bu/ac by default: SMS's import minimum, so the readings below it are not points in SMS).
      Removes nothing on an SMS export. The
      minimum is the one of the file's own import: a file that went through Import as SMS (engine/sms_import.py) was
      already checked against the minimum of ITS import, and settings['sms_import'] (the import record, e.g.
      engine.run.import_as_sms) makes S0 take that minimum (yield_clean.sms_min_yield); without a record the caller's
      min_yield is used. None or 0 = no minimum; a negative dry yield is removed whatever the minimum.
  S1  Clip To Field Boundary: the logged point inside the field of interest (a point on the boundary edge counts as
      inside: clip_tolerance_m = 1 cm, as SMS counts it). Skipped without a
      field of interest.
      With the OCCD field-of-interest pre-step (records > 50 m away set aside first) the SMS clip count is the pre-step
      plus S1.
  S2  Optimize Swath Widths, option "individual swath rectangles" with "Scale Per Area attribute values": in harvest
      time order across every dataset and combine, each reading's rectangle (width x distance along the heading) is
      tested against the ground already covered; a fully covered reading is removed, a partly covered one is narrowed
      to the uncovered strip, moved sideways to its centre; a reading overlapped at its back by its predecessor is
      shortened (Distance, and Speed = Distance / Duration). The per-area values (yield, mass per area, fuel per area)
      of a narrowed or shortened reading are multiplied by its old / new area (W x D) / (W' x D') (mass is
      conserved). engine/clean/sms_swath.py (reverse-engineered from SMS raw vs cleaned exports and SMS's cleaning
      logs). The datasets run in the order of the start time / date in their names, and the
      logged files of a GreenStar 4 SMS export, listed in file-name order, are put back in time order first
      (swath_order auto). swath_scope "operation" (default) tests every reading against the ground cut by all the
      combines, as SMS does when they are datasets of one instance; "machine" tests it against its own combine's
      ground only, as in an SMS cleaning made with one instance per combine.
      Before the rectangles are built, every combine's lateral bias is measured (lateral_bias: how far its logged
      points sit beside the centre of the strip its header cut, from its own back-and-forth passes). A bias of
      BIAS_MIN_FT or more makes the step narrow every second pass of that combine (and read its yield high);
      swath_bias "warn"
      (default) reports it, "auto" moves the points back before the step, "off" skips the measurement. SMS itself does
      not move points: the cause is the position the decoder gives (on a John Deere log: a section layout that changes
      between log files).
      NOT ESTABLISHED against SMS, so off by default: swath_bias "auto" and swath_scope "machine"; the no-value rule
      of S3 / S4 below (tested on synthetic data only).
  S3  Moisture: keep mean +/- k SD (k = 3); a negative lower limit is typed as 0 (as the SMS logs show it: 0.00).
  S4  Speed: keep mean +/- k SD; a negative lower limit is typed as 0.
      The order of S3 and S4 is the setting filter_order: "moisture_speed" (the project's method, the default) or
      "speed_moisture" (the earlier order: S3 is then the speed and S4 the moisture). The step ids are positional
      (S3 = the first of the two filters), so every consumer reads the attribute of a step from its row ('attribute'),
      from step_attributes(order) or from STEP_INFO / step_info(order), never from the id.
  S5a Dry yield, pass 1: keep mean +/- k SD of the SCALED yield, the lower limit floored at 0.
  S5b, S5c ...: another yield pass only while the previous one cut the SD by more than 25 % (the user's rule: a big
      drop means gross errors were inflating the SD; extra passes without it only trim the real tails).
  Limits are the typed ones: mean +/- k SD rounded to 2 decimals (half up), inclusive. Mean and SD are the plain point
  mean and SD of Edit Legend (not the area-weighted Statistics average); SD with ddof 0 (n = 30,000: no visible
  difference). A reading with no value of the filtered attribute (an empty moisture or speed cell) is not removed by
  that filter: SMS's Filter Data cannot put a missing value outside its limits; the step row counts them (no_value)
  and a warning names the step (a rule of this engine, tested on synthetic data only, not against an SMS log).

Grouping (S3-S5): the project's rule is one mean and SD PER COMBINE, over all its tasks and days (one filter per
combine, never task by task for one combine; with one combine, one filter for the whole field). group_by "auto" does
that: one group per combine when several are found, one group for the field when one is found. The combines are told
apart from what the file itself holds, never from a side file: the machine column, else the sources / VINs of the SMS
dataset names (dataset_machines), else the display serials at the end of the dataset names. When the file does not
tell the combines apart (a GreenStar export whose tasks all end with the same monitor name), "auto" takes the whole
file as one group and warns when it holds more than one task. "task" (one group per task) and "field" stay as explicit
choices (an SMS cleaning made task by task). S0-S2 always run over the whole
operation.

A task is: the dataset (with the Ops Center '#log' split when the record id restarts) for SMS / Ops Center exports and
Case IH cards (one task log = one SMS dataset); machine x harvest day for decoded John Deere logs (.jdl, GS3): SMS
mostly makes one task per combine per day of them, but it may keep several task logs of one combine on one day, which
this grouping joins. Only group_by "task" uses it.
"""
from __future__ import annotations

import copy
import math
import re

import numpy as np
import pandas as pd

DEFAULTS = {
    "min_yield": 5.0,           # S0: SMS import minimum dry yield (bu/ac); None or 0 = no check on the yield (the
                                # caller passes the minimum of the file's own import, engine/sms_import.py)
    "clip": True,               # S1: Clip To Field Boundary (needs a field of interest)
    "clip_tolerance_m": 0.01,   # S1: a point within this distance outside the boundary counts as inside (edge point)
    "swath": True,              # S2: Optimize Swath Widths (individual swath rectangles)
    "scale_per_area": True,     # S2: per-area values (yield, mass per area) scaled to the new area
    "swath_passes": 1,          # S2: 2 reproduces a chain where Clip + Optimize Swath Widths was added twice
    "speed": True,              # the speed filter (S4; S3 with filter_order speed_moisture)
    "moisture": True,           # the moisture filter (S3; S4 with filter_order speed_moisture)
    "filter_order": "moisture_speed",   # S3 then S4: moisture_speed (the method) | speed_moisture
    "yield": True,              # S5
    "k_sd": 3.0,
    "group_by": "auto",         # auto (one group per combine; one for the field with one) | task | machine | field
    "max_yield_passes": 5,
    "sd_drop_pct": 25.0,        # another yield pass only when the previous one cut the SD by more than this (%)
    "round_decimals": 2,        # the limits as typed in SMS (None = full precision)
    "yield_floor": 0.0,         # the lower limit is floored here (None = no floor)
    "swath_params": None,       # advanced: overrides of engine.clean.sms_swath.DEFAULTS
    "swath_order": "auto",      # S2 processing order: auto | file | chain (GreenStar 4 file blocks re-chained)
    "swath_scope": "operation",  # S2 over: operation (all combines together: one SMS instance) | machine (each
                                 # combine alone: an SMS cleaning with one instance per combine)
    "swath_bias": "warn",        # S2: a combine whose logged points sit beside the centre of the strip it cut
                                 # (lateral_bias): warn | auto (move them back before the step) | off
}
GROUP_MODES = ("auto", "task", "machine", "field")
SWATH_PASSES_MAX = 3        # swath_passes: 1 (the method) to this many Clip + Optimize Swath Widths passes
SWATH_ORDERS = ("auto", "file", "chain")
SWATH_SCOPES = ("operation", "machine")
SWATH_BIASES = ("warn", "auto", "off")
BIAS_MIN_FT = 1.0           # a lateral bias of this size (ft) or more is reported (and moved with swath_bias auto)
BIAS_MIN_PAIRS = 200        # ... when measured on this many readings or more with an opposite pass on each side
CLUSTER_GAP_M = 500.0       # without a clip: a warning when the readings form groups farther apart than this
BIAS_KNN = 64               # lateral_bias: neighbours asked per reading (k-nearest within the search radius) ...
BIAS_KNN_MAX = 512          # ... and for a reading whose 64th neighbour is still inside the radius
BIAS_CHUNK = 4096           # ... readings per query block (peak memory ~ BIAS_CHUNK x BIAS_KNN cells)
# the order of the two filters between the swath step and the yield passes: the first is S3, the second S4
FILTER_ORDERS = {"moisture_speed": ("moisture", "speed"), "speed_moisture": ("speed", "moisture")}
FILTER_ORDER_LABELS = {"moisture_speed": "moisture, then speed (the method)",
                       "speed_moisture": "speed, then moisture (the earlier order)"}
LEGACY_FILTER_ORDER = "speed_moisture"                # what a cleaning record without 'filter_order' was made with
# revision of the method's defaults; a cleaning record without 'method_revision' is revision 1.
#   1  S3 speed, S4 moisture; group_by auto = one group per task with several combines
#   2  S3 moisture, S4 speed (filter_order); group_by auto = one group per combine; a reading without a value is
#      not removed by that filter; S0 takes the minimum it is given
METHOD_REVISION = 2
STEP_ORDER = ["S0", "S1", "S2", "S3", "S4"]           # + S5a, S5b ... (yield passes)
PASS_LETTERS = "abcdefghij"
G_IMPORT, G_SWATH, G_YIELD = "Import and clip (S0-S1)", "Swath widths (S2)", "Dry yield (S5)"
SOURCE_SMS = "Ag Leader SMS Advanced (Add Simple Analysis)"
_ATTR_TEXT = {"speed": ("Speed", "speed"), "moisture": ("Moisture", "moisture")}
ATTR_UNITS = {"speed": "mph", "moisture": "%", "dry yield": "bu/ac"}


def filter_order_of(record=None) -> str:
    """The filter order of a cleaning record R['sms3sigma'] (the dict with 'settings' / 'steps' the cleaning wrote) or
    of a plain settings dict (the sms3sigma settings a caller is about to run). A RECORD without 'filter_order' was
    made before the order became a setting: it ran speed, then moisture (LEGACY_FILTER_ORDER). A SETTINGS dict
    without the key runs the default of this engine (DEFAULTS['filter_order']), as merge_settings fills it in.
    None = the default of this engine."""
    if record is None:
        return DEFAULTS["filter_order"]
    is_record = isinstance(record.get("settings"), dict) or "steps" in record or "grouping" in record
    P = record.get("settings") if isinstance(record.get("settings"), dict) else record
    fallback = LEGACY_FILTER_ORDER if is_record else DEFAULTS["filter_order"]
    o = str(P.get("filter_order") or fallback).strip().lower()
    return o if o in FILTER_ORDERS else fallback


def step_attributes(order: str | None = None) -> dict:
    """{'S3': 'moisture', 'S4': 'speed', 'S5': 'dry yield'} for the filter order (default: DEFAULTS)."""
    a, b = FILTER_ORDERS[order or DEFAULTS["filter_order"]]
    return {"S3": a, "S4": b, "S5": "dry yield"}


def step_info(order: str | None = None) -> dict:
    """Name, group, short label, attribute and source of every step id for the filter order."""
    at = step_attributes(order)
    info = {
        "S0": {"name": "SMS import check", "group": G_IMPORT, "short": "import check", "attribute": None,
               "source": "SMS import of a native monitor file (minimum yield volume); record integrity"},
        "S1": {"name": "Clip To Field Boundary", "group": G_IMPORT, "short": "clip", "attribute": None,
               "source": SOURCE_SMS + ": Clip To Field Boundary"},
        "S2": {"name": "Optimize Swath Widths", "group": G_SWATH, "short": "swath widths", "attribute": None,
               "source": SOURCE_SMS + ": Optimize Swath Widths (individual swath rectangles, Scale Per Area)"},
    }
    for fid in ("S3", "S4"):
        cap, low = _ATTR_TEXT[at[fid]]
        info[fid] = {"name": f"{cap} mean +/- k SD", "group": f"{cap} ({fid})", "short": f"{low} 3 SD",
                     "attribute": at[fid],
                     "source": SOURCE_SMS + ": Filter Data on mean +/- 3 SD (Edit Legend statistics); house procedure"}
    info["S5"] = {"name": "Dry yield mean +/- k SD", "group": G_YIELD, "short": "yield 3 SD", "attribute": "dry yield",
                  "source": SOURCE_SMS + ": Filter Data on mean +/- 3 SD, lower limit 0; another pass while the SD "
                                         "falls > 25 % (house procedure)"}
    return info


def step_groups(order: str | None = None) -> list:
    """[(group title, [step ids])] in the order of the sequence, for the filter order."""
    info = step_info(order)
    return [(G_IMPORT, ["S0", "S1"]), (G_SWATH, ["S2"]), (info["S3"]["group"], ["S3"]), (info["S4"]["group"], ["S4"]),
            (G_YIELD, ["S5"])]


# the default order (moisture, then speed): S3 = moisture, S4 = speed. A run with another order carries its own
# names in its cascade rows ('name', 'group', 'attribute'); use step_info(order) / step_groups(order) for it.
STEP_INFO = step_info()
GROUPS = step_groups()
G_MOIST, G_SPEED = (STEP_INFO[f]["group"] for f in ("S3", "S4"))
# what SMS rescales after Optimize Swath Widths (checked cell by cell on an SMS clean export): per-area
# attributes x old / new area (W x D) / (W' x D'), area-per-time and area-per-fuel ones / it, and distance-per-fuel
# x new / old distance on the shortened readings
PER_AREA_COLUMNS = ["Yld_Vol_Dr", "Yld_Vol_We", "Yld_Mass_W", "Yld_Mass_D", "Fuel_Con_A", "VRYIELDVOL", "VRYIELDMAS",
                    "WETMASS", "Yld_Vol_Dry", "Yld_Vol_Wet", "Yld_Mass_Wet", "Yld_Mass_Dry"]
INVERSE_AREA_COLUMNS = ["Prod_ac_h_", "Prod_ac_h", "Fuel_Eff__"]
PER_DISTANCE_COLUMNS = ["Fuel_Eff_m"]
SERIAL_RE = re.compile(r"\((\d{3,})\)\s*$")
SOURCE_RE = re.compile(r"\(([^()]+)\)\s*$")      # the text in the parentheses that end a dataset name


FT = 0.3048
Y_OFFSET_NAMES = ("y_offset_f", "y_offset", "yoffset", "y_offset_ft")


def y_offset_column(columns) -> str | None:
    """The SMS lateral offset column (Y_Offset_f, ft to the right of travel): SMS moves every reading by it in the
    swath step and writes 0 in the export afterwards. None when the file has none (Case IH cards, decoded data)."""
    for c in columns:
        if str(c).strip().strip("_").lower() in Y_OFFSET_NAMES:
            return c
    return None


def local_plane(X, Y, utm):
    """(x, y, back) of UTM points in a local transverse Mercator centred on them (k0 = 1, so grid north = true north
    and the logged track is a heading in this plane); back(x, y) returns UTM coordinates."""
    from pyproj import CRS, Transformer
    fin = np.isfinite(X) & np.isfinite(Y)
    to_ll = Transformer.from_crs(utm, "EPSG:4326", always_xy=True)
    lon, lat = to_ll.transform(np.where(fin, X, np.nanmedian(X)), np.where(fin, Y, np.nanmedian(Y)))
    lon0, lat0 = float(np.median(np.asarray(lon)[fin])), float(np.median(np.asarray(lat)[fin]))
    ltm = CRS.from_proj4(f"+proj=tmerc +lat_0={lat0:.6f} +lon_0={lon0:.6f} +k=1 +x_0=0 +y_0=0 +ellps=WGS84 "
                         "+units=m +no_defs")
    xl, yl = Transformer.from_crs("EPSG:4326", ltm, always_xy=True).transform(lon, lat)
    xl, yl = np.where(fin, xl, np.nan), np.where(fin, yl, np.nan)
    back_t = Transformer.from_crs(ltm, utm, always_xy=True)

    def back(x, y):
        ok = np.isfinite(x) & np.isfinite(y)
        bx, by = back_t.transform(np.where(ok, x, 0.0), np.where(ok, y, 0.0))
        return np.where(ok, bx, np.nan), np.where(ok, by, np.nan)
    return np.asarray(xl, float), np.asarray(yl, float), back


GS4_NAME_RE = re.compile(r"\(GreenStar", re.I)
PASS_NUM_NAMES = ("pass_num", "pass_numbe", "passnum", "pass_no")
SMS_START_RE = re.compile(r"(\d\d)/(\d\d)/(\d\d)-(\d\d):(\d\d):(\d\d)")
MDY_RE = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{4})(?!\d)")


def dataset_start(name) -> float:
    """Sortable start of a dataset from its name (seconds, NaN when none): the SMS start time 'YY/MM/DD-HH:MM:SS'
    (Case IH / Ag Leader tasks), else the date 'M/D/YYYY' of a GreenStar 4 / Ops Center task name. As text the
    GreenStar 4 names sort 1/10 before 1/9."""
    import datetime as _dt
    epoch = _dt.datetime(1970, 1, 1)          # plain arithmetic: datetime.timestamp() fails before 1970 on Windows
    s = str(name)
    m = SMS_START_RE.search(s)
    try:
        if m:
            y, mo, d, hh, mi, ss = (int(v) for v in m.groups())
            return (_dt.datetime(2000 + y, mo, d, hh, mi, ss) - epoch).total_seconds()
        m = MDY_RE.search(s)
        if m:
            mo, d, y = (int(v) for v in m.groups())
            return (_dt.datetime(y, mo, d) - epoch).total_seconds()
    except (ValueError, OverflowError):
        pass
    return float("nan")


def pass_column(columns) -> str | None:
    """The pass number column of an SMS export (Pass_Num), None when the file has none."""
    for c in columns:
        if str(c).strip().strip("_").lower() in PASS_NUM_NAMES:
            return c
    return None


def file_blocks(seg, pass_num) -> np.ndarray:
    """Block id per reading (in export order): a new block at every segment start and every Pass_Num decrease. In an
    SMS export of John Deere GreenStar 4 data a block is one logged .jdl file; the readings are
    chronological inside a block, but SMS exports the blocks in file-name order, not in time order (checked against
    the timestamps of the decoded logs). Known limit (the rule the swath step was calibrated with): a file that
    starts on the pass number the previous one ended on (both still on pass 1) stays in the same block."""
    seg = np.asarray(seg)
    pn = np.nan_to_num(np.asarray(pass_num, float), nan=-1.0)
    brk = np.r_[True, (seg[1:] != seg[:-1]) | (np.diff(pn) < 0)]
    return np.cumsum(brk) - 1


def chain_blocks(first_xy, last_xy) -> list:
    """Chronological order of K blocks rebuilt from spatial continuity: GreenStar 4 rotates its log file while
    harvesting, so a block usually starts 1-2 m from where the previous one ended. Successors are a minimum-cost
    assignment on end -> start distances (with a free first and last block); a closed sub-tour is spliced into the
    main path where it adds the least distance. Returns the block positions 0..K-1 in the rebuilt order."""
    from scipy.optimize import linear_sum_assignment
    first_xy, last_xy = np.asarray(first_xy, float), np.asarray(last_xy, float)
    K = len(first_xy)
    if K <= 1:
        return list(range(K))
    Cd = np.hypot(last_xy[:, 0][:, None] - first_xy[:, 0][None, :], last_xy[:, 1][:, None] - first_xy[:, 1][None, :])
    Cd = np.where(np.isfinite(Cd), Cd, 1e9)
    np.fill_diagonal(Cd, 1e9)
    M = np.zeros((K + 1, K + 1))
    M[:K, :K] = Cd
    M[K, K] = 1e12
    r, c = linear_sum_assignment(M)
    succ = dict(zip(r.tolist(), c.tolist()))           # K = the dummy (start row / end column)

    def cost(a, b):
        return 0.0 if (a == K or b == K) else Cd[a, b]
    while True:
        path, seen, cur = [K], {K}, succ[K]
        while cur != K and cur not in seen:
            path.append(cur)
            seen.add(cur)
            cur = succ[cur]
        rest = [k for k in range(K) if k not in seen]
        if not rest:
            break
        cyc, cur = [rest[0]], succ[rest[0]]
        while cur != rest[0]:
            cyc.append(cur)
            cur = succ[cur]
        best = None
        for i in range(len(path)):
            p_, q_ = path[i], (path[i + 1] if i + 1 < len(path) else K)
            for j in range(len(cyc)):
                a_, b_ = cyc[j], cyc[(j + 1) % len(cyc)]
                d = cost(p_, b_) + cost(a_, q_) - cost(p_, q_) - cost(a_, b_)
                if best is None or d < best[0]:
                    best = (d, p_, q_, a_, b_)
        _, p_, q_, a_, b_ = best
        succ[p_] = b_
        succ[a_] = q_
    return path[1:]


def chain_order(seg, pass_num, x, y, chain_mask=None) -> tuple:
    """(order, n_blocks, n_moved): the export order with the file blocks of each segment (dataset / log) put back in
    time order by chain_blocks; segments keep their order. chain_mask (bool per reading): only the segments whose
    readings are all True are re-chained (the GreenStar 4 datasets of a mixed export), the others keep their export
    order. n_blocks / n_moved count the re-chained segments' blocks and those whose position changed."""
    seg = np.asarray(seg)
    blk = file_blocks(seg, pass_num)
    parts, n_blocks, n_moved = [], 0, 0
    for s in pd.unique(seg):
        idx = np.flatnonzero(seg == s)
        if chain_mask is not None and not np.asarray(chain_mask, bool)[idx].all():
            parts.append(idx)
            continue
        bs = list(pd.unique(blk[idx]))
        n_blocks += len(bs)
        members = [idx[blk[idx] == b] for b in bs]
        if len(bs) > 1:
            ends = []
            for m in members:                           # the first and last reading with a position
                f = m[np.isfinite(x[m]) & np.isfinite(y[m])]
                ends.append((f[0], f[-1]) if f.size else (m[0], m[-1]))
            first = np.array([[x[a], y[a]] for a, _ in ends])
            last = np.array([[x[b], y[b]] for _, b in ends])
            new = chain_blocks(first, last)
            n_moved += int(sum(1 for k, b in enumerate(new) if b != k))
            members = [members[b] for b in new]
        parts.extend(members)
    return (np.concatenate(parts) if parts else np.zeros(0, np.int64)), n_blocks, n_moved


def step_id(step: str, k: int | None = None) -> str:
    """'S5a', 'S5b' ... for the yield passes (4 characters at most: the Rem_By field is C 4)."""
    return step if k is None else f"{step}{PASS_LETTERS[k - 1]}"


def step_group_of(fid: str, order: str | None = None) -> str:
    return step_info(order)[fid[:2]]["group"]


# ===================================================================================================== settings
def _num(v, name, lo, hi, kind="f"):
    if isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)):
        raise ValueError(f"sms3sigma.{name} must be a number, not {v!r}")
    if kind == "i":
        if float(v) != int(v):
            raise ValueError(f"sms3sigma.{name} must be a whole number, not {v!r}")
        v = int(v)
    else:
        v = float(v)
    if not np.isfinite(v) or v < lo or v > hi:
        raise ValueError(f"sms3sigma.{name} = {v!r} is outside the allowed range {lo:g}..{hi:g}")
    return v


def validate(P: dict) -> dict:
    """The sms3sigma settings checked in place (merge_settings calls this after merging the user's subset)."""
    for k in ("clip", "swath", "scale_per_area", "speed", "moisture", "yield"):
        if not isinstance(P[k], bool):
            raise ValueError(f"sms3sigma.{k} must be true or false, not {P[k]!r}")
    if P["min_yield"] is not None:
        P["min_yield"] = _num(P["min_yield"], "min_yield", 0, 1e6)
    P["clip_tolerance_m"] = _num(P["clip_tolerance_m"], "clip_tolerance_m", 0, 1.0)
    fo = str(P.get("filter_order", DEFAULTS["filter_order"])).strip().lower()
    if fo not in FILTER_ORDERS:
        raise ValueError(f"sms3sigma.filter_order must be one of {list(FILTER_ORDERS)}, not {P.get('filter_order')!r}")
    P["filter_order"] = fo
    P["k_sd"] = _num(P["k_sd"], "k_sd", 0.5, 10)
    g = str(P["group_by"]).strip().lower()
    if g not in GROUP_MODES:
        raise ValueError(f"sms3sigma.group_by must be one of {list(GROUP_MODES)}, not {P['group_by']!r}")
    P["group_by"] = g
    P["max_yield_passes"] = _num(P["max_yield_passes"], "max_yield_passes", 1, len(PASS_LETTERS), "i")
    P["swath_passes"] = _num(P["swath_passes"], "swath_passes", 1, SWATH_PASSES_MAX, "i")
    P["sd_drop_pct"] = _num(P["sd_drop_pct"], "sd_drop_pct", 0, 100)
    if P["round_decimals"] is not None:
        P["round_decimals"] = _num(P["round_decimals"], "round_decimals", 0, 6, "i")
    if P["yield_floor"] is not None:
        P["yield_floor"] = _num(P["yield_floor"], "yield_floor", -1e6, 1e6)
    if P["swath_params"] is not None and not isinstance(P["swath_params"], dict):
        raise ValueError("sms3sigma.swath_params must be a dict of engine.clean.sms_swath parameters or null")
    if P["swath_params"]:
        from oced_clean._engine.clean import sms_swath
        D = sms_swath.DEFAULTS
        unknown = set(P["swath_params"]) - set(D)
        if unknown:
            raise ValueError(f"sms3sigma.swath_params: unknown parameter(s) {sorted(unknown)}; the parameters are "
                             f"{sorted(D)}")
        for k, v in list(P["swath_params"].items()):
            d = D[k]
            if isinstance(d, bool):
                if not isinstance(v, bool):
                    raise ValueError(f"sms3sigma.swath_params.{k} must be true or false, not {v!r}")
            elif v is None and k == "partial_dead_ft":          # None = the dead band of full-width readings
                continue
            else:
                lo = {"cell_ft": 1e-6, "col_ft": 1e-6, "chunk": 1, "pred_k": 1}.get(k, 0)
                P["swath_params"][k] = _num(v, f"swath_params.{k}", lo, 1e9, "i" if isinstance(d, int) else "f")
    o = str(P["swath_order"]).strip().lower()
    if o not in SWATH_ORDERS:
        raise ValueError(f"sms3sigma.swath_order must be one of {list(SWATH_ORDERS)}, not {P['swath_order']!r}")
    P["swath_order"] = o
    sc = str(P.get("swath_scope", DEFAULTS["swath_scope"])).strip().lower()
    if sc not in SWATH_SCOPES:
        raise ValueError(f"sms3sigma.swath_scope must be one of {list(SWATH_SCOPES)}, not {P.get('swath_scope')!r}")
    P["swath_scope"] = sc
    sb = str(P.get("swath_bias", DEFAULTS["swath_bias"])).strip().lower()
    if sb not in SWATH_BIASES:
        raise ValueError(f"sms3sigma.swath_bias must be one of {list(SWATH_BIASES)}, not {P.get('swath_bias')!r}")
    P["swath_bias"] = sb
    return P


# ===================================================================================================== limits
def round_half_up(v: float, dec: int | None) -> float:
    if dec is None or not np.isfinite(v):
        return float(v)
    q = 10.0 ** dec
    return math.floor(v * q + 0.5) / q


def sigma_limits(values, k: float, floor, dec):
    """(n, mean, sd, lo, hi) of the finite values: mean +/- k SD (ddof 0), lo floored, both rounded half up to `dec`
    decimals as the user types them; None when fewer than 2 finite values or SD = 0."""
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    if len(v) < 2:
        return None
    mu, sd = float(v.mean()), float(v.std(ddof=0))
    if not sd > 0:
        return None
    lo, hi = mu - k * sd, mu + k * sd
    if floor is not None:
        lo = max(lo, float(floor))
    return len(v), mu, sd, round_half_up(lo, dec), round_half_up(hi, dec)


def _sd(values) -> float:
    v = np.asarray(values, float)
    v = v[np.isfinite(v)]
    return float(v.std(ddof=0)) if len(v) > 1 else float("nan")


# ===================================================================================================== grouping
def dataset_serials(names) -> set:
    """The display serials SMS writes at the end of Case IH / Ag Leader dataset names ('00/01/02-12:00:00 (123456)');
    empty when any name has none (GreenStar 4 'TaskName 1/2/2000 (GreenStar 4 Monitor)', Ops Center tasks)."""
    out = set()
    for n in set(map(str, names)):
        m = SERIAL_RE.search(n)
        if not m:
            return set()
        out.add(m.group(1))
    return out


def dataset_source(name) -> str | None:
    """The source SMS writes in parentheses at the end of a dataset name: the display serial of a Case IH / Ag Leader
    display ('00/01/02-12:00:00 (123456)' -> '123456'), else the maker or the monitor the data came from
    ('Task 1 (Maker Inc.)', 'Harvest (Another Maker)', 'TaskName 1/2/2000 (GreenStar 4 Monitor)');
    None when the name does not end with one."""
    m = SOURCE_RE.search(str(name))
    return (m.group(1).strip() or None) if m else None


DATASET_MACHINES_BASIS = "source in parentheses at the end of the dataset names"
DATASET_VIN_BASIS = "dataset names that are combine VINs"
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")     # a vehicle / product identification number: 17 characters, no I O Q


def dataset_vin(name) -> str | None:
    """The dataset name itself when it is a VIN / PIN (17 letters and digits, at least one of each, no I, O or Q): a
    John Deere export names every dataset after the combine that logged it (e.g. '1ABCD23EFGH456789')."""
    v = str(name).strip().upper()
    return v if (VIN_RE.match(v) and re.search(r"[A-Z]", v) and re.search(r"\d", v)) else None


def dataset_machines(names):
    """(combine id per name, basis) when every dataset name ends with a source (dataset_source) and at least two
    sources differ: an SMS export that holds several monitors (displays of different makers, or two displays of one
    maker); the id is the source. Else, when every dataset name is a VIN (dataset_vin) and at least two differ,
    the VIN is the combine. None otherwise: one source (every task '(GreenStar 4 Monitor)', a single display serial)
    does not tell combines apart, and a name without a source leaves its combine unknown."""
    names = pd.Series(names).astype(str)
    uniq = names.unique()
    src = {n: dataset_source(n) for n in uniq}
    if not any(v is None for v in src.values()) and len(set(src.values())) >= 2:
        return names.map(src).to_numpy(dtype=object), DATASET_MACHINES_BASIS
    vin = {n: dataset_vin(n) for n in uniq}
    if not any(v is None for v in vin.values()) and len(set(vin.values())) >= 2:
        return names.map(vin).to_numpy(dtype=object), DATASET_VIN_BASIS
    return None


def resolve_groups(g, C: dict, P: dict, src_format: str | None, session_keys=None, warnings=None) -> tuple:
    """(group key per record, info). info = {requested, mode (task | machine | field), basis, n_machines,
    machines_basis, n_groups, groups, rule}. session_keys (machine x day) is the task key of decoded John Deere logs,
    used by group_by 'task' only: 'auto' reads the combines from the file (machine column, dataset names) and gives
    one group per combine, so its groups do not depend on the decoder report travelling with the file."""
    warnings = [] if warnings is None else warnings
    requested = P["group_by"]
    n = len(g)
    has_ds = "_ds_key" in g
    ds = g[C["dataset"]].astype(str) if C.get("dataset") else None
    # combines: the machine column, else the sources of the dataset names (_machine holds them when they differ,
    # yield_clean machines.from_dataset), else the display serials of the SMS dataset names, else unknown
    if C.get("machine"):
        machine_key = g["_machine"].astype(str).to_numpy()
        n_machines, mbasis = int(pd.Series(machine_key).nunique()), f"machine column {C['machine']!r}"
    elif "_machine" in g and g["_machine"].nunique() > 1:
        machine_key = g["_machine"].astype(str).to_numpy()
        dmb = dataset_machines(ds) if ds is not None else None
        n_machines, mbasis = int(pd.Series(machine_key).nunique()), (dmb[1] if dmb else DATASET_MACHINES_BASIS)
    elif ds is not None and dataset_serials(ds):
        ser = ds.map(lambda s: SERIAL_RE.search(s).group(1)).to_numpy()
        machine_key, n_machines, mbasis = ser, len(set(ser)), "display serial in the dataset names"
    else:
        machine_key, n_machines, mbasis = None, None, ("unknown (no machine column, no serial or differing sources "
                                                       "in the dataset names)")
    # tasks: the dataset as named (never its sort key, which keeps only the start time), split by the Ops Center
    # record-id restarts (' #log' of the processing key) and by combine when a machine column exists
    fmt = (src_format or "").lower()
    if fmt in ("jd_jdl", "jd_gs3_rcd") and session_keys is not None:
        task_key, tbasis = np.asarray(session_keys, dtype=object), "machine x harvest day (Session)"
    elif ds is not None:
        tk = ds.copy()
        logs = g["_ds_key"].astype(str).str.extract(r"( #log\d+)$")[0].fillna("") if has_ds else ""
        split_logs = has_ds and bool((logs != "").any())
        if split_logs:
            tk = tk + logs
        if C.get("machine"):
            tk = g["_machine"].astype(str) + " | " + tk
        task_key = tk.to_numpy(dtype=object)
        tbasis = ("dataset (" + str(C.get("dataset")) + ")" + (", one log per record-id restart" if split_logs else "")
                  + (" x combine" if C.get("machine") else ""))
    elif machine_key is not None:
        task_key, tbasis = machine_key, "machine (no dataset column)"
    else:
        task_key, tbasis = np.array(["field"] * n, dtype=object), "no dataset or machine column: one task"
    n_tasks = int(pd.Series(task_key).nunique())
    if requested == "auto":
        if n_machines is not None:
            mode = "machine" if n_machines > 1 else "field"
            rule = (f"{n_machines} combine{'s' if n_machines != 1 else ''} ({mbasis}): "
                    + ("one mean and SD per combine, over all its tasks and days" if mode == "machine"
                       else "one mean and SD for the whole field (one combine)"))
        else:
            # the file does not tell the combines apart: never task by task (one combine that worked several days
            # would get one filter per day), so one group, and the user is told when there are several tasks
            mode = "field"
            rule = (f"combines {mbasis}; {n_tasks} task(s): one mean and SD for the whole file")
            if n_tasks > 1:
                warnings.append(f"SMS 3-sigma: this file does not tell the combines apart (no machine column, and the "
                                f"{n_tasks} dataset names carry no differing source, serial or VIN), so the mean and "
                                "SD are taken over the whole file as for one combine. If several combines cut the "
                                "field, add a machine column (or clean the monitor data, which has one), or choose "
                                "'task' to filter each dataset on its own")
    else:
        mode = requested
        rule = {"task": "one group per task (set in the settings)", "machine": "one group per combine (set in the "
                "settings)", "field": "one pooled mean and SD for the whole operation (set in the settings)"}[mode]
    if mode == "task":
        key, basis = task_key, tbasis
    elif mode == "machine":
        if machine_key is None:
            key, basis = task_key, tbasis + " (no combine id: each task is taken as a combine)"
        else:
            key, basis = machine_key, mbasis
    else:
        key, basis = np.array(["field"] * n, dtype=object), "the whole operation"
    key = np.asarray(key, dtype=object).astype(str)
    groups = list(pd.unique(key))
    if mode == "machine" and machine_key is None:
        warnings.append("SMS 3-sigma: group_by 'machine' but the file does not tell the combines apart: each task "
                        f"({tbasis}) is taken as a combine")
    info = {"requested": requested, "mode": mode, "basis": basis, "n_machines": n_machines, "machines_basis": mbasis,
            "n_tasks": n_tasks, "task_basis": tbasis, "n_groups": len(groups), "groups": groups, "rule": rule}
    return key, info


# ===================================================================================================== cascade
def lateral_bias(x_m, y_m, heading_deg, width_ft, full_tol_ft=0.3, min_pairs=BIAS_MIN_PAIRS):
    """How far (ft, + = right of travel) one combine's logged points sit from the centre of the strip its header cut,
    measured from its own back-and-forth passes. x_m, y_m: a true-north plane (m); heading_deg: true track.

    For every full-width reading (the most common width, within full_tol_ft) the nearest full-width reading of the
    same set abeam (within 1.5 m along the track, 0.5 to 1.5 header widths across) travelling the opposite way
    (heading difference > 165 deg) is taken on its left and on its right. With the point b to the right of the strip
    centre, the opposite pass on the right lies W - 2b away and the one on the left W + 2b, so b = (median left -
    median right) / 4; same-direction neighbours (< 15 deg) stay W apart whatever b is. None when either side has
    fewer than min_pairs readings, else {bias_ft, header_ft, n_full, n_left, n_right, left_ft, right_ft, same_ft,
    n_truncated}.

    Memory is bounded: the neighbours of each reading are its BIAS_KNN nearest within the search radius (a k-nearest
    query, not every pair), and only the readings whose BIAS_KNN-th neighbour is still inside the radius (slow passes,
    turns, overlapping headlands) are asked again for BIAS_KNN_MAX. A cluster of co-located readings (a frozen GNSS
    fix) therefore costs n x BIAS_KNN_MAX neighbours instead of n^2 pairs; n_truncated counts the readings that still
    had more than BIAS_KNN_MAX neighbours inside the radius (their abeam neighbour may be missed)."""
    from scipy.spatial import cKDTree
    x, y, h, w = (np.asarray(v, float) for v in (x_m, y_m, heading_deg, width_ft))
    ok = np.isfinite(x) & np.isfinite(y) & np.isfinite(h) & np.isfinite(w) & (w > 0)
    if ok.sum() < 2 * min_pairs:
        return None
    vals, cnt = np.unique(np.round(w[ok], 1), return_counts=True)
    wfull = float(vals[np.argmax(cnt)])
    idx = np.flatnonzero(ok & (np.abs(w - wfull) <= full_tol_ft))
    if len(idx) < 2 * min_pairs:
        return None
    xi, yi, hi = x[idx], y[idx], h[idx]
    m = len(idx)
    pts = np.c_[xi, yi]
    tree = cKDTree(pts)
    radius = 1.6 * wfull * FT
    lo_lat, hi_lat = 0.5 * wfull * FT, 1.5 * wfull * FT
    # per reading: the smallest |lateral| distance (ft) to an abeam full-width reading on each side, travelling the
    # opposite way (left / right) or the same way (same_l / same_r); inf = none
    best = {k: np.full(m, np.inf) for k in ("left", "right", "same_l", "same_r")}
    n_trunc = 0

    def scan(rows, k):
        """Neighbours of `rows` (k nearest within the radius) folded into `best`; returns the rows whose k-th
        neighbour is still inside the radius (more neighbours may exist)."""
        kk = min(k, m)
        d_, j_ = tree.query(pts[rows], k=kk, distance_upper_bound=radius)
        d_, j_ = np.asarray(d_).reshape(len(rows), -1), np.asarray(j_).reshape(len(rows), -1)
        valid = (j_ < m) & (j_ != rows[:, None])
        jj = np.where(valid, j_, 0)
        hr = np.radians(hi[rows])[:, None]
        dx, dy = xi[jj] - xi[rows][:, None], yi[jj] - yi[rows][:, None]
        along = dx * np.sin(hr) + dy * np.cos(hr)
        lat = dx * np.cos(hr) - dy * np.sin(hr)          # + = right of travel
        dh = np.abs(((hi[jj] - hi[rows][:, None] + 180.0) % 360.0) - 180.0)
        alat = np.abs(lat)
        near = valid & (np.abs(along) < 1.5) & (alat > lo_lat) & (alat < hi_lat)
        v = alat / FT
        for key, msk in (("left", near & (dh > 165.0) & (lat < 0)), ("right", near & (dh > 165.0) & (lat > 0)),
                         ("same_l", near & (dh < 15.0) & (lat < 0)), ("same_r", near & (dh < 15.0) & (lat > 0))):
            best[key][rows] = np.minimum(best[key][rows], np.where(msk, v, np.inf).min(axis=1))
        return rows[np.isfinite(d_[:, -1])] if kk < m else rows[:0]
    for c0 in range(0, m, BIAS_CHUNK):
        rows = np.arange(c0, min(c0 + BIAS_CHUNK, m))
        sat = scan(rows, BIAS_KNN)
        for c1 in range(0, len(sat), max(1, BIAS_CHUNK * BIAS_KNN // BIAS_KNN_MAX)):
            n_trunc += len(scan(sat[c1:c1 + max(1, BIAS_CHUNK * BIAS_KNN // BIAS_KNN_MAX)], BIAS_KNN_MAX))
    left, right = best["left"][np.isfinite(best["left"])], best["right"][np.isfinite(best["right"])]
    sm = np.r_[best["same_l"][np.isfinite(best["same_l"])], best["same_r"][np.isfinite(best["same_r"])]]
    if len(left) < min_pairs or len(right) < min_pairs:
        return None
    bias = (float(np.median(left)) - float(np.median(right))) / 4.0
    return {"bias_ft": round(bias, 3), "header_ft": wfull, "n_full": int(len(idx)), "n_left": int(len(left)),
            "n_right": int(len(right)), "left_ft": round(float(np.median(left)), 3),
            "right_ft": round(float(np.median(right)), 3),
            "same_ft": (round(float(np.median(sm)), 3) if len(sm) else None), "n_truncated": int(n_trunc)}


def run_cascade(g, S: dict, C: dict, arrays: dict, plan, utm, burn, aoi_mod, log=print, warnings=None, notes=None,
                src_format=None, session_keys=None) -> dict:
    """Run S0-S5 on the processed set g (processing order). arrays: y, moi (or None), spd, width_m, dist_m, hdg
    (pandas Series aligned to g). Returns {alive, why, log (cascade rows), steps (per group and step), grouping, swath
    (arrays + stats), scale, x_new, y_new, clip_mask, in_aoi_sms}."""
    from oced_clean._engine.clean import sms_swath
    P = S["sms3sigma"]
    warnings = [] if warnings is None else warnings
    notes = [] if notes is None else notes
    n = len(g)
    y, moi, spd = arrays["y"].to_numpy(float), arrays["moi"], arrays["spd"].to_numpy(float)
    moi = moi.to_numpy(float) if moi is not None else None
    width_ft = arrays["width_m"].to_numpy(float) / 0.3048
    dist_ft = arrays["dist_m"].to_numpy(float) / 0.3048
    hdg = arrays["hdg"].to_numpy(float)
    X, Y = g["_x"].to_numpy(float), g["_y"].to_numpy(float)
    alive = np.ones(n, bool)
    why = np.array([""] * n, dtype=object)
    rows, steps = [], []
    forder = P.get("filter_order") or DEFAULTS["filter_order"]
    info_of = step_info(forder)

    def record(fid, crit, kill, on=True, extra=None, off_note=" (off)"):
        nonlocal alive
        kill = np.asarray(kill, bool) & alive if on else np.zeros(n, bool)
        why[kill] = fid
        alive = alive & ~kill
        base = info_of[fid[:2]]
        rows.append(dict(filter=fid, name=base["name"] + (f", pass {PASS_LETTERS.index(fid[2]) + 1}"
                                                          if len(fid) > 2 and fid[2] in PASS_LETTERS else ""),
                         criterion=crit + ("" if on else off_note), source=base["source"], group=base["group"],
                         attribute=base["attribute"],
                         on=bool(on), removed=int(kill.sum()), remaining=int(alive.sum()), **(extra or {})))
        log(f"  {fid:<4} {'' if on else '(off) '}removed {int(kill.sum()):>7,}  remaining {int(alive.sum()):>7,}")

    # ---- S0 SMS import check (the minimum is the one given: None or 0 = no minimum, for a file whose own import
    # already applied another minimum, or none). A negative dry yield is never a reading SMS holds: removed always
    my = P["min_yield"]
    my = float(my) if (my is not None and float(my) > 0) else None
    bad = ~np.isfinite(X) | ~np.isfinite(Y) | g["_posbad"].to_numpy(bool) | ~np.isfinite(y)
    crit0 = "no plausible position or no yield"
    with np.errstate(invalid="ignore"):
        if my is not None:
            bad = bad | (y < my)
            src0 = str(P.get("min_yield_source") or "SMS import")
            crit0 += (f", or dry yield below {my:g} bu/ac ({src0 if src0.startswith('the ') else 'the ' + src0} "
                      "minimum)")
        else:
            bad = bad | (y < 0)
            crit0 += (", or a negative dry yield (no minimum yield"
                      + (f": {P['min_yield_source']}" if P.get("min_yield_source") else " checked here") + ")")
    record("S0", crit0, bad)

    # ---- S1 Clip To Field Boundary
    in_sms = None
    if P["clip"] and plan is not None:
        tol = float(P["clip_tolerance_m"])
        in_sms = aoi_mod.inside(plan, X, Y, utm, tol)
        crit1 = (f"logged point outside the field of interest (a point within {100 * tol:g} cm of the edge counts as "
                 "inside)")
        hl = getattr(plan, "headland", None)
        if getattr(plan, "is_plan", False) and (hl is None or not np.asarray(hl, bool).any()):
            # SMS clips to the field boundary; a design of plots only would clip the alleys and the border away
            crit1 += "; the field of interest is plots only, so S1 clips to the plots, not to a field boundary"
            warnings.append("SMS 3-sigma: the field of interest is a trial design of plots only (no headland / border "
                            "polygon): S1 clips to the plots, so the alleys and the border are removed before the "
                            "swath step and the 3-sigma statistics, unlike SMS's Clip To Field Boundary. Add the "
                            "headland polygon to the design, or give the field boundary as the field of interest")
        record("S1", crit1, ~in_sms)
    elif P["clip"]:
        record("S1", "no field of interest given: nothing to clip to", np.zeros(n, bool), on=False,
               off_note=" (skipped)")
    else:
        record("S1", "Clip To Field Boundary", np.zeros(n, bool), on=False)
    if in_sms is None and alive.sum() > 1:
        # without a clip, readings logged far apart (a second field, the yard) under one field name go into the swath
        # raster, the 3-sigma statistics, the area and the average together: say so
        try:
            fin_ = alive & np.isfinite(X) & np.isfinite(Y)
            if fin_.sum() > 1:
                lab_ = sms_swath.reading_blocks(X[fin_], Y[fin_], CLUSTER_GAP_M)
                n_bl = int(lab_.max()) + 1
                if n_bl > 1:
                    sizes = np.bincount(lab_)
                    warnings.append(
                        f"SMS 3-sigma: the readings form {n_bl} groups more than {CLUSTER_GAP_M:g} m apart "
                        f"({', '.join(f'{int(v):,}' for v in sorted(sizes, reverse=True)[:5])} readings) and no field "
                        "boundary clips them: all of them enter the swath step, the 3-sigma statistics, the area and "
                        "the average. Give the field boundary (S1), or extract one place per file")
        except (ValueError, MemoryError):              # a diagnostic only: never stop the cleaning for it
            pass

    # ---- S2 Optimize Swath Widths (the whole operation, harvest time order)
    scale = np.ones(n)
    w_new = width_ft.copy()
    d_new = dist_ft.copy()
    spd_s = spd.copy()                       # the SMS Speed after the swath step (Distance / Duration)
    dx = np.zeros(n)
    dy = np.zeros(n)
    cov = np.full(n, np.nan)
    sw_stats = {}
    yoff_col = y_offset_column(g.columns)
    yoff = (pd.to_numeric(g[yoff_col], errors="coerce").fillna(0.0).to_numpy(float) if yoff_col
            else np.zeros(n))
    if P["swath"]:
        log("  swath rectangles ...")
        # SMS builds the rectangles in a plane whose north is true north (the logged track is a true heading): a
        # local transverse Mercator centred on the field (UTM would rotate them by the grid convergence, up to 2 deg),
        # and from the point moved Y_Offset to the right of travel (where SMS puts every reading after this step)
        XL, YL, to_utm = local_plane(X, Y, utm)
        hr = np.radians(hdg)
        rx, ry = np.cos(hr), -np.sin(hr)
        xs, ys = XL + yoff * FT * rx, YL + yoff * FT * ry
        # a combine whose logged points sit beside the centre of the strip it cut has every second back-and-forth
        # pass narrowed: measured per combine, reported, and moved back with swath_bias auto (see the docstring)
        biases, moved = {}, {}
        if P.get("swath_bias", "warn") != "off":
            mlab = (g["_machine"].astype(str).to_numpy(dtype=object) if "_machine" in g
                    else np.array(["all"] * n, dtype=object))
            for m_ in dict.fromkeys(mlab[alive].tolist()):
                sel = alive & (mlab == m_)
                lb = lateral_bias(xs[sel], ys[sel], hdg[sel], width_ft[sel])
                if lb is None:
                    continue
                biases[str(m_)] = lb
                b_ = float(lb["bias_ft"])
                if abs(b_) < BIAS_MIN_FT:
                    continue
                auto = P.get("swath_bias") == "auto"
                if auto:
                    xs = np.where(sel, xs - b_ * FT * rx, xs)
                    ys = np.where(sel, ys - b_ * FT * ry, ys)
                    moved[str(m_)] = b_
                warnings.append(
                    f"SMS 3-sigma: S2: the logged points of combine {m_} sit {abs(b_):.2f} ft to the "
                    f"{'right' if b_ > 0 else 'left'} of the centre of the strip its header cut (its own passes in the "
                    f"opposite direction lie {lb['left_ft']:.1f} ft away on the left and {lb['right_ft']:.1f} ft on "
                    f"the right, header {lb['header_ft']:g} ft): "
                    + ("they were moved back before Optimize Swath Widths (swath_bias auto)" if auto else
                       "Optimize Swath Widths narrows every second pass of it and its yield per acre rises. The "
                       "position the decoder gives is the likely cause (on a John Deere log: a section layout that "
                       "changes between log files; on ISOXML task data: the positions the display logs for the "
                       "sections of its header, which SMS takes as they are); swath_bias 'auto' moves the points "
                       "back before the swath step"))
        # processing order: time when the file has it; an SMS export of GreenStar 4 data has dates only and lists the
        # logged files in file-name order, so their blocks are put back in time order by position (swath_order auto)
        order_sw = np.asarray(burn)
        has_time = bool(C.get("time"))
        order_note = "time order across combines" if has_time else "export order (dataset, then record id)"
        ds_names = g[C["dataset"]].astype(str) if C.get("dataset") else None
        is_gs4 = (ds_names.str.contains(GS4_NAME_RE).to_numpy(bool) if ds_names is not None else np.zeros(n, bool))
        mode_o = P["swath_order"]
        want_chain = mode_o == "chain" or (mode_o == "auto" and not has_time and bool(is_gs4.any()))
        if want_chain:
            pcol = pass_column(g.columns)
            if pcol is None:
                what = "swath_order 'chain'" if mode_o == "chain" else "re-chaining the GreenStar 4 file blocks"
                warnings.append(f"SMS 3-sigma: {what} needs a pass number column (Pass_Num); the "
                                + ("time order" if has_time else "export order") + " is used for the swath step")
            else:
                seg = (g["_ds_key"] if "_ds_key" in g else ds_names if ds_names is not None
                       else pd.Series(["all"] * n)).astype(str).to_numpy()
                pn = pd.to_numeric(g[pcol], errors="coerce").to_numpy(float)
                order_sw, n_blk, n_mov = chain_order(seg, pn, xs, ys, chain_mask=None if mode_o == "chain" else is_gs4)
                if mode_o == "chain" and not is_gs4.all():
                    order_note = (f"file blocks (a new block at a Pass_Num decrease) re-chained by position, set by "
                                  f"swath_order 'chain' ({n_blk} blocks, {n_mov} moved)")
                else:
                    order_note = (f"GreenStar 4 file blocks put back in time order by position ({n_blk} blocks, {n_mov} "
                                  "moved" + ("; the other datasets in export order" if not is_gs4.all() else "")
                                  + "): the SMS export lists them in file-name order")
                if has_time:
                    order_note += "; it replaces the per-record time order"
                    warnings.append("SMS 3-sigma: swath_order 'chain' replaces the per-record time order of the swath "
                                    "step with an order rebuilt from the block positions")
        if not has_time and ds_names is not None:
            # the datasets in harvest order: by the start time / date in their names (the rows are sorted by name,
            # and GreenStar 4 names sort as text: 1/10 before 1/9)
            ts = ds_names.map(dataset_start).to_numpy(float)
            if np.isfinite(ts).all():
                o2 = np.lexsort((np.arange(len(order_sw)), ts[order_sw]))
                if (o2 != np.arange(len(o2))).any():
                    order_sw = order_sw[o2]
                    order_note += "; datasets in the order of the date in their names"
        log(f"  swath order: {order_note}")
        # the scope: the whole operation, or each combine alone (an SMS cleaning with one instance per combine)
        mkey = g["_machine"].astype(str).to_numpy(dtype=object) if "_machine" in g else None
        each = (P.get("swath_scope") == "machine") and mkey is not None and len(set(mkey.tolist())) > 1
        if P.get("swath_scope") == "machine" and not each:
            notes.append("SMS 3-sigma: swath_scope 'machine' with one combine (or none told apart): Optimize Swath "
                         "Widths runs over the whole operation")
        act = alive.copy()
        rem_all = np.zeros(n, bool)
        clip_again = np.zeros(n, bool)
        for sp in range(int(P.get("swath_passes") or 1)):
            if sp > 0 and in_sms is not None:     # a repeated Clip + Swath chain clips the moved points again
                lx, ly = to_utm(xs, ys)
                inside = aoi_mod.inside(plan, np.where(np.isfinite(lx), lx, X), np.where(np.isfinite(ly), ly, Y),
                                        utm, float(P["clip_tolerance_m"]))
                clip_again |= act & ~inside
                act &= inside
            if each:
                res = sms_swath.optimize_swath_each(mkey, xs, ys, hdg, d_new, w_new, order_sw, active=act.copy(),
                                                    params=P.get("swath_params"))
            else:
                res = sms_swath.optimize_swath(xs, ys, hdg, d_new, w_new, order_sw, active=act.copy(),
                                               params=P.get("swath_params"))
            rem = np.asarray(res["removed"], bool) & act
            # a reading the module did not process (width 0 or missing: NaN new width) keeps its logged values
            kept_p = act & ~rem & np.isfinite(np.asarray(res["width_ft_new"], float))
            rem_all |= rem
            w_new = np.where(kept_p, np.asarray(res["width_ft_new"], float), w_new)
            if "dist_ft_new" in res:                 # SMS may also shorten a reading at the back
                d_new = np.where(kept_p, np.asarray(res["dist_ft_new"], float), d_new)
            xs = np.where(kept_p, xs + np.nan_to_num(np.asarray(res["dx_m"], float)), xs)
            ys = np.where(kept_p, ys + np.nan_to_num(np.asarray(res["dy_m"], float)), ys)
            st_p = dict(res.get("stats") or {})
            if sp == 0:
                cov = np.asarray(res["covered_frac"], float)
                sw_stats = st_p
            else:                                     # a later pass: add what the module counted in it
                rbr = dict(sw_stats.get("removed_by_reason") or {})
                for k_, v_ in (st_p.get("removed_by_reason") or {}).items():
                    rbr[k_] = int(rbr.get(k_, 0)) + int(v_)
                sw_stats["removed_by_reason"] = rbr
                sw_stats["seconds"] = round(float(sw_stats.get("seconds") or 0) + float(st_p.get("seconds") or 0), 2)
            act = kept_p
        kept = act
        with np.errstate(divide="ignore", invalid="ignore"):
            ratio = np.where(kept & (w_new * d_new > 0), (width_ft * dist_ft) / (w_new * d_new), 1.0)
            spd_s = np.where(kept & (dist_ft > 0), spd * d_new / dist_ft, spd)
        if P["scale_per_area"]:
            scale = np.where(np.isfinite(ratio), ratio, 1.0)
        # the new position of a kept reading (offset + the centre of its kept strip), back in UTM as a shift
        xu, yu = to_utm(xs, ys)
        # every reading that survives S2 is moved: the module-kept ones to the centre of their kept strip, the ones the
        # module skipped (width 0 / missing) by their Y_Offset only (SMS moves every reading and writes 0)
        surv = alive & ~rem_all & ~clip_again
        dx = np.where(surv & np.isfinite(xu), xu - X, 0.0)
        dy = np.where(surv & np.isfinite(yu), yu - Y, 0.0)
        if yoff_col and (yoff[surv] != 0).any():
            sw_stats["y_offset_column"] = yoff_col
            sw_stats["n_offset_moved"] = int((surv & (yoff != 0)).sum())
        nar = kept & (w_new < width_ft - 1e-6)
        short = kept & (d_new < dist_ft - 1e-6)
        sw_stats.update(n_narrowed=int(nar.sum()), n_removed=int(rem_all.sum()), n_shortened=int(short.sum()),
                        n_dist_cut=int(short.sum()), swath_passes=int(P.get("swath_passes") or 1), order=order_note,
                        scope=("each combine alone" if each else "the whole operation"),
                        lateral_bias_ft={k: v["bias_ft"] for k, v in biases.items()},
                        lateral_bias=biases, lateral_bias_moved_ft=moved,
                        model="engine/clean/sms_swath.py, reverse-engineered from SMS raw vs cleaned exports and "
                              "SMS's cleaning logs",
                        min_width_ft=(float(w_new[nar].min()) if nar.any() else None),
                        max_scale=(float(scale[nar | short].max()) if (nar | short).any() else None))
        if clip_again.any():
            record("S1b", "the repeated Clip To Field Boundary of a second Clip + Swath pass", clip_again)
            sw_stats["n_clip_second_pass"] = int(clip_again.sum())
        record("S2", "fully overlapped (rectangle over ground already cut) - removed; partly overlapped readings are "
                     "narrowed to the uncovered strip"
                     + (" with their per-area values scaled" if P["scale_per_area"] else "")
                     + (f" ({int(P.get('swath_passes') or 1)} passes)" if int(P.get("swath_passes") or 1) > 1 else "")
                     + ("; each combine against its own ground only" if each else ""),
               rem_all, extra={"narrowed": int(nar.sum())})
    else:
        record("S2", "Optimize Swath Widths", np.zeros(n, bool), on=False)
    y_map = y * scale

    # ---- grouping for the 3-sigma filters
    gkey, ginfo = resolve_groups(g, C, P, src_format, session_keys, warnings=warnings)
    log(f"  3-sigma groups: {ginfo['n_groups']} ({ginfo['mode']}: {ginfo['basis']})")
    k, floor, dec = float(P["k_sd"]), P["yield_floor"], P["round_decimals"]

    def no_value_note(fid, label, n_miss):
        """A reading without a value of the filtered attribute is kept by that filter (SMS's Filter Data cannot put
        a missing value outside its limits) and counted: one warning per step."""
        if n_miss:
            warnings.append(f"SMS 3-sigma: {fid}: {n_miss:,} reading(s) have no {label} value; they are not filtered "
                            f"on {label} (the mean and SD are those of the readings that have one)")

    def sigma_step(fid, values, label, unit, on, use_floor):
        kill = np.zeros(n, bool)
        n_miss = 0
        if on:
            for gname in ginfo["groups"]:
                sel = alive & (gkey == gname)
                # speed and moisture cannot be negative: a negative lower limit is typed as 0 (as the SMS logs show
                # it: 0.00), which keeps the same points
                lim = sigma_limits(values[sel], k, floor if use_floor else 0.0, dec)
                if lim is None:
                    steps.append(dict(group=gname, step=fid, attribute=label, unit=unit, n_before=int(sel.sum()),
                                      mean=None, sd=None, lo=None, hi=None, removed=0, n_after=int(sel.sum()),
                                      sd_after=None, sd_drop_pct=None, no_value=int((~np.isfinite(values[sel])).sum()),
                                      note="fewer than 2 values or SD = 0: no limit"))
                    continue
                nn, mu, sd, lo, hi = lim
                v = values[sel]
                miss = ~np.isfinite(v)                    # no value: not outside the limits, kept by this filter
                out_ = ~miss & ~((v >= lo) & (v <= hi))
                n_miss += int(miss.sum())
                idx = np.where(sel)[0][out_]
                kill[idx] = True
                after = values[sel][~out_]
                sda = _sd(after)
                steps.append(dict(group=gname, step=fid, attribute=label, unit=unit, n_before=int(sel.sum()),
                                  mean=mu, sd=sd, lo=lo, hi=hi, removed=int(out_.sum()), n_after=int((~out_).sum()),
                                  sd_after=sda, sd_drop_pct=(100 * (1 - sda / sd) if sd > 0 and np.isfinite(sda) else None),
                                  no_value=int(miss.sum()), note=None))
            no_value_note(fid, label, n_miss)
        mine = [s for s in steps if s["step"] == fid and s["lo"] is not None]
        if len(mine) == 1:
            s = mine[0]
            crit = f"{label} outside mean +/- {k:g} SD: {s['lo']:.{dec or 2}f}-{s['hi']:.{dec or 2}f} {unit}"
        elif mine:
            crit = (f"{label} outside mean +/- {k:g} SD per {ginfo['mode']} ({len(mine)} groups, limits "
                    f"{min(s['lo'] for s in mine):.{dec or 2}f}-{max(s['hi'] for s in mine):.{dec or 2}f} {unit}; "
                    "see the limits table)")
        else:
            crit = f"{label} outside mean +/- {k:g} SD"
        record(fid, crit, kill, on=on)

    # S3 and S4 in the order of the method (filter_order): the first of the two filters is S3
    for fid, attr in zip(("S3", "S4"), FILTER_ORDERS[forder]):
        if attr == "speed":
            sigma_step(fid, spd_s, "speed", "mph", P["speed"], False)
        elif moi is None and P["moisture"]:
            warnings.append(f"SMS 3-sigma: no moisture column, the moisture step ({fid}) is skipped")
            record(fid, "moisture outside mean +/- k SD: no moisture column", np.zeros(n, bool), on=False,
                   off_note=" (skipped)")
        else:
            sigma_step(fid, moi if moi is not None else np.full(n, np.nan), "moisture", "%", P["moisture"], False)

    # ---- S5 dry-yield passes (per group: another pass while the previous one cut the SD by > sd_drop_pct)
    passes = {}
    if P["yield"]:
        active_groups = list(ginfo["groups"])
        for p in range(1, int(P["max_yield_passes"]) + 1):
            fid = step_id("S5", p)
            kill = np.zeros(n, bool)
            ran, nxt = [], []
            for gname in active_groups:
                sel = alive & (gkey == gname)
                lim = sigma_limits(y_map[sel], k, floor, dec)
                if lim is None:
                    steps.append(dict(group=gname, step=fid, attribute="dry yield", unit="bu/ac", n_before=int(sel.sum()),
                                      mean=None, sd=None, lo=None, hi=None, removed=0, n_after=int(sel.sum()),
                                      sd_after=None, sd_drop_pct=None, note="fewer than 2 values or SD = 0: no limit"))
                    continue
                nn, mu, sd, lo, hi = lim
                v = y_map[sel]
                out_ = np.isfinite(v) & ~((v >= lo) & (v <= hi))     # S0 already removed the readings without a yield
                kill[np.where(sel)[0][out_]] = True
                sda = _sd(v[~out_])
                drop = 100 * (1 - sda / sd) if np.isfinite(sda) else None
                again = bool(drop is not None and sda > 0 and drop > float(P["sd_drop_pct"]) and out_.any()
                             and p < int(P["max_yield_passes"]))
                steps.append(dict(group=gname, step=fid, attribute="dry yield", unit="bu/ac", n_before=int(sel.sum()),
                                  mean=mu, sd=sd, lo=lo, hi=hi, removed=int(out_.sum()), n_after=int((~out_).sum()),
                                  sd_after=sda, sd_drop_pct=drop, next_pass=again, note=None))
                passes[gname] = p
                ran.append(gname)
                if again:
                    nxt.append(gname)
            if not ran:
                if p == 1:                   # S5 is on but no group can carry a limit: say so in the cascade
                    record(fid, "dry yield (scaled) outside mean +/- k SD: no group has >= 2 values with SD > 0 "
                                "(no limit)", np.zeros(n, bool))
                break
            mine = [s for s in steps if s["step"] == fid and s["lo"] is not None]
            if len(mine) == 1:
                s = mine[0]
                sd_txt = (f"SD {s['sd']:.2f} -> {s['sd_after']:.2f}, {s['sd_drop_pct']:.2f} % drop"
                          if s.get("sd_drop_pct") is not None and s.get("sd_after") is not None
                          and np.isfinite(s["sd_after"])
                          else f"SD {s['sd']:.2f} -> n/a, fewer than 2 points left")
                crit = (f"dry yield (scaled) outside mean +/- {k:g} SD: {s['lo']:.{dec or 2}f}-{s['hi']:.{dec or 2}f} "
                        f"bu/ac ({sd_txt})")
            else:
                crit = (f"dry yield (scaled) outside mean +/- {k:g} SD per {ginfo['mode']} in {len(mine)} group(s), "
                        "lower limit floored at 0 (see the limits table)")
            if p > 1:
                crit += f"; run because the previous pass cut the SD by more than {P['sd_drop_pct']:g} %"
            record(fid, crit, kill)
            active_groups = nxt
            if not active_groups:
                break
    else:
        record("S5a", "dry yield outside mean +/- k SD", np.zeros(n, bool), on=False)
    return {"alive": alive, "why": why, "log": rows, "steps": steps, "grouping": ginfo, "group_key": gkey,
            "scale": scale, "width_ft_new": w_new, "dx": dx, "dy": dy, "covered_frac": cov, "swath": sw_stats,
            "in_aoi_sms": in_sms, "yield_passes": passes, "y_map": y_map, "dist_ft_new": d_new, "speed_new": spd_s}


def criterion_table(steps: list) -> list:
    """The per-group limits table as plain rows (what the report and the app show next to the SMS Edit Legend)."""
    out = []
    for s in steps:
        out.append({"group": s["group"], "step": s["step"], "attribute": s["attribute"], "n_before": s["n_before"],
                    "mean": s["mean"], "sd": s["sd"], "lo": s["lo"], "hi": s["hi"], "unit": s["unit"],
                    "removed": s["removed"], "n_after": s["n_after"], "sd_after": s.get("sd_after"),
                    "sd_drop_pct": s.get("sd_drop_pct"), "next_pass": s.get("next_pass"),
                    "no_value": s.get("no_value", 0), "note": s.get("note")})
    return out


def patch_values(columns, scale, w_new, width_col):
    """{column: (kind, values)} of the cells an SMS export changes after Optimize Swath Widths: the width column gets
    the new width, per-area columns x scale, area-per-time and area-per-fuel columns / scale (scale = old / new area,
    (W x D) / (W' x D')). Only columns present in `columns`; the shortened Distance, the Speed and the
    distance-per-fuel columns are patched by the writer (yield_clean.write_all)."""
    cols = {str(c).upper(): c for c in columns}
    out = {}
    if width_col and width_col in columns:
        out[width_col] = ("set", w_new)
    for c in PER_AREA_COLUMNS:
        if c.upper() in cols and cols[c.upper()] != width_col:
            out[cols[c.upper()]] = ("mul", scale)
    for c in INVERSE_AREA_COLUMNS:
        if c.upper() in cols:
            out[cols[c.upper()]] = ("div", scale)
    return out
