# -*- coding: utf-8 -*-
"""Decoder for the Case IH / New Holland AFS Pro 700 data card (a ``<name>.cn1`` folder with ``index.vy1``).

The card is decoded byte by byte, without any vendor software, into the standard table of engine/formats/common.py
(one row = one yield reading at the HEADER CENTRE). The layout and the flow formula were established by matching the
decoded records of a card one by one to the Ag Leader SMS export of the SAME card. The rules marked "SMS" below were
then checked on cards of two display software generations (.tlo records of 41 and of 50 bytes): raw SMS exports point
by point, SMS exports made after a cleaning on the points that survive it (matched by position), the point count of
SMS on every task, and the SMS Summary of every task.

Card layout
    <card>.cn1/index.vy1                     CNH1 record naming the display; marks the folder as a card
    <card>.cn1/COMBINES/<veh>/<veh>.ycs      crop table, 147 records x 244 B (record index = crop number)
    <card>.cn1/COMBINES/<veh>/<veh>.ycc      current yield calibration of each crop, 147 records x 65 B
    <card>.cn1/shared/growers.gws            grower names;  shared/<farm>.fms  farm name (parent = grower)
    <card>.cn1/shared/<farm>.fmd/<field>.fls field name (parent = farm)
    <card>.cn1/shared/<farm>.fmd/<field>.fld/<task>.{tso,tsh,tst,tlo,tlh,tlt}   one task (= one SMS dataset)

Records: b"CNH1" + uint32 payload length + 2 B + 1 B type + 8 B id + 8 B parent id + 6 B time (year-1970, month,
day, hour, minute, second; display clock) = 33 B header, then the payload. In the log streams (.tlo/.tlh/.tlt) the id
starts with a uint32 record counter that joins the streams (see "Stream join"). All integers little-endian.

    .tso payload  [0:19] task name 'YY/MM/DD-HH:MM:SS' (an optional leading '#'/'%'), [19:25] start time,
                  [33:48] operation ('Grain Harv'), [52] crop number, [53:70] vehicle serial, [159:163] int16 lat and
                  int16 lon ORIGIN (integer degrees, differs between tasks), [163:167] number of logging records,
                  [195:199] the task's AREA, uint32 x 0.01 m2 (the display's own area counter).
    .tsh payload  [4] flags (bit 3 set on the tasks used as calibration loads), [5:27] the task's calibration curve
                  (11 x uint16), [27:29] moisture-compensation coefficient (x1e-4 per moisture point),
                  [29:49] 10 x uint16 and [49:89] 10 x uint32 = the CALIBRATION BINS: per curve segment (raw count
                  0-999, 1000-1999, ...) the seconds harvested and the sum of the raw counts, [89:93] the scale weight
                  entered for a calibration load, x0.1 kg, [95] the moisture sensor's offset, (value - 100) x 0.1 %,
                  [96:100] the display's own task total (kg) computed with the calibration active while harvesting,
                  [100:104] sum of kg x moisture as the sensor read it (before the offset).
    .tlo (41 B)   [0] logging flags: bit 0 = area counting on (the records with bit 0 set are the .tso's count of
                  logging records); a newer display software also sets bit 2 (values 0, 1, 4, 5),
                  [1] interval x0.1 s, [2:4] distance of the record in mm, [4:6] active
                  cut width mm, [6:10]/[10:14] lat/lon int32 x1e-8 deg
                  ADDED to the task origin, [14:18] altitude mm, [18] GNSS status (0 = no fix; the position is then
                  0x80000001 or 0), [19:23] GNSS time of day ms (UTC), [23:25] heading x0.1 deg,
                  [25:33] and [33:41] lat/lon of the two header ENDS (same encoding).
    .tlh (17 B)   [1:3] raw mass-flow count hA, [5] grain moisture x0.1 % (hB), [11] 255 = flow sample invalid.
    .ycs payload  [2:4] test weight lb/bu x10, [4] standard moisture x10, [6] a second moisture. (SMS does not read
                  them: it uses the weight and the dry moisture of its own product, which can differ from the card's.
                  The card holds the crop as a NUMBER, .tso[52]; CROP_NAMES gives the SMS product of the numbers
                  confirmed against SMS, report["crop_name"] the product of the decoded tasks, so that the import
                  can take SMS's product table; report["monitor_product"] is None for this format.)
    .ycc payload  [0:22] current calibration curve (11 x uint16), [46] moisture-compensation coefficient.

Mass flow (wet) = SMS's Crop_Flw_M on every point of the raw SMS exports compared row by row (within the rounding of
the export):
    M = hB / 10                                    grain moisture, % (the sensor's offset is already in it)
    u = hA / 1000 * (1 + c * (14.0 - M))           c = .tsh[27:29] / 10000
    j = floor(hA / 1000)                           the curve segment: the one of the RAW count, not of u
    x = curve[j] + (u - j) * (curve[j+1] - curve[j])    the task's .tsh curve, 11 points at 0, 1, ..., 10
    flow (lb/s) = (x - 1000) / 100                 negative values (below the curve's zero, empty machine) are set to 0
Only two combinations of (raw step, coefficient, reference moisture) are identifiable; with the raw step at 1000
counts the fitted coefficient equals the one stored in the task (75 -> 0.0075) and the reference moisture is 14.0 %.
Reading u on the segment of u itself (the first version of this decoder) differs where the raw count and the
compensated count lie on either side of a curve point. The second 11-point array of the .ycc is NOT used. Across crops
the formula is checked against the display's own task totals (.tsh @96, one logging period per record).

Which curve: each task carries the curve stored in its .tsh (what the display and SMS use; option
``calibration="task"``, the default). When the operator recalibrates, only the tasks used as calibration loads are
rewritten with the new curve; older tasks keep theirs. ``calibration="latest"`` applies the crop's current .ycc curve
to every task instead. Every task whose curve differs from the current one, or whose decoded mass differs from the
display's own total (.tsh @96) by more than 2 %, gets a warning with the factor.

Time and distance of a reading (option ``time_basis``; the default "sms": the readings are SMS's points, and the SMS
Summary of the card, which their sum does not reproduce, is reported apart in report["sms_summary"]; a comparison with
the SMS Summary of a card must read report["sms_summary"], not add up the readings):
    "sms" (default)  Duration_s = byte [1], Distance_f = [2:4], speed = [2:4] / [1], wet mass = flow x Duration_s:
                     SMS's values on the points of the raw SMS exports compared row by row and on the points matched
                     in the exports made after a cleaning. A record whose byte [1] is 0 has no time, so
                     no mass (SMS: yield 0, left out by the minimum yield); it is counted in the pre-step.
    "period"         every reading = the logging period P (the median display-clock step, 1 s): Distance_f = speed x P,
                     wet mass = flow x P. The first version of this decoder; the time basis of the display's totals on
                     the 41-byte software.
The yield of a reading (flow / speed / width) is the same in both; the sums are not. The display writes ONE record per
second of its clock (over a card, the display-clock and GNSS-clock steps sum to about one second per record), but byte
[1] is 2 s or more on some records. On the 41-byte software nothing compensates them: byte [1] can add up to more time
than the records of a task, and the points of SMS then carry more mass than SMS's own Summary of that task (the
display's total), while one period per record stays close to it. The 50-byte software also writes records of 0 s
next to records of 2 s or more: "period" drops the 0-s records with their grain, "sms" keeps the time on the long
neighbour. Over all the logging records of a task the display's own weight follows one period per record more closely
than the records' own intervals. Every task whose readings carry over 2 % more time than one period each gets a
warning.

Task totals = the SMS Summary. For a dataset of a card SMS does not add up its points: its Summary shows the display's
own totals of the task, stored in the .tso / .tsh. So a task whose log is not on the card still has its Summary line,
and a dataset clipped to a boundary keeps the whole task's area and weight. Reproduced to the digits SMS shows, with
    Area               .tso [195:199] x 0.01 m2
    Avg. Moisture      .tsh [100:104] / [96:100] + ([95] - 100) / 10
    Est. Weight (Wet)  sum over the calibration bins of  seconds_i x flow(mean raw count of bin i), the mean count
                       compensated with the task's average moisture and read on segment i of the task's curve (the
                       flow formula above; the flow is linear within a segment, so the bins give the task's weight
                       under any curve, which is how the display and SMS re-weigh a task after a calibration)
    Est. Volume (Dry)  weight / weight to volume x shrink at the average moisture;  Avg. Yield = volume / area;
                       several datasets: areas, weights and volumes add up, the moisture is weighted by the area
The decode report carries them per task and for the file (report["sms_summary"]), next to the totals of the readings
(report["totals"]). The two differ by design: the display's area counts every logging second, also those whose
reading SMS leaves out (no flow, yield outside the limits, no GNSS fix, and records whose logging bit is off: a task
whose records all have the bit off, .tlo[0] 0 or 4, can still show an area on the display), and can count what the
log does not hold (a task can show more area on the display than over all its logged records); its weight follows its
own clock.
A harvest task with totals whose log (.tlo / .tlh) is not on the card is NOT a unit: it has no reading, and the copy of
a task on another display's card can be an older state of it (SMS can show such a task with the totals of the copy
it read first, under another product). totals_only_tasks() lists them; decode(totals_only="field" or [ids]) adds their
totals to the Summary only when the caller asks (an SMS Summary may or may not hold such a task).

Stream join: the .tlo and .tlh records of a task pair on the record counter. A display that restarts in the middle of
a task goes back to a saved counter, so the records logged after the restart repeat counters already used. The streams
are therefore cut into runs (a new run where the counter does not increase) and paired run by run; SMS has the records
of every run (reading only the first record of each counter loses some of SMS's points). Where a display restarts,
both streams have the same number of runs.

File names: a card can be written in either case (index.vy1 or INDEX.VY1, shared/<farm>.fmd/<field>.fld/*.tlo or
SHARED/<FARM>.FMD/<FIELD>.FLD/*.TLO): every lookup of a card file ignores the case of its letters.

Kept readings (pre-step, counted by reason): logging bit on, valid GNSS fix, valid flow sample, distance > 0,
interval byte > 0, cut width > 0. Nothing is dropped by yield value here; with SMS's minimum and maximum yield the
import step then leaves the same points as SMS (inside the field boundary when SMS clipped the dataset to one).
Position = midpoint of the two header ends (the point SMS uses).
Dry yield: one-sided shrink at the standard moisture - SMS's rule - is this format's default
(DEFAULT_OPTIONS / DEFAULT_SHRINK).
"""
from __future__ import annotations

import collections
import os
import re
import struct
import time as _time

import numpy as np
import pandas as pd

from oced_clean._engine.formats.common import (DecodeOptions, Probe, Unit, FT_PER_M, MPH_PER_MS, SQFT_PER_AC, LB_PER_KG,
                                   dataset_name, summarize, yield_columns, check_standard, shrink_factor)

FORMAT_ID = "cnh_cn1"
LABEL = "Case IH / New Holland AFS Pro 700 card (.cn1)"
DEFAULT_SHRINK = "one_sided"                       # SMS's rule for this format
DEFAULT_OPTIONS = DecodeOptions(shrink=DEFAULT_SHRINK)

MAGIC = b"CNH1"
HDR = 33
TLO_LEN, TLH_LEN, YCS_LEN, YCC_LEN = 41, 17, 244, 65
TSO_AREA, TSH_TOTALS = 195, 104                    # payload offset of the task area; payload bytes holding the totals
BINS = 10                                          # calibration bins of a task (.tsh): one per curve segment
NO_FIX = -2147483647                               # 0x80000001: position offset written when there is no fix
# flow calibration constants (see the module docstring)
RAW_STEP = 1000.0                                  # raw counts per curve index
FLOW_ZERO = 1000.0                                 # curve value of zero flow
FLOW_SCALE_LB_S = 0.01                             # lb/s per curve unit
MOIST_REF = 14.0                                   # reference moisture of the compensation, %
DISPLAY_TOL = 0.02                                 # warn when decoded mass / display total differs by more
CURVE_TOL = 0.01                                   # warn when the latest calibration changes a task's mass by more
INTERVAL_TOL = 0.02                                # warn when the readings' time exceeds their records' by more
TIME_BASES = ("sms", "period")                     # what a reading's time and distance are (see decode)
TOTALS_ONLY = ("field",)                           # decode(totals_only=...): a keyword, or a list of task ids
TASK_EXTS = (".tso", ".tsh", ".tst", ".tlo", ".tlh", ".tlt")
# crop number (.tso[52]) -> (the SMS product SMS gives such a task, key of common.CROPS): confirmed against the
# product of the SMS dataset
CROP_NAMES = {17: ("Barley - Fall", "barley"), 40: ("Field Peas", "peas"), 47: ("Wheat - Durum", "durum"),
              48: ("Wheat - Spring", "wheat")}
# best guess from (test weight, standard moisture) when the number is not confirmed; shown with a "?"
CROP_GUESS = {(48.0, 14.5): "barley", (50.0, 8.5): "canola", (60.0, 13.5): "wheat", (32.0, 14.0): "oats"}


# ===================================================================================================== record level
def _time_tuple(h):
    y, mo, d, hh, mi, ss = h[27:33]
    try:
        return pd.Timestamp(year=1970 + y, month=mo, day=d, hour=hh, minute=mi, second=ss)
    except ValueError:
        return pd.NaT


def read_records(path):
    """Every CNH1 record of a file: list of dicts {type, id, parent, time, payload}. Bytes between records are skipped."""
    b = open(path, "rb").read()
    out, i = [], 0
    while i <= len(b) - HDR:
        if b[i:i + 4] != MAGIC:
            j = b.find(MAGIC, i + 1)
            if j < 0:
                break
            i = j
            continue
        n = struct.unpack_from("<I", b, i + 4)[0]
        h = b[i:i + HDR]
        out.append(dict(type=h[10], id=h[11:19], parent=h[19:27], time=_time_tuple(h), payload=b[i + HDR:i + HDR + n]))
        i += HDR + n
    return out


def read_stream(path, length):
    """Fixed-length log stream -> (counter int64[n], time int64[n, 6] (y-1970, mo, d, h, mi, s), payload uint8[n, L],
    n_irregular, record_length). `length` is the known layout (the first L payload bytes); a newer display software
    appends bytes to the record (.tlo of 50 bytes, record type 3, instead of 41, type 2 -- the
    first 41 bytes unchanged, then a 0/1 flag and 8 x 0xFF), so a record of `length` or more bytes is read and only
    the first `length` bytes are used. Records shorter than `length` are skipped and counted. record_length is the
    payload length found on the card (the most common one). Fast path when every record sits on a regular stride."""
    b = np.fromfile(path, np.uint8)
    if len(b) >= HDR + 4:
        n0 = int(struct.unpack_from("<I", b, 4)[0])
        st = HDR + n0
        if n0 >= length and len(b) % st == 0:
            R = b.reshape(-1, st)
            magic_ok = (R[:, :4] == np.frombuffer(MAGIC, np.uint8)).all()
            if magic_ok and (R[:, 4:8].copy().view("<u4").ravel() == n0).all():
                return (R[:, 11:15].copy().view("<u4").ravel().astype(np.int64), R[:, 27:33].astype(np.int64),
                        R[:, HDR:HDR + length], 0, n0)
    recs = read_records(path)
    keep = [r for r in recs if len(r["payload"]) >= length]
    if not keep:
        return np.zeros(0, np.int64), np.zeros((0, 6), np.int64), np.zeros((0, length), np.uint8), len(recs), None
    lengths = collections.Counter(len(r["payload"]) for r in keep)
    ctr = np.array([struct.unpack_from("<I", r["id"], 0)[0] for r in keep], np.int64)
    tm = np.array([[0, 0, 0, 0, 0, 0] if pd.isna(r["time"]) else
                   [r["time"].year - 1970, r["time"].month, r["time"].day, r["time"].hour, r["time"].minute,
                    r["time"].second] for r in keep], np.int64)
    P = np.frombuffer(b"".join(r["payload"][:length] for r in keep), np.uint8).reshape(-1, length)
    return ctr, tm, P, len(recs) - len(keep), lengths.most_common(1)[0][0]


def _u16(A, o):
    return A[:, o].astype(np.int64) | (A[:, o + 1].astype(np.int64) << 8)


def _i32(A, o):
    return A[:, o:o + 4].copy().view("<i4").ravel().astype(np.int64)


def _cstr(b):
    return b.split(b"\0")[0].decode("latin-1").strip()


# ===================================================================================================== card catalogue
def _listing(d):
    """{lower-case name: name} of the entries of folder d ({} when it cannot be listed)."""
    try:
        return {x.lower(): x for x in sorted(os.listdir(d))}
    except OSError:
        return {}


def _ci_path(d, name):
    """The entry `name` of folder d whatever the case of its letters, or None."""
    hit = _listing(d).get(name.lower())
    return os.path.join(d, hit) if hit else None


def task_file(base, ext):
    """The file <base><ext> of a task (ext one of TASK_EXTS) whatever the case of its letters, or None."""
    p = base + ext
    if os.path.isfile(p):
        return p
    q = _ci_path(os.path.dirname(base), os.path.basename(base) + ext)
    return q if q and os.path.isfile(q) else None


def _walk_files(root, suffix, parent_suffix=None):
    """Every file under root whose name ends with `suffix`, only in folders whose name ends with `parent_suffix` when
    given (both compared without case), sorted by path."""
    out = []
    for d, dirs, files in os.walk(root):
        dirs.sort()
        if parent_suffix and not os.path.basename(d).lower().endswith(parent_suffix):
            continue
        out += [os.path.join(d, f) for f in files if f.lower().endswith(suffix)]
    return sorted(out)


def _is_card(d):
    p = _ci_path(d, "index.vy1")
    if p and os.path.isfile(p):
        with open(p, "rb") as f:
            return f.read(4) == MAGIC
    return False


def _has_cnh_tasks(d):
    """A folder holding <field>.fld sub-folders with CNH1 .tso files (a card copied without its .cn1 parent)."""
    for low, x in _listing(d).items():
        fld = os.path.join(d, x)
        if not (low.endswith(".fld") and os.path.isdir(fld)):
            continue
        for low2, y in _listing(fld).items():
            if low2.endswith(".tso"):
                with open(os.path.join(fld, y), "rb") as f:
                    return f.read(4) == MAGIC
    return False


def find_cards(root):
    """Card folders under root: [(folder, complete)]; complete = has index.vy1 (any case). Partial copies (loose .fld
    folders, no index) are returned only when no complete card holds them."""
    full, partial = [], []
    for d, dirs, files in os.walk(root):
        dirs.sort()
        if any(f.lower() == "index.vy1" for f in files) and _is_card(d):
            full.append(d)
            dirs[:] = []                                  # the card's sub-folders belong to it
            continue
        if any(x.lower().endswith(".fld") for x in dirs) and _has_cnh_tasks(d):
            partial.append(d)
            dirs[:] = [x for x in dirs if not x.lower().endswith(".fld")]
    # a partial copy whose fields are all on a complete card next to it is the same data twice: keep the card
    on_cards = {x.lower() for c in full for _, ds, _ in os.walk(c) for x in ds if x.lower().endswith(".fld")}
    partial = [d for d in partial if not {x.lower() for x in os.listdir(d) if x.lower().endswith(".fld")} <= on_cards]
    return [(d, True) for d in full] + [(d, False) for d in partial]


def _names(card):
    """{id: name} of growers, farms and fields found anywhere in the card, and {farm id: grower id}."""
    names, parent = {}, {}
    for p in _walk_files(card, ".gws") + _walk_files(card, ".fms") + _walk_files(card, ".fls"):
        try:
            for r in read_records(p):
                rid = _cstr(r["id"])
                if rid:
                    names[rid] = _cstr(r["payload"])
                    parent[rid] = _cstr(r["parent"])
        except OSError:
            continue
    return names, parent


def _crop_tables(card):
    """{vehicle folder name: {"ycs": {crop: payload}, "ycc": {crop: (time, payload)}}} for every .ycs/.ycc found."""
    out = {}
    for ext, L in (("ycs", YCS_LEN), ("ycc", YCC_LEN)):
        for p in _walk_files(card, "." + ext):
            veh = os.path.splitext(os.path.basename(p))[0]
            d = out.setdefault(veh, {"ycs": {}, "ycc": {}})
            for r in read_records(p):
                if len(r["payload"]) != L:
                    continue
                k = struct.unpack_from("<I", r["id"], 0)[0]
                d[ext][k] = r["payload"] if ext == "ycs" else (r["time"], r["payload"])
    return out


def _vehicle_tables(tables, vehicle):
    """The crop tables of the combine that logged a task: the COMBINES folder whose name starts the vehicle serial
    ('ABCDE' for 'ABCDE0000000'). Returns (folder name or None, tables or None, note)."""
    hits = [v for v in tables if vehicle.upper().startswith(v.upper())]
    if hits:
        v = max(hits, key=len)
        return v, tables[v], ""
    if len(tables) == 1:
        v = next(iter(tables))
        return v, tables[v], f"no crop table named after vehicle {vehicle!r}; used the only one on the card ({v})"
    return None, None, f"no crop table for vehicle {vehicle!r}"


def _crop_info(tbl, k):
    """(test weight lb/bu, standard moisture %, second moisture %, product name, crop key) of crop number k."""
    if tbl is None or k not in tbl["ycs"]:
        name, key = CROP_NAMES.get(k, (f"crop {k}", None))
        return None, None, None, name, key
    p = tbl["ycs"][k]
    tw, m4, m6 = struct.unpack_from("<H", p, 2)[0] / 10.0, p[4] / 10.0, p[6] / 10.0
    if k in CROP_NAMES:
        name, key = CROP_NAMES[k]
    else:
        key = CROP_GUESS.get((tw, m4))
        name = f"crop {k}" + (f" ({key}?)" if key else "")
    return tw, m4, m6, name, key


def _task_meta(tso_path):
    so = open(tso_path, "rb").read()
    p = so[HDR:]
    name = _cstr(p[:19])
    start = pd.Timestamp(year=1970 + p[19], month=p[20], day=p[21], hour=p[22], minute=p[23], second=p[24]) \
        if 1 <= p[20] <= 12 and 1 <= p[21] <= 31 else pd.NaT
    if pd.isna(start):
        m = re.search(r"(\d\d)/(\d\d)/(\d\d)-(\d\d):(\d\d):(\d\d)", name)
        if m:
            y, mo, d, hh, mi, ss = map(int, m.groups())
            start = pd.Timestamp(2000 + y, mo, d, hh, mi, ss)
    season = re.search(rb"[0-9A-Z]{8}((?:19|20)\d\d)\x00", p)
    olat, olon = struct.unpack_from("<hh", so, 192) if len(so) >= 196 else (0, 0)
    n_log = struct.unpack_from("<I", so, 196)[0] if len(so) >= 200 else 0
    area = struct.unpack_from("<I", so, HDR + TSO_AREA)[0] / 100.0 if len(so) >= HDR + TSO_AREA + 4 else None
    return dict(name=name, start=start, end=_time_tuple(so[:HDR]), op=_cstr(p[33:48]), crop=p[52] if len(p) > 52 else -1,
                vehicle=_cstr(p[53:70]), season=int(season.group(1)) if season else (start.year if not pd.isna(start) else None),
                origin=(olat, olon), n_log=n_log, area_m2=area)


def _task_sh(tsh_path):
    """The task's .tsh: flags, calibration curve and coefficient, and the display's totals (see the module docstring):
    seconds / counts = the calibration bins, display_kg = its own total, moisture_pct = the average moisture (sensor
    offset included), scale_kg = the scale weight of a calibration load. Totals a short record lacks are None."""
    b = open(tsh_path, "rb").read()
    p = b[HDR:]
    full = len(p) >= TSH_TOTALS
    kg = struct.unpack_from("<I", p, 96)[0] if len(p) >= 100 else None
    msum = struct.unpack_from("<I", p, 100)[0] if full else None
    return dict(flags=p[4], curve=np.array(struct.unpack_from("<11H", p, 5), float), written=_time_tuple(b[:HDR]),
                comp=struct.unpack_from("<H", p, 27)[0], display_kg=kg,
                seconds=np.array(struct.unpack_from(f"<{BINS}H", p, 29), float) if full else None,
                counts=np.array(struct.unpack_from(f"<{BINS}I", p, 49), float) if full else None,
                scale_kg=struct.unpack_from("<I", p, 89)[0] / 10.0 if full else 0.0,
                moisture_pct=msum / kg + (p[95] - 100) / 10.0 if (full and kg) else None)


def catalogue(root):
    """Every harvest task with logged records under root: list of dicts (one per task) + the cards found. A task the
    display counted records for but whose log (.tlo / .tlh) is not on the card is in the catalogue too (has_log
    False) when it is a grain harvest with totals: SMS shows such a task as a dataset with an area and a weight and
    no point (it is not a unit: see list_units and totals_only_tasks). Every row carries "files", the path of each
    task file found ({extension: path or None}, any case)."""
    cards = find_cards(root)
    rows = []
    for card, complete in cards:
        names, parent = _names(card)
        tables = _crop_tables(card)
        rel = os.path.relpath(card, root).replace("\\", "/")
        for tso in _walk_files(card, ".tso", ".fld"):
            base = tso[:-4]
            files = {ext: task_file(base, ext) for ext in TASK_EXTS}
            files[".tso"] = tso
            has_log = bool(files[".tlo"] and os.path.getsize(files[".tlo"]) > 0 and files[".tlh"])
            try:
                meta = _task_meta(tso)
            except (OSError, struct.error, ValueError):
                continue
            if meta["n_log"] == 0:
                continue
            if not has_log and not (meta["op"].lower().startswith("grain harv") and meta["area_m2"]
                                    and files[".tsh"]):
                continue
            fld = os.path.basename(os.path.dirname(base))
            fid = os.path.splitext(fld)[0]
            farm = parent.get(fid, "")
            veh_dir, tbl, note = _vehicle_tables(tables, meta["vehicle"])
            tw, m4, m6, prod, key = _crop_info(tbl, meta["crop"])
            task = os.path.basename(base)
            rows.append(dict(
                id=f"{rel}::{os.path.relpath(base, card).replace(os.sep, '/')}", card=card, card_complete=complete,
                base=base, files=files, task=task, field_id=fid, field=names.get(fid, fid), farm_id=farm, farm=names.get(farm, farm),
                grower=names.get(parent.get(farm, ""), parent.get(farm, "")), vehicle=meta["vehicle"], veh_dir=veh_dir,
                tables=tbl, table_note=note, crop=meta["crop"], tw=tw, std_m=m4, m6=m6, product=prod, crop_key=key,
                season=meta["season"], start=meta["start"], end=meta["end"], n_log=meta["n_log"], op=meta["op"],
                task_name=meta["name"], origin=meta["origin"], has_log=has_log, area_m2=meta["area_m2"],
                # the season of a unit is its HARVEST year (task start on the display clock); the card's season tag is
                # set per field by the operator and is not always moved on (a field can carry the tag of the year
                # before its harvest)
                year=meta["start"].year if not pd.isna(meta["start"]) else meta["season"]))
    return _one_per_task(rows), cards


def _one_per_task(rows):
    """One row per task over every card copy under the root. A grower's upload can hold the same card twice
    (e.g. <card>.cn1 and <card>_2.cn1/<card>.cn1, byte-identical): every task would be
    listed and decoded twice, doubling the readings and leaving each point on top of its copy (the cleaning then
    removes everything). A task is the display's task id (case ignored) + vehicle; a copy with the task's log is kept
    before one with its totals only, then the copy with the most logging records (a later copy of a card can hold a
    task that grew after the first copy), a complete card before a partial one, then the first in folder order. Kept
    rows carry copies = how many card copies hold that task."""
    out, index = [], {}
    for r in rows:
        k = (r["vehicle"], r["task"].upper())
        if k not in index:
            index[k] = len(out)
            out.append(dict(r, copies=1))
            continue
        cur = out[index[k]]
        if (r["has_log"], r["n_log"], r["card_complete"]) > (cur["has_log"], cur["n_log"], cur["card_complete"]):
            out[index[k]] = dict(r, copies=cur["copies"] + 1)
        else:
            cur["copies"] += 1
    return out


def probe(root):
    """Cheap recognition (folder walk + a few file headers, no log decoding). A task id found on more than one copy of
    the card is counted once and reported (see _one_per_task)."""
    cards = find_cards(root)
    if not cards:
        return None
    n_files, vehicles, fields, warns = 0, set(), set(), []
    tasks, repeated = set(), set()
    for card, complete in cards:
        for d, _, files in os.walk(card):
            n_files += len(files)
        for tso in _walk_files(card, ".tso", ".fld"):
            tlo = task_file(tso[:-4], ".tlo")
            if tlo and os.path.getsize(tlo) > 0:
                task = os.path.basename(tso)[:-4].upper()
                (repeated if task in tasks else tasks).add(task)
                fields.add(os.path.basename(os.path.dirname(tso)).lower())
                try:
                    with open(tso, "rb") as f:
                        vehicles.add(_cstr(f.read(HDR + 70)[HDR + 53:HDR + 70]))
                except OSError:
                    pass
        if not complete:
            warns.append(f"{os.path.basename(card) or card}: partial card copy (no index.vy1 / .cn1 parent); names or "
                         "the crop table may be missing")
    if repeated:
        warns.append(f"{len(repeated)} task log(s) are on more than one copy of the card in this upload (same task id): "
                     "each task is listed and decoded once, from the copy with the most records")
    rec_root = cards[0][0] if len(cards) == 1 else root
    count = {}
    for c, _ in cards:
        count[os.path.basename(c)] = count.get(os.path.basename(c), 0) + 1
    names = ", ".join(n if k == 1 else f"{n} ({k} copies)" for n, k in sorted(count.items()))
    return Probe(format_id=FORMAT_ID, label=LABEL, root=rec_root,
                 summary=f"AFS Pro 700 card {names}: {len(tasks)} task logs on {len(fields)} fields, "
                         f"combine(s) {', '.join(sorted(v for v in vehicles if v)) or '?'}",
                 n_files=n_files, machines=sorted(v for v in vehicles if v), warnings=warns)


def _unit_label(r):
    s, e = r["start"], r["end"]
    span = f"{s:%d %b %H:%M}-{e:%d %b %H:%M}" if not (pd.isna(s) or pd.isna(e)) else "?"
    season = str(r["year"])
    if r["season"] is not None and r["year"] is not None and int(r["season"]) != int(r["year"]):
        season = f"{r['year']} (season tag {r['season']} on the card)"
    return (f"{r['field']} · {season} · {r['product']} · task {r['task']} ({span}) · "
            f"{r['n_log']:,} harvest records" + ("" if r["has_log"] else " · totals only, no log on the card"))


def _by_start(r):
    return r["field"], r["start"] if not pd.isna(r["start"]) else pd.Timestamp(0)


def list_units(root):
    """The tasks the user can pick: every harvest task whose log is on the card; those of the latest harvest year are
    pre-selected. A task whose log is not on the card is not a unit (it has no reading, and the copy of a task on
    another display's card can be an older state of it): totals_only_tasks() lists them, decode(totals_only=...) adds
    their totals to the Summary when asked."""
    rows = [r for r in catalogue(root)[0] if r["has_log"]]
    if not rows:
        return []
    latest = max((r["year"] or 0) for r in rows)
    return [Unit(id=r["id"], label=_unit_label(r), field=r["field"], crop=r["crop_key"] or r["product"], year=r["year"],
                 start=str(r["start"]), end=str(r["end"]), machine=r["vehicle"], n_records=int(r["n_log"]),
                 default=r["year"] == latest) for r in sorted(rows, key=_by_start)]


def totals_only_tasks(root, unit_ids=None):
    """The harvest tasks whose totals are on the card but not their log (.tlo / .tlh): SMS lists such a task as a
    dataset with an area and a weight and no point. With unit_ids, only those of the fields (by name) and harvest years
    of these units. Returns [{id, task, label, field, year, machine, crop, product, logged_records, area_ac}], in field
    and start order; give the ids (or "field") to decode(totals_only=...) to add their totals to the Summary."""
    rows, _ = catalogue(root)
    keep = None
    if unit_ids is not None:
        by_id = {r["id"]: r for r in rows}
        keep = {(by_id[u]["field"].strip().lower(), by_id[u]["year"]) for u in unit_ids if u in by_id}
    out = []
    for r in sorted((r for r in rows if not r["has_log"]), key=_by_start):
        if keep is not None and (r["field"].strip().lower(), r["year"]) not in keep:
            continue
        out.append(dict(id=r["id"], task=r["task"], label=_unit_label(r), field=r["field"], year=r["year"],
                        machine=r["vehicle"], crop=r["crop_key"] or r["product"], product=r["product"],
                        logged_records=int(r["n_log"]),
                        area_ac=None if r["area_m2"] is None else r["area_m2"] * FT_PER_M ** 2 / SQFT_PER_AC))
    return out


# ===================================================================================================== decoding
def curve_last_point(curve):
    """Index of the curve's last defined point: the points after it that sit at the zero value (FLOW_ZERO) are unset.
    A full 11-point curve -> 10. A newer display software writes a single-point calibration as
    [1000, c1, 1000, 1000, ...] -> 1."""
    c = np.asarray(curve, float)
    defined = np.nonzero(c != FLOW_ZERO)[0]
    return int(defined[-1]) if len(defined) else 0


def flow_lb_s(hA, moisture_pct, curve, comp):
    """Wet mass flow (lb/s) from the raw count, the moisture and an 11-point curve (see the module docstring).
    The curve segment is the one of the RAW count (floor(hA / 1000)); the moisture-compensated count u is then read
    on that segment's line, also when the compensation carries it past the segment's end (SMS's rule).
    The curve is extended linearly from its last defined segment (curve_last_point): above point 10 for a full curve
    and above point 1 for a single-point calibration, whose unset points would otherwise fold the flow back
    to zero above u = 1 (the decoded mass would then fall well under the display's totals). Returns (flow,
    n_negative): values below the curve's zero are set to 0 and counted."""
    hA = np.asarray(hA, float)
    u = hA / RAW_STEP * (1.0 + comp / 1e4 * (MOIST_REF - np.asarray(moisture_pct, float)))
    last = curve_last_point(curve)
    if last < 1:                                         # no defined point: no flow can be computed
        return np.zeros_like(u), 0
    i = np.clip(np.floor(hA / RAW_STEP).astype(np.int64), 0, last - 1)
    x = curve[i] + (u - i) * (curve[i + 1] - curve[i])
    f = (x - FLOW_ZERO) * FLOW_SCALE_LB_S
    neg = f < 0
    return np.where(neg, 0.0, f), int(neg.sum())


def bins_wet_lb(seconds, counts, moisture_pct, curve, comp):
    """Wet mass (lb) of a task from the display's calibration bins (.tsh): bin i holds the seconds harvested with the
    raw count in curve segment i and the sum of those counts. The flow is linear in the count within a segment, so the
    task's mass on any curve is  sum_i seconds_i x flow(mean count of bin i)  with the mean count compensated by the
    task's average moisture: SMS's Est. Weight (Wet) of the dataset. moisture_pct None = no compensation."""
    n, S = np.asarray(seconds, float), np.asarray(counts, float)
    last = curve_last_point(curve)
    if last < 1:
        return 0.0
    k = 1.0 + comp / 1e4 * (MOIST_REF - (MOIST_REF if moisture_pct is None else float(moisture_pct)))
    i = np.clip(np.arange(len(n)), 0, last - 1)
    u = np.divide(S, n, out=np.zeros_like(S), where=n > 0) / RAW_STEP * k
    x = curve[i] + (u - i) * (curve[i + 1] - curve[i])
    return float((n * np.maximum(x - FLOW_ZERO, 0.0)).sum() * FLOW_SCALE_LB_S)


def stream_runs(ctr):
    """Run number of each record of a log stream: a new run starts where the record counter does not increase. A
    display that restarts in the middle of a task goes back to a saved counter, so the records logged after the
    restart repeat counters already used (see "Stream join" in the module docstring)."""
    ctr = np.asarray(ctr, np.int64)
    return np.r_[0, np.cumsum(np.diff(ctr) <= 0)].astype(np.int64) if len(ctr) else np.zeros(0, np.int64)


def read_task(base):
    """Join the .tlo and .tlh streams of one task -> (DataFrame of raw channels in the order of the .tlo, notes).
    The records pair on (run, record counter). Two streams that do not hold the same number of runs cannot be paired
    run by run: the first record of each counter is then used, and the notes say so."""
    co, to, O, irr_o, len_o = read_stream(task_file(base, ".tlo") or base + ".tlo", TLO_LEN)
    ch, _, H, irr_h, len_h = read_stream(task_file(base, ".tlh") or base + ".tlh", TLH_LEN)
    notes = {"record bytes": {".tlo": len_o, ".tlh": len_h}}          # the layout read is TLO_LEN / TLH_LEN
    ro, rh = stream_runs(co), stream_runs(ch)
    runs_o, runs_h = (int(ro[-1]) + 1 if len(ro) else 0), (int(rh[-1]) + 1 if len(rh) else 0)
    paired = runs_o == runs_h
    ko, kh = ((ro << 32) | co, (rh << 32) | ch) if paired else (co, ch)
    ko_u, io = np.unique(ko, return_index=True)
    kh_u, ih = np.unique(kh, return_index=True)
    _, a, b = np.intersect1d(ko_u, kh_u, return_indices=True)
    order = np.argsort(io[a], kind="stable")
    io, ih = io[a][order], ih[b][order]
    notes["records in .tlo"] = len(co)
    notes["no .tlh record (counter join)"] = len(co) - len(io)
    notes["records of another length"] = irr_o + irr_h
    notes["counter runs"] = {".tlo": runs_o, ".tlh": runs_h}
    notes["records after a counter restart"] = int((ro[io] > 0).sum()) if paired else 0
    ctr = co[io]
    O, H, T = O[io], H[ih], to[io]
    notes["logging byte values"] = sorted(int(v) for v in np.unique(O[:, 0]))
    so = open(task_file(base, ".tso") or base + ".tso", "rb").read()
    olat, olon = struct.unpack_from("<hh", so, 192)
    lat_o, lon_o = _i32(O, 6), _i32(O, 10)
    latL, lonL, latR, lonR = _i32(O, 25), _i32(O, 29), _i32(O, 33), _i32(O, 37)
    disp = pd.to_datetime(dict(year=1970 + T[:, 0], month=T[:, 1], day=T[:, 2], hour=T[:, 3], minute=T[:, 4],
                               second=T[:, 5]), errors="coerce")
    df = pd.DataFrame({
        "ctr": ctr, "disp_time": disp, "logging": (O[:, 0] & 1) == 1, "dt_byte": O[:, 1] / 10.0,
        "dist_mm": _u16(O, 2).astype(float), "width_m": _u16(O, 4) / 1000.0,
        "lat0": olat + lat_o * 1e-8, "lon0": olon + lon_o * 1e-8,
        "latL": olat + latL * 1e-8, "lonL": olon + lonL * 1e-8, "latR": olat + latR * 1e-8, "lonR": olon + lonR * 1e-8,
        "alt_m": np.where(_i32(O, 14) == NO_FIX, np.nan, _i32(O, 14) / 1000.0),
        "gnss": O[:, 18].astype(int), "gnss_ms": _i32(O, 19), "heading": _u16(O, 23) / 10.0,
        "hA": _u16(H, 1).astype(float), "hB": H[:, 5].astype(int), "flow_ok": H[:, 11] != 255,
        "pos_bad": (O[:, 18] == 0) | (np.abs(lat_o) > 2e8) | (np.abs(lon_o) > 2e8) | ((lat_o == 0) & (lon_o == 0)),
        "ends_bad": (np.abs(latL) > 2e8) | (np.abs(lonL) > 2e8) | (np.abs(latR) > 2e8) | (np.abs(lonR) > 2e8)
                    | ((latL == 0) & (lonL == 0)) | ((latR == 0) & (lonR == 0)),
    })
    return df, notes


def select_readings(df):
    """Readings the vendor software exports (pre-step): returns (mask, {reason: n dropped}) in a fixed order."""
    keep = np.ones(len(df), bool)
    counts = {}
    for reason, bad in (("logging off (header up / area counting off)", ~df.logging.values),
                        ("no GNSS fix (status 0 / no position)", df.pos_bad.values),
                        ("flow sample invalid (.tlh[11] = 255)", ~df.flow_ok.values),
                        ("not moving (distance 0)", df.dist_mm.values <= 0),
                        ("interval byte 0 (no time: no mass, no speed)", df.dt_byte.values <= 0),
                        ("cut width 0", df.width_m.values <= 0)):
        hit = keep & bad
        if hit.any():
            counts[reason] = int(hit.sum())
        keep &= ~bad
    return keep, counts


def _period_s(df):
    """Logging period: median step of the display clock between consecutive records (normally 1 s)."""
    t = df["disp_time"].values.astype("datetime64[s]").astype(np.int64)
    st = np.diff(t)
    st = st[(np.diff(df["ctr"].values) == 1) & (st >= 0) & (st <= 10)]
    return float(np.median(st)) if len(st) else 1.0, st


def _utc_offset_h(df):
    """Display clock minus GNSS UTC, rounded to 0.25 h (None when the GNSS time is missing)."""
    tod = (df["disp_time"] - df["disp_time"].dt.normalize()).dt.total_seconds().values
    g = df["gnss_ms"].values / 1000.0
    ok = (df["gnss"].values > 0) & np.isfinite(tod) & (g > 0) & (g < 86400)
    if not ok.any():
        return None
    d = (tod[ok] - g[ok]) / 3600.0
    d = (d + 12) % 24 - 12
    return float(np.round(np.median(d) * 4) / 4)


def task_summary(r, sh, curve, comp, tw, sm, shrink, n_readings=None):
    """The Summary SMS shows for one task, from the display's own totals on the card (see "Task totals" in the module
    docstring): area, average moisture, estimated wet weight on `curve` / `comp`, dry volume at the test weight `tw`
    and standard moisture `sm`. A task without totals (older software, cut file) gets None for what is missing.
    decoded_readings = the task's readings after the pre-step, BEFORE the import's minimum / maximum yield: not the
    GPS count of SMS (which is the count after them)."""
    area = None if r["area_m2"] is None else r["area_m2"] * FT_PER_M ** 2 / SQFT_PER_AC
    m = sh["moisture_pct"]
    wet = bins_wet_lb(sh["seconds"], sh["counts"], m, curve, comp) if sh["seconds"] is not None else None
    dry = None if wet is None else wet / tw * float(shrink_factor(sm if m is None else m, sm, shrink))
    return dict(task=r["task"], task_name=r["task_name"],
                dataset=dataset_name(r["task"], r["start"]) if not pd.isna(r["start"]) else r["task"],
                machine=r["vehicle"], has_log=bool(r["has_log"]), logged_records=int(r["n_log"]),
                decoded_readings=None if n_readings is None else int(n_readings), area_ac=area, moisture_pct=m, wet_lb=wet,
                dry_bu=dry, bu_ac=dry / area if (dry is not None and area) else None,
                display_wet_lb=None if sh["display_kg"] is None else sh["display_kg"] * LB_PER_KG,
                scale_wet_lb=sh["scale_kg"] * LB_PER_KG if sh["scale_kg"] else None)


def summary_totals(rows):
    """The Summary of several tasks together, as SMS adds them up: areas, weights and volumes summed, the yield =
    volume / area, the moisture weighted by the area."""
    def total(k):
        v = [x[k] for x in rows if x[k] is not None]
        return float(sum(v)) if v else None
    area, dry = total("area_ac"), total("dry_bu")
    wm = [(x["moisture_pct"], x["area_ac"]) for x in rows if x["moisture_pct"] is not None and x["area_ac"]]
    return dict(tasks=len(rows), logged_records=int(sum(x["logged_records"] for x in rows)),
                decoded_readings=int(sum(x["decoded_readings"] or 0 for x in rows)), area_ac=area,
                moisture_pct=sum(m * a for m, a in wm) / sum(a for _, a in wm) if wm else None,
                wet_lb=total("wet_lb"), dry_bu=dry, bu_ac=dry / area if (dry is not None and area) else None)


def decode(root, unit_ids, opts: DecodeOptions | None = None, log=print, calibration: str = "task",
           time_basis: str = "sms", totals_only=None):
    """Decode the chosen units into the standard table + decode report (see the module docstring).

    calibration: "task" (default) = each task's own curve (.tsh), as the display and SMS; "latest" = the crop's
    current curve (.ycc) for every task.
    time_basis: "sms" (default) = every reading has the time and the distance the display logged for its record
    (interval byte, distance field), as the points of SMS; "period" = one logging period per record, the time basis
    of the display's own totals.
    totals_only: None (default) = no task without its log; "field" = also the totals of every task whose log is not on
    the card, of the fields and harvest years of the units (totals_only_tasks(root, unit_ids)); a list = those task ids
    (from totals_only_tasks). They add their totals to report["sms_summary"] and no reading. A task id of that kind
    given in unit_ids is taken too (explicitly chosen)."""
    t_start = _time.time()
    opts = opts or DEFAULT_OPTIONS
    if calibration not in ("task", "latest"):
        raise ValueError(f"calibration must be 'task' or 'latest', not {calibration!r}")
    if time_basis not in TIME_BASES:
        raise ValueError(f"time_basis must be one of {TIME_BASES}, not {time_basis!r}")
    rows, _ = catalogue(root)
    by_id = {r["id"]: r for r in rows}
    unit_ids = list(unit_ids)
    missing = [u for u in unit_ids if u not in by_id]
    if missing:
        raise ValueError(f"unknown unit id(s): {missing[:5]}")
    if totals_only is None or totals_only == () or totals_only == []:
        extra = []
    elif isinstance(totals_only, str):
        if totals_only not in TOTALS_ONLY:
            raise ValueError(f"totals_only must be None, {TOTALS_ONLY} or a list of task ids, not {totals_only!r}")
        extra = [t["id"] for t in totals_only_tasks(root, unit_ids)]
    else:
        extra = list(totals_only)
        bad = [u for u in extra if u not in by_id or by_id[u]["has_log"]]
        if bad:
            raise ValueError(f"not a task with totals only (no log on the card): {bad[:5]}")
    unit_ids += [u for u in extra if u not in unit_ids]
    warnings, pre, frames, units_rep, cal_units, summary = [], {}, [], [], [], []
    n_read, longer_seen, tag_seen, copies_seen, partial_seen = 0, set(), set(), [], set()
    bump = lambda k, n: pre.__setitem__(k, pre.get(k, 0) + int(n))   # noqa: E731
    tz_offsets = []
    for uid in unit_ids:
        r = by_id[uid]
        if not r["card_complete"]:
            warnings.append(f"{r['task']}: partial card copy (no index.vy1); names may be missing")
        if r["table_note"]:
            warnings.append(f"{r['task']}: {r['table_note']}")
        if r.get("copies", 1) > 1 and not copies_seen:
            copies_seen.append(r["task"])
            warnings.append(f"the upload holds {r['copies']} copies of the card with the same tasks: each task is "
                            "decoded once (the copy with the most records)")
        lagging_tag = r["season"] is not None and r["year"] is not None and int(r["season"]) != int(r["year"])
        if lagging_tag and (r["field"], r["season"]) not in tag_seen:
            tag_seen.add((r["field"], r["season"]))
            warnings.append(f"{r['field']}: season tag {r['season']} on the card, tasks harvested in {r['year']}: the "
                            "harvest year is used (the operator did not move the field's season on the display)")
        # a crop chosen by the user that differs from the card's crop replaces the card's test weight and standard
        # moisture too (they belong to the card's crop), as jd_gs3_rcd does
        override = bool(opts.crop) and r["crop_key"] is not None and opts.crop.strip().lower() != r["crop_key"]
        try:
            if override:
                crop, tw, sm = opts.resolve(None, None, None)
                warnings.append(f"{r['task']}: card crop {r['product']!r} (crop {r['crop']}) replaced by {opts.crop!r}; "
                                f"the card's test weight ({r['tw']}) and standard moisture ({r['std_m']}) are not used")
            else:
                crop, tw, sm = opts.resolve(r["crop_key"], r["tw"], r["std_m"])
        except ValueError as e:
            raise ValueError(f"{r['task']} ({r['field']} {r['year']}): crop number {r['crop']} has no test weight / "
                             f"standard moisture on this card (crop table .ycs missing or unknown crop): choose the "
                             f"crop or give test_weight_lb_bu and std_moisture_pct ({e})") from None
        sh = _task_sh(task_file(r["base"], ".tsh") or r["base"] + ".tsh")
        latest = None
        if r["tables"] is not None and r["crop"] in r["tables"]["ycc"]:
            ycc_t, ycc_p = r["tables"]["ycc"][r["crop"]]
            latest = dict(curve=np.array(struct.unpack_from("<11H", ycc_p, 0), float), comp=ycc_p[46], time=ycc_t)
        if curve_last_point(sh["curve"]) < len(sh["curve"]) - 1 and "partial curve" not in partial_seen:
            partial_seen.add("partial curve")
            warnings.append(f"{r['task']}: calibration curve defined up to point {curve_last_point(sh['curve'])} only "
                            "(single-point calibration of a newer display software): the flow is extended linearly "
                            "from its last defined segment")
        if calibration == "latest":
            if latest is None:
                raise ValueError(f"{r['task']}: no current calibration (.ycc) for crop {r['crop']} on the card")
            curve, comp, src = latest["curve"], latest["comp"], f"crop {r['crop']} current curve (.ycc, {latest['time']})"
        else:
            curve, comp, src = sh["curve"], sh["comp"], f"task curve (.tsh of {r['task']})"
        if not r["has_log"]:
            row = task_summary(r, sh, curve, comp, tw, sm, opts.shrink, 0)
            summary.append(dict(unit=uid, **row))
            warnings.append(f"{r['task']}: the card holds the totals of this task but not its log (.tlo / .tlh): "
                            f"{r['n_log']:,} logged records"
                            + (f", {row['area_ac']:,.2f} ac" if row["area_ac"] is not None else "")
                            + (f", {row['wet_lb']:,.0f} lb" if row["wet_lb"] is not None else "")
                            + " are in the summary and have no readings")
            continue
        df, notes = read_task(r["base"])
        n_read += notes["records in .tlo"]
        if notes["no .tlh record (counter join)"]:
            bump("no .tlh record for the .tlo counter", notes["no .tlh record (counter join)"])
        if notes["records of another length"]:
            warnings.append(f"{r['task']}: {notes['records of another length']} records shorter than the layout skipped")
        if notes["records after a counter restart"]:
            warnings.append(f"{r['task']}: the record counter goes back {notes['counter runs']['.tlo'] - 1} time(s) "
                            f"(the display restarted during the task): the {notes['records after a counter restart']:,} "
                            "records logged after a restart are read too, as the vendor software does")
        elif notes["counter runs"][".tlo"] != notes["counter runs"][".tlh"]:
            warnings.append(f"{r['task']}: the record counter goes back {notes['counter runs']['.tlo'] - 1} time(s) in "
                            f"the .tlo and {notes['counter runs']['.tlh'] - 1} in the .tlh: the two logs cannot be "
                            "paired restart by restart, the first record of each counter is read")
        for ext, known in ((".tlo", TLO_LEN), (".tlh", TLH_LEN)):
            found = notes["record bytes"][ext]
            if found is not None and found != known and (ext, found) not in longer_seen:
                longer_seen.add((ext, found))
                warnings.append(f"{ext} records of {found} bytes on this card (documented layout {known} bytes): the "
                                f"first {known} are read, the {found - known} appended bytes are ignored (newer display "
                                "software; positions, width, flow and moisture decode as in the documented layout)")
        flags = notes["logging byte values"]
        if any(v not in (0, 1) for v in flags) and ("logging bits",) not in longer_seen:
            longer_seen.add(("logging bits",))
            warnings.append(f".tlo[0] holds the values {flags} on this card (newer display software): bit 0 (area "
                            "counting on) decides the logging state, the other bits are ignored")
        period, _ = _period_s(df)
        off = _utc_offset_h(df)
        if off is not None:
            tz_offsets.append(off)

        # ---- readings the vendor software exports, everything else counted by reason
        keep, counts = select_readings(df)
        for reason, n in counts.items():
            bump(reason, n)
        all_log = df[df.logging.values & df.flow_ok.values]
        g = df[keep].reset_index(drop=True)
        summary.append(dict(unit=uid, **task_summary(r, sh, curve, comp, tw, sm, opts.shrink, len(g))))
        if not len(g):
            warnings.append(f"{r['task']}: no reading left after the pre-step")
            continue
        M = g.hB.values / 10.0
        fl, n_neg = flow_lb_s(g.hA.values, M, curve, comp)
        if n_neg:
            bump("(kept) flow below the curve zero -> 0 lb/s", n_neg)
        speed = g.dist_mm.values / 1000.0 / g.dt_byte.values                    # m/s
        if time_basis == "sms":
            dur = g.dt_byte.values.astype(float)
            dist_ft = g.dist_mm.values / 1000.0 * FT_PER_M
        else:
            dur = np.full(len(g), period)
            dist_ft = speed * period * FT_PER_M
        width_ft = g.width_m.values * FT_PER_M
        wet_lb = fl * dur
        area_ac = dist_ft * width_ft / SQFT_PER_AC
        y = yield_columns(wet_lb, area_ac, M, tw, sm, opts.shrink)
        mid_ok = ~g.ends_bad.values
        if (~mid_ok).any():
            bump("(kept) header ends missing -> logged point used", (~mid_ok).sum())
            warnings.append(f"{r['task']}: {(~mid_ok).sum()} readings without header ends use the logged point")
        lat = np.where(mid_ok, (g.latL.values + g.latR.values) / 2, g.lat0.values)
        lon = np.where(mid_ok, (g.lonL.values + g.lonR.values) / 2, g.lon0.values)
        tloc = g.disp_time
        if opts.tz_offset_h is not None:
            if off is None:
                warnings.append(f"{r['task']}: no GNSS time to measure the display clock offset; Time left on the "
                                "display clock (tz_offset_h ignored)")
            else:
                tloc = tloc + pd.to_timedelta(opts.tz_offset_h - off, unit="h")
        frames.append(pd.DataFrame({
            "Field": r["field"], "Product": crop if override else r["product"],
            "Dataset": dataset_name(r["task"], r["start"] if not pd.isna(r["start"]) else g.disp_time.min()),
            "Obj__Id": np.arange(1, len(g) + 1), "Time": tloc.dt.strftime("%Y-%m-%d %H:%M:%S.0").values,
            "Duration_s": dur, "Elevation_": g.alt_m.values * FT_PER_M, "Speed_mph_": speed * MPH_PER_MS,
            "Distance_f": dist_ft, "Track_deg_": g.heading.values, "Swth_Wdth_": width_ft, "Moisture__": M,
            **y, "Machine": r["vehicle"], "Src_Fmt": FORMAT_ID, "lon": lon, "lat": lat}))

        # ---- calibration bookkeeping for this unit (one logging period per record, the display's time basis)
        Ma = all_log.hB.values / 10.0
        kg_task = flow_lb_s(all_log.hA.values, Ma, sh["curve"], sh["comp"])[0].sum() * period / LB_PER_KG
        kg_used = flow_lb_s(all_log.hA.values, Ma, curve, comp)[0].sum() * period / LB_PER_KG
        kg_latest = flow_lb_s(all_log.hA.values, Ma, latest["curve"], latest["comp"])[0].sum() * period / LB_PER_KG \
            if latest is not None else None
        same_curve = latest is not None and np.array_equal(latest["curve"], sh["curve"]) and latest["comp"] == sh["comp"]
        disp_ratio = kg_task / sh["display_kg"] if sh["display_kg"] else None
        cu = dict(unit=uid, task=r["task"], crop=int(r["crop"]), product=r["product"], curve_used=src,
                  crop_used=crop, test_weight_lb_bu=tw, std_moisture_pct=sm, crop_replaced_by_user=override,
                  curve=[int(v) for v in curve], moisture_comp_per_point=comp / 1e4,
                  task_curve_written=str(sh["written"]), current_curve_written=None if latest is None else str(latest["time"]),
                  task_curve_equals_current=bool(same_curve), calibration_load=bool(sh["flags"] & 0x08),
                  display_total_kg=sh["display_kg"], decoded_total_kg_task_curve=round(float(kg_task), 1),
                  decoded_over_display=None if disp_ratio is None else round(float(disp_ratio), 4),
                  latest_over_task_mass=None if kg_latest is None or kg_task <= 0 else round(float(kg_latest / kg_task), 4),
                  used_total_kg=round(float(kg_used), 1))
        cal_units.append(cu)
        if latest is not None and not same_curve and kg_task > 0 and abs(kg_latest / kg_task - 1) > CURVE_TOL:
            warnings.append(
                f"{r['task']}: its curve differs from crop {r['crop']}'s current calibration ({latest['time']}); "
                f"the current one would change its mass x{kg_latest / kg_task:.3f}"
                + (" (applied: calibration='latest')" if calibration == "latest" else " (not applied: calibration='task', as SMS)"))
        if disp_ratio is not None and abs(disp_ratio - 1) > DISPLAY_TOL:
            warnings.append(
                f"{r['task']}: decoded mass with its stored curve = {kg_task:,.0f} kg vs the display's own total "
                f"{sh['display_kg']:,} kg (x{disp_ratio:.3f}): the calibration in effect while harvesting was replaced "
                "afterwards" + (" (this task was a calibration load)" if sh["flags"] & 0x08 else ""))
        dsum = float(g.dt_byte.sum())
        over = dsum / (period * len(g))
        units_rep.append(dict(id=uid, label=_unit_label(r), task=r["task"], field=r["field"], farm=r["farm"],
                              grower=r["grower"], season=r["year"], card_season_tag=r["season"], crop=int(r["crop"]),
                              product=r["product"],
                              test_weight_lb_bu=tw, std_moisture_pct=sm, n_records=int(len(df)), n_readings=int(len(g)),
                              logging_period_s=period, interval_byte_sum_s=round(dsum, 1),
                              interval_byte_over_period=round(over, 4), display_minus_utc_h=off))
        if abs(period - 1.0) > 1e-9:
            warnings.append(f"{r['task']}: logging period {period:g} s (display clock), not 1 s")
        if time_basis == "sms" and over - 1 > INTERVAL_TOL:
            warnings.append(
                f"{r['task']}: its readings carry {100 * (over - 1):.1f} % more time than one logging period each "
                f"(interval byte above {period:g} s on {int((g.dt_byte.values > period + 1e-9).sum()):,} readings): as "
                "in SMS, their mass and area add up to more than the display counted for the task (the summary)")
        log(f"  {r['task']} {r['field']} {r['year']}: {len(df):,} records -> {len(g):,} readings "
            f"({units_rep[-1]['product']}, {tw:g} lb/bu, std {sm:g} %)")

    if not frames:
        only = [by_id[u] for u in unit_ids if not by_id[u]["has_log"]]
        if len(only) == len(unit_ids):
            tot = summary_totals(summary)
            raise ValueError(
                f"the {len(only)} selected task(s) ({', '.join(r['task'] for r in only[:5])}) hold only the display's "
                "totals, not their log (.tlo / .tlh): there is no reading to decode"
                + (f" (their totals: {tot['area_ac']:,.2f} ac" if tot["area_ac"] else " (")
                + (f", {tot['wet_lb']:,.0f} lb)" if tot["wet_lb"] else ")")
                + "; select a task that has its log, or leave these out")
        raise ValueError("no readings decoded from the selected units"
                         + (f" (pre-step: {pre})" if pre else ""))
    out = pd.concat(frames, ignore_index=True)
    probs = check_standard(out)
    if probs:
        raise ValueError("decoded table fails the standard schema: " + "; ".join(probs))
    machines, totals = summarize(out)
    tb = "local" if (opts.tz_offset_h is not None or tz_offsets) else "unknown"
    off_txt = sorted(set(tz_offsets))
    names = sorted({by_id[u]["product"] for u in unit_ids})
    report = {
        "format": FORMAT_ID, "label": LABEL, "units": units_rep, "n_records_read": int(n_read), "n_readings": int(len(out)),
        "pre_step": pre, "machines": machines, "totals": totals,
        # the card holds the crop as a number: the SMS product of the decoded tasks (CROP_NAMES; "crop N" when the
        # number is not confirmed), so that the import takes SMS's product table; no crop definition of the monitor
        "crop_name": names[0] if len(names) == 1 else None, "crop_names": names, "monitor_product": None,
        "totals_only_tasks": [u for u in unit_ids if not by_id[u]["has_log"]],
        "sms_summary": {
            "what": "the Summary SMS shows for each dataset of a card = the display's own totals of the task (.tso "
                    "area counter; .tsh moisture and calibration bins on the curve used), not the sum of the "
                    "readings; decoded_readings = the readings after the pre-step, before the import's minimum / "
                    "maximum yield (not SMS's GPS count)",
            "datasets": summary, "total": summary_totals(summary)},
        "time_basis": tb,
        "calibration": {
            "mass_channel": ".tlh[1:3] raw mass-flow count hA (wet grain), with grain moisture .tlh[5]/10 %",
            "formula": "flow_lb_s = (curve(u) - 1000) / 100, u = hA/1000 * (1 + c*(14.0 - M)), curve = 11 points at "
                       "0..10, the segment chosen by the raw count hA/1000 (linear beyond the last defined point: 10, "
                       "or 1 for a single-point calibration), c = moisture compensation / 10000",
            "curve_source": calibration,
            "record_time": time_basis,
            "interval": ("one reading = the time (.tlo[1]) and the distance (.tlo[2:4]) logged for its record, as the "
                         "points of SMS" if time_basis == "sms" else
                         "one reading = the logging period (median display-clock step, normally 1 s), "
                         "the time basis of the display's own totals; the interval byte .tlo[1] only gives the speed"),
            "validation": "checked against Ag Leader SMS on cards of two display software generations: the points "
                          "of raw SMS exports reproduced one by one (the same records, Duration_s, Distance_f, "
                          "Swth_Wdth_, Moisture and Crop_Flw_M within the rounding of the export), the point count of "
                          "SMS on every task, and the SMS Summary of every task from the card's totals (area, "
                          "moisture, wet weight, dry volume to the digits SMS shows).",
            "shrink": opts.shrink, "units": cal_units,
        },
        "warnings": warnings,
    }
    if off_txt:
        report["calibration"]["time_note"] = (
            f"Time = display clock (local), display - GNSS UTC = {', '.join(f'{o:+.2f}' for o in off_txt)} h"
            + (f"; shifted to UTC{opts.tz_offset_h:+g} h on request" if opts.tz_offset_h is not None else ""))
    if opts.shrink != DEFAULT_SHRINK:
        warnings.append(f"shrink '{opts.shrink}' requested; the vendor software uses '{DEFAULT_SHRINK}' for this format")
    log(f"decoded {len(out):,} readings from {n_read:,} records in {_time.time() - t_start:.1f} s")
    return out, report
