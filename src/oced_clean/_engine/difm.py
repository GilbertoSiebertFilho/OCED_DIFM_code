# -*- coding: utf-8 -*-
"""OCED: a clean file as a shapefile for DIFM (difm.farm), in the layout of an Ag Leader SMS shapefile export.

    from oced_clean._engine import difm
    W = difm.write_zip("clean_shapefiles/T_sms3s_clean.shp", source=D.get("source"), columns=R["columns"])
    W["zip"]        # clean_shapefiles/T_sms3s_clean_DIFM.zip: T_sms3s_clean_DIFM .shp / .shx / .dbf / .prj / .cpg
    A = difm.write_applied_zip("clean_shapefiles/P_3s_clean.shp")       # a planter / sprayer file
    W = difm.write_zip(clean_shp, columns=R["columns"], basis=difm.import_basis(R, imported=path))   # see below
    B = difm.read_zip(W["zip"], map_column="Yld_Level")                 # a zip written earlier, read back
    J = difm.adjustment_of(R, difm.field_names(clean_shp))              # what the cleaning adjusted, or None
    X = difm.write_zip(clean_shp, columns=R["columns"], adjustment=J, adjusted=True)
    X["zip"]        # clean_shapefiles/T_sms3s_clean_adjusted_DIFM.zip: the same layout with the adjusted yield

A HARVEST (write_zip). DIFM takes the clean yield as a zipped point shapefile laid out as SMS exports it and maps ten
features from it: Mass (Yld_Mass_D, lb/ac), Volume (Yld_Vol_Dr, bu/ac), Angle (Track_deg_), Distance (Distance_f, ft),
Elevation (Elevation_, ft), Moisture (Moisture__), Offset (Y_Offset_f, ft), Speed (Speed_mph_, mph), Time (Time) and
Width (Swth_Wdth_, ft). The clean shapefile itself (every attribute of the file and the columns OCED adds, the time
of day as text) stays as it is. This file holds the same readings, in the same order and at the same positions, with
exactly these fields in this order and these dBASE descriptors (FIELDS, WIDTHS: the names, types and widths of the
file DIFM was seen to read and map):

    Field C 80, Dataset C 80, Product C 80, Obj__Id N 18.0, Distance_f N 24.6, Track_deg_ N 24.6,
    Duration_s N 24.6, Elevation_ N 24.6, Time D 8, Swth_Wdth_ N 24.6, Y_Offset_f N 24.6, Moisture__ N 24.6,
    Yld_Mass_W N 24.6, Yld_Vol_We N 24.6, Yld_Mass_D N 24.6, Yld_Vol_Dr N 24.6, Speed_mph_ N 24.6, Date D 8

The numbers have 6 decimals (N_DEC): more than any monitor resolves (an SMS export itself has 4). With the 15
decimals of a decoded file's own numbers, digits that do not compress, the zip of a large field passed 10 MB.

Values are the clean file's (the column of each field is found by the names the cleaning reads,
engine/clean/yield_clean.py detect_columns, or the cleaning record's own columns). Text is written as it is (a point
export's C 254 in C 80; a text longer than that keeps its field's own width, said in the notes). A number is the
same decimal number with 6 decimals: its decimals padded with zeros when the clean file holds fewer (a point export's
N 15.4), rounded on its decimal text, half to even, when it holds more (a decoded file's N 24.15); a zero is written
0.000000, never -0.000000. A number the clean file holds as text, in exponent notation, or too long for 24
characters is written from its numeric value (with fewer decimals where it would not fit; said in the notes). A
cell that holds no number (a value the source did not log: the elevation of ISOXML task data whose display logs
none) is left empty and counted in the notes.
Obj__Id (N 18.0) is never rounded: an id with decimals keeps its field's own width.

A FIELD THE CLEAN FILE DOES NOT HOLD (a point export under other names: an Operations Center export has no Field,
Dataset, Product, Duration_s, Yld_Vol_We or Yld_Mass_D) is filled only with what follows exactly from the clean
file's own values and from what the import of the file knows (write_zip's `basis`: import_basis of the cleaning
record), never with an invented value:
    Yld_Mass_D = Yld_Vol_Dr x lb/bu, Yld_Vol_Dr = Yld_Mass_D / lb/bu, Yld_Mass_W = Yld_Vol_We x lb/bu and Yld_Vol_We
        = Yld_Mass_W / lb/bu, with the weight to volume of the file's import as SMS, and only when that import
        computed the file's yields with it (engine/sms_import.py: wet volume = wet mass / lb/bu, dry mass = dry
        volume x lb/bu on every reading), so that mass / volume is that lb/bu on the wet and on the dry side;
    Duration_s = the time step of the readings (time_step): the step between consecutive readings of a combine that
        at least TIME_STEP_SHARE of them share (1 s for a log of one reading per second), from the clean file's
        times of day; none when the readings were logged at irregular intervals;
    Field = the field the export's Operations Center metadata names; Product = the crop the import read from the
        file, else the SMS product it was imported as; Dataset = the combine of each reading (the clean file's
        machine column) for an Operations Center export, whose datasets SMS names by the combine;
    Y_Offset_f = 0 (the readings of a John Deere Gen4 log and of ISOXML task data carry their offset, 0 once SMS's
        Optimize Swath Widths has moved them; other sources have none) and Obj__Id = the record's number in the file.
What does not follow stays empty and the notes say so (with the reason); readings left without Yld_Mass_D or
Yld_Vol_Dr, the fields DIFM maps as the yield, are a warning (blank_yield_warning), when the zip is written and when
it is read back.

Time and Date are dBASE dates (D 8, 'YYYYMMDD'), the date of each reading (harvest_days): the clean file's own
dBASE date when its time column is one (an SMS point export: SMS's per-reading date, copied byte for byte); else the
calendar day of the reading's time of day, by one rule: a time in UTC is an instant, dated in this computer's clock
time at that instant (clock_utc_offset_h; Mountain time in summer is UTC-6), as SMS shows it, or with the decoder's
time-zone offset when one was given; any other time is taken as it is. The times in UTC are those of a source that
writes UTC (the John Deere decoders: the decode record's time basis, or Src_Fmt) and those that say their zone (ISO
8601 with 'Z' or an offset, which the cleaning's parser takes to UTC: the IsoTime of an Operations Center export;
when the cleaning's time column says no zone and another time column of the file does, the date is taken from that
one). The time of ISOXML task data is the display's own clock, which SMS shows as it is (OWN_CLOCK_FORMATS): taken
as it is, each reading on its own day, whatever the decode record's time basis ('local'; 'unknown' without a GNSS
time; 'UTC' when that clock is the GNSS UTC time). Else the clean file's Harv_Day; else the date in its dataset
name; empty when none is known (warned). The
result's 'days' and notes say which. The clock is this computer's: a log opened on a computer
set to another time zone than the field's has its readings near midnight dated by that zone, as SMS on that computer
would date them. The cleaning's own Harv_Day (engine/clean/sessions.py) takes UTC times to the solar day,
round(longitude / 15) hours, which in Alberta is UTC-8 or UTC-7, so a reading logged between local midnight and about
02:00 has the previous day there and its own day here. Point geometry in WGS 84 with the .prj text SMS writes
(ESRI_WGS84_PRJ); the .shp and .shx are the clean file's own, byte for byte, when they already hold points in WGS 84,
else written again from each reading's position (projected to WGS 84). The .cpg is the clean file's.

THE YIELD: 3-SIGMA ONLY, OR ADJUSTED. write_zip writes one of two files, and its result says which ('yield',
'label', 'line'):

  3-sigma only (the default; <clean>_DIFM.zip): DIFM's mass and volume are the monitor's dry yield as cleaned. A
  column the cleaning added and names as the one to map has no field of its own in this layout; the result's warnings
  say so (map_column_warning), when the zip is written and when it is read back (read_zip): for an adjusted yield,
  that the other file carries it; for the symmetric dry yield Yld_Dry_Sy (a moisture basis, not an adjustment), that
  it is in the clean shapefile only.

  adjusted (adjusted=True with the cleaning's adjustment, adjustment_of; <clean>_adjusted_DIFM.zip): the same layout,
  readings, positions, dates and every other field, with the yield the cleaning adjusted: the column it names as the
  one to map, on the dry-volume basis (bu/ac), which is
      Yld_Harm   = the clean dry yield x the combine factor     a combine factor applied, no grower's weight (or
                                                                the weight used to validate only);
      Yld_Level  = the clean dry yield x L                      the grower's weight used to adjust, no combine factor;
      Yld_Level  = the clean dry yield x the combine factor x L both;
      (Yld_Lvl_Sy, the symmetric dry yield leveled, when a cleaning by the cascade names it; never the SMS 3-sigma
      method). The grower's weight used to validate only and no combine factor applied: no adjusted yield, so no
      adjusted file (ValueError).
  Yld_Vol_Dr holds that column's value (the same decimal number: the cleaning holds it with 4 decimals); Yld_Mass_D,
  Yld_Mass_W and Yld_Vol_We are their monitor value (the clean file's, or the one filled from the import's weight to
  volume) x the reading's own factor (adjusted yield / the monitor's dry yield, both as the clean file holds them),
  so that DIFM reads the same yield by Mass or by Volume and the bushel weight (mass / volume) and the moisture
  (Moisture__, and wet against dry) of every reading stay the monitor's. A reading whose monitor yield is 0 or blank
  keeps its four fields as they are (0 stays 0); one that has a monitor yield and no adjusted one keeps the monitor's
  (warned).

A PLANTER / SPRAYER FILE (write_applied_zip, engine/clean/applied3s.py output). SMS's as-applied exports have no single
layout (their fields and their order follow the monitor, and the rate column's name loses its unit to the 10
characters of dBASE: the unit is chosen in DIFM's Define Features), so the DIFM file keeps the clean file's own fields
in their order, every cell byte for byte, except its dates: the time column and a field named Time or Date that holds
dates (text, as GDAL writes a dBASE date back, or a time of day) become dBASE dates (D 8), each reading's date by the
rule above (no Harv_Day or dataset date: none is given). Same points, WGS 84 and .prj as a harvest.

THE ZIP: the members .shp .shx .dbf .prj .cpg named <zip name without .zip>.<ext>, deflated, with the attributes of the
zip DIFM read (made on Windows, regular files read-write for all: ZIP_CREATE_SYSTEM, ZIP_EXTERNAL_ATTR), dated by the
clean .dbf, so that the same clean file gives the same zip byte for byte. It is written under a short temporary name in
the same folder and then put in place, so that a failure leaves the previous zip as it was. A zip over MAX_ZIP_BYTES
(10 MB) is a warning (size_warning), when it is written and when it is read back.
"""
from __future__ import annotations

import datetime as _dt
import decimal
import hashlib
import io
import os
import re
import struct
import zipfile

import numpy as np
import pandas as pd

SUFFIX = "_DIFM"
ZIP_SUFFIX = SUFFIX + ".zip"              # <clean shapefile name without .shp> + this (a harvest: 3-sigma only)
ADJUSTED_ZIP_SUFFIX = "_adjusted" + ZIP_SUFFIX          # ... + this: the yield the cleaning adjusted (a harvest)
YIELDS = ("monitor", "adjusted")                        # the yield a harvest's file carries (write_zip's 'yield')
YIELD_FIELDS = ("Yld_Mass_W", "Yld_Vol_We", "Yld_Mass_D", "Yld_Vol_Dr")     # the fields an adjustment changes
ADJUSTMENT_PARTS = {"combine": "combine correction", "weight": "grower's weight"}
LAYOUT = "SMS shapefile export"
APPLIED_LAYOUT = "the clean file's own fields, its dates as dBASE dates"
ESRI_WGS84_PRJ = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
                  'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')
FIELDS = (("Field", "C"), ("Dataset", "C"), ("Product", "C"), ("Obj__Id", "N"), ("Distance_f", "N"),
          ("Track_deg_", "N"), ("Duration_s", "N"), ("Elevation_", "N"), ("Time", "D"), ("Swth_Wdth_", "N"),
          ("Y_Offset_f", "N"), ("Moisture__", "N"), ("Yld_Mass_W", "N"), ("Yld_Vol_We", "N"), ("Yld_Mass_D", "N"),
          ("Yld_Vol_Dr", "N"), ("Speed_mph_", "N"), ("Date", "D"))
FIELD_NAMES = tuple(f for f, _ in FIELDS)
FEATURES = {"Mass": "Yld_Mass_D", "Volume": "Yld_Vol_Dr", "Angle": "Track_deg_", "Distance": "Distance_f",
            "Elevation": "Elevation_", "Moisture": "Moisture__", "Offset": "Y_Offset_f", "Speed": "Speed_mph_",
            "Time": "Time", "Width": "Swth_Wdth_"}
MEMBER_EXTS = (".shp", ".shx", ".dbf", ".prj", ".cpg")
C_WIDTH, N_WIDTH, N_DEC, ID_WIDTH = 80, 24, 6, 18       # the widths of the layout DIFM read; numbers with 6 decimals
WIDTHS = {"C": (C_WIDTH, 0), "N": (N_WIDTH, N_DEC), "D": (8, 0), "Obj__Id": (ID_WIDTH, 0)}
MAX_ZIP_BYTES = 10_000_000                              # a zip over this size is warned (size_warning)
ZIP_CREATE_SYSTEM = 0                                   # the zip DIFM read: made on Windows (DOS attributes) ...
ZIP_EXTERNAL_ATTR = 0o100666 << 16                      # ... each member a regular file, read-write for all
# the field -> the key of yield_clean.detect_columns (the names the cleaning reads)
ROLE = {"Dataset": "dataset", "Obj__Id": "order", "Distance_f": "distance_ft", "Track_deg_": "heading",
        "Duration_s": "duration_s", "Swth_Wdth_": "width_ft", "Moisture__": "moisture",
        "Yld_Mass_W": "wet_mass_lbac", "Yld_Vol_We": "yield_wet", "Yld_Vol_Dr": "yield", "Speed_mph_": "speed_mph"}
# the field -> its names in a clean file (lower case, without leading / trailing underscores)
BY_NAME = {"Field": ("field",), "Product": ("product",), "Elevation_": ("elevation", "elevation_ft"),
           "Yld_Mass_D": ("yld_mass_d", "yld_mass_dry", "drymass", "dry_mass"),
           "Y_Offset_f": ("y_offset_f", "y_offset", "yoffset", "y_offset_ft")}
DAY_COLUMN = "harv_day"
DATE_NAMES = ("time", "date")                           # as-applied: the fields that hold dates, by name
DATE_SHARE = 0.99                                       # ... when this share of their values reads as dates
UTC_FORMATS = ("jd_jdl", "jd_gs3_rcd")                  # the decoders that write UTC times
OWN_CLOCK_FORMATS = ("isoxml",)                         # the decoders whose Time is the log's own clock, as SMS shows it
TIME_NAMES = ("time", "timestamp", "datetime", "date_time", "isotime")   # the time columns the cleaning reads
OPS_CENTER = "jd_ops_center"                            # the import's format of an Operations Center export
MASS_OF = {"Yld_Mass_D": "Yld_Vol_Dr", "Yld_Mass_W": "Yld_Vol_We"}       # mass = volume x lb/bu, each side
TIME_STEP_SHARE = 0.9                                   # the time step: shared by this share of the steps ...
TIME_STEP_TOL = 0.01                                    # ... within this many seconds, or this share of the step
_DAY_RE = re.compile(r"^\s*(\d{4})-(\d{2})-(\d{2})\s*$")
_NUM_RE = re.compile(rb"^([+-]?)(\d*)(?:\.(\d*))?$")
# a time of day that says its zone: 'Z' or an offset after the time (ISO 8601)
_ZONED_RE = re.compile(r"\d{1,2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?\s*(?:Z|[+-]\d{2}(?::?\d{2})?)\s*$", re.I)
_CTX = decimal.Context(prec=400, rounding=decimal.ROUND_HALF_EVEN)       # numbers rounded on their decimal text
WHAT_IS = {"Yld_Harm": "the dry yield x the combine factor",
           "Yld_Level": "the dry yield leveled to the grower's weight",
           "Yld_Lvl_Sy": "the symmetric dry yield leveled to the grower's weight",
           "Yld_Dry_Sy": "the symmetric dry yield"}
OWN_DATE = "the reading's own date ({col}, a dBASE date of the clean file)"


def zip_path_for(clean_shp: str, adjusted: bool = False) -> str:
    """<clean shapefile without .shp>_DIFM.zip, next to it; adjusted: ..._adjusted_DIFM.zip (the file that carries
    the yield the cleaning adjusted)."""
    return os.path.splitext(clean_shp)[0] + (ADJUSTED_ZIP_SUFFIX if adjusted else ZIP_SUFFIX)


def field_names(clean_shp: str) -> list:
    """The field names of a shapefile's .dbf, from its header only (nothing else is read)."""
    with open(os.path.splitext(clean_shp)[0] + ".dbf", "rb") as fh:
        head = fh.read(32)
        if len(head) < 32:
            return []
        desc = fh.read(max(0, struct.unpack("<H", head[8:10])[0] - 32))
    names = []
    for i in range(0, len(desc) - 31, 32):
        if desc[i] == 0x0D:
            break
        names.append(desc[i:i + 11].split(b"\0")[0].decode("latin-1"))
    return names


def adjustment_of(R: dict | None, names=None) -> dict | None:
    """What a harvest cleaning adjusted the monitor's yield by, and the column of the clean file that carries it: the
    column the cleaning names the one to map (engine/run.py recommended_yield_column) when it is the dry yield x the
    combine factor (Yld_Harm: a factor other than 1 was applied) or the dry yield leveled to the grower's weight
    (Yld_Level, or Yld_Lvl_Sy in a cleaning by the cascade: the weight used to adjust, the combine factor inside when
    one was applied). None when the cleaning adjusted nothing (no combine factor applied and no grower's weight, or
    the weight used to validate only), when the column to map is another one (the symmetric dry yield Yld_Dry_Sy is a
    moisture basis, not an adjustment) and, with `names` (the clean file's columns, field_names), when the column is
    not in the file. Returns {column, monitor_column (the cleaning's dry yield), parts ('combine' and / or 'weight'),
    combine {combine: factor, every combine of the cleaning; {} when none was applied}, level (L in the file, after
    the combine factor; None without a grower's weight and with one L per combine), per_machine_level, what (the
    column as a product, with the factors)}."""
    if not isinstance(R, dict) or not R:
        return None
    from oced_clean._engine import run as er
    try:
        col = er.recommended_yield_column(R)[0]
        lev = er.leveled_column(R)
    except Exception:                                    # noqa: BLE001 - a record that cannot say: no adjustment known
        return None
    ycol = (R.get("columns") or {}).get("yield")
    if not col or col == ycol or (names is not None and col not in set(names)):
        return None
    fa = {}
    for m, f in ((R.get("machines") or {}).get("factor_applied") or {}).items():
        try:
            fa[str(m)] = float(f)
        except (TypeError, ValueError):
            pass
    on = {m: f for m, f in fa.items() if abs(f - 1.0) > 1e-12}
    L = R.get("level") if isinstance(R.get("level"), dict) else {}
    leveled = lev is not None and lev[0] == col
    if not leveled and not (on and str(col).startswith("Yld_Harm")):
        return None
    per_machine = bool(leveled and L.get("per_machine"))
    Lf = None
    if leveled and not per_machine:
        Lf = L.get("factor_final")
        if Lf is None:
            Lf = L.get("factor_harmonised", L.get("factor"))
        Lf = float(Lf) if Lf is not None else None
    combine = bool(on) and not per_machine               # per-combine tickets: each L holds its combine's factor
    parts = (["combine"] if combine else []) + (["weight"] if leveled else [])
    what = (f"{col} = " + ("the symmetric dry yield (Yld_Dry_Sy)" if str(col).startswith("Yld_Lvl_Sy") else
                           "the monitor's dry yield as cleaned"))
    if combine:
        what += (" x the combine factor (" + ", ".join(f"{m} x {f:.4f}" for m, f in on.items())
                 + ("; the others x 1" if len(on) < len(fa) else "") + ")")
    if per_machine:
        what += (" x the L of its combine (each combine leveled to its own weight: "
                 + ", ".join(f"{m} x {float(v['L']):.4f}" for m, v in sorted(L["per_machine"].items())
                             if isinstance(v, dict) and v.get("L") is not None) + ")")
    elif leveled:
        what += (f" x L {Lf:.4f}" if Lf is not None else " x L") + " (leveled to the grower's weight)"
    return {"column": col, "monitor_column": ycol, "parts": parts, "combine": fa if combine else {}, "level": Lf,
            "per_machine_level": per_machine, "what": what}


def yield_label(which: str, adjustment: dict | None = None) -> str:
    """The name of a yield choice: '3-sigma only (the monitor's yield)'; adjusted: '3-sigma + combine correction and
    grower's weight (adjusted yield)', naming only what the cleaning applied (adjustment_of's parts)."""
    if which != "adjusted":
        return "3-sigma only (the monitor's yield)"
    parts = [p for p in ((adjustment or {}).get("parts") or ADJUSTMENT_PARTS) if p in ADJUSTMENT_PARTS]
    return "3-sigma + " + " and ".join(ADJUSTMENT_PARTS[p] for p in parts) + " (adjusted yield)"


def yield_line(which: str, adjustment: dict | None = None) -> str:
    """One sentence that says which yield a harvest's file carries and what the adjustment was."""
    if which == "adjusted":
        return (yield_label(which, adjustment) + ": Yld_Vol_Dr = " + str((adjustment or {}).get("what") or "")
                + "; Yld_Mass_D, Yld_Mass_W and Yld_Vol_We x the same factor per reading, so DIFM reads the same "
                  "yield by Mass or by Volume, and the bushel weight and the moisture stay the monitor's")
    return (yield_label(which) + ": Yld_Mass_D and Yld_Vol_Dr are the monitor's dry yield as cleaned"
            + (f"; the adjusted yield of the cleaning ({adjustment['what']}) is NOT in this file"
               if adjustment else "; the cleaning adjusted no yield (no combine factor applied, no grower's weight "
                                  "used to adjust)"))


def _norm(name) -> str:
    return str(name).strip().strip("_").lower()


def _dbf_fields(dbf: dict) -> dict:
    """{field name: (offset in the record, type, width, decimals)} of a .dbf read by yield_clean._read_dbf."""
    out, off = {}, 1
    for d in dbf["fdesc"]:
        name = d[:11].split(b"\0")[0].decode("latin-1")
        typ, w, dec = chr(d[11]), d[16], d[17]
        out.setdefault(name, (off, typ, w, dec))
        off += w
    return out


def _desc(name: str, typ: str, width: int, dec: int) -> bytes:
    return name.encode("latin-1")[:10].ljust(11, b"\0") + typ.encode("ascii") + b"\0" * 4 + bytes([width, dec]) \
        + b"\0" * 14


def _cells(strings, width: int, right: bool) -> np.ndarray:
    """(n x width) bytes of fixed-width cells (numbers right-aligned, text left-aligned)."""
    enc = [s.encode("ascii")[:width] for s in strings]
    raw = b"".join(e.rjust(width) if right else e.ljust(width) for e in enc)
    return np.frombuffer(raw, np.uint8).reshape(len(enc), width) if enc else np.zeros((0, width), np.uint8)


def _plain(v: float, q: int) -> str:
    """A number with q decimals; a zero carries no sign (0.000000, never -0.000000)."""
    s = f"{v:.{q}f}" if q else f"{int(round(v))}"
    return s[1:] if s.startswith("-") and not s.strip("-0.") else s


def _number_cells(values, width: int = N_WIDTH, dec: int = N_DEC) -> np.ndarray:
    """N cells of `values` (NaN -> blank), fewer decimals where a value would not fit (as the cleaning's own patch)."""
    out = []
    for v in np.asarray(values, float):
        if not np.isfinite(v):
            out.append("")
            continue
        q = dec
        s = _plain(v, q)
        while len(s) > width and q > 0:
            q -= 1
            s = _plain(v, q)
        out.append(s if len(s) <= width else "*" * width)
    return _cells(out, width, True)


def _numbers(block: np.ndarray) -> np.ndarray:
    """The numbers of N / F cells (n x width bytes); a blank cell, or one that holds no finite number, is NaN."""
    n, w = block.shape
    if not n:
        return np.zeros(0)
    arr = np.ascontiguousarray(block).view(f"S{w}").ravel()
    uniq, inv = np.unique(arr, return_inverse=True)
    vals = np.full(len(uniq), np.nan)
    for k, u in enumerate(uniq):
        t = bytes(u).strip(b" \0")
        if t:
            try:
                v = float(t)
            except ValueError:
                continue
            if np.isfinite(v):
                vals[k] = v
    return vals[np.asarray(inv).ravel()]


def _refit(block: np.ndarray, typ: str, width: int, dec: int, to_type: str, to_width: int, to_dec: int,
           rounded: bool = False):
    """The cells of `block` (n x width bytes, dBASE type `typ`) in a field of `to_type` to_width.to_dec with the same
    content: text the same (trailing blanks aside), a number the same decimal number (its decimals padded with zeros);
    byte for byte when the widths are the same. A number with more decimals than to_dec: `rounded`, it is rounded to
    them on its decimal text (half to even: no binary number in between) and a zero carries no sign (0.000000, never
    -0.000000); else it does not fit. None when a cell would not fit exactly (or, rounded, is no plain decimal number
    or is too long)."""
    n = block.shape[0]
    if typ == to_type and width == to_width and (to_type == "C" or (dec == to_dec and not rounded)):
        return block
    if n == 0:
        return np.zeros((0, to_width), np.uint8)
    arr = np.ascontiguousarray(block).view(f"S{width}").ravel()
    uniq, inv = np.unique(arr, return_inverse=True)
    quantum = decimal.Decimal(1).scaleb(-to_dec)
    out = []
    for u in uniq:
        s = bytes(u)
        if to_type == "C":
            t = (s.strip() if typ in ("N", "F") else s).rstrip(b" \0")
            if len(t) > to_width:
                return None
            out.append(t.ljust(to_width))
            continue
        t = s.strip(b" \0")
        if not t:
            out.append(b" " * to_width)
            continue
        m = _NUM_RE.match(t)
        if not m or not (m.group(2) or m.group(3)):
            return None
        sign = b"-" if m.group(1) == b"-" else b""
        ip, fp = (m.group(2) or b"0"), (m.group(3) or b"").rstrip(b"0")
        if len(fp) > to_dec:
            if not rounded:
                return None
            digits = format(decimal.Decimal(t.decode("ascii")).copy_abs().quantize(quantum, context=_CTX), "f")
            digits = digits.encode("ascii")
        else:
            digits = ip + ((b"." + fp.ljust(to_dec, b"0")) if to_dec else b"")
        if rounded and not digits.strip(b"0."):
            sign = b""                                    # a zero carries no sign
        txt = sign + digits
        if len(txt) > to_width:
            return None
        out.append(txt.rjust(to_width))
    cells = np.frombuffer(b"".join(out), np.uint8).reshape(len(out), to_width)
    return cells[np.asarray(inv).ravel()]


def _date_cells(block: np.ndarray) -> np.ndarray:
    """'YYYYMMDD' per cell of D cells (n x 8 bytes) that hold a calendar date, else ''."""
    out = np.array([""] * block.shape[0], dtype=object)
    if not block.shape[0]:
        return out
    arr = np.ascontiguousarray(block).view("S8").ravel()
    uniq, inv = np.unique(arr, return_inverse=True)
    good = []
    for u in uniq:
        s = bytes(u).decode("latin-1")
        try:
            ok = s.isdigit() and bool(_dt.date(int(s[:4]), int(s[4:6]), int(s[6:8])))
        except ValueError:
            ok = False
        good.append(s if ok else "")
    return np.array(good, dtype=object)[np.asarray(inv).ravel()]


def _column_of(field: str, names, detected: dict, record_cols: dict | None) -> str | None:
    """The clean file's column for a DIFM field: the cleaning record's column for its key when the file has it,
    else the detected one (detect_columns), else (no key) the field's own names (BY_NAME)."""
    by_norm = {}
    for c in names:
        by_norm.setdefault(_norm(c), c)
    key = ROLE.get(field)
    if key is not None:
        rc = (record_cols or {}).get(key)
        if rc and _norm(rc) in by_norm:
            return by_norm[_norm(rc)]
        return detected.get(key)
    return next((by_norm[n] for n in BY_NAME.get(field, ()) if n in by_norm), None)


def _source_for_days(source, fmt_values) -> dict:
    """The decode record the date is taken with: the John Deere decoders write UTC, so a file whose Src_Fmt is one
    of them and whose record says nothing of its time basis is read as UTC (the cleaning's own rule). The time of
    ISOXML task data (the record's format, or Src_Fmt) is the log's own clock, which SMS shows as it is: never an
    instant to date in this computer's clock time, also when that clock happens to be the GNSS UTC time (the
    record's time basis 'UTC'), and whatever offset the decoder was given (it moved the times itself)."""
    src = dict(source) if isinstance(source, dict) else {}
    fm = pd.unique(pd.Series(fmt_values).astype(str)) if fmt_values is not None else []
    one = str(fm[0]) if len(fm) == 1 else None
    if str(src.get("format") or one or "") in OWN_CLOCK_FORMATS:
        src["time_basis"], src["tz_offset_h"] = "local", None
    elif not src.get("time_basis") and one in UTC_FORMATS:
        src["time_basis"] = "UTC"
    return src


def clock_utc_offset_h(epoch_s: float) -> float:
    """Hours from UTC to this computer's clock time at the instant epoch_s (seconds since 1970-01-01 UTC), daylight
    time included: the time SMS shows UTC logs in."""
    local = _dt.datetime.fromtimestamp(epoch_s)
    utc = _dt.datetime.fromtimestamp(epoch_s, _dt.timezone.utc).replace(tzinfo=None)
    return (local - utc).total_seconds() / 3600.0


def _clock_days(tt: pd.Series, lon_med) -> tuple[np.ndarray, list, str]:
    """('YYYY-MM-DD' or '' per UTC time in clock time, the offsets used in order of first use, a fallback note):
    the offset of each hour (offsets change on the hour); an instant the clock cannot give (OSError) takes the solar
    offset of the longitude (sessions.local_day_offset_h)."""
    from oced_clean._engine.clean import sessions as sessions_mod
    tt = pd.Series(tt).reset_index(drop=True)
    ok = tt.notna().to_numpy()
    secs = np.zeros(len(tt), np.int64)
    if ok.any():
        secs[ok] = tt[ok].astype("datetime64[s]").astype("int64").to_numpy()
    hours = secs // 3600
    off = np.zeros(len(tt))
    used, note = [], ""
    if ok.any():
        uh, first = np.unique(hours[ok], return_index=True)
        per, solar = {}, None
        for h in uh:
            try:
                per[h] = float(clock_utc_offset_h(float(h) * 3600.0))
            except (OSError, OverflowError, ValueError):
                if solar is None:
                    solar, note = sessions_mod.local_day_offset_h({"time_basis": "UTC"}, lon_med)
                per[h] = float(solar)
        off[ok] = np.array([per[h] for h in hours[ok]])
        for h in uh[np.argsort(first)]:
            if per[h] not in used:
                used.append(per[h])
    shifted = tt + pd.to_timedelta(off, unit="h")
    days = shifted.dt.strftime("%Y-%m-%d").fillna("").to_numpy(dtype=object)
    days[~ok] = ""
    return days, used, note


def _zoned(v) -> bool:
    """Whether the values of a time column say their time zone (ISO 8601: 'Z' or an offset after the time of day, as
    the IsoTime of an Operations Center export; a column of timestamps with a zone): DATE_SHARE of those given do.
    The cleaning's parser (yield_clean._parse_time) takes such times to UTC, so each is an instant."""
    v = pd.Series(v)
    if pd.api.types.is_datetime64_any_dtype(v):
        return getattr(v.dt, "tz", None) is not None
    s = v.astype(str).str.strip()
    s = s[~s.isin(["", "None", "nan", "NaT"])]
    if not len(s):
        return False
    return bool(s.str.contains(_ZONED_RE).mean() >= DATE_SHARE)


def time_step(times, groups=()) -> tuple[float | None, float]:
    """(the time step of the readings in seconds or None, the share of the steps that are it): the step between
    consecutive readings of a group (`groups`: columns that tell the combines / datasets apart; in time order) that
    at least TIME_STEP_SHARE of the steps share: the mean of the steps around the median one, to 0.01 s, a step
    counting as it within TIME_STEP_TOL seconds (or that share of the step). A log of one reading per second gives
    1.0, whatever readings the cleaning removed in between and however its milliseconds wander. None when the times
    hold no time of day, when there are fewer than two steps, or when no step is shared that widely (readings logged
    at irregular intervals): no duration follows from the times then."""
    from oced_clean._engine.clean import yield_clean as yc
    t, ok = yc._parse_time(pd.Series(times).reset_index(drop=True))
    if not ok:
        return None, 0.0
    d = pd.DataFrame({"t": pd.to_datetime(pd.Series(t).reset_index(drop=True), errors="coerce")})
    d["k"] = ""
    for g in groups:
        d["k"] = d["k"] + "|" + pd.Series(g).reset_index(drop=True).astype(str)
    d = d.dropna(subset=["t"]).sort_values(["k", "t"], kind="stable")
    step = d.groupby("k", sort=False)["t"].diff().dt.total_seconds().to_numpy(float)
    step = step[np.isfinite(step)]
    if len(step) < 2:
        return None, 0.0
    med = float(np.median(step))
    if not med > 0:
        return None, 0.0
    near = step[np.abs(step - med) <= max(TIME_STEP_TOL, 5 * TIME_STEP_TOL * med)]
    nominal = round(float(near.mean()), 2)
    if not nominal > 0:
        return None, 0.0
    share = float((np.abs(step - nominal) <= max(TIME_STEP_TOL, TIME_STEP_TOL * nominal) + 1e-9).mean())
    return (nominal if share >= TIME_STEP_SHARE else None), share


def import_basis(R: dict | None, imported: str | None = None) -> dict:
    """What the import of a cleaning's file knows, for the fields of the layout that its clean file does not hold
    (write_zip's `basis`; the module's docstring): {lb_bu, lb_bu_from, product, product_from, field, field_from,
    format, machines}, None / {} for what it does not know.
      lb_bu     the weight to volume of the file's import as SMS (the cleaning record's settings['sms_import']:
                resolved test_weight_lb_bu), only when that import computed the file's yields with it (its 'applied'
                weight_and_moisture: wet volume = wet mass / lb/bu, dry mass = dry volume x lb/bu, on every reading);
      product   the crop the import read from the file (its 'crop'), else the SMS product it was imported as;
      format    the import's source format (OPS_CENTER for an Operations Center export);
      field, machines   what the Operations Center metadata beside `imported` names (the file the import read:
                '<stem>-Deere-Metadata.json', the file the import took the crop from): the field, and {machine index
                of the export: serial number}.
    R = the cleaning record; imported = the path of the file its import read (None: no metadata is looked for)."""
    out = {"lb_bu": None, "lb_bu_from": None, "product": None, "product_from": None, "field": None,
           "field_from": None, "format": None, "machines": {}}
    S = R.get("settings") if isinstance(R, dict) else None
    IM = S.get("sms_import") if isinstance(S, dict) else None
    if not isinstance(IM, dict):
        return out
    out["format"] = IM.get("fmt")
    RS = IM.get("resolved") if isinstance(IM.get("resolved"), dict) else {}
    ap = IM.get("applied") if isinstance(IM.get("applied"), dict) else {}
    try:
        tw = float(RS.get("test_weight_lb_bu"))
    except (TypeError, ValueError):
        tw = float("nan")
    if ap.get("weight_and_moisture") and np.isfinite(tw) and tw > 0:
        out["lb_bu"] = tw
        out["lb_bu_from"] = "the weight to volume of the file's import as SMS, which computed its yields with it"
    for key, what in (("crop", "the crop the import read from the file"),
                      ("sms_product", "the SMS product the file was imported as")):
        v = str(IM.get(key) or "").strip()
        if v:
            out["product"], out["product_from"] = v, what
            break
    if imported and os.path.isfile(str(imported)):
        from oced_clean._engine.formats import detect
        try:
            meta = detect._deere_metadata(str(imported)) or {}
        except Exception:                                # noqa: BLE001 - a metadata file that cannot say: none
            meta = {}
        if meta.get("field"):
            out["field"] = str(meta["field"])
            out["field_from"] = "the field the export's Operations Center metadata names"
        out["machines"] = {str(k): str(v) for k, v in (meta.get("machines") or {}).items()}
    return out


def blank_yield_warning(n_mass: int, n_vol: int, n: int) -> str | None:
    """The warning of readings left without Yld_Mass_D or Yld_Vol_Dr (the fields DIFM maps as the yield)."""
    parts = [f"{k:,} of {n:,} readings have no {f} ({what})"
             for f, what, k in (("Yld_Mass_D", "the field DIFM maps as the yield mass", n_mass),
                                ("Yld_Vol_Dr", "the field DIFM maps as the yield volume", n_vol)) if k]
    if not parts:
        return None
    return ("; ".join(parts) + ": the clean file does not hold it and it does not follow from the file's import "
            "(no weight to volume of an import that computed the yields), so it is left empty; DIFM cannot map "
            "that yield from this file")


MAPPED_ALWAYS = (("Moisture__", "the moisture"), ("Swth_Wdth_", "the swath width"))    # mapped in DIFM for every yield file


def blank_mapped_warning(blank: dict, n: int) -> str | None:
    """The warning of readings without the moisture or the swath width: DIFM's Define Features is to have both mapped
    for a yield file (without the width DIFM falls back to the harvester width typed in the trial)."""
    parts = [f"{blank.get(f, 0):,} of {n:,} readings have no {f} ({what})" for f, what in MAPPED_ALWAYS if blank.get(f)]
    if not parts:
        return None
    return ("; ".join(parts) + ": the clean file does not hold it, so it is left empty; DIFM's Define Features is to "
            "have the moisture and the swath width of the readings mapped")


def size_warning(n_bytes) -> str | None:
    """The warning of a zip over MAX_ZIP_BYTES."""
    if n_bytes is None or n_bytes <= MAX_ZIP_BYTES:
        return None
    return (f"the zip is {n_bytes / 1e6:.1f} MB, over {MAX_ZIP_BYTES / 1e6:g} MB: DIFM may not take a file of this "
            "size in one upload")


def _blank_rows(block: np.ndarray) -> int:
    """The number of blank cells of a field (n x width bytes)."""
    return int(((block == 0x20) | (block == 0)).all(axis=1).sum()) if block.shape[0] else 0


def _encoding(K: dict) -> str:
    """The Python codec of the clean file's code page (its .cpg, else what the cleaning gives it); UTF-8 when the
    name is not one Python knows."""
    import codecs
    from oced_clean._engine.clean import yield_clean as yc
    try:
        if os.path.exists(K["base"] + ".cpg"):
            name = open(K["base"] + ".cpg", "rb").read()
        else:
            name = yc._cpg_for(K["base"], {"recs": K["dbf"]["recs"]})
        name = (name.decode("ascii", "replace") if isinstance(name, bytes) else str(name)).strip()
    except Exception:                                    # noqa: BLE001 - no code page said: UTF-8
        return "utf-8"
    for cand in (name, "cp" + name, name.upper().replace("LATIN", "latin_")):
        try:
            return codecs.lookup(cand).name
        except LookupError:
            continue
    return "utf-8"


def _text_block(texts: list) -> tuple[np.ndarray, int]:
    """(cells, width) of a text field from encoded texts (bytes per record): C_WIDTH wide, or as wide as the longest
    (at most 254) when one is longer."""
    w = max([C_WIDTH] + [min(len(t), 254) for t in texts])
    raw = b"".join(t[:w].ljust(w) for t in texts)
    return (np.frombuffer(raw, np.uint8).reshape(len(texts), w) if texts else np.zeros((0, w), np.uint8)), w


def _machine_texts(block: np.ndarray, width: int, machines: dict) -> list:
    """The combine of each reading as text (bytes), from the cells of the clean file's machine column: the cell as it
    is (a whole number as its integer), or, where it is a machine index of the export's metadata (`machines` {index:
    serial number}), that machine's serial number."""
    if not block.shape[0]:
        return []
    arr = np.ascontiguousarray(block).view(f"S{width}").ravel()
    uniq, inv = np.unique(arr, return_inverse=True)
    out = []
    for u in uniq:
        t = bytes(u).strip(b" \0")
        key = t.decode("latin-1")
        try:
            if key and float(key) == int(float(key)):
                key = str(int(float(key)))
        except (ValueError, OverflowError):
            pass
        out.append(str(machines[key]).encode("utf-8") if key in machines else key.encode("latin-1"))
    return [out[i] for i in np.asarray(inv).ravel()]


def harvest_days(df: pd.DataFrame, lon_deg, source=None, time_col: str | None = None,
                 dataset_col: str | None = None, own_days=None, own_col: str | None = None) -> tuple[np.ndarray, dict]:
    """('YYYYMMDD' or '' per reading, {basis: number of readings}): the date of each reading (the module's docstring),
    in this order: own_days (the clean file's own dBASE dates, 'YYYYMMDD' or '', from its column own_col); the
    reading's time (a time of day: its calendar day, UTC times in this computer's clock time or with the decoder's
    time-zone offset; a date only: that date); Harv_Day; the date in the dataset name. UTC times: those of a source
    that writes UTC (`source`, or Src_Fmt) and those that say their zone (_zoned)."""
    from oced_clean._engine.clean import sessions as sessions_mod
    from oced_clean._engine.clean import yield_clean as yc
    n = len(df)
    out = np.array([""] * n, dtype=object)
    basis = {}

    def fill(days, what):
        days = np.asarray(days, dtype=object)
        put = (out == "") & np.array([bool(d) for d in days], bool)
        if put.any():
            out[put] = days[put]
            basis[what] = basis.get(what, 0) + int(put.sum())

    if own_days is not None:
        fill(own_days, OWN_DATE.format(col=own_col or "Time"))
    if (out == "").any() and time_col and time_col in df:
        t, ok = yc._parse_time(df[time_col])
        if not ok:                                       # too many blank cells for the cleaning's rule: the cells
            tg = pd.Series(t).dropna()                   # that do parse still say whether these are times of day
            if len(tg):
                ok = bool(((tg - tg.dt.normalize()).dt.total_seconds() != 0).mean() >= 0.5)
        if ok:
            tt = pd.to_datetime(pd.Series(t).reset_index(drop=True), errors="coerce")
            src = _source_for_days(source, df["Src_Fmt"] if "Src_Fmt" in df else None)
            # one rule for every time in UTC: the decoders that write UTC, and a time that says its zone (the
            # cleaning's parser has taken it to UTC): an instant, dated in this computer's clock time
            zoned = _zoned(df[time_col])
            if not zoned and str(src.get("time_basis") or "").upper() != "UTC":
                fill(tt.dt.strftime("%Y%m%d").fillna("").to_numpy(dtype=object),
                     f"the day of the reading's time ({time_col})")
            elif not zoned and src.get("tz_offset_h") is not None:
                tz = float(src["tz_offset_h"])
                fill((tt + pd.Timedelta(hours=tz)).dt.strftime("%Y%m%d").fillna("").to_numpy(dtype=object),
                     f"the day of the reading's time ({time_col}, UTC {tz:+g} h: the decoder's time-zone offset)")
            else:
                lon = np.asarray(lon_deg, float)
                lon = lon[np.isfinite(lon)]
                days, used, note = _clock_days(tt, float(np.median(lon)) if len(lon) else None)
                fill([d.replace("-", "") for d in days],
                     f"the day of the reading's time ({time_col}, UTC in this computer's clock time: UTC "
                     + ", ".join(f"{o:+g} h" for o in used) + (f"; {note}" if note else "") + ")")
        else:
            d = sessions_mod._day_from_column(df[time_col])
            fill([str(x).replace("-", "") for x in d], f"the date of the reading ({time_col})")
    hd = next((c for c in df.columns if _norm(c) == DAY_COLUMN), None)
    if (out == "").any() and hd is not None:
        fill([("".join(m.groups()) if m else "") for m in (_DAY_RE.match(str(v or "")) for v in df[hd])],
             "Harv_Day")
    if (out == "").any() and dataset_col and dataset_col in df:
        cache = {}
        days = []
        for v in df[dataset_col].astype(str):
            if v not in cache:
                p = sessions_mod.parse_date_in_name(v)
                cache[v] = p.replace("-", "") if p else ""
            days.append(cache[v])
        fill(days, f"the date in the dataset name ({dataset_col})")
    for i in np.nonzero(out != "")[0]:                  # a day that is no calendar date: left empty
        s = out[i]
        try:
            if not (len(s) == 8 and s.isdigit()):
                raise ValueError(s)
            _dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            out[i] = ""
    return out, basis


def _crs_of(prj_path: str):
    """(pyproj CRS or None, is WGS 84 geographic)."""
    from oced_clean._engine.clean import yield_clean as yc
    if not os.path.exists(prj_path):
        return None, False
    try:
        from pyproj import CRS
        crs = CRS.from_user_input(open(prj_path, encoding="utf-8", errors="replace").read())
    except Exception:                                    # noqa: BLE001 - an unreadable .prj: not WGS 84
        return None, False
    return crs, yc._is_wgs84(crs)


def _point_files(xy: np.ndarray) -> tuple[bytes, bytes]:
    """(.shp, .shx) of Point records (type 1); a reading without a position is a null shape."""
    recs = []
    for x, y in xy:
        recs.append(struct.pack("<i2d", 1, float(x), float(y)) if np.isfinite(x) and np.isfinite(y)
                    else struct.pack("<i", 0))
    fin = np.isfinite(xy).all(axis=1) if len(xy) else np.zeros(0, bool)
    box = ((float(xy[fin, 0].min()), float(xy[fin, 1].min()), float(xy[fin, 0].max()), float(xy[fin, 1].max()))
           if fin.any() else (0.0, 0.0, 0.0, 0.0))

    def header(total):
        return (struct.pack(">7i", 9994, 0, 0, 0, 0, 0, total // 2) + struct.pack("<2i", 1000, 1)
                + struct.pack("<4d", *box) + struct.pack("<4d", 0.0, 0.0, 0.0, 0.0))
    shp_len = 100 + sum(8 + len(c) for c in recs)
    main, index, off = io.BytesIO(), io.BytesIO(), 100
    main.write(header(shp_len))
    index.write(header(100 + 8 * len(recs)))
    for k, c in enumerate(recs):
        main.write(struct.pack(">2i", k + 1, len(c) // 2) + c)
        index.write(struct.pack(">2i", off // 2, len(c) // 2))
        off += 8 + len(c)
    return main.getvalue(), index.getvalue()


def _read_clean(clean_shp: str) -> dict:
    """The clean shapefile's parts: {base, dbf (yield_clean._read_dbf), fdef, names, n, shp, shx, stype}.
    ValueError when it cannot be read as one point shapefile."""
    from oced_clean._engine.clean import yield_clean as yc
    base = os.path.splitext(clean_shp)[0]
    try:
        dbf = yc._read_dbf(base + ".dbf")
        shp_bytes = open(base + ".shp", "rb").read()
        shx_bytes = open(base + ".shx", "rb").read()
    except FileNotFoundError as e:
        raise ValueError(f"the clean shapefile is not complete ({os.path.basename(e.filename or '')} is missing)") \
            from None
    except (struct.error, ValueError, IndexError) as e:
        raise ValueError(f"the clean shapefile's .dbf cannot be read ({e})") from None
    n = int(dbf["n"])
    n_shx = (len(shx_bytes) - 100) // 8
    if n_shx != n:
        raise ValueError(f"the clean shapefile holds {n_shx:,} points and {n:,} attribute rows")
    if n and (dbf["recs"][:, 0] == 0x2A).any():
        raise ValueError("the clean shapefile's .dbf holds deleted records")
    stype = struct.unpack("<i", shp_bytes[32:36])[0] if len(shp_bytes) >= 36 else 0
    if stype not in (1, 11, 21) and n:
        raise ValueError(f"the clean shapefile holds shapes of type {stype}, not points")
    fdef = _dbf_fields(dbf)
    return {"base": base, "dbf": dbf, "fdef": fdef, "names": list(fdef), "n": n, "shp": shp_bytes,
            "shx": shx_bytes, "stype": stype}


def _positions(K: dict, notes: list) -> tuple[bytes, bytes, str, np.ndarray]:
    """(.shp, .shx, what the geometry is, lon / lat per reading) in WGS 84: the clean file's own .shp / .shx when they
    are points in WGS 84, else written again from each reading's position."""
    from oced_clean._engine.clean import yield_clean as yc
    n, stype = K["n"], K["stype"]
    crs, wgs = _crs_of(K["base"] + ".prj")
    contents = yc._read_shp(K["base"] + ".shp")["contents"] if n else []
    xy = np.array([struct.unpack("<2d", c[4:20]) if len(c) >= 20 and struct.unpack("<i", c[:4])[0] in (1, 11, 21)
                   else (np.nan, np.nan) for c in contents], float).reshape(-1, 2)
    if crs is None and n:
        fin = np.isfinite(xy).all(axis=1)
        if fin.any() and (np.abs(xy[fin, 0]) <= 180).all() and (np.abs(xy[fin, 1]) <= 90).all():
            wgs = True
            notes.append("the clean file has no readable .prj: its coordinates are degrees, read as WGS 84")
        else:
            raise ValueError("the clean shapefile has no readable .prj and its coordinates are not degrees")
    if not wgs and n:
        from pyproj import CRS, Transformer
        tr = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
        x2, y2 = tr.transform(xy[:, 0], xy[:, 1])
        xy = np.c_[np.asarray(x2, float), np.asarray(y2, float)]
        notes.append(f"the clean file is in {crs.name}: the positions are projected to WGS 84")
    if stype == 1 and wgs:
        return K["shp"], K["shx"], "the clean file's .shp and .shx, byte for byte", xy
    shp_out, shx_out = _point_files(xy)
    return shp_out, shx_out, ("points written again from the clean file's positions"
                              + (" (projected to WGS 84)" if not wgs else f" (shape type {stype} -> Point)")), xy


def _dbf_bytes(K: dict, descs: list, blocks: list) -> bytes:
    """A dBASE III file: the clean file's date of last update and language driver, `descs`, the records of
    `blocks` (the deletion flags of the clean file first)."""
    n = K["n"]
    body = np.hstack([K["dbf"]["recs"][:, 0:1]] + blocks) if n else \
        np.zeros((0, 1 + sum(b.shape[1] for b in blocks)), np.uint8)
    rlen, hlen = int(body.shape[1]), 32 + 32 * len(descs) + 1
    head = bytearray(32)
    head[0] = 0x03
    head[1:4] = bytes(K["dbf"]["head"][1:4])
    struct.pack_into("<IHH", head, 4, n, hlen, rlen)
    head[29] = K["dbf"]["head"][29]
    return bytes(head) + b"".join(descs) + b"\x0D" + body.tobytes() + b"\x1A"


def _put_zip(K: dict, zip_path: str, shp_out: bytes, shx_out: bytes, dbf_out: bytes) -> list:
    """Writes the zip (the module's docstring): its member names."""
    from oced_clean._engine.clean import yield_clean as yc
    base = K["base"]
    if os.path.exists(base + ".cpg"):
        cpg = open(base + ".cpg", "rb").read()
    else:
        cpg = yc._cpg_for(base, {"recs": K["dbf"]["recs"]})
    stem = os.path.splitext(os.path.basename(zip_path))[0]
    members = [(stem + ".shp", shp_out), (stem + ".shx", shx_out), (stem + ".dbf", dbf_out),
               (stem + ".prj", ESRI_WGS84_PRJ.encode("ascii")), (stem + ".cpg", cpg)]
    try:
        stamp = _dt.datetime.fromtimestamp(os.path.getmtime(base + ".dbf")).timetuple()[:6]
    except (OSError, ValueError, OverflowError):
        stamp = (1980, 1, 1, 0, 0, 0)
    stamp = max(tuple(stamp), (1980, 1, 1, 0, 0, 0))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members:
            zi = zipfile.ZipInfo(name, date_time=stamp)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = ZIP_CREATE_SYSTEM
            zi.external_attr = ZIP_EXTERNAL_ATTR
            z.writestr(zi, data)
    folder = os.path.dirname(os.path.abspath(zip_path))
    os.makedirs(folder, exist_ok=True)
    tmp = os.path.join(folder, "~" + hashlib.md5(os.path.basename(zip_path).encode("utf-8")).hexdigest()[:8]
                       + ".tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(buf.getvalue())
        os.replace(tmp, zip_path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return [m for m, _ in members]


def no_day_warning(n_no_day: int, n: int, applied: bool = False) -> str | None:
    """The warning of readings left without a date."""
    if not n_no_day:
        return None
    if applied:
        return (f"{n_no_day:,} of {n:,} readings have no date (their date fields left empty): the clean file gives "
                "none for them")
    return (f"{n_no_day:,} of {n:,} readings have no harvest day (Time and Date left empty): the clean file has no "
            "date, no time, no Harv_Day and no date in the dataset name for them")


def map_column_warning(map_column: str | None, mapped=(), adjustment: dict | None = None) -> str | None:
    """The warning when the column the cleaning names the one to map is not among the clean file's columns that this
    file carries (`mapped`): DIFM's Mass and Volume are then the monitor's dry yield as cleaned. adjustment
    (adjustment_of): that column is the cleaning's adjusted yield, which the adjusted file carries (this one is the
    3-sigma only file); else it has no field in this layout."""
    if not map_column or _norm(map_column) in {_norm(c) for c in mapped if c}:
        return None
    if adjustment and _norm(adjustment.get("column")) == _norm(map_column):
        return ("3-sigma only: DIFM's Mass and Volume are the monitor's dry yield as cleaned (Yld_Mass_D, "
                f"Yld_Vol_Dr), without the adjustment of the cleaning ({adjustment.get('what') or map_column}); the "
                f"file that carries the adjusted yield is the other choice (its name ends in {ADJUSTED_ZIP_SUFFIX})")
    return (f"the cleaning names {map_column} the column to map"
            + (f" ({WHAT_IS[map_column]})" if map_column in WHAT_IS else "")
            + ": it has no field in this layout, so DIFM's Mass and Volume are the monitor's dry yield as cleaned "
              "(Yld_Mass_D, Yld_Vol_Dr); " + map_column + " is in the clean shapefile")


def write_zip(clean_shp: str, zip_path: str | None = None, *, source: dict | None = None,
              columns: dict | None = None, map_column: str | None = None, adjustment: dict | None = None,
              adjusted: bool = False, basis: dict | None = None) -> dict:
    """Writes the DIFM shapefile zip of a clean harvest shapefile (the module's docstring): zip_path, default
    zip_path_for(clean_shp, adjusted). source = the file's decode record (its time basis for the date); columns = the
    cleaning record's columns (R['columns']); map_column = the column the cleaning says to map (a warning when this
    file does not carry it); adjustment = what the cleaning adjusted (adjustment_of; None: nothing); adjusted: write
    the file with the adjusted yield (the module's docstring) instead of the 3-sigma only one; basis = what the
    file's import knows (import_basis), for the fields the clean file does not hold (None: they stay empty). Returns
    {zip, members, n, fields [(name, type, width, decimals)], columns {field: clean column or None}, derived {field:
    how it was filled from the clean file and the import}, days {basis: readings}, n_no_day, n_blank_yield
    {Yld_Mass_D, Yld_Vol_Dr: readings left without it}, zip_bytes, geometry, layout, yield ('monitor' | 'adjusted'),
    label, line (which yield, and the adjustment), adjustment, adjusted_column, n_adjusted, factor {min, max, mean of
    the per-reading factor}, notes, warnings}. ValueError when the clean file cannot be read as one point shapefile
    and, adjusted, when the cleaning adjusted nothing or the clean file does not hold the adjusted column or a dry
    yield to adjust from."""
    from oced_clean._engine.clean import yield_clean as yc
    K = _read_clean(clean_shp)
    if adjusted and not (adjustment or {}).get("column"):
        raise ValueError("the cleaning adjusted no yield (no combine factor applied, no grower's weight used to "
                         "adjust): there is no adjusted shapefile for DIFM")
    zip_path = zip_path or zip_path_for(clean_shp, adjusted)
    n, fdef, names, recs = K["n"], K["fdef"], K["names"], K["dbf"]["recs"]
    detected = yc.detect_columns(names)
    B = basis if isinstance(basis, dict) else {}
    notes, warnings = [], []

    def note(text):
        if text not in notes:
            notes.append(text)

    shp_out, shx_out, geometry, xy = _positions(K, notes)
    # ---- the fields
    used = {f: _column_of(f, names, detected, columns) for f in FIELD_NAMES if f not in ("Time", "Date")}
    for f, typ in FIELDS:
        if typ == "N" and used.get(f) is not None and fdef[used[f]][1] not in ("N", "F", "C"):
            note(f"{f}: the clean file's {used[f]} is a {fdef[used[f]][1]} field, not a number: left out")
            used[f] = None
    adj_col = None
    if adjusted:
        adj_col = adjustment["column"] if adjustment["column"] in fdef else None
        if adj_col is None:
            raise ValueError(f"the clean shapefile has no column {adjustment['column']} (the yield the cleaning "
                             "adjusted): the adjusted shapefile for DIFM cannot be written")
        if used.get("Yld_Vol_Dr") is None or fdef[adj_col][1] not in ("N", "F", "C"):
            raise ValueError("the clean shapefile has no dry yield to adjust from (or it, or "
                             f"{adj_col}, is not a number): the adjusted shapefile for DIFM cannot be written")
    time_col = (columns or {}).get("time") if (columns or {}).get("time") in fdef else detected.get("time")
    mach_col = (columns or {}).get("machine") if (columns or {}).get("machine") in fdef else detected.get("machine")
    mach_col = mach_col if mach_col in fdef else None
    # a time column that can say its zone (text), besides the cleaning's own
    zone_cols = [c for c in names if _norm(c) in TIME_NAMES and c != time_col and fdef[c][1] == "C"]
    need_step = used.get("Duration_s") is None
    read_cols = [c for c in dict.fromkeys([time_col, used.get("Dataset"), "Src_Fmt"] + zone_cols
                                          + ([mach_col] if need_step else [])
                                          + [c for c in names if _norm(c) == DAY_COLUMN]) if c and c in fdef]
    num_cols = [c for c in [used[f] for f, t in FIELDS if t == "N" and used.get(f)] + ([adj_col] if adj_col else [])
                if fdef[c][1] == "C"]
    frame = pd.DataFrame(index=range(n))
    if n and (read_cols or num_cols):
        import pyogrio
        frame = pyogrio.read_dataframe(K["base"] + ".shp", columns=list(dict.fromkeys(read_cols + num_cols)),
                                       read_geometry=False)
        if len(frame) != n:
            raise ValueError(f"the clean shapefile reads as {len(frame):,} records, its .dbf holds {n:,}")
    own = None
    if time_col and fdef[time_col][1] == "D" and fdef[time_col][2] == 8:
        off = fdef[time_col][0]
        own = _date_cells(recs[:, off:off + 8])
    # the time the date is taken from: the cleaning's time column, or, when that one says no zone and another time
    # column of the file does (an Operations Center export: Time beside IsoTime), the one that says it
    day_col = time_col
    if n and not (time_col in frame and _zoned(frame[time_col])):
        day_col = next((c for c in zone_cols if c in frame and _zoned(frame[c])), time_col)
    days, day_basis = harvest_days(frame, xy[:, 0] if n else [], source, day_col, used.get("Dataset"),
                                   own_days=own, own_col=time_col)
    n_no_day = int((days == "").sum())
    day_cells = _cells([str(d) for d in days], 8, False)

    def numbers(col):
        """The values of a number column of the clean file (a number held as text: read as numbers)."""
        off, typ_, w_, _ = fdef[col]
        if typ_ in ("N", "F"):
            return _numbers(recs[:, off:off + w_])
        return pd.to_numeric(frame[col], errors="coerce").to_numpy(float) if col in frame else np.full(n, np.nan)

    def number_block(f, col):
        """A number column of the clean file as cells of the layout: the same decimal numbers with N_DEC decimals."""
        off, typ_, w_, dec_ = fdef[col]
        if typ_ in ("N", "F"):
            cells = recs[:, off:off + w_]
            # a cell that holds no number (a value the source did not log, e.g. ISOXML task data without an
            # elevation: GDAL writes it as asterisks) is left empty; the others keep their decimal text
            none = ~np.isfinite(numbers(col)) & ~((cells == 0x20) | (cells == 0)).all(axis=1)
            if none.any():
                cells = np.array(cells, np.uint8)
                cells[none] = 0x20
                note(f"{f}: {int(none.sum()):,} of {n:,} readings hold no number in the clean file's {col} (a "
                     "value the source did not log): left empty")
            block = _refit(cells, typ_, w_, dec_, "N", N_WIDTH, N_DEC, rounded=True)
            if block is not None:
                return block
            note(f"{f}: the clean file's {col} holds values that are no plain decimal numbers, or too long for N "
                 f"{N_WIDTH}.{N_DEC}: written from their numeric value (fewer decimals where one would not fit)")
        else:
            note(f"{f}: the clean file's {col} is a text field, written as numbers (N {N_WIDTH}.{N_DEC})")
        return _number_cells(numbers(col), N_WIDTH, N_DEC)

    ok = factor = dry = adj_v = None
    if adjusted:
        # the reading's own factor: the adjusted yield / the monitor's dry yield, both as the clean file holds them
        dry, adj_v = numbers(used["Yld_Vol_Dr"]), numbers(adj_col)
        ok = np.isfinite(dry) & np.isfinite(adj_v) & (dry != 0)
        factor = np.ones(n)
        factor[ok] = adj_v[ok] / dry[ok]

    # ---- what the clean file does not hold, where it follows exactly from its own values and the file's import
    derived, how, why_not = {}, {}, {}
    try:
        tw = float(B.get("lb_bu"))
        tw = tw if (np.isfinite(tw) and tw > 0) else None
    except (TypeError, ValueError):
        tw = None
    for mass, vol in MASS_OF.items():
        for f, other, op in ((mass, vol, "x"), (vol, mass, "/")):
            if used.get(f) is not None:
                continue
            if used.get(other) is None:
                why_not[f] = f"the clean file holds neither it nor {other}"
            elif tw is None:
                why_not[f] = "no weight to volume of an import that computed the file's yields"
            else:
                v = numbers(used[other])
                derived[f] = v * tw if op == "x" else v / tw
                how[f] = (f"{other} ({used[other]}) {op} {tw:g} lb/bu, " + str(B.get("lb_bu_from") or "the weight to "
                                                                                   "volume of the file's import"))
    if need_step:
        t_col = next((c for c in (time_col, day_col) if c and c in frame), None)
        step, share = (time_step(frame[t_col], [frame[c] for c in (mach_col, used.get("Dataset"))
                                                 if c and c in frame]) if (t_col and n) else (None, 0.0))
        if step is not None:
            derived["Duration_s"] = np.full(n, step)
            how["Duration_s"] = (f"{step:g} s, the time step of the readings ({t_col}: {100 * share:.1f} % of the "
                                 "steps between consecutive readings of a combine)")
        else:
            why_not["Duration_s"] = ("no duration column, and its times of day give no step that "
                                     f"{100 * TIME_STEP_SHARE:g} % of the readings share")
    texts, enc = {}, _encoding(K)
    for f, key in (("Field", "field"), ("Product", "product")):
        if used.get(f) is None and str(B.get(key) or "").strip():
            texts[f] = [str(B[key]).strip().encode(enc, "replace")] * n
            how[f] = f"{str(B[key]).strip()}, " + str(B.get(key + "_from") or "from the file's import")
        elif used.get(f) is None:
            why_not[f] = "the file's import names none"
    if used.get("Dataset") is None:
        if B.get("format") == OPS_CENTER and mach_col is not None:
            off, _, w_, _ = fdef[mach_col]
            texts["Dataset"] = _machine_texts(recs[:, off:off + w_], w_, B.get("machines") or {})
            how["Dataset"] = (f"the combine of each reading ({mach_col}): SMS names the datasets of an Operations "
                              "Center export by the combine")
        else:
            why_not["Dataset"] = "the clean file names no dataset"

    descs, blocks, out_fields, missing, own_width = [], [], [], [], []
    for f, typ in FIELDS:
        # adjusted: Yld_Vol_Dr is the adjusted column's own cells (the same decimal numbers)
        col = adj_col if (adjusted and f == "Yld_Vol_Dr") else used.get(f)
        w, dec = WIDTHS.get(f) or WIDTHS[typ]
        if typ == "D":
            block = day_cells
        elif col is not None and (typ == "C" or (f == "Obj__Id" and fdef[col][1] in ("N", "F"))):
            off, src_type, sw, sdec = fdef[col]           # text, and the record's id: never rounded
            block = _refit(recs[:, off:off + sw], src_type, sw, sdec, typ, w, dec)
            if block is None:                             # a value that would not fit exactly: the field's own width
                block, w, dec = recs[:, off:off + sw], sw, (sdec if typ == "N" else 0)
                own_width.append(f"{f} ({typ} {w}" + (f".{dec}" if typ == "N" else "") + ")")
        elif col is not None:
            w, dec, block = N_WIDTH, N_DEC, number_block(f, col)
        elif f in derived:
            block = _number_cells(derived[f], w, dec)
        elif f in texts:
            block, w = _text_block(texts[f])
            if w != C_WIDTH:
                own_width.append(f"{f} (C {w})")
        elif f == "Y_Offset_f":
            block = _number_cells(np.zeros(n), w, dec)
        elif f == "Obj__Id":
            block = _number_cells(np.arange(1, n + 1), ID_WIDTH, 0)
            missing.append(f)
        else:
            block = _cells([""] * n, w, typ == "N")
            missing.append(f)
        if adjusted and f in YIELD_FIELDS and n and (col is not None or f in derived):
            block = np.array(block, np.uint8).reshape(n, w)       # a copy: the clean file's records stay as read
            if f == "Yld_Vol_Dr":         # no factor for the reading (a monitor yield of 0 or blank, or no adjusted
                rows = ~ok                # yield): the monitor's own value, so 0 stays 0
                if rows.any():
                    block[rows] = number_block(f, used[f])[rows]
            else:                         # the monitor's value x the reading's factor (adjusted / dry yield)
                v = derived[f] if col is None else numbers(col)
                rows = ok & np.isfinite(v)
                if rows.any():
                    block[rows] = _number_cells(v[rows] * adj_v[rows] / dry[rows], w, dec)
        descs.append(_desc(f, typ, w, dec))
        blocks.append(np.asarray(block, np.uint8).reshape(n, w))
        out_fields.append((f, typ, int(w), int(dec)))
    if own_width:
        notes.append("a value of the clean file does not fit the width of the layout DIFM read exactly: these fields "
                     "keep their own width: " + ", ".join(own_width))
    if how:
        notes.append("not in the clean file, filled with what follows exactly from its own values and the file's "
                     "import: " + "; ".join(f"{f} = {how[f]}" for f in FIELD_NAMES if f in how))
    if "Obj__Id" in missing:
        notes.append("Obj__Id = the record's number in the file (the clean file has none)")
    if [f for f in missing if f != "Obj__Id"]:
        notes.append("not in the clean file, left empty: " + ", ".join(
            f + (f" ({why_not[f]})" if f in why_not else "") for f in missing if f != "Obj__Id"))
    if used.get("Y_Offset_f") is None:
        notes.append("Y_Offset_f = 0: the clean file has no lateral offset")
    if day_basis:
        notes.append("Time and Date: " + "; ".join(f"{k} ({v:,} readings)" for k, v in day_basis.items()))
    which = "adjusted" if adjusted else "monitor"
    fstat, n_adj = None, 0
    if adjusted:
        n_adj = int(ok.sum())
        mon = np.isfinite(dry) & (dry != 0)
        n_zero, n_lost = int((~mon).sum()), int((mon & ~ok).sum())
        if n_adj:
            fstat = {"min": float(factor[ok].min()), "max": float(factor[ok].max()), "mean": float(factor[ok].mean())}
        others = [f for f in YIELD_FIELDS if f != "Yld_Vol_Dr" and (used.get(f) is not None or f in derived)]
        notes.append(f"adjusted yield: Yld_Vol_Dr = {adj_col} on {n_adj:,} of {n:,} readings"
                     + (f"; {', '.join(others)} = their monitor value x {adj_col} / {used['Yld_Vol_Dr']} of the "
                        f"reading (x {fstat['min']:.4f} to x {fstat['max']:.4f})" if (others and fstat) else "")
                     + (f"; {n_zero:,} readings whose monitor yield is 0 or blank keep their values" if n_zero else ""))
        if n_lost:
            warnings.append(f"{n_lost:,} of {n:,} readings have a monitor yield and no adjusted one ({adj_col} is "
                            "blank in the clean file): they keep the monitor's yield")
    at = {f: i for i, (f, _) in enumerate(FIELDS)}
    blank = {f: _blank_rows(blocks[at[f]]) for f in ("Yld_Mass_D", "Yld_Vol_Dr")}
    blank_m = {f: _blank_rows(blocks[at[f]]) for f, _ in MAPPED_ALWAYS}
    for w_ in (no_day_warning(n_no_day, n),
               map_column_warning(map_column, list(used.values()) + [adj_col], adjustment),
               blank_yield_warning(blank["Yld_Mass_D"], blank["Yld_Vol_Dr"], n),
               blank_mapped_warning(blank_m, n)):
        if w_:
            warnings.append(w_)
    members = _put_zip(K, zip_path, shp_out, shx_out, _dbf_bytes(K, descs, blocks))
    size = os.path.getsize(zip_path)
    if size_warning(size):
        warnings.append(size_warning(size))
    return {"zip": zip_path, "members": members, "n": n, "fields": out_fields,
            "columns": {f: (adj_col if (adjusted and f == "Yld_Vol_Dr") else used.get(f))
                        for f in FIELD_NAMES if f not in ("Time", "Date")}, "derived": how, "days": day_basis,
            "n_no_day": n_no_day, "n_blank_yield": blank, "zip_bytes": int(size), "geometry": geometry,
            "layout": LAYOUT, "yield": which,
            "label": yield_label(which, adjustment), "line": yield_line(which, adjustment),
            "adjustment": dict(adjustment) if adjustment else None, "adjusted_column": adj_col,
            "n_adjusted": n_adj, "factor": fstat, "notes": notes, "warnings": warnings}


def write_applied_zip(clean_shp: str, zip_path: str | None = None, *, source: dict | None = None) -> dict:
    """Writes the DIFM shapefile zip of a clean planter / sprayer shapefile (engine/clean/applied3s.py; the module's
    docstring): the clean file's own fields, its dates as dBASE dates. Returns {zip, members, n, fields, dates {field:
    basis}, n_no_day, zip_bytes, geometry, layout, notes, warnings}. ValueError when the clean file cannot be read as
    one point shapefile."""
    from oced_clean._engine.clean import yield_clean as yc
    K = _read_clean(clean_shp)
    zip_path = zip_path or zip_path_for(clean_shp)
    n, fdef, names, recs = K["n"], K["fdef"], K["names"], K["dbf"]["recs"]
    notes, warnings = [], []
    shp_out, shx_out, geometry, xy = _positions(K, notes)
    tcol = yc.detect_columns(names).get("time")
    cands = [c for c in names if (c == tcol or _norm(c) in DATE_NAMES) and fdef[c][1] in ("C", "D")]
    frame = pd.DataFrame(index=range(n))
    text_cols = [c for c in cands if fdef[c][1] == "C"] + (["Src_Fmt"] if "Src_Fmt" in fdef else [])
    if n and text_cols:
        import pyogrio
        frame = pyogrio.read_dataframe(K["base"] + ".shp", columns=text_cols, read_geometry=False)
    dates, day_of = {}, {}
    for c in cands:
        off, typ, w, _ = fdef[c]
        if typ == "D":
            day_of[c] = _date_cells(recs[:, off:off + w]) if w == 8 else np.array([""] * n, dtype=object)
            dates[c] = OWN_DATE.format(col=c)
            continue
        sub = frame[[c] + (["Src_Fmt"] if "Src_Fmt" in frame else [])]
        days, basis = harvest_days(sub, xy[:, 0] if n else [], source, c)
        filled = int((days != "").sum())
        given = int(sum(1 for v in frame[c] if str(v if v is not None else "").strip()
                        not in ("", "None", "nan", "NaT")))
        if given and filled >= DATE_SHARE * given:
            day_of[c] = days
            dates[c] = "; ".join(basis)
        else:
            notes.append(f"{c}: its values are not dates; kept as they are")
    descs, blocks, out_fields = [], [], []
    for c in names:
        off, typ, w, dec = fdef[c]
        if c in day_of:
            typ, w, dec, block = "D", 8, 0, _cells([str(d) for d in day_of[c]], 8, False)
        else:
            block = recs[:, off:off + w]
        descs.append(_desc(c, typ, w, dec))
        blocks.append(np.asarray(block, np.uint8).reshape(n, w))
        out_fields.append((c, typ, int(w), int(dec)))
    n_no_day = int(np.all([day_of[c] == "" for c in day_of], axis=0).sum()) if (day_of and n) else 0
    w_ = no_day_warning(n_no_day, n, applied=True)
    if w_:
        warnings.append(w_)
    if not day_of:
        notes.append("the clean file has no date field: none is added")
    members = _put_zip(K, zip_path, shp_out, shx_out, _dbf_bytes(K, descs, blocks))
    size = os.path.getsize(zip_path)
    if size_warning(size):
        warnings.append(size_warning(size))
    return {"zip": zip_path, "members": members, "n": n, "fields": out_fields, "dates": dates,
            "n_no_day": n_no_day, "zip_bytes": int(size), "geometry": geometry, "layout": APPLIED_LAYOUT,
            "notes": notes, "warnings": warnings}


def read_zip(zip_path: str, *, applied: bool = False, map_column: str | None = None,
             columns: dict | None = None, adjustment: dict | None = None, adjusted: bool = False) -> dict:
    """What write_zip (applied: write_applied_zip) returned for a zip written earlier, read back from it without
    writing anything (a work opened again): {zip, members, n, fields, n_no_day, zip_bytes, layout, notes: [],
    warnings} with the warnings the writer gives (readings without a date; the column to map that this file does not
    carry: map_column, checked against the layout's fields and the cleaning record's `columns`; a harvest's readings
    without Yld_Mass_D or Yld_Vol_Dr, n_blank_yield; a zip over MAX_ZIP_BYTES). A harvest's also says which yield it
    carries (yield, label, line, adjustment, adjusted_column): adjustment = what the cleaning adjusted (adjustment_of)
    and adjusted = the zip is the one written with the adjusted yield (its name: zip_path_for(clean, True)), which
    carries the cleaning's column to map. ValueError when it is not such a zip, and for a harvest's zip written by an
    earlier version of OCED (numbers with more than N_DEC decimals: the app then writes it again)."""
    try:
        with zipfile.ZipFile(zip_path) as z:
            members = [i.filename for i in z.infolist()]
            dbfs = [m for m in members if m.lower().endswith(".dbf")]
            if len(dbfs) != 1 or not any(m.lower().endswith(".shp") for m in members):
                raise ValueError(f"{os.path.basename(zip_path)} holds no single shapefile")
            b = z.read(dbfs[0])
    except (OSError, zipfile.BadZipFile, KeyError) as e:
        raise ValueError(f"{os.path.basename(zip_path)} cannot be read ({e})") from None
    try:
        n, hlen, rlen = struct.unpack("<IHH", b[4:12])
        fields, i = [], 32
        while b[i] != 0x0D:
            d = b[i:i + 32]
            fields.append((d[:11].split(b"\0")[0].decode("latin-1"), chr(d[11]), d[16], d[17]))
            i += 32
        recs = np.frombuffer(b, np.uint8, count=n * rlen, offset=hlen).reshape(n, rlen)
    except (struct.error, IndexError, ValueError) as e:
        raise ValueError(f"the .dbf of {os.path.basename(zip_path)} cannot be read ({e})") from None
    if not applied and [(f, t) for f, t, _, _ in fields] != list(FIELDS):
        raise ValueError(f"{os.path.basename(zip_path)} is not in the layout of an SMS shapefile export")
    if not applied and any(t == "N" and f != "Obj__Id" and d > N_DEC for f, t, _, d in fields):
        raise ValueError(f"{os.path.basename(zip_path)} was written by an earlier version of OCED (its numbers have "
                         f"more than {N_DEC} decimals)")
    offs, off = {}, 1
    for f, t, w, _ in fields:
        offs.setdefault(f, off)
        off += w
    dfields = [f for f, t, w, _ in fields if t == "D" and w == 8]
    if applied:
        blank = [_date_cells(recs[:, offs[f]:offs[f] + 8]) == "" for f in dfields]
        n_no_day = int(np.all(blank, axis=0).sum()) if (blank and n) else 0
    else:
        n_no_day = int((_date_cells(recs[:, offs["Date"]:offs["Date"] + 8]) == "").sum()) if n else 0
    warnings = []
    w_ = no_day_warning(n_no_day, n, applied=applied)
    if w_:
        warnings.append(w_)
    try:
        size = int(os.path.getsize(zip_path))
    except OSError:
        size = None
    out = {"zip": zip_path, "members": members, "n": int(n),
           "fields": [(f, t, int(w), int(d)) for f, t, w, d in fields], "n_no_day": n_no_day, "zip_bytes": size,
           "layout": APPLIED_LAYOUT if applied else LAYOUT, "notes": [], "warnings": warnings}
    if not applied:
        adjusted = bool(adjusted and (adjustment or {}).get("column"))
        adj_col = adjustment["column"] if adjusted else None
        widths = {f: w for f, _, w, _ in fields}
        blank = {f: _blank_rows(recs[:, offs[f]:offs[f] + widths[f]]) for f in ("Yld_Mass_D", "Yld_Vol_Dr")}
        blank_m = {f: _blank_rows(recs[:, offs[f]:offs[f] + widths[f]]) for f, _ in MAPPED_ALWAYS}
        for w_ in (map_column_warning(map_column, list(FIELD_NAMES) + [(columns or {}).get(k) for k in ROLE.values()]
                                      + [adj_col], adjustment),
                   blank_yield_warning(blank["Yld_Mass_D"], blank["Yld_Vol_Dr"], int(n)),
                   blank_mapped_warning(blank_m, int(n))):
            if w_:
                warnings.append(w_)
        which = "adjusted" if adjusted else "monitor"
        out.update({"yield": which, "label": yield_label(which, adjustment), "line": yield_line(which, adjustment),
                    "adjustment": dict(adjustment) if adjustment else None, "adjusted_column": adj_col,
                    "n_blank_yield": blank})
    if size_warning(size):
        warnings.append(size_warning(size))
    return out
