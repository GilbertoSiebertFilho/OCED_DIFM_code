# -*- coding: utf-8 -*-
"""Contract shared by every raw-monitor decoder of the app.

A decoder turns a raw monitor dataset (a CNH .cn1 card, John Deere .jdl logs, a GreenStar 3 .fdd/.fdl card, ...) into
ONE table of yield readings in the STANDARD SCHEMA below: the column names, units and meaning of an Ag Leader SMS /
John Deere Ops Center point export. The decoded table is written as a point shapefile (``write_standard``) and from
then on goes through exactly the same cleaning as an SMS export, so the clean file looks the same whatever
the source.

Decoder module interface (every module in engine/formats/ that decodes a raw format exposes):

    FORMAT_ID = "cnh_cn1"                      short id
    LABEL = "Case IH / New Holland AFS Pro 700 card (.cn1)"
    def probe(root: str) -> Probe | None       cheap check of an unpacked folder: is this my format?
    def list_units(root: str) -> list[Unit]    what the user can pick (field x year x task / machine x day / load ...)
    def decode(root, unit_ids, opts: DecodeOptions, log=print) -> (pandas.DataFrame, dict)
        the DataFrame has the STANDARD columns plus ``lon``/``lat`` (WGS 84 degrees, header centre); the dict is the
        decode report (see DECODE_REPORT_KEYS).

``root`` is a folder (the app unpacks uploaded zips, nested zips included, before probing).
"""
from __future__ import annotations

import dataclasses
import math
import os
from typing import Callable

import numpy as np
import pandas as pd

# ------------------------------------------------------------------------------------------------- standard schema
# (name <= 10 chars as written in the .dbf, dtype, unit, meaning). Names follow the SMS / Ops Center export so that the
# cleaning engine and the yield column to map are identical for decoded and exported data.
STANDARD_COLUMNS = [
    ("Field", "str", "", "field name as stored on the monitor (empty when the format has none)"),
    ("Dataset", "str", "", "task / load / log name; for sorting it should contain the start time 'YY/MM/DD-HH:MM:SS'"),
    ("Product", "str", "", "crop or product name as stored on the monitor"),
    ("Obj__Id", "int", "", "record order within the dataset, 1..n"),
    ("Time", "str", "", "local or UTC timestamp of the reading, 'YYYY-MM-DD HH:MM:SS.s' (say which in the report)"),
    ("Duration_s", "float", "s", "time represented by the reading"),
    ("Elevation_", "float", "ft", "GNSS altitude (NaN when the format does not carry it)"),
    ("Speed_mph_", "float", "mph", "ground speed"),
    ("Distance_f", "float", "ft", "distance travelled during the reading"),
    ("Track_deg_", "float", "deg", "heading, degrees clockwise from true north"),
    ("Swth_Wdth_", "float", "ft", "active (harvesting) width of the header for this reading"),
    ("Moisture__", "float", "%", "grain moisture, wet basis"),
    ("Yld_Mass_W", "float", "lb/ac", "wet grain mass per area"),
    ("Yld_Mass_D", "float", "lb/ac", "grain mass per area adjusted to the standard moisture"),
    ("Yld_Vol_We", "float", "bu/ac", "wet volume yield = Yld_Mass_W / test weight"),
    ("Yld_Vol_Dr", "float", "bu/ac", "yield at the standard moisture = Yld_Mass_D / test weight"),
    ("Machine", "str", "", "combine identifier (VIN / serial / display id)"),
    ("Src_Fmt", "str", "", "decoder FORMAT_ID"),
]
STANDARD_NAMES = [c[0] for c in STANDARD_COLUMNS]
# columns a decoder may add after the standard ones; write_standard writes those the table has (same tuple layout)
OPTIONAL_COLUMNS = [
    ("Y_Offset_f", "float", "ft", "lateral offset of the centre of the active sections from the point, + = right of "
     "travel (John Deere Gen4 SMS points: SMS's Optimize Swath Widths moves every reading by it)"),
]
OPTIONAL_NAMES = [c[0] for c in OPTIONAL_COLUMNS]
STR_WIDTH = {"Field": 64, "Dataset": 64, "Product": 40, "Time": 24, "Machine": 40, "Src_Fmt": 16}
DECODE_REPORT_KEYS = [
    "format", "label", "units", "n_records_read", "n_readings", "pre_step",  # pre_step: {reason: n dropped} before output
    "machines",        # [{machine, n_readings, wet_lb, area_ac, bu_wet, mean_bu_ac_dry, moisture_mean}]
    "totals",          # {wet_lb, area_ac, bu_wet, bu_dry}
    "time_basis",      # "UTC" | "local" | "unknown"
    "calibration",     # what the yield value is (which channel, which calibration), and its validation status
    "warnings",        # list of str
]
# Also in every decoder's report (engine.sms_import.resolve(S, crop, monitor=report["monitor_product"]) uses it):
#   "monitor_product"  the Processing Settings SMS gives the product of this source, from the crop definition the file
#                      carries: {crop (key of CROPS or None), test_weight_lb_bu, dry_moisture_pct (None = not known:
#                      the product table applies), min_yield, max_yield (bu/ac), source (where in the file)}; None when
#                      the format carries no crop definition SMS uses (Case IH / New Holland cards)
#   "crop_name"        the crop / product name the file gives the decoded data (None when they differ)

LB_PER_KG = 2.20462262185
FT_PER_M = 1.0 / 0.3048
MPH_PER_MS = 1.0 / 0.44704
SQFT_PER_AC = 43560.0

# ------------------------------------------------------------------------------------------------- crops
# Bushel weight (lb/bu) and standard moisture (%) — Canadian Grain Commission conventions for western Canada (dry
# limits), US conventions for corn and soybeans. Every value can be overridden in DecodeOptions.
CROPS = {
    "barley": (48.0, 14.8), "wheat": (60.0, 14.5), "durum": (60.0, 14.5), "oats": (34.0, 13.5),
    "canola": (50.0, 10.0), "flax": (56.0, 10.0), "peas": (60.0, 16.0), "lentils": (60.0, 13.0),
    "rye": (56.0, 14.0), "corn": (56.0, 15.5), "soybeans": (60.0, 13.0),
}


# What Ag Leader SMS makes of John Deere Gen4 / G5 logs (.jdl) and of Operations Center exports: the weight to volume
# and dry moisture of SMS's own crop for the file's CROP, not the test weight and standard moisture the file states
# (logs of 'Wheat (Hard Red Spring)' and 'Wheat (Durum)' are read at 60 lb/bu and 13.5 % where the files say 60.01
# lb/bu and 13.0 %, 'Barley' at 48 lb/bu and 15.0 % where they say 47.99 lb/bu and 14.0 %); John Deere crop name
# (lower case) -> (key of CROPS, lb/bu, dry moisture %).
# A crop not listed is not established (values None: the product table applies). JD_MIN_YIELD / JD_MAX_YIELD: the
# Min. / Max. Yield Volume of these sources (the owner's decision: SMS's 10,000 bu/ac for John Deere files).
JD_SMS_CROPS = {"wheat (hard red spring)": ("wheat", 60.0, 13.5), "wheat (durum)": ("wheat", 60.0, 13.5),
                "barley": ("barley", 48.0, 15.0)}
# the John Deere crop ids of these names (.jdl definition 21.1.1, Operations Center 'CropId')
JD_CROP_IDS = {25: "Wheat (Hard Red Spring)", 24: "Wheat (Durum)", 2: "Barley"}
JD_MIN_YIELD, JD_MAX_YIELD = 5.0, 10_000.0


def crop_key_of(name) -> str | None:
    """The CROPS key a crop / product name contains ('Wheat (Durum)' -> durum, 'Barley - Fall' -> barley), else None."""
    s = str(name or "").strip().lower()
    for k in sorted(CROPS, key=len, reverse=True):
        if k in s or (k.endswith("s") and k[:-1] in s):
            return k
    return None


def jd_monitor_product(crop_name, source: str, crop_id=None, file_values: dict | None = None) -> dict:
    """report["monitor_product"] of a John Deere Gen4 log or an Operations Center export (see JD_SMS_CROPS): SMS's
    values for the file's crop (the crop name, else its id), min / max JD_MIN_YIELD / JD_MAX_YIELD, source = where
    the crop is in the file; file_values = what the file itself states (kept for the record, not used by SMS);
    basis = "sms_crop" when the weight to volume and dry moisture are SMS's for the crop (not the file's own: the
    import says so, engine.sms_import.SRC_JD_CROP), None when no value is established for the crop."""
    name = str(crop_name or "").strip() or JD_CROP_IDS.get(crop_id) or ""   # the id only without a name
    hit = JD_SMS_CROPS.get(name.lower())
    out = {"crop": hit[0] if hit else crop_key_of(name), "test_weight_lb_bu": hit[1] if hit else None,
           "dry_moisture_pct": hit[2] if hit else None, "min_yield": JD_MIN_YIELD, "max_yield": JD_MAX_YIELD,
           "basis": "sms_crop" if hit else None,
           "source": source + ("; weight to volume and dry moisture = what SMS gives this crop (common.JD_SMS_CROPS), "
                               "not the file's own values" if hit else
                               "; no SMS value established for this crop: the product table applies")}
    if file_values:
        out["file"] = dict(file_values)
    return out


@dataclasses.dataclass
class DecodeOptions:
    crop: str | None = None                  # key of CROPS; None = take it from the monitor if the format stores it
    test_weight_lb_bu: float | None = None   # overrides the crop default / the monitor's value
    std_moisture_pct: float | None = None    # overrides the crop default / the monitor's value
    shrink: str = "symmetric"                # "symmetric": (100-M)/(100-Ms) both ways; "one_sided": never above 1
    tz_offset_h: float | None = None         # add to UTC timestamps to get local time (None = keep what the format has)

    def resolve(self, monitor_crop: str | None = None, monitor_tw: float | None = None,
                monitor_std_m: float | None = None) -> tuple[str | None, float, float]:
        """(crop, test weight, standard moisture): explicit option > monitor value > crop table. Raises if unknown."""
        crop = (self.crop or monitor_crop or "").strip().lower() or None
        tw0, m0 = CROPS.get(crop, (None, None)) if crop else (None, None)
        tw = self.test_weight_lb_bu or monitor_tw or tw0
        sm = self.std_moisture_pct if self.std_moisture_pct is not None else (monitor_std_m if monitor_std_m is not None else m0)
        if not tw or sm is None:
            raise ValueError(f"unknown crop {crop!r}: give test_weight_lb_bu and std_moisture_pct")
        return crop, float(tw), float(sm)


@dataclasses.dataclass
class Probe:
    format_id: str
    label: str
    root: str                     # the folder the decoder recognised (may be a sub-folder of what was uploaded)
    summary: str                  # one or two lines for the user
    n_files: int = 0
    machines: list = dataclasses.field(default_factory=list)
    warnings: list = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Unit:
    """One selectable piece of a raw dataset (e.g. one task of one field, or one machine-day)."""
    id: str                       # stable id passed back to decode()
    label: str                    # human label: "<field> · <season> · <crop> · task <id> (<day> <start>-<end>)"
    field: str = ""
    crop: str = ""
    year: int | None = None
    start: str = ""               # ISO time
    end: str = ""
    machine: str = ""
    n_records: int = 0
    default: bool = True          # pre-selected in the app


# ------------------------------------------------------------------------------------------------- yield arithmetic
def shrink_factor(moisture_pct, std_moisture_pct: float, shrink: str = "symmetric"):
    """Factor that turns wet mass into mass at the standard moisture: (100 - M) / (100 - Ms).
    'one_sided' caps it at 1 (grain drier than the standard is not credited, the John Deere convention)."""
    m = np.asarray(moisture_pct, float)
    f = (100.0 - m) / (100.0 - float(std_moisture_pct))
    if shrink == "one_sided":
        f = np.minimum(f, 1.0)
    elif shrink != "symmetric":
        raise ValueError(f"shrink must be 'symmetric' or 'one_sided', not {shrink!r}")
    return f


def yield_columns(wet_lb, area_ac, moisture_pct, test_weight_lb_bu: float, std_moisture_pct: float,
                  shrink: str = "symmetric") -> dict:
    """The four yield columns from the wet grain mass of each reading (lb) and the area it covered (ac).
    Readings with no area get NaN. Returns {Yld_Mass_W, Yld_Mass_D, Yld_Vol_We, Yld_Vol_Dr} as float arrays."""
    wet = np.asarray(wet_lb, float)
    area = np.asarray(area_ac, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        mw = np.where(area > 0, wet / area, np.nan)
    md = mw * shrink_factor(moisture_pct, std_moisture_pct, shrink)
    return {"Yld_Mass_W": mw, "Yld_Mass_D": md, "Yld_Vol_We": mw / test_weight_lb_bu, "Yld_Vol_Dr": md / test_weight_lb_bu}


def heading_deg(lon, lat):
    """True heading (deg from north, clockwise) from consecutive WGS 84 positions; the first value repeats the second."""
    lon, lat = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
    if len(lon) < 2:
        return np.full(len(lon), np.nan)
    dl = np.diff(lon)
    y = np.sin(dl) * np.cos(lat[1:])
    x = np.cos(lat[:-1]) * np.sin(lat[1:]) - np.sin(lat[:-1]) * np.cos(lat[1:]) * np.cos(dl)
    h = (np.degrees(np.arctan2(y, x)) + 360.0) % 360.0
    return np.r_[h[:1], h]


def step_m(lon, lat):
    """Distance (m) from the previous position (haversine); 0 for the first."""
    lon, lat = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
    if len(lon) < 2:
        return np.zeros(len(lon))
    a = np.sin(np.diff(lat) / 2) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(np.diff(lon) / 2) ** 2
    return np.r_[0.0, 2 * 6371008.8 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))]


def dataset_name(prefix: str, start: pd.Timestamp) -> str:
    """'<prefix> YY/MM/DD-HH:MM:SS' — the cleaning engine sorts datasets chronologically on that stamp."""
    return f"{prefix} {pd.Timestamp(start).strftime('%y/%m/%d-%H:%M:%S')}".strip()


# ------------------------------------------------------------------------------------------------- output
def check_standard(df: pd.DataFrame) -> list[str]:
    """Problems that make a decoded table unusable (empty list = fine)."""
    probs = [f"missing column {c}" for c in STANDARD_NAMES + ["lon", "lat"] if c not in df.columns]
    if probs:
        return probs
    if not len(df):
        return ["no readings"]
    if not (df.lat.between(-90, 90).all() and df.lon.between(-180, 180).all()):
        probs.append("lon/lat outside WGS 84 ranges")
    for c in ("Speed_mph_", "Distance_f", "Swth_Wdth_", "Yld_Mass_W"):
        if (pd.to_numeric(df[c], errors="coerce") < 0).any():
            probs.append(f"negative values in {c}")
    for name in STANDARD_NAMES:
        if len(name) > 10:
            probs.append(f"column name longer than 10 characters: {name}")
    return probs


def to_geodataframe(df: pd.DataFrame):
    import geopandas as gpd
    probs = check_standard(df)
    if probs:
        raise ValueError("decoded table does not follow the standard schema: " + "; ".join(probs))
    opt = [c for c in OPTIONAL_COLUMNS if c[0] in df.columns]
    out = df[STANDARD_NAMES + [c[0] for c in opt]].copy()
    for name, kind, _, _ in STANDARD_COLUMNS + opt:
        if kind == "str":
            out[name] = out[name].fillna("").astype(str).str.slice(0, STR_WIDTH.get(name, 64))
        elif kind == "int":
            out[name] = pd.to_numeric(out[name], errors="coerce").fillna(0).astype("int64")
        else:
            out[name] = pd.to_numeric(out[name], errors="coerce").astype(float)
    return gpd.GeoDataFrame(out, geometry=gpd.points_from_xy(df.lon.values, df.lat.values), crs="EPSG:4326")


def write_standard(df: pd.DataFrame, path: str, extra: dict | None = None) -> str:
    """Write a decoded table as a WGS 84 point shapefile (.shp/.shx/.dbf/.prj/.cpg, UTF-8) and return the .shp path:
    the standard columns, then the OPTIONAL_COLUMNS the table has.
    extra: {column: constant} appended after the standard columns (e.g. {"Crop": "canola"}, the crop key the decoder
    used, so that an opened decoded file carries the crop; engine.run.export_crop_guess reads it first)."""
    g = to_geodataframe(df)
    for k, v in (extra or {}).items():
        if v is not None and str(k)[:10] not in g.columns:
            g[str(k)[:10]] = str(v)[:40]
    path = os.path.splitext(path)[0] + ".shp"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        p = os.path.splitext(path)[0] + ext
        if os.path.exists(p):
            os.remove(p)
    g.to_file(path, driver="ESRI Shapefile", encoding="UTF-8")
    return path


def summarize(df: pd.DataFrame) -> tuple[list, dict]:
    """Per-machine and total figures for the decode report (wet mass integrated over the readings' areas)."""
    area = df.Distance_f.astype(float) * df.Swth_Wdth_.astype(float) / SQFT_PER_AC
    wet = df.Yld_Mass_W.astype(float) * area
    dry = df.Yld_Mass_D.astype(float) * area
    tw = (df.Yld_Mass_W / df.Yld_Vol_We).replace([np.inf, -np.inf], np.nan).median()
    rows = []
    for m, d in df.assign(_a=area, _w=wet, _d=dry).groupby("Machine", sort=True):
        a = float(d._a.sum())
        rows.append(dict(machine=str(m), n_readings=int(len(d)), wet_lb=float(d._w.sum()), area_ac=a,
                         bu_wet=float(d._w.sum() / tw) if tw else math.nan,
                         mean_bu_ac_dry=float(d._d.sum() / tw / a) if (tw and a > 0) else math.nan,
                         moisture_mean=float(d.Moisture__.mean())))
    tot = dict(wet_lb=float(wet.sum()), area_ac=float(area.sum()), bu_wet=float(wet.sum() / tw) if tw else math.nan,
               bu_dry=float(dry.sum() / tw) if tw else math.nan)
    return rows, tot


Logger = Callable[[str], None]
