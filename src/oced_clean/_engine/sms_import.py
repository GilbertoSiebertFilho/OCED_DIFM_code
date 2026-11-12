# -*- coding: utf-8 -*-
"""Import as SMS: the Processing Settings Ag Leader SMS Advanced applies when it imports a harvest (Grain Harvest
operation), so that the data OCED maps and cleans is the data SMS would hold after its import.

    from oced_clean._engine import sms_import as si
    S = si.settings({"product": "barley"})          # every key, validated (missing keys = the defaults of the source)
    R = si.resolve(S, crop="Barley - Fall", monitor=report.get("monitor_product"), fmt=report.get("format"))
    R["sources"]                                    # where each value came from: you / file / SMS product table ...
    rep = si.apply(shp, out_shp, R)                 # writes the imported file (records byte for byte, cells patched)

SMS's settings (Processing Settings of a Grain Harvest operation) and the values OCED gives them when the user leaves
them empty (resolve), per source, as SMS made its products for the raw data it read:
    John Deere files (GreenStar 3 cards, Gen4 .jdl logs) and John Deere Operations Center exports: Weight To Volume
        and Dry Moisture of the file's crop (the decode report's "monitor_product": a GreenStar 3 card's own crop
        definition; for a Gen4 log or an Operations Center export the values SMS gives that crop,
        common.JD_SMS_CROPS), Min. Yield Volume 5.000 and Max. Yield Volume 10,000.00 bu/ac (SMS_JD_MIN_YIELD /
        SMS_JD_MAX_YIELD, SMS's values for these files; SMS can hold another maximum for a product, so the maximum
        stays editable); SMS's product table (below)
        only when the file carries no crop definition (or a crop with no SMS value established).
    ISOXML task data (ISO 11783 TASKDATA with TLG time logs, engine/formats/isoxml.py): Weight To Volume and Dry
        Moisture = what SMS gives the task's crop (isoxml.ISO_SMS_CROPS, in the decode report's "monitor_product";
        not the density and reference moisture the log carries), Min. Yield Volume 5.000 and Max. Yield Volume
        10,000.00 bu/ac; SMS's product table for a crop with no SMS value established; the log's own density
        and reference moisture for a crop code or name OCED does not know ("from the file"; what SMS gives such a
        task is not known); and, for a task with no crop and no density in its log, what SMS stores for a dataset
        without a product (isoxml.ISO_SMS_NO_PRODUCT, labelled SRC_ISO_NONE).
    Case IH / New Holland cards and every other input (point exports): SMS's product table, SMS_PRODUCTS, as SMS
        stores it: Wheat - Spring 60 lb/bu, 13.5 %, 5-300 bu/ac; Wheat - Durum 60, 13.0 %, 5-300; Barley (Fall /
        Spring) 48, 14.5 %, 5-200; Canola 50, 8.5 %, 10-400; Field Peas and Chick Peas 60, 13.0 %, 5-200; Lentils 60,
        14.0 %, 5-200; Oats - Spring 32, 14.0 %, 5-200; Flax 60, 10.0 %, 10-400; Rye - Annual 56, 14.0 %, 0-500. The
        crop names cards and exports carry ('DURUM', 'Durum Wheat', 'CANOLA', 'HRS wheat', 'Barley - Fall' ...) are
        mapped to these products by sms_product(). A crop outside the table: the crop table of
        engine.formats.common.CROPS, 5-300 bu/ac.
    Expand Dry Moisture       off everywhere: grain drier than the dry moisture is not raised (one-sided shrink)
    Use Manual Swath          off; Manual Swath Width 15.00 ft (the per-area yields are scaled by logged / manual)
    Use Manual Moisture       off; Manual Moisture = the dry moisture
    Start Delay / Stop Delay  0.00 s: the first / last seconds of every pass left out (grain filling / draining)
    Flow Shift                0.00 s: the grain mass of a reading moved to the position logged that many seconds
                              earlier (positive) or later (negative), within each run of continuous logging
A value the user sets always wins; resolve() says per value where it came from (R["sources"]: "you", "file", "SMS
for this John Deere crop", "SMS for this crop in ISOXML task data", "SMS for ISOXML task data without a product",
"SMS product table", "SMS for John Deere files", "SMS for ISOXML task data", "crop table" or "OCED default").

Checked on an SMS export: wet mass / wet volume = the product's weight to volume, dry / wet volume = (100 - M) /
(100 - Ms) above the dry moisture Ms and exactly 1 below it, no reading below the minimum yield. Pure Python (no
Streamlit).

The yield columns are found by what they hold, not by one vendor's names (yield_columns): the wet mass per area
(Yld_Mass_W of SMS, WetMass of the John Deere Operations Center), the wet and the dry volume (Yld_Vol_We; Yld_Vol_Dr,
VRYIELDVOL) and the dry mass (Yld_Mass_D). SMS computes the dry volume itself from the wet mass, the moisture and the
product's settings and ignores the dry yield a point file carries: its import of a point file gives wet mass x
one-sided shrink / lb/bu, with the Operations Center names and with the SMS names alike (a dry yield the file carries,
computed elsewhere with another dry moisture, can differ from it). The Operations Center's VRYIELDVOL is its own dry
yield (wet mass x one-sided shrink to the crop's standard moisture), so it is replaced like any other. The Operations
Center's VRYIELDMAS (yield as mass; no sample says whether it is wet or dry) is scaled with the manual swath and
otherwise left as the file gives it. A file without a moisture column keeps the dry / wet ratio of each of its
readings (nothing to dry the wet mass with) unless a manual moisture is used.
"""
from __future__ import annotations

import os
import re

import numpy as np
import pandas as pd

from oced_clean._engine.formats import common

AUTO = "auto"                     # min_yield: the source's / product's minimum (None = no minimum)
SRC_YOU, SRC_FILE, SRC_TABLE = "you", "file", "SMS product table"
SRC_JD, SRC_CROPS, SRC_OCED = "SMS for John Deere files", "crop table", "OCED default"
# a Gen4 log's or an Operations Center export's crop: the weight to volume and dry moisture SMS gives that crop
# (common.JD_SMS_CROPS), not the values the file states (monitor_product "basis" "sms_crop")
SRC_JD_CROP = "SMS for this John Deere crop"
# ISOXML task data: the weight to volume and dry moisture SMS gives the task's crop (isoxml.ISO_SMS_CROPS), and the
# yield volume limits SMS stores with the products it makes of such task data
SRC_ISO_CROP, SRC_ISO = "SMS for this crop in ISOXML task data", "SMS for ISOXML task data"
SRC_ISO_NONE = "SMS for ISOXML task data without a product"     # monitor_product "basis" "sms_no_product"

# SMS's product table: SMS product -> (Weight To Volume lb/bu, Dry Moisture %, Min. / Max. Yield Volume bu/ac), as
# SMS stores the products it made for the Case IH / New Holland cards it read (Processing Settings)
SMS_PRODUCTS = {
    "Wheat - Spring": (60.0, 13.5, 5.0, 300.0),
    "Wheat - Durum": (60.0, 13.0, 5.0, 300.0),
    "Barley - Fall": (48.0, 14.5, 5.0, 200.0),
    "Barley - Spring": (48.0, 14.5, 5.0, 200.0),
    "Canola": (50.0, 8.5, 10.0, 400.0),
    "Field Peas": (60.0, 13.0, 5.0, 200.0),
    "Chick Peas": (60.0, 13.0, 5.0, 200.0),
    "Lentils": (60.0, 14.0, 5.0, 200.0),
    "Oats - Spring": (32.0, 14.0, 5.0, 200.0),
    "Flax": (60.0, 10.0, 10.0, 400.0),
    "Rye - Annual": (56.0, 14.0, 0.0, 500.0),
}
# the crop key (engine.formats.common.CROPS, what the decoders and the rest of OCED name crops by) of each product, and
# the product a bare crop key stands for (both barley products hold the same values)
PRODUCT_CROP = {"Wheat - Spring": "wheat", "Wheat - Durum": "durum", "Barley - Fall": "barley",
                "Barley - Spring": "barley", "Canola": "canola", "Field Peas": "peas", "Chick Peas": "peas",
                "Lentils": "lentils", "Oats - Spring": "oats", "Flax": "flax", "Rye - Annual": "rye"}
CROP_PRODUCT = {"wheat": "Wheat - Spring", "durum": "Wheat - Durum", "barley": "Barley - Fall", "canola": "Canola",
                "peas": "Field Peas", "lentils": "Lentils", "oats": "Oats - Spring", "flax": "Flax",
                "rye": "Rye - Annual"}
# the names cards and exports give a crop -> the SMS product; the first rule that matches wins, so the specific names
# come before the general ones ('Durum Wheat' is durum, 'Chick Peas' are not field peas, 'Ryegrass' is not rye)
_PRODUCT_RULES = (
    (r"durum|\bcwad\b|\bamber\b", "Wheat - Durum"),
    (r"chick\s*-?\s*peas?|garbanzo|kabuli|\bdesi\b", "Chick Peas"),
    (r"lentil", "Lentils"),
    (r"\bpeas?\b|\bfield\s*-?\s*peas?", "Field Peas"),
    (r"canola|rape\s*seed|\brape\b", "Canola"),
    (r"flax|linseed", "Flax"),
    (r"\boats?\b", "Oats - Spring"),
    (r"\brye\b", "Rye - Annual"),
    (r"barley.*\b(spring|spr)\b|\b(spring|spr)\b.*barley", "Barley - Spring"),
    (r"barley", "Barley - Fall"),
    (r"wheat|\bhrs\b|\bhrw\b|\bsrw\b|\bcwrs\b|\bcpsr\b|\bcwsws\b", "Wheat - Spring"),
)
_PRODUCT_RE = [(re.compile(p, re.I), name) for p, name in _PRODUCT_RULES]

# John Deere files: the yield volume limits SMS stores with the products it makes from them (its Processing Settings
# of GreenStar 3 and Gen4 datasets: 5 and 10,000 bu/ac)
SMS_JD_MIN_YIELD, SMS_JD_MAX_YIELD = 5.0, 10_000.0
SMS_MIN_YIELD = 5.0               # SMS's Min. Yield Volume of a product
MAX_YIELD_OTHER = 300.0           # a crop outside SMS's table
JOHN_DEERE_FORMATS = ("jd_gs3_rcd", "jd_jdl", "jd_ops_center")    # jd_ops_center: an Operations Center export
CNH_FORMATS = ("cnh_cn1",)
ISOXML_FORMATS = ("isoxml",)
# the families whose import takes the crop definition of the file (the decode report's monitor_product) and SMS's
# 5 / 10,000 bu/ac limits; their decoder keeps the file's crop unless the user types a weight or a moisture
FILE_CROP_FAMILIES = ("john_deere", "isoxml")
# (lb/bu, dry moisture, max) of the product a crop key stands for (the table's earlier form)
PRODUCT_DEFAULTS = {k: (SMS_PRODUCTS[p][0], SMS_PRODUCTS[p][1], SMS_PRODUCTS[p][3]) for k, p in CROP_PRODUCT.items()}
DEFAULTS = {
    "on": True,
    "product": None,                  # a key of common.CROPS (or an SMS product name); None = the crop of the data
    "test_weight_lb_bu": None,        # Weight To Volume; None = the source's
    "dry_moisture_pct": None,         # Dry Moisture; None = the source's
    "expand_dry_moisture": False,
    "use_manual_swath": False,
    "manual_swath_ft": 15.0,
    "use_manual_moisture": False,
    "manual_moisture_pct": None,      # None = the dry moisture
    "min_yield": AUTO,                # Min. Yield Volume (bu/ac); "auto" = the source's; None = no minimum
    "max_yield": None,                # Max. Yield Volume (bu/ac); None = the source's
    "start_delay_s": 0.0,
    "stop_delay_s": 0.0,
    "flow_shift_s": 0.0,
}
RESOLVED_KEYS = ("test_weight_lb_bu", "dry_moisture_pct", "min_yield", "max_yield")
PASS_BREAK_M = 15.0                   # a pass ends where the next reading is farther than this (as the engine)
PASS_BREAK_DEG = 60.0                 # or turns by more than this
GAP_PERIODS = 2.0                     # logging stopped where the time step exceeds the reading's own duration by
                                      # more than this many median durations (a new run: a new pass for the delays)
DRY_MASS_NAMES = ("yld_mass_d", "yld_mass_dry", "drymass", "dry_mass")       # no key of detect_columns: by its name
OTHER_YIELD_NAMES = ("vryieldmas",)   # per-area yields with no known basis: scaled with the manual swath only


def sms_product(text) -> str | None:
    """The SMS product (a key of SMS_PRODUCTS) a crop name stands for ('DURUM' -> Wheat - Durum, 'HRS wheat' ->
    Wheat - Spring, 'Barley - Fall' -> Barley - Fall, 'CANOLA' -> Canola), else None (corn, soybeans, unknown)."""
    s = str(text or "").strip()
    if not s:
        return None
    if s in SMS_PRODUCTS:
        return s
    low = s.lower()
    if low in CROP_PRODUCT:
        return CROP_PRODUCT[low]
    for rx, name in _PRODUCT_RE:
        if rx.search(s):
            return name
    return None


def crop_key(text) -> str | None:
    """The common.CROPS key a crop name points at ('Barley - Fall' -> barley, 'Durum Wheat' -> durum, 'crop 48
    (wheat?)' -> wheat), else None."""
    s = str(text or "").strip().lower()
    if not s:
        return None
    if s in common.CROPS:
        return s
    p = sms_product(s)
    if p:
        return PRODUCT_CROP[p]
    for k in sorted(common.CROPS, key=len, reverse=True):     # the crops SMS's table does not hold (corn ...)
        if k not in CROP_PRODUCT and k in s:
            return k
    return None


def format_family(fmt) -> str | None:
    """'john_deere' for a GreenStar 3 card, Gen4 .jdl logs or an Operations Center export, 'cnh' for a Case IH / New
    Holland card, 'isoxml' for ISOXML task data (whatever the maker of the display), None for anything else (a
    point export of another software, unknown)."""
    f = str(fmt or "").strip().lower()
    if not f:
        return None
    if f in ISOXML_FORMATS:
        return "isoxml"
    if f in CNH_FORMATS or f.startswith("cnh"):
        return "cnh"
    if f in JOHN_DEERE_FORMATS or f.startswith("jd") or "deere" in f or "ops_center" in f:
        return "john_deere"
    return None


def product_defaults(crop) -> tuple[float | None, float | None, float]:
    """(weight to volume, dry moisture, max yield volume) of a product: SMS's product table, the crop table and 300
    bu/ac for another crop, (None, None, 300) when the crop is unknown."""
    tw, ms, _, mx = table_values(crop)[:4]
    return tw, ms, mx


def table_values(crop) -> tuple:
    """(weight to volume, dry moisture, min, max, SMS product or None, source of the weight and moisture) of a crop
    name: SMS's product table, else the crop table with 5-300 bu/ac."""
    p = sms_product(crop)
    if p in SMS_PRODUCTS:
        tw, ms, mn, mx = SMS_PRODUCTS[p]
        return tw, ms, mn, mx, p, SRC_TABLE
    k = crop_key(crop)
    tw, ms = common.CROPS.get(k, (None, None)) if k else (None, None)
    return tw, ms, SMS_MIN_YIELD, MAX_YIELD_OTHER, None, (SRC_CROPS if k else None)


def settings(S: dict | None = None) -> dict:
    """Every key of DEFAULTS, the given ones validated."""
    out = dict(DEFAULTS)
    for k, v in (S or {}).items():
        if k not in DEFAULTS:
            raise ValueError(f"unknown SMS import setting {k!r}")
        out[k] = v
    for k in ("expand_dry_moisture", "use_manual_swath", "use_manual_moisture", "on"):
        out[k] = bool(out[k])
    for k, lo, hi in (("test_weight_lb_bu", 1, 200), ("dry_moisture_pct", 0, 60), ("manual_swath_ft", 0.1, 300),
                      ("manual_moisture_pct", 0, 80), ("min_yield", 0, 1e4), ("max_yield", 0, 1e5),
                      ("start_delay_s", 0, 600), ("stop_delay_s", 0, 600), ("flow_shift_s", -600, 600)):
        v = out[k]
        if v is None or (k == "min_yield" and isinstance(v, str) and v.strip().lower() == AUTO):
            out[k] = AUTO if v is not None else None
            continue
        v = float(v)
        if not np.isfinite(v) or not lo <= v <= hi:
            raise ValueError(f"SMS import setting {k} = {v!r} is outside {lo:g}-{hi:g}")
        out[k] = v
    if out["product"] is not None:
        k = crop_key(out["product"])
        if k is None:
            raise ValueError(f"unknown product {out['product']!r}: use one of {sorted(common.CROPS)} or an SMS "
                             f"product ({', '.join(SMS_PRODUCTS)})")
        out["product"] = k
    return out


def resolve(S: dict, crop=None, monitor: dict | None = None, fmt: str | None = None) -> dict:
    """The settings of one file: every value the user left empty filled in as SMS does for this source (module
    docstring), the manual moisture = the dry moisture when empty.

    crop: the crop name of the data (a unit's crop, the file's Crop / Product column, the Deere metadata).
    monitor: the decode report's "monitor_product" - {"crop", "test_weight_lb_bu", "dry_moisture_pct", and
        optionally "min_yield", "max_yield" (None = no maximum), "source"}: the crop definition the file carries.
    fmt: the source format (engine.formats FORMAT_ID: "jd_gs3_rcd", "jd_jdl", "cnh_cn1"; "jd_ops_center" for an
        Operations Center export; None for any other point export).
    John Deere sources: the file's weight and dry moisture stand in for the product table when no product is chosen or
    the product chosen is the file's crop, min / max from the monitor dict, else 5 / 10,000 bu/ac. ISOXML task data
    ("isoxml"): the same, with the monitor dict's values labelled SRC_ISO_CROP / SRC_ISO. Case IH / New
    Holland: the product table (the file's values only for a crop outside it). fmt None with a monitor dict: the file's
    values, its min / max where it gives them, else the product table's. No monitor dict and fmt None (a point
    export of another software): the product table.
    The weight and dry moisture of a monitor dict whose "basis" is "sms_crop" (a Gen4 log, an Operations Center
    export: SMS's values for the crop, common.jd_monitor_product) are labelled SRC_JD_CROP, not "file".
    Returns the settings plus "sources" {key: you | file | SMS for this John Deere crop | SMS product table | SMS for
    John Deere files | crop table | OCED default}, "defaults" {key: the value an empty setting gets}, "sms_product",
    "family", "monitor_source"."""
    S = settings(S)
    fam = format_family(fmt)
    mon = monitor if isinstance(monitor, dict) and monitor else None
    mon_key = crop_key(mon.get("crop")) if mon else None
    prod = S["product"] or crop_key(crop) or (mon_key if mon else None)
    name = crop if (not S["product"] and crop and crop_key(crop) == prod) else prod
    t_tw, t_dm, t_min, t_max, sms_name, t_src = table_values(name)
    if sms_name is None and prod in CROP_PRODUCT:
        t_tw, t_dm, t_min, t_max, sms_name, t_src = table_values(CROP_PRODUCT[prod])
    same = not S["product"] or mon_key == prod
    use_file = bool(mon) and same and (fam != "cnh" or sms_name is None)
    # the monitor dict carries SMS's values for the file's John Deere crop, not the file's own (a record made before
    # "basis" was kept says so in its source text)
    sms_crop = bool(mon) and (mon.get("basis") == "sms_crop" or "JD_SMS_CROPS" in str(mon.get("source") or ""))
    no_product = bool(mon) and mon.get("basis") == "sms_no_product"
    d, src = {}, {}
    for k, tv in (("test_weight_lb_bu", t_tw), ("dry_moisture_pct", t_dm)):
        fv = mon.get(k) if use_file else None
        if fv is not None and np.isfinite(float(fv)) and (k != "test_weight_lb_bu" or float(fv) > 0):
            d[k], src[k] = float(fv), ((SRC_ISO_CROP if fam == "isoxml" else SRC_JD_CROP) if sms_crop else
                                       SRC_ISO_NONE if no_product else SRC_FILE)
        else:
            d[k], src[k] = tv, (t_src if tv is not None else None)
    for k, tv, jd in (("min_yield", t_min, SMS_JD_MIN_YIELD), ("max_yield", t_max, SMS_JD_MAX_YIELD)):
        if fam in FILE_CROP_FAMILIES:
            lab = SRC_ISO if fam == "isoxml" else SRC_JD
            if mon and k in mon:
                d[k], src[k] = (None if mon[k] is None else float(mon[k])), lab
            else:
                d[k], src[k] = jd, lab
        elif fam is None and use_file and k in mon:
            d[k], src[k] = (None if mon[k] is None else float(mon[k])), SRC_FILE
        else:
            d[k], src[k] = tv, (SRC_TABLE if sms_name else SRC_OCED)
    out = dict(S)
    out["product"] = prod
    for k in RESOLVED_KEYS:
        v = S[k]
        given = v is not None and v != AUTO
        if k == "min_yield" and v is None:
            given = True                                 # None = no minimum, the user's choice
        out[k] = (None if v is None else float(v)) if given else d[k]
        if given and not (v is not None and d[k] is not None and abs(float(v) - float(d[k])) <= 1e-9):
            src[k] = SRC_YOU                             # the user's value differs from the source's: it wins
    out["manual_moisture_pct"] = (S["manual_moisture_pct"] if S["manual_moisture_pct"] is not None
                                  else out["dry_moisture_pct"])
    out["sources"] = src
    out["defaults"] = d
    out["sms_product"] = sms_name
    out["family"] = fam
    out["monitor_source"] = mon.get("source") if (mon and use_file) else None
    return out


def describe(R: dict) -> str:
    """One line of the settings, as SMS's dialog lists them."""
    def f(v, d=2):
        return "-" if v is None else f"{float(v):,.{d}f}"
    mn = R["min_yield"] if R["min_yield"] != AUTO else None
    s = (f"product {R.get('product') or 'unknown'}; weight to volume {f(R['test_weight_lb_bu'])} lb/bu; dry moisture "
         f"{f(R['dry_moisture_pct'])} %; expand dry moisture {'on' if R['expand_dry_moisture'] else 'off'}")
    if R["use_manual_swath"]:
        s += f"; manual swath {f(R['manual_swath_ft'])} ft"
    if R["use_manual_moisture"]:
        s += f"; manual moisture {f(R['manual_moisture_pct'])} %"
    if R["max_yield"] is not None:
        s += (f"; yield volume {f(mn, 3)}-{f(R['max_yield'])} bu/ac" if mn is not None
              else f"; yield volume up to {f(R['max_yield'])} bu/ac, no minimum")
    else:
        s += (f"; yield volume from {f(mn, 3)} bu/ac, no maximum" if mn is not None
              else "; no yield volume limits")
    s += (f"; start delay {f(R['start_delay_s'])} s, stop delay {f(R['stop_delay_s'])} s, flow shift "
          f"{f(R['flow_shift_s'])} s")
    return s


def describe_sources(R: dict) -> str:
    """Where the weight to volume, the dry moisture and the yield volume limits came from, for the user:
    'weight to volume <x> lb/bu (file), dry moisture <y> % (file), min. yield volume 5.000 bu/ac (SMS for John
    Deere files), max. yield volume 10,000.00 bu/ac (SMS for John Deere files)'."""
    src = R.get("sources") or {}
    head = (f"SMS product {R['sms_product']}: " if R.get("sms_product") and SRC_TABLE in src.values() else "")

    def f(v, d=2):
        return "none" if v is None else f"{float(v):,.{d}f}"
    parts = [f"weight to volume {f(R.get('test_weight_lb_bu'))} lb/bu", f"dry moisture {f(R.get('dry_moisture_pct'))} %",
             f"min. yield volume {f(R.get('min_yield'), 3)} bu/ac", f"max. yield volume {f(R.get('max_yield'))} bu/ac"]
    return head + ", ".join(p + (f" ({src[k]})" if src.get(k) else "") for p, k in zip(parts, RESOLVED_KEYS))


# ================================================================================================ apply
def _num(df, col):
    return pd.to_numeric(df[col], errors="coerce").to_numpy(float) if (col and col in df) else None


def yield_columns(columns, C: dict | None = None) -> dict:
    """{"wet_mass", "dry_mass", "wet_vol", "dry_vol": column or None}: the per-area yield columns of a harvest point
    file whatever the vendor calls them (engine.clean.yield_clean.detect_columns: Yld_Mass_W or WetMass, Yld_Vol_We,
    Yld_Vol_Dr or VRYIELDVOL ...; the dry mass by its name, DRY_MASS_NAMES)."""
    cols = [c for c in columns if c != "geometry"]
    if C is None:
        from oced_clean._engine.clean import yield_clean as yc
        C = yc.detect_columns(cols)
    low = {str(c).strip().strip("_").lower(): c for c in reversed(cols)}
    taken = {C.get(k) for k in ("wet_mass_lbac", "yield_wet", "yield")}
    dry_mass = next((low[n] for n in DRY_MASS_NAMES if n in low and low[n] not in taken), None)
    return {"wet_mass": C.get("wet_mass_lbac"), "dry_mass": dry_mass, "wet_vol": C.get("yield_wet"),
            "dry_vol": C.get("yield")}


def other_yield_columns(columns, Y: dict) -> list:
    """The per-area yield columns that are none of the four of yield_columns (the Operations Center's VRYIELDMAS)."""
    taken = {c for c in Y.values() if c}
    return [c for c in columns if c != "geometry" and str(c).strip().strip("_").lower() in OTHER_YIELD_NAMES
            and c not in taken]


def _sequences(df, C) -> np.ndarray:
    """A sequence number per record: a new sequence where the dataset or the machine changes (file order)."""
    brk = np.zeros(len(df), bool)
    if len(df):
        brk[0] = True
    for key in ("dataset", "machine"):
        col = C.get(key)
        if col and col in df:
            v = df[col].astype(str).to_numpy()
            brk[1:] |= v[1:] != v[:-1]
    return np.cumsum(brk)


def _time_of_day_s(df, C) -> np.ndarray | None:
    """Seconds of the Time column (from its first value) when it carries a time of day, else None (an SMS export's
    Time is a date only)."""
    tcol = C.get("time")
    if not tcol or tcol not in df:
        return None
    from oced_clean._engine.clean import yield_clean as yc
    t, ok = yc._parse_time(df[tcol])
    if not ok:
        return None
    s = (t - t.min()).dt.total_seconds().to_numpy(float)
    return s if np.isfinite(s).any() else None


def _timeline(df, C, seq):
    """(clock, runs, period, notes) for the flow shift and the delays. A run is a stretch of continuous logging: a new
    run with a new sequence and, when the Time column carries a time of day, where the time step exceeds the reading's
    own duration by more than GAP_PERIODS median durations (header up, a stop, the night: a hole in the file, which the
    summed durations do not see). clock = seconds since the start of the run: the durations added up (finer than a
    Time logged to the second), else the Time column. None when neither gives a clock."""
    notes = []
    dur = _num(df, C.get("duration_s"))
    has_dur = dur is not None and np.isfinite(dur).any() and (dur > 0).any()
    ts = _time_of_day_s(df, C)
    n = len(df)
    brk = np.r_[True, seq[1:] != seq[:-1]] if n else np.zeros(0, bool)
    if has_dur:
        d = np.where(np.isfinite(dur) & (dur > 0), dur, 0.0)
        period = float(np.median(d[d > 0]))
    elif ts is not None:
        step = np.diff(ts)
        pos = step[np.isfinite(step) & (step > 0)]
        if not len(pos):
            return None, None, None, notes
        period = float(np.median(pos))
        d = None
    else:
        return None, None, None, notes
    if ts is not None and n > 1:
        step = np.r_[0.0, np.diff(ts)]
        own = d if d is not None else np.full(n, period)
        with np.errstate(invalid="ignore"):
            gap = np.isfinite(step) & ((step - own) > GAP_PERIODS * period)
        brk |= gap
    elif has_dur:
        notes.append("the file's Time has no time of day: holes in the logging cannot be seen, the durations are "
                     "added up over them")
    runs = np.cumsum(brk)
    if d is not None:
        c = np.cumsum(d)
        start = pd.Series(c - d).groupby(runs).transform("min").to_numpy()
        clock = c - start
    else:
        clock = ts - pd.Series(ts).groupby(runs).transform("min").to_numpy() + period
    return clock, runs, period, notes


def _passes(df, C, runs, xy) -> np.ndarray:
    """A pass number per record: a new pass with a new run (logging restarted), a step longer than PASS_BREAK_M or a
    turn sharper than PASS_BREAK_DEG (the engine's pass break)."""
    brk = np.r_[True, runs[1:] != runs[:-1]]
    if xy is not None:
        step = np.r_[0.0, np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1]))]
        brk |= step > PASS_BREAK_M
    hd = _num(df, C.get("heading"))
    if hd is not None:
        dh = np.r_[0.0, ((np.diff(hd) + 180) % 360) - 180]
        brk |= np.abs(np.nan_to_num(dh)) > PASS_BREAK_DEG
    return np.cumsum(brk)


def _shift(clock, runs, period, values, s) -> np.ndarray:
    """values moved by s seconds within each run: each record takes the value of the record nearest to its own time
    + s, when one lies within half a period of it (else NaN: no grain from beyond the ends of a run)."""
    out = np.full(len(values), np.nan)
    for q in np.unique(runs):
        idx = np.flatnonzero(runs == q)
        t = clock[idx]
        tgt = t + s
        j = np.searchsorted(t, tgt - 0.5 * period)
        jj = np.minimum(j, len(idx) - 1)
        ok = (j < len(idx)) & (np.abs(t[jj] - tgt) <= 0.5 * period + 1e-9)
        out[idx[ok]] = values[idx[jj[ok]]]
    return out


def _group_counts(df, C, mask) -> dict:
    """{machine (or dataset): readings of mask}, for the left-out counts per combine."""
    col = C.get("machine") or C.get("dataset")
    if not col or col not in df or not mask.any():
        return {}
    v = df[col].astype(str).to_numpy()[mask]
    u, k = np.unique(v, return_counts=True)
    return {str(a): int(b) for a, b in zip(u, k)}


def apply(shp: str, out_shp: str, R: dict, log=lambda m: None) -> dict:
    """Import a standard harvest point file as SMS would: manual swath / moisture, flow shift, the weight to volume
    and dry moisture (wet volume, dry mass and dry volume computed again from the wet mass), the start / stop delays
    and the yield volume limits. Writes out_shp with the kept records byte for byte and the changed cells patched
    (engine.clean.yield_clean.write_points); a file whose records GDAL reads differently from the raw .dbf (deleted
    records, a damaged .shx) is written through GDAL instead, which a note says. When nothing changes and nothing is
    left out the file is not copied.
    Returns {path, n_in, n_out, left_out: {step: n}, left_out_by_group: {step: {machine: n}}, changed: [columns],
    applied: {setting: True / False}, settings, notes}."""
    import pyogrio
    from oced_clean._engine.clean import yield_clean as yc
    if R.get("min_yield") == AUTO or "sources" not in R:
        R = {**resolve({k: R.get(k, DEFAULTS[k]) for k in DEFAULTS}, R.get("product")),
             **{k: R[k] for k in R if k not in DEFAULTS}}
    base = os.path.splitext(shp)[0]
    g = pyogrio.read_dataframe(shp)
    C = yc.detect_columns([c for c in g.columns if c != "geometry"])
    n = len(g)
    applied = {"manual_swath": False, "manual_moisture": False, "flow_shift": False, "weight_and_moisture": False,
               "start_stop_delay": False, "yield_limits": False}
    rep = {"path": shp, "n_in": n, "n_out": n, "left_out": {}, "left_out_by_group": {}, "changed": [],
           "applied": applied, "settings": R, "notes": []}
    if n == 0:
        return rep
    Y = yield_columns(g.columns, C)                  # what each yield column holds, whatever its name
    other = other_yield_columns(g.columns, Y)
    vals = {k: _num(g, c) for k, c in Y.items() if c}
    oth = {c: _num(g, c) for c in other}
    wcol, mcol = C.get("width_ft"), C.get("moisture")
    width = _num(g, wcol)
    moist = _num(g, mcol)
    tw, ms = R["test_weight_lb_bu"], R["dry_moisture_pct"]
    can_wet = vals.get("wet_mass") is not None or (vals.get("wet_vol") is not None and bool(tw))
    can_compute = can_wet and bool(tw) and ms is not None
    new = {}
    # Use Manual Swath: the logged width replaced, the per-area yields scaled by logged / manual (the mass is kept)
    if R["use_manual_swath"] and width is None:
        rep["notes"].append("manual swath not applied: the file has no width column")
    elif R["use_manual_swath"] and not (vals or oth):
        rep["notes"].append("manual swath not applied: the file has no per-area yield column to scale")
    elif R["use_manual_swath"]:
        mw = float(R["manual_swath_ft"])
        sc = np.where(np.isfinite(width) & (width > 0), width / mw, np.nan)
        for k, v in vals.items():
            vals[k] = v * sc
            new[Y[k]] = vals[k]
        for c, v in oth.items():
            new[c] = v * sc
        width = np.full(n, mw)
        new[wcol] = width
        applied["manual_swath"] = True
    # Use Manual Moisture: every reading at the manual moisture; the dry yield follows (with or without a column)
    if R["use_manual_moisture"] and not can_compute:
        rep["notes"].append("manual moisture not applied: the file has no wet mass or wet volume column (or the "
                            "weight to volume is unknown), so the dry yield cannot be computed from it")
    elif R["use_manual_moisture"]:
        moist = np.full(n, float(R["manual_moisture_pct"]))
        applied["manual_moisture"] = True
        if mcol:
            new[mcol] = moist
        else:
            rep["notes"].append("manual moisture: the file has no moisture column; the dry yield is computed with "
                                "the manual moisture")
    wet = vals.get("wet_mass")
    if wet is None and vals.get("wet_vol") is not None and tw:
        wet = vals["wet_vol"] * float(tw)
    seq = _sequences(g, C)
    need_time = bool(R["flow_shift_s"]) or bool(R["start_delay_s"]) or bool(R["stop_delay_s"])
    clock = runs = period = None
    if need_time:                                    # the Time strings are parsed only when a setting needs them
        clock, runs, period, tnotes = _timeline(g, C, seq)
        rep["notes"] += tnotes
    xy = None
    if R["start_delay_s"] or R["stop_delay_s"]:
        try:
            if g.crs is not None:
                utm = g.estimate_utm_crs()
                pts = g.geometry.to_crs(utm)
                xy = np.c_[pts.x.to_numpy(float), pts.y.to_numpy(float)]
        except Exception:                            # no usable geometry: passes from the heading only
            xy = None
    # Flow Shift: the grain mass logged s seconds later belongs to this position (positive s)
    s = float(R["flow_shift_s"] or 0)
    if s and wet is None:
        rep["notes"].append("flow shift: the file has no wet mass or wet volume column: not applied")
    elif s:
        dist = _num(g, C.get("distance_ft"))
        if clock is None or dist is None or width is None:
            rep["notes"].append("flow shift: the file needs duration (or time), distance and width columns: not applied")
        else:
            area = dist * width / 43560.0
            shifted = _shift(clock, runs, period, wet * area, s)
            with np.errstate(divide="ignore", invalid="ignore"):
                wet = np.where(area > 0, shifted / area, np.nan)
            applied["flow_shift"] = True
            rep["notes"].append(f"flow shift {s:+g} s: the grain mass moved within each run of continuous logging "
                                "(the readings at the ends of a run receive none)")
    # Weight To Volume, Dry Moisture, Expand Dry Moisture: the yields again from the wet mass
    if wet is not None and tw and ms is not None:
        calc = {"wet_mass": wet, "wet_vol": wet / float(tw)}
        if moist is not None:
            f = common.shrink_factor(moist, float(ms), "symmetric" if R["expand_dry_moisture"] else "one_sided")
            f = np.where(np.isfinite(f), f, 1.0)
            calc.update({"dry_mass": wet * f, "dry_vol": wet * f / float(tw)})
        else:
            # nothing to dry the wet mass with: each reading keeps the dry / wet ratio the file gives it
            w0 = _num(g, Y["wet_mass"]) if Y["wet_mass"] else None
            d0 = _num(g, Y["dry_mass"]) if Y["dry_mass"] else None
            if w0 is None or d0 is None:
                w0 = _num(g, Y["wet_vol"]) if Y["wet_vol"] else None
                d0 = _num(g, Y["dry_vol"]) if Y["dry_vol"] else None
            if w0 is not None and d0 is not None:
                with np.errstate(divide="ignore", invalid="ignore"):
                    f = np.where(np.isfinite(d0), np.where(w0 > 0, d0 / w0, 1.0), np.nan)
                calc.update({"dry_mass": wet * f, "dry_vol": wet * f / float(tw)})
                rep["notes"].append("no moisture column: the dry yields keep the file's own dry / wet ratio of each "
                                    "reading (tick 'Use manual moisture' to compute them from one moisture)")
            elif Y["dry_vol"] or Y["dry_mass"]:
                rep["notes"].append("no moisture column: the dry yield is kept as the file gives it (use a manual "
                                    "moisture to compute it)")
        for k, v in calc.items():
            vals[k] = v                              # the dry volume counts for the limits also without a column
            if Y[k]:
                new[Y[k]] = v
        applied["weight_and_moisture"] = True
    elif wet is None:
        rep["notes"].append("weight to volume and dry moisture not applied: the file has no wet mass or wet volume "
                            "column, the yields are used as the file gives them")
    else:
        rep["notes"].append("weight to volume or dry moisture unknown (product not recognised): the yields are used "
                            "as the file gives them")
    if other and (applied["flow_shift"] or applied["weight_and_moisture"]):
        rep["notes"].append(f"{', '.join(other)}: the Operations Center's yield as mass (wet or dry is not known) is "
                            "left as the file gives it" + (", scaled with the manual swath" if applied["manual_swath"]
                                                          else ""))
    # which records stay
    keep = np.ones(n, bool)
    dv = vals.get("dry_vol")

    def drop(name, bad):
        nonlocal keep
        bad = np.asarray(bad, bool) & keep
        if bad.any():
            rep["left_out"][name] = int(bad.sum())
            by = _group_counts(g, C, bad)
            if by:
                rep["left_out_by_group"][name] = by
        keep &= ~bad
    if (R["start_delay_s"] or R["stop_delay_s"]) and clock is not None:
        ps = _passes(g, C, runs, xy)
        dur = _num(g, C.get("duration_s"))
        d = (np.where(np.isfinite(dur) & (dur > 0), dur, 0.0) if dur is not None and np.isfinite(dur).any()
             else np.full(n, period))
        t0 = pd.Series(clock - d).groupby(ps).transform("min").to_numpy()
        t1 = pd.Series(clock).groupby(ps).transform("max").to_numpy()
        if R["start_delay_s"]:
            drop("start delay", (clock - d) - t0 < float(R["start_delay_s"]))
        if R["stop_delay_s"]:
            drop("stop delay", t1 - (clock - d) <= float(R["stop_delay_s"]))
        applied["start_stop_delay"] = True
    elif R["start_delay_s"] or R["stop_delay_s"]:
        rep["notes"].append("start / stop delay: the file has no duration column nor a Time with a time of day: not "
                            "applied")
    if dv is not None:
        drop("no yield", ~np.isfinite(dv))
        if R["min_yield"] is not None:
            drop("below the min. yield volume", dv < float(R["min_yield"]))
        if R["max_yield"] is not None:
            drop("above the max. yield volume", dv > float(R["max_yield"]))
        applied["yield_limits"] = True
    # the cells that really change at the field's own decimals
    dbf = yc._read_dbf(base + ".dbf")
    dec = {d[:11].split(b"\0")[0].decode("latin-1"): d[17] for d in dbf["fdesc"]}
    rows = np.flatnonzero(keep)
    patch = {}
    for c, v in new.items():
        old = _num(g, c)
        q = int(dec.get(c, 6))
        a, b = np.round(np.asarray(v, float), q), np.round(old, q)
        # a cell changes when it moves by more than one unit of its last decimal: SMS computes the volumes from the
        # unrounded mass, so recomputing them from the rounded one differs by that unit (noise, not a change)
        with np.errstate(invalid="ignore"):
            diff = ~((np.abs(a - b) <= 1.5 * 10.0 ** -q) | (~np.isfinite(a) & ~np.isfinite(b)))
        if diff[rows].any():
            patch[c] = (np.asarray(v, float)[rows], diff[rows])
            rep["changed"].append(c)
    rep["n_out"] = int(keep.sum())
    if not patch and keep.all():
        return rep
    os.makedirs(os.path.dirname(os.path.abspath(out_shp)), exist_ok=True)
    bc, why = yc.byte_copy_source(shp, g)
    if bc is not None:
        yc.write_points(out_shp, shp, g, rows, [], byte_copy=bc, patch=patch or None)
    else:
        # the raw records are not the features GDAL read (deleted records ...): written from the features instead
        _write_gdal(out_shp, g, rows, patch)
        rep["notes"].append(f"the file could not be copied record by record ({why}): it was written again with GDAL "
                            "(the field definitions may differ from the original)")
    rep["path"] = out_shp
    log(f"import as SMS: {n - rep['n_out']:,} of {n:,} readings left out"
        + (" (" + ", ".join(f"{k} {v:,}" for k, v in rep["left_out"].items()) + ")" if rep["left_out"] else "")
        + (f"; changed {', '.join(rep['changed'])}" if rep["changed"] else ""))
    return rep


def _write_gdal(out_shp, g, rows, patch):
    """The records `rows` of g with the patched cells, through GDAL (+ a UTF-8 .cpg)."""
    noext = os.path.splitext(out_shp)[0]
    for e in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        if os.path.exists(noext + e):
            os.remove(noext + e)
    out = g.iloc[rows].copy()
    for name, (v, mask) in (patch or {}).items():
        col = np.array(pd.to_numeric(out[name], errors="coerce"), dtype=float)      # a writable copy
        m = np.asarray(mask, bool)
        col[m] = np.asarray(v, float)[m]
        out[name] = col
    out.to_file(noext + ".shp", engine="pyogrio", encoding="UTF-8")
