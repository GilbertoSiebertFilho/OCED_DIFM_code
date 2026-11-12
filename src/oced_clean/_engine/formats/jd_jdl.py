# -*- coding: utf-8 -*-
"""John Deere Gen4 / G5 display logs (``JD-Data/log/<year>/<guid>.jdl``, X9 / S-series combines) -> standard readings.

METHOD (the rules below were measured against Ag Leader SMS and John Deere Operations Center; the logs and the
comparison scripts are not part of this repository; tests: tests/test_jd_jdl_sms_points.py and tests/test_dec_jdl.py on
synthetic logs)

Container. A .jdl file is a sequence of frames ``0x0D | uint32 LE length | protobuf message``, read without a .proto by
walking the protobuf wire format along KNOWN paths only (never guessing whether a byte string is a sub-message).
Frame 0 is a small header; the frame with top-level field 5 holds the DEFINITIONS of the file (machine VIN / model,
header width and geometry, the section-control sections, every logged channel with its ISO 11783 DDI, unit, factor and
offset, client / farm / field names, crop, variety, test weight, standard moisture). Every other frame holds one
~30 s DATA BLOCK (frame field 4): header {3 sequence, 4 start offset in ms (signed) from the file reference time,
5 duration ms} and the records {1 record types, 2 element / channel ids, 3 record bodies}.

Delta streams. Every record is an anchored delta stream: the anchor fields hold the LAST value of the previous block
(the carry) and its time; packed zig-zag varint arrays hold the new samples as differences.
    sample time  = file reference + block start + cumsum(time deltas)      (the anchor time is NOT the base)
    sample value = (anchor + cumsum(value deltas)) / factor + offset
Evidence: block after block, anchor_k+1 = anchor_k + sum(deltas_k) and anchor time_k+1 = last sample time_k, exactly.
A record with no anchor at all (first block of a log session) is absolute (anchor 0: its first latitude delta is
the latitude itself x 1e9). Raw values <= -2^31 are "not available" and dropped.
    type 3 = one logged channel (id -> DDI / unit / factor / offset from the definitions)
    type 1 = GNSS receiver, 5 Hz: lat, lon x 1e9 deg (anchors 1, 2 / deltas 12, 13), altitude mm (4 / 16), time (- / 10)
    type 2 = the header element, 5 Hz: the two END POINTS of the cutter bar as placed by the display (antenna + the
             configured header offset), lat/lon x 1e9 deg (anchors 1-4 / deltas 17-20), time (- / 15)
    type 4 = condensed section work state per group of 16 sections: carry state (3), change times (1), new states (2)

Two ways to turn the logs into readings (``decode(points=...)``):

Points as Ag Leader SMS Advanced makes them, the default (points="sms"; _sms_groups, _sms_file_points). SMS reads
every log file on its own and makes its points of the 5 Hz header samples (type 2), about one per second: the samples
whose intervals add up to no more than 0.9 s are one point, at the position of its last sample. Per point: Duration =
the time since the point before; distance = the antenna's (type 1) own 5 Hz steps added up; width = the sections on
0.1 s after the last sample; flow and moisture = the distance-weighted mean of the channel over the point's samples,
a sample with the header not working counting as 0; wet mass = flow x Duration. Every point with a width is kept,
the first and last of a pass and the ones after a stop included: SMS's own import then leaves out those under its
minimum yield (engine/sms_import.py). Measured against raw SMS point exports: every SMS point is one of these points,
with the same Duration, distance, width, flow and moisture on nearly every point, and the area and wet mass of a field
met closely. Not reproduced: a few points SMS leaves out (most of them short, before a gap) or keeps with another flow
or section state at a pass end or after a long stop, and the flow of the points after a long stop, which SMS reads
lower; against the SMS RAW Summary (the reference of record) a small difference in wet and dry mass is not explained.
A unit's log files can define different section layouts (another header profile chosen on the display for a while):
every file is read with its own layout, so a reading is placed and its width checked with the layout of the file it
comes from, in both modes (points="sms": each file on its own; points="ops_center": per flow sample).

Readings on the flow clock, as John Deere Operations Center exports them (points="ops_center"). One reading per
sample of DDI 510 (wet mass flow, mg/s, 1 Hz), the clock of the yield channels. The yield
channels (DDI 510, 6 moisture, 39/43 yields) carry a -14 s delay in their definitions and are logged already shifted
to the moment the crop was cut. Matching an Ops Center export point by point fixes the rest of the timing (a scan of
the lags, the same optimum on every combine):
    position     the header centre at t + POS_LAG_MS (0.8 s): Ops Center's flow at a point = DDI 510 interpolated
                 0.8 s earlier; moved along the bar to the centre of the ACTIVE sections (type-4 states at the same
                 instant; ISO 11783 y is positive to the right of travel - the side is read, not assumed)
    width        DDI 12001 (active cutting width) at t + POS_LAG_MS + WIDTH_LAG_MS (the logged width trails the
                 section states by 0.8 s); without that channel, or when it reads wider than the header, the sum
                 of the active sections at the position time; the
                 header and its sections are those of the log file the flow sample comes from
    Duration_s   time since the previous flow sample (gaps longer than DT_MAX_S -> the median interval, counted)
    distance     path of the full-width header centre (5 Hz end points) over the reading interval; speed = distance
                 / duration. Ops Center speed is this track speed; the wheel speed DDI 560 reads a little low and
                 is only a diagnostic
    wet mass     flow x duration = the display's calibrated wet grain mass; the resulting wet yield equals the
                 monitor's own DDI 39 yield
    heading      perpendicular to the cutter bar, pointing in the direction of travel (sign fixed per machine)
    moisture     DDI 6 (ppm -> %), nearest sample within 1.5 s of t; over a gap in the moisture channel the last
                 sample is held for up to MOIST_HOLD_MS (10 s), as Ops Center does (readings in a gap of a few
                 seconds carry the previous value in the export)
    elevation    GNSS antenna altitude at the position time, ft (= Ops Center Elevation_)
Only readings the vendor software exports are kept; the rest are counted in report["pre_step"] by reason, in this
order: header_up (logged width 0; SMS points: no section on), no_position (no header sample within 1 s, or outside the
position record), stationary (track speed below V_MIN_KMH), no_distance (SMS points only: no antenna step, so no
area), position_outlier (more than 50 km from the machine's median position), no_moisture (the moisture channel
exists but has no sample within the hold time, so there is no dry yield).
Readings are never dropped by yield value - the cleaning does that (Ops Center itself drops readings below 5 bu/ac).

Yield. common.yield_columns from the wet mass and area. Test weight: DDI 8 / the monitor's crop record. Standard
moisture: DecodeOptions > OPS_CENTER_STD_MOISTURE (measured: Ops Center applies 13.5 % to wheat, the monitor's crop
record says 13.0 %, a warning records the difference) > the monitor's crop record > common.CROPS. A crop chosen in
DecodeOptions.crop that differs from the monitor's replaces the monitor's test weight and standard moisture as well
(they belong to the monitor's crop) and becomes the Product; a warning records it. The John Deere dry
convention measured on the export is ONE-SIDED shrink (grain drier than the standard is not credited; it explains the
export's Yld_Vol_Dr / Yld_Vol_We); it is this format's default (``default_options()``; ``decode(opts=None)``).

What SMS imports a .jdl with (report["monitor_product"], common.jd_monitor_product). The definition frame holds the
crop (21.1: John Deere crop id 21.1.1, name 21.1.2, test weight 21.1.5 with its unit, standard moisture 21.1.6) and the
variety (21.2.1.1: id, name); DDI 8 / DDI 75 log the same test weight (lb/bu) and moisture. SMS does NOT take these
numbers: it reads 'Wheat (Hard Red Spring)' and 'Wheat (Durum)' logs, which say 27.22 kg/bu = 60.01 lb/bu (DDI 8:
60.0) and 13.0 %, at 60 lb/bu and 13.5 %, and 'Barley' logs, which say 21.77 kg/bu = 47.99 lb/bu (DDI 8: 48.0) and
14.0 %, at 48 lb/bu and 15.0 %. What in the file gives SMS's value is the crop: common.JD_SMS_CROPS; the file's own
values are kept in monitor_product["file"]. Not established: another crop (None: the product table applies), and a
variety SMS already holds as a product keeps that product's values.
"""
from __future__ import annotations

import dataclasses
import os
import struct
import time as _time

import numpy as np
import pandas as pd

from . import common as C

FORMAT_ID = "jd_jdl"
LABEL = "John Deere Gen4 / G5 display logs (JD-Data/log/*.jdl)"

DEFAULT_SHRINK = "one_sided"         # measured on an Ops Center export
# Standard moisture John Deere Operations Center applies when it computes Yld_Vol_Dr, MEASURED per crop on a reference
# export (it can differ from the monitor's own crop record).
# Crops not listed fall back to the monitor's crop record, then to common.CROPS.
OPS_CENTER_STD_MOISTURE = {"wheat": 13.5}
NA_RAW = -2147483648                 # raw values <= this are "not available"
DT_MAX_S = 3.0                       # a flow interval longer than this is a logging gap -> nominal duration
POS_MAX_GAP_MS = 1000                # the header position must have a sample within 1 s of the reading
MOIST_TOL_MS = 1500
MOIST_HOLD_MS = 10_000               # over a moisture gap hold the last sample this long (as Ops Center holds it)
V_MIN_KMH = 0.3                      # below this the combine is stationary (Ops Center keeps even very slow readings)
POS_LAG_MS = 800                     # the flow sample at t belongs to the header position at t + 0.8 s (Ops Center, docs)
WIDTH_LAG_MS = 800                   # DDI 12001 (logged width) reports the section state 0.8 s after the position (docs)
WIDTH_TOL = 0.02                     # a DDI 12001 width more than 2 % over the header's is not an active width
MONITOR_RATIO_TOL = 0.10             # decoded / monitor wet yield (DDI 39) farther than this from 1: warn
VALIDATION_NOTE = ("points='sms': matched point by point against raw SMS point exports; points='ops_center': matched "
                   "point by point against an Ops Center export (see the module docstring)")
LAYOUT_TOL = 0.02                    # a section layout whose width differs from the logged bar length more: warn
OUTLIER_KM = 50.0                    # fixes farther than this from the machine's median position are garbage (lat 0 / lon 0)
POINTS = ("sms", "ops_center")       # how the logs become readings: SMS's points (default) or the Ops Center's
SMS_WINDOW_S = 0.9                   # SMS: the intervals of the samples of one point add up to this at most
SMS_SNAP_MS = 100                    # SMS: a sample or a section change up to this long after an epoch belongs to it
SMS_CARRY_S = 1.0                    # SMS: what the first sample of a file (or its carry) lasts
SMS_LONE_M = 1.0                     # SMS: the distance of a file's only sample (3.2808 ft in its export)
DDI_FLOW, DDI_MOIST, DDI_YWET, DDI_YDRY, DDI_SPEED, DDI_WIDTH, DDI_TW, DDI_STDM = 510, 6, 39, 43, 560, 12001, 8, 75
WANT = {(DDI_FLOW, "mg1sec-1"), (DDI_MOIST, "ppm"), (DDI_YWET, "bu1ac-1"), (DDI_YDRY, "bu1ac-1"),
        (DDI_SPEED, "km1hr-1"), (DDI_WIDTH, "m"), (DDI_TW, "lb1bu-1"), (DDI_STDM, "prcnt")}
KG_PER_LB = 1.0 / C.LB_PER_KG
_D = struct.Struct("<d")
_F = struct.Struct("<f")
_I = struct.Struct("<I")


class JdlError(ValueError):
    pass


def default_options() -> C.DecodeOptions:
    """DecodeOptions with this format's default shrink convention (John Deere: one-sided)."""
    return C.DecodeOptions(shrink=DEFAULT_SHRINK)


# ================================================================================================ protobuf wire walking
def _varint(b, i):
    r = 0
    s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        if x < 0x80:
            return r, i
        s += 7


def _s64(v):
    return v - (1 << 64) if v >= (1 << 63) else v


def _fields(b):
    """Shallow walk of one protobuf message -> list of (field, wiretype, value); length-delimited values stay bytes."""
    out = []
    i, n = 0, len(b)
    while i < n:
        key, i = _varint(b, i)
        f, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 2:
            ln, i = _varint(b, i)
            v = b[i:i + ln]
            i += ln
        elif wt == 1:
            v = _D.unpack_from(b, i)[0]
            i += 8
        elif wt == 5:
            v = _F.unpack_from(b, i)[0]
            i += 4
        else:
            raise JdlError(f"unsupported wire type {wt} (field {f})")
        out.append((f, wt, v))
    return out


def _first(fs, f, default=None):
    for a, _, v in fs:
        if a == f:
            return v
    return default


def _all(fs, f):
    return [v for a, _, v in fs if a == f]


def _num(v):
    """Scalar of a wt0 (varint, signed) or wt1 (double) field."""
    if v is None:
        return None
    return _s64(v) if isinstance(v, int) else float(v)


def _str(v):
    return v.decode("utf-8", errors="replace") if isinstance(v, (bytes, bytearray)) else ""


def _uvarints(b) -> np.ndarray:
    """Packed varints -> uint64 array (vectorised)."""
    if not b:
        return np.zeros(0, np.uint64)
    a = np.frombuffer(b, np.uint8)
    end = a < 0x80
    starts = np.r_[0, np.flatnonzero(end)[:-1] + 1]
    grp = np.cumsum(np.r_[0, end[:-1]].astype(np.int64))
    pos = (np.arange(len(a)) - starts[grp]).astype(np.uint64)
    vals = (a & 0x7F).astype(np.uint64) << (np.uint64(7) * pos)
    return np.add.reduceat(vals, starts)


def _zz(b) -> np.ndarray:
    """Packed zig-zag varints -> int64 array."""
    u = _uvarints(b)
    return (u >> np.uint64(1)).astype(np.int64) ^ -((u & np.uint64(1)).astype(np.int64))


# ================================================================================================ file level
def _read_frames(path, max_bytes=None):
    """Frame payloads of a .jdl file. Returns (payloads, warning or None). A truncated last frame is dropped."""
    with open(path, "rb") as fh:
        data = fh.read() if max_bytes is None else fh.read(max_bytes)
    out, i, n = [], 0, len(data)
    while i + 5 <= n:
        if data[i] != 0x0D:
            return out, f"{os.path.basename(path)}: bad frame marker 0x{data[i]:02x} at byte {i}; rest of file ignored"
        ln = _I.unpack_from(data, i + 1)[0]
        if i + 5 + ln > n:
            if max_bytes is None:
                return out, f"{os.path.basename(path)}: truncated last frame at byte {i}"
            break
        out.append(data[i + 5:i + 5 + ln])
        i += 5 + ln
    return out, None


@dataclasses.dataclass
class FileMeta:
    path: str
    guid: str = ""
    ref_ms: int = 0
    vin: str = ""
    model: str = ""
    width_m: float = float("nan")
    display: str = ""
    software: str = ""
    client: str = ""
    farm: str = ""
    field: str = ""
    crop: str = ""
    variety: str = ""
    tw_kg_bu: float | None = None
    std_moisture: float | None = None
    year: int | None = None
    header_id: int | None = None
    header_xyz_mm: tuple = ()
    gnss_id: int | None = None
    gnss_xyz_m: tuple = ()
    channels: dict = dataclasses.field(default_factory=dict)    # id -> (ddi, unit, offset, factor, delay_s)
    sections: pd.DataFrame | None = None    # id, width_m, y_m, cws_id, cws_idx, grp (left-to-right rank of the group)
    size: int = 0


def _parse_definitions(pay, path) -> FileMeta | None:
    """The definition frame (top-level field 5) -> FileMeta; None when the payload is not a definition frame."""
    top = _fields(pay)
    blk = _first(top, 5)
    if blk is None:
        return None
    b = _fields(blk)
    m = FileMeta(path=path, guid=_str(_first(b, 2)), ref_ms=_num(_first(b, 3)) or 0, display=_str(_first(b, 5)),
                 software=_str(_first(b, 24)), year=_num(_first(b, 17)), size=os.path.getsize(path))
    for key, attr in ((11, "client"), (12, "farm"), (13, "field")):
        x = _first(b, key)
        if x is not None:
            setattr(m, attr, _str(_first(_fields(_first(_fields(x), 1, b"")), 2, b"")).strip())
    crop = _first(b, 21)
    if crop is not None:
        cf = _fields(crop)
        c1 = _first(cf, 1)
        if c1 is not None:
            c1f = _fields(c1)
            m.crop = _str(_first(c1f, 2)).strip()
            for key, attr in ((5, "tw_kg_bu"), (6, "std_moisture")):
                x = _first(c1f, key)
                if x is not None:
                    xf = _fields(x)
                    v, unit = _first(xf, 1), _str(_first(xf, 3))
                    if v is not None:
                        v = float(v)
                        if attr == "tw_kg_bu" and unit == "lb1bu-1":
                            v = v * KG_PER_LB
                        setattr(m, attr, v)
        c2 = _first(cf, 2)                                # 21.2.1.1.2 = variety name
        if c2 is not None:
            v1 = _first(_fields(c2), 1)
            v2 = _first(_fields(v1), 1) if v1 is not None else None
            if v2 is not None:
                m.variety = _str(_first(_fields(v2), 2)).strip()
    eq = _first(b, 10)
    if eq is None:
        return m
    eqf = _fields(eq)
    rcv = _first(eqf, 3)
    if rcv is not None:
        rf = _fields(rcv)
        m.gnss_id = _first(rf, 1)
        g = _first(rf, 3)
        if g is not None:
            gf = _fields(g)
            m.gnss_xyz_m = tuple(float(_first(gf, k, 0.0)) for k in (1, 2, 3))
    secs = []
    for mach in _all(eqf, 2):                             # machine device(s): the combine (and any implement device)
        mf = _fields(mach)
        if not m.vin and _first(mf, 4) is not None:
            m.vin = _str(_first(mf, 4)).strip()
            m.model = _str(_first(mf, 5)).strip()
            w = _first(mf, 6)
            m.width_m = float(w) if w is not None else float("nan")
        if m.gnss_id is None:
            m.gnss_id = _first(mf, 26)
        for el in _all(mf, 7):                            # device elements
            ef = _fields(el)
            hdr = _first(ef, 3)                           # the header (cutter bar) element: id + geometry (mm)
            if hdr is not None and m.header_id is None:
                hf = _fields(hdr)
                m.header_id = _first(hf, 1)
                g = _first(hf, 3)
                if g is not None:
                    gf = _fields(g)
                    m.header_xyz_mm = tuple(float(_first(gf, k, 0.0)) for k in (1, 2, 3))
            for s in _all(ef, 4):                         # sections: id, width (m), offsets (mm), work-state group
                sf = _fields(s)
                cws = _first(sf, 7)
                if cws is None:
                    continue                              # the boom-level element (whole header), not a section
                g = _fields(_first(sf, 3, b""))
                secs.append((_first(sf, 1), float(_first(sf, 2, 0.0)), float(_first(g, 2, 0.0)) / 1000.0, cws,
                             _first(sf, 16, 0)))
            for c in _all(ef, 5):                         # logged channels
                cf = _fields(c)
                cid = _first(cf, 1)
                off = _first(cf, 3)
                fac = _first(cf, 4)
                m.channels[cid] = (_first(cf, 2), _str(_first(cf, 5)), float(off) if off is not None else 0.0,
                                   float(fac) if fac else 1.0, _first(cf, 9))
    if secs:
        sec = pd.DataFrame(secs, columns=["id", "width_m", "y_m", "cws_id", "cws_idx"]).sort_values("y_m")
        # element ids change between log sessions of the same machine: the work-state groups are identified by their
        # left-to-right rank instead (the section layout of a header does not change)
        rank = sec.groupby("cws_id").y_m.min().rank(method="first").astype(int) - 1
        sec["grp"] = sec.cws_id.map(rank).astype(int)
        m.sections = sec.reset_index(drop=True)
    return m


def read_meta(path) -> FileMeta | None:
    """Definitions of one .jdl without decoding its data (reads only the first frames)."""
    with open(path, "rb") as fh:
        head = fh.read(5)
        if len(head) < 5 or head[0] != 0x0D:
            return None
        ln0 = _I.unpack_from(head, 1)[0]
        fh.seek(5 + ln0)
        h1 = fh.read(5)
        if len(h1) < 5 or h1[0] != 0x0D:
            return None
        ln1 = _I.unpack_from(h1, 1)[0]
        pay = fh.read(ln1)
    try:
        return _parse_definitions(pay, path)
    except (IndexError, JdlError, struct.error):
        return None


@dataclasses.dataclass
class FileData:
    meta: FileMeta
    samples: dict            # (ddi, unit) -> [t_ms int64 array, value float array]
    gnss: pd.DataFrame       # t_ms, lat, lon, alt_m
    ends: pd.DataFrame       # t_ms, lat1, lon1, lat2, lon2   (header end points)
    cws: dict                # group rank -> (t_ms int64 array, states uint8 2-D array (n_events x n_sections))
    n_blocks: int = 0
    n_records: int = 0
    n_absolute: int = 0      # records without a carry (first block of a log session): absolute arrays
    n_incomplete: int = 0    # position records missing a delta array (skipped)
    warnings: list = dataclasses.field(default_factory=list)
    t0_ms: int | None = None                                 # start of the file's first data block
    carry: dict = dataclasses.field(default_factory=dict)    # (ddi, unit) -> the value a channel brings into the file
    ends_carry: tuple | None = None                          # (lat1, lon1, lat2, lon2) the header record brings in
    gnss_carry: tuple | None = None                          # (lat, lon, t_ms) the GNSS record brings in


def _stream(rec_fields, anchors, deltas, t_delta, bs):
    """Anchored delta stream of a type-1 / type-2 record -> (t_ms, [value arrays], absolute), "carry" (no new samples)
    or None (malformed).
    A record with NONE of its anchors is an absolute record (first block of a log session): the first delta is the
    absolute value, i.e. the anchor is 0 (verified: the first latitude delta of such records is the latitude x 1e9)."""
    d = {a: v for a, _, v in rec_fields}
    if t_delta not in d:
        return "carry"                                   # anchors only: no new sample in this block
    if any(k not in d for k in deltas):
        return None
    have = [a in d for a in anchors]
    if any(have) and not all(have):
        return None
    dts = _zz(d[t_delta])
    arrs = [_zz(d[k]) for k in deltas]
    n = min([len(dts)] + [len(x) for x in arrs])
    if n == 0:
        return None
    t = bs + np.cumsum(dts[:n])
    absolute = not any(have)
    vals = [(0.0 if absolute else float(_num(d[a]))) + np.cumsum(x[:n]).astype(float) for a, x in zip(anchors, arrs)]
    return t, vals, absolute


def decode_file(path, meta: FileMeta | None = None) -> FileData:
    """Decode the data blocks of one .jdl (only the channels in WANT, the GNSS, the header ends and the section states).
    What the first record of a stream carries into the file (the last value of the log before it) is kept apart, in
    carry / ends_carry / gnss_carry: SMS reads every file on its own and starts from those values."""
    frames, warn = _read_frames(path)
    if meta is None:
        meta = next((x for x in (_parse_definitions(p, path) for p in frames[:3]) if x is not None), None)
        if meta is None:
            raise JdlError(f"{os.path.basename(path)}: no definition frame")
    ref = int(meta.ref_ms)
    want = {cid: c for cid, c in meta.channels.items() if (c[0], c[1]) in WANT}
    S = {}
    G, E = [], []
    CW = {}
    out = FileData(meta=meta, samples={}, gnss=None, ends=None, cws={})
    if warn:
        out.warnings.append(warn)
    first = set()                                         # streams whose first record of the file has been seen
    for pay in frames:
        for f, wt, blk in _fields(pay):
            if f != 4 or wt != 2:
                continue
            hf = _fields(blk)
            bs = ref + _s64(_first(hf, 4, 0))
            if out.t0_ms is None:
                out.t0_ms = bs
            out.n_blocks += 1
            for v9 in _all(hf, 9):
                types, ids, recs = [], None, []
                for f3, wt3, v3 in _fields(v9):
                    if f3 == 1:
                        if wt3 == 0:
                            types.append(v3)
                        else:
                            types.extend(_uvarints(v3).tolist())
                    elif f3 == 2 and wt3 == 2:
                        ids = _uvarints(v3).tolist()
                    elif f3 == 3 and wt3 == 2:
                        recs.append(v3)
                if ids is None:
                    continue
                if not (len(types) == len(ids) == len(recs)):
                    out.warnings.append(f"{os.path.basename(path)}: block with {len(types)} types / {len(ids)} ids / "
                                        f"{len(recs)} records skipped")
                    continue
                out.n_records += len(recs)
                for t, cid, raw in zip(types, ids, recs):
                    if t == 3:
                        c = want.get(cid)
                        if c is None:
                            continue
                        d = {a: (w, v) for a, w, v in _fields(raw)}
                        if cid not in first:
                            first.add(cid)
                            if 1 in d and d[1][0] != 2 and _num(d[1][1]) > NA_RAW:
                                out.carry.setdefault((c[0], c[1]), float(_num(d[1][1])) / c[3] + c[2])
                        if 2 not in d or 3 not in d or d[2][0] != 2 or d[3][0] != 2:
                            continue                                    # carry only, no new samples
                        if 1 in d:
                            v0 = _num(d[1][1])
                        else:                                           # absolute record (no carry): anchor 0
                            v0 = 0
                            out.n_absolute += 1
                        dts, dvs = _zz(d[2][1]), _zz(d[3][1])
                        n = min(len(dts), len(dvs))
                        if n == 0:
                            continue
                        raw_v = float(v0) + np.cumsum(dvs[:n]).astype(float)
                        ok = raw_v > NA_RAW
                        key = (c[0], c[1])
                        S.setdefault(key, [[], []])
                        S[key][0].append((bs + np.cumsum(dts[:n]))[ok])
                        S[key][1].append(raw_v[ok] / c[3] + c[2])
                    elif t == 1 and (meta.gnss_id is None or cid == meta.gnss_id):
                        if "gnss" not in first:
                            first.add("gnss")
                            a = {f1: v1 for f1, _, v1 in _fields(raw) if f1 in (1, 2, 26)}
                            if 1 in a and 2 in a:
                                out.gnss_carry = (float(_num(a[1])) / 1e9, float(_num(a[2])) / 1e9,
                                                  ref + _s64(a[26]) if 26 in a else bs)
                        r = _stream(_fields(raw), (1, 2, 4), (12, 13, 16), 10, bs)
                        if r is None:
                            out.n_incomplete += 1
                        if r is None or r == "carry":
                            continue
                        out.n_absolute += r[2]
                        G.append((r[0], r[1][0] / 1e9, r[1][1] / 1e9, r[1][2] / 1000.0))
                    elif t == 2 and (meta.header_id is None or cid == meta.header_id):
                        if "ends" not in first:
                            first.add("ends")
                            a = {f1: v1 for f1, _, v1 in _fields(raw) if f1 in (1, 2, 3, 4)}
                            if len(a) == 4:
                                out.ends_carry = tuple(float(_num(a[k])) / 1e9 for k in (1, 2, 3, 4))
                        r = _stream(_fields(raw), (1, 2, 3, 4), (17, 18, 19, 20), 15, bs)
                        if r is None:
                            out.n_incomplete += 1
                        if r is None or r == "carry":
                            continue
                        out.n_absolute += r[2]
                        E.append((r[0], *(x / 1e9 for x in r[1])))
                    elif t == 4:
                        d = _fields(raw)
                        ev_t, ev_s = [], []
                        carry = _first(d, 3)
                        t12 = _first(d, 12)
                        if carry is not None and t12 is not None:
                            ev_t.append(np.array([ref + _s64(t12)], np.int64))
                            ev_s.append([_first(_fields(carry), 1, b"")])
                        times = _first(d, 1)
                        states = _all(d, 2)
                        if times is not None and states:
                            tt = bs + np.cumsum(_zz(times))
                            k = min(len(tt), len(states))
                            ev_t.append(tt[:k])
                            ev_s.append([_first(_fields(s), 1, b"") for s in states[:k]])
                        if ev_t:
                            lst = CW.setdefault(cid, [[], []])
                            lst[0].extend(ev_t)
                            lst[1].extend(ev_s)
    for key, (tt, vv) in S.items():
        out.samples[key] = [np.concatenate(tt), np.concatenate(vv)]
    cols = ["t_ms", "lat", "lon", "alt_m"]
    out.gnss = (pd.DataFrame({c: np.concatenate([g[i] for g in G]) for i, c in enumerate(cols)}) if G
                else pd.DataFrame(columns=cols))
    cols = ["t_ms", "lat1", "lon1", "lat2", "lon2"]
    out.ends = (pd.DataFrame({c: np.concatenate([e[i] for e in E]) for i, c in enumerate(cols)}) if E
                else pd.DataFrame(columns=cols))
    for cid, (tt, ss) in CW.items():
        flat = [s for grp in ss for s in grp]
        width = max((len(s) for s in flat), default=0)
        arr = np.zeros((len(flat), width), np.uint8)
        for i, s in enumerate(flat):
            arr[i, :len(s)] = np.frombuffer(s, np.uint8)
        grp = (meta.sections.loc[meta.sections.cws_id == cid, "grp"].iloc[0]
               if meta.sections is not None and (meta.sections.cws_id == cid).any() else None)
        if grp is None:
            continue                                      # a work-state group that maps to no section
        out.cws[int(grp)] = (np.concatenate(tt).astype(np.int64), arr)
    return out


# ================================================================================================ discovery
def _jdl_files(root):
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if not d.startswith(".") and d != "__MACOSX"]
        out.extend(os.path.join(dp, f) for f in fn if f.lower().endswith(".jdl"))
    return sorted(out)


def _scan(root):
    """Definitions of every .jdl under root, de-duplicated by file GUID and size. -> (list[FileMeta], warnings)"""
    metas, warns, seen = [], [], set()
    bad = dup = 0
    for p in _jdl_files(root):
        m = read_meta(p)
        if m is None or not m.vin:
            bad += 1
            continue
        key = (m.guid or os.path.basename(p).lower(), m.size)
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        metas.append(m)
    if bad:
        warns.append(f"{bad} .jdl file(s) without a readable definition frame were ignored")
    if dup:
        warns.append(f"{dup} duplicate .jdl file(s) (same GUID and size, e.g. a zip unpacked twice) were ignored")
    return metas, warns


def _crop_id(path):
    """The John Deere crop id of a file's crop record (definition 21.1.1), None when absent."""
    try:
        frames, _ = _read_frames(path, max_bytes=4_000_000)
        for pay in frames[:3]:
            blk = _first(_fields(pay), 5)
            if blk is None:
                continue
            crop = _first(_fields(blk), 21)
            c1 = _first(_fields(crop), 1) if crop is not None else None
            return _num(_first(_fields(c1), 1)) if c1 is not None else None
    except (IndexError, JdlError, struct.error, OSError):
        return None
    return None


def _crop_key(name):
    s = (name or "").lower()
    for k in ("durum", "wheat", "barley", "canola", "oats", "flax", "peas", "lentils", "rye", "corn", "soybeans"):
        if k in s or (k.endswith("s") and k[:-1] in s):
            return k
    return None


def _season(m: FileMeta) -> int:
    """Harvest year of a file: the definition's year field, else the UTC year of its reference time."""
    return int(m.year) if m.year else int(pd.Timestamp(m.ref_ms, unit="ms").year)


def _unit_key(m: FileMeta):
    """machine x field x crop x season: one JD-Data/log tree can hold several <year> folders, and two seasons of the
    same field must not be decoded into one dataset."""
    return f"{m.vin}::{m.field}::{m.crop}::{_season(m)}"


def _record_root(root, metas):
    """The folder that holds every JD-Data tree (the parent of JD-Data), or the files' common folder without one."""
    tops = []
    for m in metas:
        parts = os.path.normpath(m.path).split(os.sep)
        low = [x.lower() for x in parts]
        tops.append(os.sep.join(parts[:low.index("jd-data")]) if "jd-data" in low else os.path.dirname(m.path))
    try:
        rr = os.path.commonpath(tops)
    except ValueError:
        return root
    return rr if os.path.normcase(rr).startswith(os.path.normcase(os.path.abspath(root))) else root


def probe(root: str) -> C.Probe | None:
    """Recognise John Deere .jdl logs anywhere under root (only the definition frame of each file is read)."""
    metas, warns = _scan(root)
    if not metas:
        return None
    vins = sorted({m.vin for m in metas})
    models = {m.vin: m.model for m in metas}
    fields = sorted({m.field for m in metas if m.field})
    crops = sorted({m.crop for m in metas if m.crop})
    clients = sorted({x for m in metas for x in (m.client, m.farm) if x})
    summary = (f"{len(metas)} John Deere .jdl log file(s), {len(vins)} machine(s) "
               f"({', '.join(models[v] + ' ' + v[-6:] for v in vins)}); field: {', '.join(fields) or '(none)'}; "
               f"crop: {', '.join(crops) or '(none)'}; client/farm: {', '.join(clients) or '(none)'}")
    return C.Probe(format_id=FORMAT_ID, label=LABEL, root=_record_root(os.path.abspath(root), metas), summary=summary,
                   n_files=len(metas), machines=[f"{models[v]} {v}" for v in vins], warnings=warns)


def _block_span(path, ref):
    """(first sample time, last sample time) of a file from its block headers only (no record decoding)."""
    frames, _ = _read_frames(path)
    t0, t1 = None, None
    for pay in frames:
        for f, wt, blk in _fields(pay):
            if f != 4 or wt != 2:
                continue
            hf = []
            i, n = 0, len(blk)
            while i < n:                      # walk only the block header, stop at the data (field 9)
                key, j = _varint(blk, i)
                fn, w = key >> 3, key & 7
                if fn == 9:
                    break
                if w == 0:
                    v, j = _varint(blk, j)
                elif w == 2:
                    ln, j = _varint(blk, j)
                    v = None
                    j += ln
                elif w == 1:
                    v = None
                    j += 8
                else:
                    v = None
                    j += 4
                hf.append((fn, w, v))
                i = j
            s = ref + _s64(_first(hf, 4, 0))
            e = s + (_first(hf, 5, 0) or 0)
            t0 = s if t0 is None else min(t0, s)
            t1 = e if t1 is None else max(t1, e)
    return t0, t1


def list_units(root: str) -> list[C.Unit]:
    """One unit per machine x field x crop x season (a machine's logs of one field, all days of one season). The units
    of the latest season are pre-selected."""
    metas, _ = _scan(root)
    groups = {}
    for m in metas:
        groups.setdefault(_unit_key(m), []).append(m)
    latest = max((_season(m) for m in metas), default=None)
    units = []
    for key, ms in sorted(groups.items()):
        ms = sorted(ms, key=lambda x: x.ref_ms)
        spans = [_block_span(m.path, m.ref_ms) for m in ms]
        st = min((s[0] for s in spans if s[0] is not None), default=ms[0].ref_ms)
        en = max((s[1] for s in spans if s[1] is not None), default=ms[-1].ref_ms)
        n_s = int(sum((s[1] - s[0]) for s in spans if s[0] is not None) / 1000)
        m0 = ms[0]
        ts = pd.Timestamp(st, unit="ms", tz="UTC")
        te = pd.Timestamp(en, unit="ms", tz="UTC")
        # a tractor (grain cart) or a sprayer logs the same field and crop on the same display: no mass-flow channel
        harvest = any(v[0] == DDI_FLOW for m in ms for v in m.channels.values())
        label = (f"{m0.field or '(no field)'} · {_season(m0)} · {m0.crop or '?'} · {m0.model} {m0.vin[-6:]} · "
                 f"{ts:%d %b %Y %H:%M} - {te:%d %b %Y %H:%M} UTC · {len(ms)} file(s)"
                 + ("" if harvest else " · no grain-flow channel (not a combine)"))
        units.append(C.Unit(id=key, label=label, field=m0.field, crop=m0.crop, year=_season(m0),
                            start=ts.isoformat(), end=te.isoformat(), machine=m0.vin, n_records=n_s,
                            default=harvest and _season(m0) == latest))
    return units


# ================================================================================================ readings
def _concat_files(fds):
    """Merge the decoded files of one unit into sorted, de-duplicated streams."""
    S = {}
    for fd in fds:
        for k, (t, v) in fd.samples.items():
            S.setdefault(k, [[], []])
            S[k][0].append(t)
            S[k][1].append(v)
    samples = {}
    for k, (tt, vv) in S.items():
        t = np.concatenate(tt)
        v = np.concatenate(vv)
        o = np.argsort(t, kind="stable")
        t, v = t[o], v[o]
        keep = np.r_[True, np.diff(t) > 0]
        samples[k] = (t[keep], v[keep])
    gn = pd.concat([fd.gnss for fd in fds if len(fd.gnss)], ignore_index=True) if any(len(fd.gnss) for fd in fds) else None
    en = pd.concat([fd.ends for fd in fds if len(fd.ends)], ignore_index=True) if any(len(fd.ends) for fd in fds) else None
    if gn is not None:
        gn = gn.sort_values("t_ms", kind="stable").drop_duplicates("t_ms").reset_index(drop=True)
    if en is not None:
        en = en.sort_values("t_ms", kind="stable").drop_duplicates("t_ms").reset_index(drop=True)
    return samples, gn, en, _concat_cws(fds)


def _concat_cws(fds):
    """The section work states of several decoded files merged per work-state group: {group rank: (t, states)}."""
    cws = {}
    for fd in fds:
        for cid, (t, s) in fd.cws.items():
            cws.setdefault(cid, [[], []])
            cws[cid][0].append(t)
            cws[cid][1].append(s)
    cw = {}
    for cid, (tt, ss) in cws.items():
        w = max(s.shape[1] for s in ss)
        ss = [np.pad(s, ((0, 0), (0, w - s.shape[1]))) for s in ss]
        t = np.concatenate(tt)
        s = np.concatenate(ss)
        o = np.argsort(t, kind="stable")
        cw[cid] = (t[o], s[o])
    return cw


def _sample_files(fds, key):
    """(t, file index) of the samples of one channel merged as _concat_files merges them (time order, the first
    file's sample where two files hold the same time): which log file each merged sample comes from."""
    tt, ff = [], []
    for i, fd in enumerate(fds):
        if key in fd.samples:
            tt.append(fd.samples[key][0])
            ff.append(np.full(len(fd.samples[key][0]), i, np.int64))
    if not tt:
        return np.zeros(0, np.int64), np.zeros(0, np.int64)
    t, f = np.concatenate(tt), np.concatenate(ff)
    o = np.argsort(t, kind="stable")
    t, f = t[o], f[o]
    keep = np.r_[True, np.diff(t) > 0]
    return t[keep], f[keep]


_LAYOUT_COLS = ["width_m", "y_m", "grp", "cws_idx"]


def layout_key(secs):
    """A hashable key of a section layout (None without sections): two files with the same key share the layout."""
    if secs is None or not len(secs):
        return None
    return tuple(np.round(secs[_LAYOUT_COLS].values.astype(float), 6).ravel().tolist())


def _layout_text(secs, width_m):
    return (f"{len(secs)} sections / {float(secs.width_m.sum()):.3f} m" if secs is not None and len(secs)
            else f"no sections / {width_m:.3f} m")


def _step_at(t_src, v_src, t):
    """Value of a step function (last sample at or before t); NaN before the first sample."""
    i = np.searchsorted(t_src, t, side="right") - 1
    out = np.full(len(t), np.nan)
    ok = i >= 0
    out[ok] = v_src[i[ok]]
    return out


def _nearest(t_src, v_src, t, tol):
    if len(t_src) == 0:
        return np.full(len(t), np.nan)
    i = np.clip(np.searchsorted(t_src, t), 1, len(t_src) - 1) if len(t_src) > 1 else np.zeros(len(t), int)
    j = i - 1 if len(t_src) > 1 else i
    pick = np.where(np.abs(t_src[j] - t) <= np.abs(t_src[i] - t), j, i)
    out = v_src[pick].astype(float)
    out[np.abs(t_src[pick] - t) > tol] = np.nan
    return out


def _interval_mean(t_src, v_src, t_end, dur_ms):
    """Mean of the samples in (t_end - dur, t_end] (NaN when none)."""
    cs = np.r_[0.0, np.cumsum(v_src)]
    i1 = np.searchsorted(t_src, t_end, side="right")
    i0 = np.searchsorted(t_src, t_end - dur_ms, side="right")
    n = i1 - i0
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(n > 0, (cs[i1] - cs[i0]) / np.maximum(n, 1), np.nan)


def _gap_ms(t_src, t):
    """Distance (ms) to the nearest source sample; infinite outside the source's time span (no extrapolation)."""
    i = np.clip(np.searchsorted(t_src, t), 0, len(t_src) - 1)
    j = np.clip(i - 1, 0, len(t_src) - 1)
    gap = np.minimum(np.abs(t_src[i] - t), np.abs(t_src[j] - t)).astype(float)
    gap[(t < t_src[0]) | (t > t_src[-1])] = np.inf
    return gap


def _radii(lat0):
    """WGS 84 meridional (M) and prime-vertical (N) radii of curvature at lat0 (m)."""
    a, e2 = 6378137.0, 6.69437999014e-3
    s2 = np.sin(np.radians(lat0)) ** 2
    w = np.sqrt(1.0 - e2 * s2)
    return a * (1.0 - e2) / w ** 3, a / w


def _local_xy(lat, lon, lat0, lon0):
    """East / north metres from (lat0, lon0) on the WGS 84 ellipsoid's local tangent plane (field scale: < 1 mm per
    km of error from the curvature, unlike a mean-radius sphere, which is a few tenths of a percent off at mid
    latitudes)."""
    M, N = _radii(lat0)
    return (np.radians(np.asarray(lon) - lon0) * N * np.cos(np.radians(lat0)), np.radians(np.asarray(lat) - lat0) * M)


def _local_ll(x, y, lat0, lon0):
    """Inverse of _local_xy."""
    M, N = _radii(lat0)
    return lat0 + np.degrees(np.asarray(y) / M), lon0 + np.degrees(np.asarray(x) / (N * np.cos(np.radians(lat0))))


def _section_sum(on, v) -> np.ndarray:
    """Per row of `on` (samples x sections, True where the section is on): the sum of v[j] over the sections that
    are on, added section by section in the order of the columns (j = 0, 1, ...) from 0.0, with element-wise
    operations only. The same bits on any computer: a matrix product (on @ v) is handed to BLAS, whose order of
    summation depends on the processor and on its number of threads, so the last digits of the sum changed with
    them."""
    on = np.asarray(on, bool)
    v = np.asarray(v, float)
    out = np.zeros(on.shape[0])
    for j in range(on.shape[1]):
        out += np.where(on[:, j], v[j], 0.0)
    return out


OFFSET_NOISE_M = 1e-9            # a section centre closer to the header's middle than this is float noise, not an offset


def _section_centre(on, w, y) -> np.ndarray:
    """Per row of `on`: the width-weighted centre of the sections that are on (sum of w x y over their width, with
    _section_sum), NaN where none is on. Sections that cancel around the header's middle leave the noise of the sum
    (about 1e-16 m): a centre within OFFSET_NOISE_M of 0 is 0, so that it is not read as a lateral offset."""
    w = np.asarray(w, float)
    ws = _section_sum(on, w)
    with np.errstate(invalid="ignore", divide="ignore"):
        c = np.where(ws > 0, _section_sum(on, w * np.asarray(y, float)) / ws, np.nan)
    c[np.abs(c) < OFFSET_NOISE_M] = 0.0
    return c


def _readings(fds, meta: FileMeta):
    """One row per DDI 510 sample with every attribute and the reason it is not exported ('' = exported).
    Returns (DataFrame, info dict)."""
    samples, gn, en, cw = _concat_files(fds)
    info = {"warnings": []}
    if (DDI_FLOW, "mg1sec-1") not in samples:
        raise JdlError(f"{meta.vin}: no mass-flow channel (DDI 510) in the logs - not a harvest log?")
    if en is None or not len(en):
        raise JdlError(f"{meta.vin}: no header end-point records (type 2) in the logs")
    t, flow = samples[(DDI_FLOW, "mg1sec-1")]
    n0 = len(t)
    d = pd.DataFrame({"t_ms": t, "flow_mgs": flow})
    dt = np.r_[np.nan, np.diff(t) / 1000.0]
    nominal = float(np.nanmedian(dt)) if n0 > 1 else 1.0
    bad_dt = ~((dt > 0) & (dt <= DT_MAX_S))
    info["nominal_duration_s"] = nominal
    info["n_nominal_duration"] = int(bad_dt.sum())
    d["dur_s"] = np.where(bad_dt, nominal, dt)
    tp = t + POS_LAG_MS                                   # position / geometry time of the reading
    t0p = tp - np.round(d.dur_s.values * 1000.0).astype(np.int64)
    # ---- yield-side channels at the flow time stamp
    info["moisture_channel"] = (DDI_MOIST, "ppm") in samples
    if info["moisture_channel"]:
        tm, vm = samples[(DDI_MOIST, "ppm")]
        mo = _nearest(tm, vm, t, MOIST_TOL_MS)
        held = np.zeros(n0, bool)
        if np.isnan(mo).any() and len(tm):
            k = np.searchsorted(tm, t, side="right") - 1        # last sample at or before t
            held = np.isnan(mo) & (k >= 0)
            held[held] &= (t[held] - tm[k[held]]) <= MOIST_HOLD_MS
            mo[held] = vm[k[held]].astype(float)
        d["moist"] = mo / 1e4
        d["moist_held"] = held
    else:
        d["moist"] = np.nan
        d["moist_held"] = False
        info["warnings"].append(f"{meta.vin}: no moisture channel (DDI 6) - Moisture__ and the dry columns are NaN")
    for ddi, col in ((DDI_YWET, "y39"), (DDI_YDRY, "y43")):
        if (ddi, "bu1ac-1") in samples:
            ty, vy = samples[(ddi, "bu1ac-1")]
            d[col] = _nearest(ty, vy, t, 600)
        else:
            d[col] = np.nan
    # ---- geometry-side channels at the position time
    if (DDI_SPEED, "km1hr-1") in samples:                 # wheel / radar speed: diagnostic only
        tv, vv = samples[(DDI_SPEED, "km1hr-1")]
        d["speed560_kmh"] = _interval_mean(tv, vv, tp, d.dur_s.values * 1000.0)
    else:
        d["speed560_kmh"] = np.nan
    if (DDI_WIDTH, "m") in samples:
        tw, vw = samples[(DDI_WIDTH, "m")]
        d["width_m"] = _step_at(tw, vw, tp + WIDTH_LAG_MS)
    else:
        d["width_m"] = np.nan
    # ---- the section layout of every reading: the one of the log file its flow sample comes from. A unit's files
    # can define different layouts (another header profile chosen on the display for a while; the first file's layout
    # for every reading would put the full-width readings beside the bar midpoint)
    keys = [layout_key(fd.meta.sections) for fd in fds]
    groups = {}
    for i, k in enumerate(keys):
        groups.setdefault(k, []).append(i)
    _, src = _sample_files(fds, (DDI_FLOW, "mg1sec-1"))
    d["n_act"] = -1
    d["y_act_m"] = np.nan
    hw_r = np.full(n0, np.nan)                      # header width of each reading's layout (else its file's width)
    layouts = []
    single = len(groups) == 1
    if not single:
        info["warnings"].append(
            f"{meta.vin}: the log files define {len(groups)} section layouts ("
            + "; ".join(f"{_layout_text(fds[ix[0]].meta.sections, fds[ix[0]].meta.width_m)} in {len(ix)} file(s), "
                        f"{int(np.isin(src, ix).sum()):,} flow samples" for ix in groups.values())
            + "): every reading takes the layout of its own log file")
    for k, ix in groups.items():
        rows = np.flatnonzero(np.isin(src, ix)) if not single else np.arange(n0)
        secs = fds[ix[0]].meta.sections
        layouts.append({"key": k, "files": ix, "sections": secs, "rows": rows})
        if k is None:
            hw_r[rows] = [fds[f].meta.width_m for f in src[rows]] if not single else meta.width_m
            if not len(rows):
                continue
            info["warnings"].append(f"{meta.vin}: no section-control states in "
                                    + ("the log" if single else f"{len(ix)} log file(s) ({len(rows):,} flow samples)")
                                    + " - readings placed at the full-header centre")
            continue
        hw_r[rows] = float(secs.width_m.sum())
        cwk = cw if single else _concat_cws([fds[i] for i in ix])
        if not (len(rows) and cwk):
            if len(rows):
                info["warnings"].append(f"{meta.vin}: no section-control states in the log"
                                        + ("" if single else f" of {len(ix)} file(s)")
                                        + " - readings placed at the full-header centre")
            continue
        tq = tp[rows]
        on = np.zeros((len(rows), len(secs)), bool)
        have = np.zeros(len(secs), bool)
        for col, (cid, ixs) in enumerate(zip(secs.grp.values, secs.cws_idx.values)):
            if cid not in cwk:
                continue
            ct, cs = cwk[cid]
            if ixs >= cs.shape[1]:
                continue
            i = np.searchsorted(ct, tq, side="right") - 1
            ok = i >= 0
            on[ok, col] = cs[i[ok], ixs] == 1
            have[col] = True
        if have.all():
            w = secs.width_m.values
            ws = _section_sum(on, w)
            if "sec_width_m" not in d:
                d["sec_width_m"] = np.nan
            d.loc[rows, "n_act"] = on.sum(1)
            d.loc[rows, "sec_width_m"] = ws
            d.loc[rows, "y_act_m"] = _section_centre(on, w, secs.y_m.values)
        else:
            info["warnings"].append(f"{meta.vin}: section states incomplete ({int(have.sum())}/{len(secs)} sections) - "
                                    "readings placed at the full-header centre")
    # DDI 12001 is the ACTIVE cutting width: a sample wider than the header (the sum of its file's sections, else the
    # file's defined width) cannot be one (held by the step lookup over every later reading, it would make the area
    # several times too large, the yield as many times too low, and F8 remove most of the points)
    with np.errstate(invalid="ignore"):
        wide = (np.isfinite(hw_r) & (hw_r > 0)) & (d.width_m.values > hw_r * (1 + WIDTH_TOL))
    if wide.any():
        hw_w = sorted({round(float(x), 3) for x in hw_r[wide]})
        txt = (f"{meta.vin}: the working-width channel (DDI 12001) reads up to {np.nanmax(d.width_m.values):.3f} m on "
               f"{int(wide.sum()):,} flow samples, wider than the header ({', '.join(f'{x:.3f}' for x in hw_w)} m): "
               "not the active width")
        if "sec_width_m" in d:
            sw = d.sec_width_m.values
            d["width_m"] = np.where(np.isfinite(sw), sw, np.where(wide, hw_r, d.width_m.values))
            info["warnings"].append(txt + " - width = sum of active sections")
        else:
            d.loc[wide, "width_m"] = hw_r[wide]
            info["warnings"].append(txt + " - those samples take the header width (no section states to use)")
    if np.isnan(d.width_m.values).all():
        if "sec_width_m" in d:
            d["width_m"] = d.sec_width_m
            info["warnings"].append(f"{meta.vin}: no working-width channel (DDI 12001) - width = sum of active sections")
        else:
            raise JdlError(f"{meta.vin}: neither a working-width channel (DDI 12001) nor section states")
    # ---- header end points (type 2): garbage fixes far from the machine's median position are removed first
    lat0, lon0 = float(np.median(en.lat1)), float(np.median(en.lon1))
    far = np.hypot(*_local_xy(en.lat1.values, en.lon1.values, lat0, lon0)) > OUTLIER_KM * 1000
    far |= np.hypot(*_local_xy(en.lat2.values, en.lon2.values, lat0, lon0)) > OUTLIER_KM * 1000
    info["end_samples_outlier"] = int(far.sum())
    en = en[~far].reset_index(drop=True)
    et = en.t_ms.values
    ex1, ey1 = _local_xy(en.lat1.values, en.lon1.values, lat0, lon0)
    ex2, ey2 = _local_xy(en.lat2.values, en.lon2.values, lat0, lon0)
    emx, emy = (ex1 + ex2) / 2, (ey1 + ey2) / 2           # full-width header centre, 5 Hz

    def at(tq, a):
        return np.interp(tq.astype(float), et.astype(float), a)
    x1, y1, x2, y2 = at(tp, ex1), at(tp, ey1), at(tp, ex2), at(tp, ey2)
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    d["pos_gap_ms"] = np.maximum(_gap_ms(et, tp), _gap_ms(et, t0p))
    # distance = path of the header centre during the reading interval (chord of the 5 Hz track)
    vx, vy = mx - at(t0p, emx), my - at(t0p, emy)
    dist = np.hypot(vx, vy)
    d["dist_m"] = dist
    d["speed_kmh"] = dist / d.dur_s.values * 3.6
    bar = np.hypot(x2 - x1, y2 - y1)
    info["bar_length_m_median"] = float(np.nanmedian(bar))
    # self-check: the layout a reading takes must be as wide as the cutter bar logged with it (end points)
    info["layouts"] = []
    for L in layouts:
        r = L["rows"][(flow[L["rows"]] > 0) & np.isfinite(bar[L["rows"]])] if len(L["rows"]) else L["rows"]
        hwl = float(L["sections"].width_m.sum()) if L["key"] is not None else float(fds[L["files"][0]].meta.width_m)
        bl = float(np.median(bar[r])) if len(r) else float("nan")
        info["layouts"].append({"layout": _layout_text(L["sections"], fds[L["files"][0]].meta.width_m),
                                "files": len(L["files"]), "flow_samples": int(len(L["rows"])),
                                "flow_samples_with_grain": int(len(r)), "bar_length_m_median": bl})
        if len(r) and np.isfinite(hwl) and hwl > 0 and abs(bl - hwl) > LAYOUT_TOL * hwl:
            info["warnings"].append(
                f"{meta.vin}: the section layout of {len(L['files'])} log file(s) ({hwl:.3f} m) does not match the "
                f"cutter-bar length logged with its {len(r):,} flow samples with grain ({bl:.3f} m): the width and the "
                "active-width offset of these readings may be wrong - check the header set up on the display")
    # travel direction: perpendicular to the bar; which perpendicular points forward is fixed per machine from the track
    px, py = -(y2 - y1), (x2 - x1)                         # end1 -> end2 rotated +90 deg (counter-clockwise)
    mov = (dist > 0.5) & (d.pos_gap_ms.values <= POS_MAX_GAP_MS)
    dot = (px * vx + py * vy)[mov]
    sgn = 1.0 if np.nansum(np.sign(dot)) >= 0 else -1.0
    info["end1_side"] = "left" if sgn > 0 else "right"
    info["forward_agreement"] = float(np.mean(np.sign(dot) == sgn)) if len(dot) else float("nan")
    d["heading"] = (np.degrees(np.arctan2(sgn * px, sgn * py)) + 360.0) % 360.0
    # centre of the ACTIVE sections: ISO 11783 y is positive to the RIGHT of travel (sign verified on Ops Center
    # Y_Offset_f); the unit vector towards the right end is end1 -> end2 when end1 is the left end
    with np.errstate(invalid="ignore", divide="ignore"):
        rx, ry = sgn * (x2 - x1) / bar, sgn * (y2 - y1) / bar
    yy = np.where(np.isfinite(d.y_act_m.values), d.y_act_m.values, 0.0)
    d["lat"], d["lon"] = _local_ll(mx + rx * yy, my + ry * yy, lat0, lon0)
    d["y_off_m"] = yy
    # elevation: GNSS antenna altitude (type 1) at the position time
    if gn is not None and len(gn):
        gfar = np.hypot(*_local_xy(gn.lat.values, gn.lon.values, lat0, lon0)) > OUTLIER_KM * 1000
        info["gnss_samples_outlier"] = int(gfar.sum())
        g2 = gn[~gfar]
        alt = np.interp(tp.astype(float), g2.t_ms.values.astype(float), g2.alt_m.values)
        alt[_gap_ms(g2.t_ms.values, tp) > POS_MAX_GAP_MS] = np.nan
        d["alt_m"] = alt
    else:
        d["alt_m"] = np.nan
        info["warnings"].append(f"{meta.vin}: no GNSS records (type 1) - Elevation_ is NaN")
    with np.errstate(invalid="ignore", divide="ignore"):
        r560 = d.speed_kmh / d.speed560_kmh
    okv = (d.speed560_kmh > 2) & np.isfinite(r560)
    info["track_over_wheel_speed_median"] = float(np.median(r560[okv])) if okv.any() else None
    # ---- what the vendor software exports (exclusive reasons, in this order)
    reason = np.full(n0, "", object)
    act = d.width_m.values > 0
    if "sec_width_m" in d:
        info["n_width_on_sections_off"] = int((act & (d.n_act.values == 0)).sum())
    reason[~act] = "header_up"
    reason[(reason == "") & (d.pos_gap_ms.values > POS_MAX_GAP_MS)] = "no_position"
    reason[(reason == "") & ~(d.speed_kmh.values >= V_MIN_KMH)] = "stationary"
    d["reason"] = reason
    return d, info


# ================================================================================================ points as SMS
def _sms_groups(t, t0, carried):
    """Which header samples of one file make one SMS point -> (first index, last index, Duration_s) of every point.

    SMS adds the intervals between the samples up, in seconds, from the first sample of a point, and the first sample
    that takes the sum over SMS_WINDOW_S starts the next point (five 5 Hz samples in normal running; a gap in the
    header record closes the point before it). A file that starts with a carry counts that carry as a sample at the
    start of its first block (t0), SMS_CARRY_S long; without a carry the first sample itself is that long. The
    Duration of a point is the time since the last sample of the point before it. The sum is kept in floating-point
    seconds as SMS's own: at exactly 0.9 s the rounding of the sum decides which side the sample falls on."""
    n = len(t)
    first, last, dur = [], [], []
    if n == 0:
        return np.zeros(0, int), np.zeros(0, int), np.zeros(0)
    prev = int(t0) if carried else int(t[0])
    ended = prev - int(round(SMS_CARRY_S * 1000))         # where the Duration of the first point starts
    start, acc = 0, 0.0
    for k in range(n):
        tk = int(t[k])
        acc += (tk - prev) / 1000.0
        prev = tk
        if acc > SMS_WINDOW_S:
            if k > start:
                first.append(start)
                last.append(k - 1)
                dur.append((int(t[k - 1]) - ended) / 1000.0)
                ended = int(t[k - 1])
            else:                                         # the carry alone (no header sample in its 0.9 s): no point
                ended = int(t0)
            start, acc = k, 0.0
    first.append(start)
    last.append(n - 1)
    dur.append((int(t[n - 1]) - ended) / 1000.0)
    return np.array(first, int), np.array(last, int), np.array(dur, float)


def _sms_sections(meta: FileMeta):
    """The sections of the header, without the whole-header element some machines list among them with a work state
    of its own (many narrow sections and one element as wide as the header; with it every width doubles). An element
    is left out only when the header has at least 3 elements and its width equals the sum of all the others (within
    max(0.01 m, 0.5 %)): a header of two unequal sections (e.g. 5.0 + 4.0 m) keeps both."""
    secs = meta.sections
    if secs is None or len(secs) < 3:
        return secs
    w = secs.width_m.values.astype(float)
    rest = float(w.sum()) - w
    whole = np.abs(w - rest) <= np.maximum(0.01, 0.005 * w)
    return secs[~whole].reset_index(drop=True) if whole.sum() == 1 else secs


def _held(fd: FileData, key, t):
    """A channel at the times t as SMS reads it: the last sample not later than SMS_SNAP_MS after t, the value the
    file carries in before its first sample, NaN when the file has neither."""
    ts, vs = fd.samples.get(key, (np.zeros(0, np.int64), np.zeros(0)))
    if key in fd.carry:
        ts = np.r_[np.iinfo(np.int64).min, ts]
        vs = np.r_[fd.carry[key], vs]
    return _step_at(ts, vs, np.asarray(t, np.int64) + SMS_SNAP_MS) if len(ts) else np.full(len(t), np.nan)


def _sms_file_points(fd: FileData, lat0, lon0):
    """The points SMS makes of ONE log file (it reads every file on its own) -> (DataFrame, info), one row per point,
    exported or not ('reason'), from the 5 Hz header samples (type 2) of the file:

        point       the samples _sms_groups puts together; its time and position are those of its last sample
        position    the middle of the two header end points, moved across the header to the middle of the sections
                    on at the file's first widest point (0 for a header that works at full width once)
        Duration_s  _sms_groups; the last point of a file runs on to the next flow sample when that comes before
                    another GNSS fix
        distance    per sample, what the antenna (type 1) moved since the fix before the sample's own, on the
                    ellipsoid; 0 for a sample without a fix of its own; the carry of the GNSS record gives the first
                    fix its step; a file of one sample and no carry: SMS_LONE_M. A point adds its samples up
        width       the sections on SMS_SNAP_MS after the last sample (the logged width DDI 12001 without section
                    states); Y offset = the middle of those sections from the reference above
        flow,       the mean over the point's samples, weighted by their distance, of the channel held as _held
        moisture    does; a sample with the header not working (no section on) counts as 0, so a point that starts
                    or ends a pass reads low, as in SMS
        elevation   the antenna altitude at the last sample
    Positions farther than OUTLIER_KM from (lat0, lon0) are garbage and left out before anything else."""
    info = {"end_samples_outlier": 0, "gnss_samples_outlier": 0, "no_section_states": False}
    en = fd.ends.drop_duplicates("t_ms")
    far = np.hypot(*_local_xy(en.lat1.values, en.lon1.values, lat0, lon0)) > OUTLIER_KM * 1000
    far |= np.hypot(*_local_xy(en.lat2.values, en.lon2.values, lat0, lon0)) > OUTLIER_KM * 1000
    info["end_samples_outlier"] = int(far.sum())
    en = en[~far]
    n = len(en)
    if n == 0:
        return None, info
    t = en.t_ms.values.astype(np.int64)
    x1, y1 = _local_xy(en.lat1.values, en.lon1.values, lat0, lon0)
    x2, y2 = _local_xy(en.lat2.values, en.lon2.values, lat0, lon0)
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    carried = fd.ends_carry is not None
    first, last, dur = _sms_groups(t, fd.t0_ms if fd.t0_ms is not None else t[0], carried)
    # ---- distance: the antenna's own 5 Hz steps
    gn = fd.gnss.drop_duplicates("t_ms")
    gx, gy = _local_xy(gn.lat.values, gn.lon.values, lat0, lon0)
    gfar = np.hypot(gx, gy) > OUTLIER_KM * 1000
    info["gnss_samples_outlier"] = int(gfar.sum())
    gt, gx, gy, galt = gn.t_ms.values.astype(np.int64)[~gfar], gx[~gfar], gy[~gfar], gn.alt_m.values[~gfar]
    gstep = np.r_[0.0, np.hypot(np.diff(gx), np.diff(gy))]
    gdt = np.r_[0.0, np.diff(gt) / 1000.0]
    if len(gt) and fd.gnss_carry is not None:
        cx, cy = _local_xy(np.array([fd.gnss_carry[0]]), np.array([fd.gnss_carry[1]]), lat0, lon0)
        if np.hypot(cx[0], cy[0]) <= OUTLIER_KM * 1000:
            gstep[0] = float(np.hypot(gx[0] - cx[0], gy[0] - cy[0]))
            gdt[0] = max((int(gt[0]) - fd.gnss_carry[2]) / 1000.0, 0.0)
    j = np.minimum(np.searchsorted(gt, t), max(len(gt) - 1, 0))
    own = (gt[j] == t) if len(gt) else np.zeros(n, bool)
    step = np.where(own, gstep[j], 0.0) if len(gt) else np.zeros(n)
    moving = np.where(own, gdt[j], 0.0) if len(gt) else np.zeros(n)
    alt = np.where(own, galt[j], np.nan) if len(gt) else np.full(n, np.nan)
    if n == 1 and not carried:
        step[0] = SMS_LONE_M
    # ---- sections on at every sample
    secs = _sms_sections(fd.meta)
    width = np.zeros(n)
    centre = np.zeros(n)
    n_on = np.zeros(n, int)
    on = np.zeros((n, len(secs) if secs is not None else 0), bool)
    if secs is not None and len(secs) and fd.cws:
        for col, (cid, ix) in enumerate(zip(secs.grp.values, secs.cws_idx.values)):
            if cid in fd.cws and ix < fd.cws[cid][1].shape[1]:
                o = np.argsort(fd.cws[cid][0], kind="stable")
                ct, cs = fd.cws[cid][0][o], fd.cws[cid][1][o]
                i = np.searchsorted(ct, t + SMS_SNAP_MS, side="right") - 1
                ok = i >= 0
                on[ok, col] = cs[i[ok], ix] == 1
        w = secs.width_m.values
        width = _section_sum(on, w)
        n_on = on.sum(1)
        centre = np.nan_to_num(_section_centre(on, w, secs.y_m.values), nan=0.0)
    else:
        info["no_section_states"] = True
        width = np.nan_to_num(_held(fd, (DDI_WIDTH, "m"), t))
        n_on = (width > 0).astype(int)
    working = width > 0
    # ---- the channels, per sample, then per point
    flow = np.where(working, np.nan_to_num(_held(fd, (DDI_FLOW, "mg1sec-1"), t)), 0.0)
    has_moist = (DDI_MOIST, "ppm") in fd.samples or (DDI_MOIST, "ppm") in fd.carry
    moist = np.where(working, np.nan_to_num(_held(fd, (DDI_MOIST, "ppm"), t)), 0.0) / 1e4
    y39 = _held(fd, (DDI_YWET, "bu1ac-1"), t)
    v560 = _held(fd, (DDI_SPEED, "km1hr-1"), t)

    def total(v):
        return np.add.reduceat(v, first)

    dist = total(step)
    with np.errstate(invalid="ignore", divide="ignore"):
        cnt = (last - first + 1).astype(float)
        f_pt = np.where(dist > 0, total(flow * step) / dist, total(flow) / cnt)
        m_pt = np.where(dist > 0, total(moist * step) / dist, total(moist) / cnt)
        speed = np.where(total(moving) > 0, dist / total(moving), 0.0) * 3.6
    if (DDI_FLOW, "mg1sec-1") in fd.samples:                # the last point runs on to the next flow sample
        ft = fd.samples[(DDI_FLOW, "mg1sec-1")][0]
        k = np.searchsorted(ft, t[-1], side="right")
        g_next = gt[np.searchsorted(gt, t[-1], side="right"):][:1]
        if k < len(ft) and (not len(g_next) or ft[k] < g_next[0]):
            dur[-1] += (int(ft[k]) - int(t[-1])) / 1000.0
    # ---- where the point is: the reference across the header, the heading
    c_ref = 0.0
    wl = width[last]
    widest = on[last][int(np.argmax(wl >= wl.max() - 1e-9))] if on.shape[1] and len(wl) and wl.max() > 0 else None
    if widest is not None and widest.any():           # a file without section states (width DDI 12001): c_ref 0
        c_ref = float(((secs.y_m.values - secs.width_m.values / 2)[widest].min()
                       + (secs.y_m.values + secs.width_m.values / 2)[widest].max()) / 2)
    bar = np.hypot(x2 - x1, y2 - y1)
    dmx, dmy = np.r_[0.0, np.diff(mx)], np.r_[0.0, np.diff(my)]
    px, py = -(y2 - y1), (x2 - x1)                         # end1 -> end2 rotated +90 deg
    dot = (px * dmx + py * dmy)[np.hypot(dmx, dmy) > 0.1]
    sgn = 1.0 if np.nansum(np.sign(dot)) >= 0 else -1.0     # which perpendicular of the bar points forward
    with np.errstate(invalid="ignore", divide="ignore"):
        rx, ry = np.nan_to_num(sgn * (x2 - x1) / bar), np.nan_to_num(sgn * (y2 - y1) / bar)
    lat, lon = _local_ll(mx + rx * c_ref, my + ry * c_ref, lat0, lon0)
    out = pd.DataFrame({
        "t_ms": t[last], "dur_s": dur, "dist_m": dist, "width_m": wl, "flow_mgs": f_pt, "moist": m_pt,
        "has_moist": has_moist, "speed_kmh": speed, "alt_m": alt[last], "lat": lat[last], "lon": lon[last],
        "heading": ((np.degrees(np.arctan2(sgn * px, sgn * py)) + 360.0) % 360.0)[last],
        "y_off_m": np.where(wl > 0, centre[last] - c_ref, 0.0), "y39": y39[last], "speed560_kmh": v560[last],
        "n_act": n_on[last], "n_samples": last - first + 1})
    out["reason"] = np.where(wl > 0, np.where(dist > 0, "", "no_distance"), "header_up")
    return out, info


def _sms_points(fds, meta: FileMeta):
    """The SMS points of one unit: every log file on its own (_sms_file_points), in time order. Of two copies of one
    log (the same GUID, one cut short by an earlier export of the card) only the longest is read, so that no point
    comes twice. Returns (DataFrame as _readings, info dict)."""
    info = {"warnings": [], "points": "sms"}
    best = {}
    for fd in fds:
        k = fd.meta.guid or fd.meta.path
        if k not in best or fd.meta.size > best[k].meta.size:
            best[k] = fd
    if len(best) < len(fds):
        info["warnings"].append(f"{meta.vin}: {len(fds) - len(best)} shorter cop(y/ies) of a log file left out "
                                "(the same GUID twice: SMS would read every point of it twice)")
    use = [fd for fd in best.values() if len(fd.ends)]
    if not use:
        raise JdlError(f"{meta.vin}: no header end-point records (type 2) in the logs")
    if not any((DDI_FLOW, "mg1sec-1") in fd.samples for fd in use):
        raise JdlError(f"{meta.vin}: no mass-flow channel (DDI 510) in the logs - not a harvest log?")
    lat0 = float(np.median(np.concatenate([fd.ends.lat1.values for fd in use])))
    lon0 = float(np.median(np.concatenate([fd.ends.lon1.values for fd in use])))
    parts, n_end_far, n_gnss_far, n_nosec, n_samples, k_nosec = [], 0, 0, 0, 0, 0
    for fd in sorted(use, key=lambda f: f.meta.ref_ms):
        p, i = _sms_file_points(fd, lat0, lon0)
        n_end_far += i["end_samples_outlier"]
        n_gnss_far += i["gnss_samples_outlier"]
        n_nosec += int(i["no_section_states"])
        if p is not None and i["no_section_states"]:
            k_nosec += int((p.reason.values == "").sum())
        if p is not None:
            n_samples += int(p.n_samples.sum())
            parts.append(p)
    if not parts:
        raise JdlError(f"{meta.vin}: no usable header position in the logs")
    d = pd.concat(parts, ignore_index=True).sort_values("t_ms", kind="stable").reset_index(drop=True)
    info.update(end_samples_outlier=n_end_far, gnss_samples_outlier=n_gnss_far, n_header_samples=n_samples,
                moisture_channel=bool(d.has_moist.any()), files_without_section_states=n_nosec,
                points_from_files_without_section_states=k_nosec)
    if k_nosec:
        # a note, not a fault: many logs hold a few files without work states (often none of their points has a
        # width); only say it when such a file gives points
        info["warnings"].append(f"{meta.vin}: {k_nosec:,} point(s) come from {n_nosec} log file(s) without "
                                "section-control states: their width is the logged working width (DDI 12001), as SMS "
                                "reads them")
    if not info["moisture_channel"]:
        info["warnings"].append(f"{meta.vin}: no moisture channel (DDI 6) - Moisture__ and the dry columns are NaN")
        d["moist"] = np.nan
    d["moist_held"] = False
    return d, info


# ================================================================================================ decode
PRE_STEP_REASONS = ["header_up", "no_position", "stationary", "no_distance", "position_outlier", "no_moisture"]
DIAG_COLUMNS = ["t_ms", "Y_Offset_f", "Yld_Mon_W", "Spd560_mph", "n_act"]


def decode_detailed(root: str, unit_ids=None, opts: C.DecodeOptions | None = None, log=print, points: str = "sms"):
    """decode() plus diagnostic columns (DIAG_COLUMNS: time stamp of the reading in ms UTC, lateral offset of the
    active-width centre (ft, + = right), the monitor's own wet yield DDI 39 (bu/ac), wheel speed DDI 560 (mph), active
    sections)."""
    t_start = _time.time()
    opts = opts or default_options()
    if points not in POINTS:
        raise JdlError(f"points must be one of {POINTS}, not {points!r}")
    metas, warns = _scan(root)
    if not metas:
        raise JdlError(f"no John Deere .jdl logs under {root}")
    groups = {}
    for m in metas:
        groups.setdefault(_unit_key(m), []).append(m)
    missing = [u for u in (unit_ids or []) if u not in groups]
    if missing:
        raise JdlError(f"unknown unit id(s): {missing}")
    sel = sorted(groups) if not unit_ids else list(unit_ids)
    report = {k: None for k in C.DECODE_REPORT_KEYS}
    report.update(format=FORMAT_ID, label=LABEL, units=list(sel), warnings=list(warns))
    pre_total = {k: 0 for k in PRE_STEP_REASONS}
    frames, details, n_read, skipped = [], [], 0, []
    resolved = None
    override = False
    for uid in sel:
        ms = sorted(groups[uid], key=lambda x: x.ref_ms)
        m0 = ms[0]
        log(f"decoding {m0.model} {m0.vin} | {m0.field} | {m0.crop}: {len(ms)} file(s)")
        fds = []
        for m in ms:
            fd = decode_file(m.path, m)
            report["warnings"].extend(fd.warnings)
            fds.append(fd)
        if not any((DDI_FLOW, "mg1sec-1") in fd.samples for fd in fds):
            # a tractor with the grain cart in the same field (selected with the combines by the field of
            # interest): no grain flow, so the unit is left out and the combines are decoded
            skipped.append(m0.vin)
            report["warnings"].append(f"{m0.vin} ({m0.model}): no mass-flow channel (DDI 510) in its {len(ms)} log(s) - "
                                      "not a combine (a tractor with the grain cart?); this unit was left out")
            continue
        n_inc = sum(fd.n_incomplete for fd in fds)
        if n_inc:
            report["warnings"].append(f"{m0.vin}: {n_inc} malformed position record(s) (time deltas without value "
                                      "deltas) skipped")
        d, info = _sms_points(fds, m0) if points == "sms" else _readings(fds, m0)
        report["warnings"].extend(info.pop("warnings"))
        n_read += len(d)
        # crop / test weight / standard moisture: option > monitor > crop table
        tw_s = [fd.samples[(DDI_TW, "lb1bu-1")][1] for fd in fds if (DDI_TW, "lb1bu-1") in fd.samples]
        mon_tw = (float(np.median(np.concatenate(tw_s))) if tw_s else
                  (m0.tw_kg_bu * C.LB_PER_KG if m0.tw_kg_bu else None))
        ck = _crop_key(m0.crop)
        # a crop chosen in the options that differs from the monitor's: the monitor's test weight and standard
        # moisture belong to the monitor's crop and are not used (as jd_gs3_rcd)
        override = bool(opts.crop) and ck is not None and opts.crop.strip().lower() != ck
        if override:
            report["warnings"].append(f"{m0.vin}: monitor crop {m0.crop!r} replaced by {opts.crop!r}; the monitor's "
                                      f"test weight ({mon_tw}) and standard moisture ({m0.std_moisture}) are not used")
            mon_tw = None
            sm_src = "option" if opts.std_moisture_pct is not None else "common.CROPS table (crop chosen by the user)"
            crop, tw, sm = opts.resolve(None, None, None)
        else:
            sm_src = ("option" if opts.std_moisture_pct is not None else
                      "Ops Center standard for this crop (OPS_CENTER_STD_MOISTURE)" if ck in OPS_CENTER_STD_MOISTURE
                      else "monitor crop record" if m0.std_moisture is not None else "common.CROPS table")
            crop, tw, sm = opts.resolve(ck, mon_tw, OPS_CENTER_STD_MOISTURE.get(ck, m0.std_moisture))
        if not override and m0.std_moisture is not None and abs(sm - m0.std_moisture) > 1e-6:
            msg = (f"dry moisture {sm:g} % (" + ("what SMS gives this crop" if sm_src.startswith("Ops Center") else
                                                 sm_src) + f") differs from the crop record of the logs "
                   f"({m0.std_moisture:g} %): the import uses {sm:g} %; to use the logs' value, set the dry moisture "
                   "in the import settings")
            if msg not in report["warnings"]:
                report["warnings"].append(msg)
        if resolved and (abs(resolved[1] - tw) > 1e-6 or abs(resolved[2] - sm) > 1e-6):
            report["warnings"].append(f"{m0.vin}: test weight / standard moisture differ between units "
                                      f"({resolved[1]:.2f}/{resolved[2]:.2f} vs {tw:.2f}/{sm:.2f})")
        resolved = (crop, tw, sm)
        # garbage positions far from the machine's track, after the vendor filters
        keep = d.reason.values == ""
        if keep.any():
            x, y = _local_xy(d.lat.values, d.lon.values, float(np.median(d.lat[keep])), float(np.median(d.lon[keep])))
            d.loc[keep & (np.hypot(x, y) > OUTLIER_KM * 1000), "reason"] = "position_outlier"
        if info.get("moisture_channel"):
            d.loc[(d.reason.values == "") & d.moist.isna().values, "reason"] = "no_moisture"
        n_h = int(((d.reason.values == "") & d.moist_held.values).sum()) if "moist_held" in d else 0
        if n_h:
            report["warnings"].append(f"{m0.vin}: {n_h} flow sample(s) in a gap of the moisture channel carry the "
                                      f"last moisture sample (held up to {MOIST_HOLD_MS / 1000:g} s, as Ops Center)")
        pre = {k: int((d.reason.values == k).sum()) for k in PRE_STEP_REASONS}
        for k in pre:
            pre_total[k] += pre[k]
        r = d[d.reason.values == ""].reset_index(drop=True)
        tz = opts.tz_offset_h
        ts = pd.to_datetime(r.t_ms.values, unit="ms")
        start = pd.Timestamp(int(r.t_ms.min()) if len(r) else m0.ref_ms, unit="ms")
        if tz is not None:
            ts = ts + pd.to_timedelta(tz, unit="h")
            start = start + pd.Timedelta(hours=tz)
        area_ac = r.width_m.values * r.dist_m.values / 4046.8564224
        wet_lb = r.flow_mgs.values / 1e6 * C.LB_PER_KG * r.dur_s.values
        yc = C.yield_columns(wet_lb, area_ac, r.moist.values, tw, sm, opts.shrink)
        out = pd.DataFrame({
            "Field": m0.field, "Dataset": C.dataset_name(f"{m0.field} {m0.vin[-6:]}".strip(), start),
            "Product": crop if override else (m0.variety or m0.crop), "Obj__Id": np.arange(1, len(r) + 1),
            "Time": ts.strftime("%Y-%m-%d %H:%M:%S.") + ((r.t_ms.values % 1000) // 100).astype(str),
            "Duration_s": r.dur_s.values, "Elevation_": r.alt_m.values * C.FT_PER_M,
            "Speed_mph_": r.speed_kmh.values / 3.6 * C.MPH_PER_MS, "Distance_f": r.dist_m.values * C.FT_PER_M,
            "Track_deg_": r.heading.values, "Swth_Wdth_": r.width_m.values * C.FT_PER_M, "Moisture__": r.moist.values,
            **yc, "Machine": m0.vin, "Src_Fmt": FORMAT_ID, "lon": r.lon.values, "lat": r.lat.values,
            "t_ms": r.t_ms.values, "Y_Offset_f": r.y_off_m.values * C.FT_PER_M, "Yld_Mon_W": r.y39.values,
            "Spd560_mph": r.speed560_kmh.values / 3.6 * C.MPH_PER_MS, "n_act": r.n_act.values})
        frames.append(out)
        # calibration evidence: our wet yield vs the monitor's own wet yield (DDI 39) on full-width readings
        with np.errstate(invalid="ignore", divide="ignore"):
            ratio = out.Yld_Vol_We.values / r.y39.values
        okr = np.isfinite(ratio) & (r.y39.values > 10) & (r.speed_kmh.values > 2)
        if okr.sum() >= 30 and abs(float(np.median(ratio[okr])) - 1) > MONITOR_RATIO_TOL:
            report["warnings"].append(
                f"{m0.vin}: the decoded wet yield is {100 * float(np.median(ratio[okr])):.0f} % of the monitor's own wet "
                f"yield (DDI 39, median over {int(okr.sum()):,} readings; it should be close to 100 %): the area "
                "(width or distance) or the mass flow was read wrong - check this decode before cleaning it")
        details.append(dict(
            machine=m0.vin, model=m0.model, client=m0.client, farm=m0.farm, field=m0.field, crop=m0.crop,
            variety=m0.variety, display=m0.display, software=m0.software, files=len(ms),
            **{("n_sms_points" if points == "sms" else "n_flow_samples"): int(len(d))},
            n_readings=int(len(out)), pre_step=pre, n_absolute_records=int(sum(fd.n_absolute for fd in fds)),
            wet_lb_all_flow_samples=float((d.flow_mgs.values / 1e6 * C.LB_PER_KG * d.dur_s.values).sum()),
            ratio_to_monitor_wet_yield_median=float(np.median(ratio[okr])) if okr.any() else None,
            ratio_to_monitor_wet_yield_p05_p95=([float(np.percentile(ratio[okr], 5)),
                                                 float(np.percentile(ratio[okr], 95))] if okr.any() else None),
            **info))
    if skipped and not frames:
        raise JdlError(f"{', '.join(skipped)}: no mass-flow channel (DDI 510) in the logs - not a harvest log? Select the "
                       "combines' units")
    df = (pd.concat(frames, ignore_index=True) if frames
          else pd.DataFrame(columns=C.STANDARD_NAMES + ["lon", "lat"] + DIAG_COLUMNS))
    crop, tw, sm = resolved if resolved else (None, None, None)
    rows, tot = C.summarize(df) if len(df) else ([], {})
    report.update(n_records_read=int(n_read), n_readings=int(len(df)), pre_step=pre_total, machines=rows, totals=tot,
                  time_basis="UTC" if opts.tz_offset_h is None else "local")
    report["points"] = points
    report["calibration"] = {
        "channel": "DDI 510 mass flow (mg/s, the display's calibrated wet grain flow) x reading duration = wet mass",
        "area": ("width of the sections on x the antenna's 5 Hz steps added up over the point (as SMS)" if points == "sms"
                 else "DDI 12001 active width x path length of the header centre (5 Hz header end points) during the "
                      "reading"),
        "crop": crop, "test_weight_lb_bu": tw, "std_moisture_pct": sm, "shrink": opts.shrink,
        "test_weight_source": "option" if opts.test_weight_lb_bu else (
            "common.CROPS table (crop chosen by the user)" if resolved and override else "monitor DDI 8 / crop record"),
        "std_moisture_source": sm_src if resolved else None,
        "monitor_std_moisture_pct": metas[0].std_moisture,
        "monitor_crop": metas[0].crop,
        "check_monitor_yield": "details[].ratio_to_monitor_wet_yield_median = decoded wet yield / monitor DDI 39",
        "validation": VALIDATION_NOTE}
    report["details"] = details
    # what SMS imports these logs with: the crop of the decoded units (common.jd_monitor_product)
    used = [groups[u][0] for u in sel if u in groups and groups[u][0].vin not in skipped]
    names = sorted({m.crop for m in used if m.crop})
    mu = next((m for m in used if m.crop), metas[0])
    file_vals = {"crop_id": _crop_id(mu.path), "crop_name": mu.crop, "variety": mu.variety,
                 "test_weight_lb_bu": round(mu.tw_kg_bu * C.LB_PER_KG, 4) if mu.tw_kg_bu else None,
                 "std_moisture_pct": mu.std_moisture}
    report["monitor_product"] = C.jd_monitor_product(
        names[0] if len(names) == 1 else None,
        "John Deere .jdl definition frame: crop record 21.1 (crop id 21.1.1, name 21.1.2)" if len(names) == 1 else
        f"John Deere .jdl: the decoded units carry {len(names)} crops ({', '.join(names)})",
        crop_id=file_vals["crop_id"] if len(names) == 1 else None, file_values=file_vals)
    report["crop_name"] = names[0] if len(names) == 1 else None
    report["decode_seconds"] = round(_time.time() - t_start, 1)
    log(f"decoded {len(df):,} readings from {n_read:,} " + ("SMS points" if points == "sms" else "flow samples")
        + f" in {report['decode_seconds']} s")
    return df, report


def decode(root: str, unit_ids=None, opts: C.DecodeOptions | None = None, log=print, points: str = "sms"):
    """Decode the selected units (None = all) -> (DataFrame in the standard schema + lon/lat, decode report).
    opts=None uses default_options() (John Deere one-sided shrink). points: "sms" = the points Ag Leader SMS makes of
    the logs (the default: the file then holds what SMS holds after its import), "ops_center" = one reading per flow
    sample, as the John Deere Operations Center export.
    points "sms" also returns Y_Offset_f (common.OPTIONAL_COLUMNS): the point sits at SMS's reference across the header
    and the centre of the sections on lies Y_Offset_f to the right of it, as in SMS's own point export of the logs;
    SMS's Optimize Swath Widths (engine.clean.sms3sigma S2) moves every reading by it before it narrows partial-width
    readings (without it, S2 removes many more readings than SMS does and the final points fall below SMS's).
    "ops_center" places the point at the centre of the sections on already, so it has no offset column."""
    df, report = decode_detailed(root, unit_ids, opts, log, points)
    cols = C.STANDARD_NAMES + ["lon", "lat"] + (["Y_Offset_f"] if points == "sms" else [])
    out = df[cols].copy()
    if "Y_Offset_f" in out:
        out["Y_Offset_f"] = pd.to_numeric(out["Y_Offset_f"], errors="coerce").fillna(0.0).astype(float)
    return out, report
