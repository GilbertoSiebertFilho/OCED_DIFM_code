# -*- coding: utf-8 -*-
"""A yield POINT export saved as KML / KMZ (Ag Leader SMS "Export to Google Earth": one Placemark per logged reading,
its attributes as ExtendedData) -> the point shapefile the cleaning reads.

    from oced_clean._engine.formats import kml_points
    if kml_points.looks_like_points(path):
        shp, n, notes = kml_points.to_shapefile(path, out_dir)

The attribute names are SMS's long names ("Yld Vol(Dry)(bu/ac)", "Swth Wdth(ft)", "Obj. Id"). SMS writes the same
fields to a shapefile by replacing every character other than a letter or a digit with '_' and cutting the name to
10 characters (Yld_Vol_Dr, Swth_Wdth_, Obj__Id, Moisture__, Distance_f, Track_deg_), so the converted file has the
column names of an SMS shapefile export and the column detection of the cleaning applies unchanged. The field types
follow the SMS shapefile export too: a column whose
values are all ISO dates (Time, Date: SMS writes the date only) is a dBASE date, a column whose values are all plain
decimal numbers is numeric with as many decimals as the KML gives (SMS writes 6), anything else is text. The values
are written as the KML holds them; the coordinates are WGS 84 longitude, latitude (the .prj SMS writes).

The file is read as a stream (memory stays well below the size of the KML). Placemarks
without a Point (lines, polygons, a legend) are skipped and counted. A KML that ends early or is not valid XML (an
interrupted download) raises ValueError: a partial export would give a wrong field total.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import struct
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import pandas as pd

PEEK_BYTES = 4 * 1024 ** 2               # looks_like_points reads at most this much of the KML
NAME_LEN = 10                            # dBASE field names: SMS cuts its names to 10 characters
TEXT_MAX = 254                           # dBASE text cells
NUM_MAX = 24                             # widest numeric cell (GDAL writes doubles as N(24,15))
MAX_FIELDS = 2046                        # the dBASE header length is 16 bits
WGS84_PRJ = ('GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
             'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]')
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUM = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)")


def _local(tag) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _open_kml(path):
    """A binary file object of the KML: the file itself, or the first .kml inside a .kmz (doc.kml by convention)."""
    if path.lower().endswith(".kmz"):
        z = zipfile.ZipFile(path)
        names = [n for n in z.namelist() if n.lower().endswith(".kml")]
        if not names:
            z.close()
            raise ValueError(f"{os.path.basename(path)}: no .kml inside the .kmz")
        return z.open(sorted(names, key=lambda n: (n.count("/"), n.lower() != "doc.kml", n))[0])
    return open(path, "rb")


def looks_like_points(path) -> bool:
    """Cheap check (the first PEEK_BYTES): placemarks with a Point and attributes (ExtendedData). A boundary KML
    (polygons, no attributes on points) gives False."""
    try:
        with _open_kml(path) as f:
            head = f.read(PEEK_BYTES)
    except (OSError, ValueError, zipfile.BadZipFile):
        return False
    return b"<Placemark" in head and b"<Point" in head and (b"<SimpleData" in head or b"<Data " in head)


def sms_name(name: str) -> str:
    """SMS's shapefile field name of an attribute: every character other than a letter or digit -> '_', then the
    first 10 characters ('Yld Vol(Dry)(bu/ac)' -> 'Yld_Vol_Dr', 'Obj. Id' -> 'Obj__Id')."""
    return re.sub(r"[^0-9A-Za-z]", "_", str(name))[:NAME_LEN] or "field"


def _unique(names):
    out, seen = [], set()
    for n in names:
        m, k = n, 1
        while m.lower() in seen:
            suffix = str(k)
            m, k = n[:NAME_LEN - len(suffix)] + suffix, k + 1
        seen.add(m.lower())
        out.append(m)
    return out


def read_points(path, log=print) -> tuple[pd.DataFrame, list]:
    """(DataFrame, notes) of every Point placemark of the KML: one column per attribute name with the text the KML
    holds (None where a placemark lacks it), plus lon and lat (float)."""
    cols, lon, lat = {}, [], []
    n, skipped = 0, 0
    try:
        with _open_kml(path) as f:
            for _, el in ET.iterparse(f, events=("end",)):
                if _local(el.tag) != "Placemark":
                    continue
                coords, attrs = None, {}
                for sub in el.iter():
                    t = _local(sub.tag)
                    if t == "SimpleData":
                        attrs[sub.get("name")] = sub.text
                    elif t == "Data":
                        attrs[sub.get("name")] = next((c.text for c in sub if _local(c.tag) == "value"), None)
                    elif t == "Point" and coords is None:
                        coords = next((c.text for c in sub.iter() if _local(c.tag) == "coordinates"), None)
                el.clear()
                try:
                    x, y = (float(v) for v in coords.strip().split(",")[:2])
                except (AttributeError, ValueError):
                    skipped += 1                             # no Point (a line, a polygon) or no usable position
                    continue
                for k in attrs:
                    if k not in cols:
                        cols[k] = [None] * n                 # an attribute first seen late: earlier rows empty
                for k, v in cols.items():
                    v.append(attrs.get(k))
                lon.append(x)
                lat.append(y)
                n += 1
                if n % 25000 == 0:
                    log(f"  {n:,} points read")
    except ET.ParseError as e:
        raise ValueError(f"{os.path.basename(path)}: the KML could not be read after {n:,} points ({e}); the file is "
                         "damaged or incomplete (an interrupted download?) - export it again") from None
    notes = [f"{os.path.basename(path)}: {skipped:,} placemark(s) without a point position skipped"] if skipped else []
    df = pd.DataFrame({k: pd.Series(v, dtype=object) for k, v in cols.items()})      # None stays None (no NaN)
    df["lon"], df["lat"] = np.asarray(lon, float), np.asarray(lat, float)
    return df, notes


def _field(values):
    """(dBASE type, width, decimals, cells as bytes) of one column of KML text: D when every given value is an ISO
    date, N when every given value is a plain decimal number (as many decimals as the KML gives), else C (text)."""
    s = ["" if v is None or (isinstance(v, float) and v != v) else str(v).strip() for v in values]
    given = [v for v in s if v]
    if given and all(_DATE.fullmatch(v) for v in given):
        return "D", 8, 0, [v.replace("-", "").encode() for v in s]
    if given and all(_NUM.fullmatch(v) for v in given):
        dec = min(max(len(v.partition(".")[2]) for v in given), 15)
        cells = [f"{float(v):.{dec}f}".encode() if v else b"" for v in s]
        width = max(len(c) for c in cells)
        if width <= NUM_MAX:
            return "N", width, dec, cells
    cells = [v.encode("utf-8")[:TEXT_MAX].decode("utf-8", "ignore").encode("utf-8") for v in s]   # whole characters
    return "C", max([len(c) for c in cells] + [1]), 0, cells


def _write_shapefile(noext, lon, lat, names, fields):
    """noext.shp/.shx/.dbf/.prj/.cpg: points (WGS 84) with the dBASE columns `fields` [(type, width, dec, cells)]."""
    n = len(lon)
    bbox = (float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max()))

    def header(words):
        return struct.pack(">7i", 9994, 0, 0, 0, 0, 0, words) + struct.pack("<2i8d", 1000, 1, *bbox, 0, 0, 0, 0)

    rec = np.zeros(n, dtype=[("num", ">i4"), ("len", ">i4"), ("type", "<i4"), ("x", "<f8"), ("y", "<f8")])
    rec["num"], rec["len"], rec["type"], rec["x"], rec["y"] = np.arange(1, n + 1), 10, 1, lon, lat
    idx = np.zeros(n, dtype=[("off", ">i4"), ("len", ">i4")])
    idx["off"], idx["len"] = 50 + 14 * np.arange(n), 10
    with open(noext + ".shp", "wb") as f:
        f.write(header(50 + 14 * n) + rec.tobytes())
    with open(noext + ".shx", "wb") as f:
        f.write(header(50 + 4 * n) + idx.tobytes())
    rlen = 1 + sum(w for _, w, _, _ in fields)
    if rlen > 65535:
        raise ValueError(f"{len(fields)} columns of {rlen:,} bytes per record: too wide for a shapefile")
    body = np.full((n, rlen), ord(" "), np.uint8)
    at, desc = 1, []
    for name, (typ, w, dec, cells) in zip(names, fields):
        pad = (lambda c: c.rjust(w)) if typ == "N" else (lambda c: c.ljust(w))
        body[:, at:at + w] = np.frombuffer(b"".join(pad(c) for c in cells), np.uint8).reshape(n, w)
        desc.append(name.encode("ascii").ljust(11, b"\0") + typ.encode() + b"\0" * 4 + bytes([w, dec]) + b"\0" * 14)
        at += w
    today = dt.date.today()
    with open(noext + ".dbf", "wb") as f:
        f.write(struct.pack("<4BIHH20x", 3, today.year - 1900, today.month, today.day, n, 32 + 32 * len(fields) + 1,
                            rlen))
        f.write(b"".join(desc) + b"\r" + body.tobytes() + b"\x1a")
    with open(noext + ".prj", "w") as f:
        f.write(WGS84_PRJ)
    with open(noext + ".cpg", "w") as f:
        f.write("UTF-8")


def to_shapefile(path, out_dir, log=print) -> tuple[str, int, list]:
    """Convert a KML / KMZ of yield points to out_dir/<stem>.shp (WGS 84, SMS field names and types). Returns (shp
    path, number of points, notes). Raises ValueError for a damaged KML or one without a point placemark."""
    log(f"reading the KML points of {os.path.basename(path)} ...")
    df, notes = read_points(path, log=log)
    if not len(df):
        raise ValueError(f"{os.path.basename(path)}: no point placemark")
    attrs = [c for c in df.columns if c not in ("lon", "lat")]
    if len(attrs) > MAX_FIELDS:
        raise ValueError(f"{os.path.basename(path)}: {len(attrs):,} attributes, more than a shapefile holds")
    names = _unique([sms_name(c) for c in attrs])
    fields = [_field(df[c].tolist()) for c in attrs]
    os.makedirs(out_dir, exist_ok=True)
    stem = re.sub(r"[^0-9A-Za-z_-]+", "_", os.path.splitext(os.path.basename(path))[0]).strip("_") or "kml_points"
    noext = os.path.join(out_dir, stem)
    _write_shapefile(noext, df["lon"].to_numpy(), df["lat"].to_numpy(), names, fields)
    renamed = [f"'{a}' -> {b}" for a, b in zip(attrs, names) if a != b]
    how = (f"{len(renamed)} of {len(attrs)} names shortened as SMS does, e.g. {'; '.join(renamed[:3])}" if renamed else
           f"the {len(attrs)} names kept")
    notes.append(f"{os.path.basename(path)}: {len(df):,} points converted from KML to {stem}.shp with the SMS shapefile "
                 f"field names ({how})")
    return noext + ".shp", len(df), notes
