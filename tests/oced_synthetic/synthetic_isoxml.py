# -*- coding: utf-8 -*-
"""Synthetic ISOXML task data (ISO 11783-10) for the tests of engine/formats/isoxml.py: a made-up combine with a
made-up header on a made-up field. Every name, id and number here is invented.

    from tests.oced_synthetic.synthetic_isoxml import harvest, write_set, zip_sets
    R = harvest(n_pass=4, n=40)                 # the records of a made-up harvest (dict of arrays)
    write_set(folder, R)                        # folder/TASKDATA.XML + TLG00001.XML / .BIN
    write_set(folder, R, split=True, idle_header=True, moisture=False, sections=False, xfr=True, ...)

The combine logs once per second; its header has N sections of SECTION_M each, hangs X_CONNECTOR_M in front of the
position and reports the grain LATENCY_S after it was cut. Positions are laid out on the flat earth the reader
measures on (111,120 m per degree), so a step is its metres exactly.
"""
from __future__ import annotations

import io
import os
import struct
import zipfile

import numpy as np
import pandas as pd

LAT0, LON0 = 51.95, -114.05                    # a made-up position
M_PER_DEG = 111_120.0
N_SECTIONS, SECTION_M = 8, 1.0
X_CONNECTOR_M = 3.0
LATENCY_S = 3
SPEED_MS = 2.0
EPOCH = pd.Timestamp("1980-01-01")
SERIAL = "SYN-ISO-0001"
DENSITY_MG_L = 772_312                         # 60 lb/bu as a density (60 x 0.45359237 kg / 0.0352390704 m3)
REF_MOISTURE_PPM = 135_000


def harvest(n_pass: int = 4, n: int = 40, start: str = "2026-09-10 14:00:00", turn: int = 6, seed: int = 4,
            flow_kg_s: float = 6.0) -> dict:
    """The records of a made-up harvest: n_pass east-west passes of n one-second records, the header up for `turn`
    records between them. Arrays per record: t (ms since 1980, the display's clock), lat, lon (deg), on (records x
    sections, bool), flow (mg/s as the sensor measures it: the grain cut LATENCY_S earlier), moist (ppm), work (0 /
    1); every section is on inside a pass except on the first two records of a pass (half the header)."""
    rng = np.random.default_rng(seed)
    t0 = int((pd.Timestamp(start) - EPOCH) / pd.Timedelta(milliseconds=1))
    x, y, on, k = [], [], [], 0
    for p in range(n_pass):
        east = p % 2 == 0
        for i in range(n + turn):
            xi = SPEED_MS * min(i, n - 1)
            x.append(xi if east else SPEED_MS * (n - 1) - xi)
            y.append(p * N_SECTIONS * SECTION_M + (0.0 if i < n else (i - n + 1) * N_SECTIONS * SECTION_M / (turn + 1)))
            row = np.zeros(N_SECTIONS, bool)
            if i < n:
                row[:] = True
                if i < 2:
                    row[N_SECTIONS // 2:] = False              # the left half only
            on.append(row)
            k += 1
    m = len(x)
    x, y, on = np.asarray(x, float), np.asarray(y, float), np.asarray(on)
    cut = on.any(1)
    # the grain is measured LATENCY_S after it was cut
    cut_then = np.r_[np.zeros(LATENCY_S, bool), cut[:-LATENCY_S]]
    flow = np.where(cut_then, flow_kg_s * 1e6 * (1 + 0.05 * rng.standard_normal(m)), 0.0)
    moist = np.rint(128_000 + 4_000 * rng.standard_normal(m))
    # positions on the 1e-7 degree grid a time log stores them on
    lat = np.rint((LAT0 + y / M_PER_DEG) * 1e7) / 1e7
    lon = np.rint((LON0 + x / (M_PER_DEG * np.cos(np.radians(LAT0)))) * 1e7) / 1e7
    return {"t": t0 + 1000 * np.arange(m, dtype=np.int64), "lat": lat, "lon": lon, "on": on, "flow": np.rint(flow),
            "moist": moist, "work": cut.astype(int)}


def take(R: dict, a: int, b: int) -> dict:
    """Records a..b-1 of R (a slice of the same log)."""
    return {k: v[a:b] for k, v in R.items()}


def _condensed(on: np.ndarray, group: int) -> np.ndarray:
    v = np.zeros(len(on), np.int64)
    for b in range(16):
        s = 16 * group + b
        if s < on.shape[1]:
            v |= on[:, s].astype(np.int64) << (2 * b)
    return v


def _harvest_device(dvc: int, det0: int, n_sections: int) -> tuple:
    """(XML of one harvest device, ids {totals, connector, header, sections})."""
    ids = {"totals": f"DET-{det0}", "connector": f"DET-{det0 + 1}", "header": f"DET-{det0 + 2}",
           "sections": [f"DET-{det0 + 10 + j}" for j in range(n_sections)]}
    L = [f'<DVC A="DVC-{dvc}" B="Synthetic harvest data" C="1.0" D="A00E840000000001" E="{SERIAL}" F="0102030405"'
         ' G="FF000000006E65">',
         f'<DET A="{ids["totals"]}" B="1" C="1" D="Synthetic totals" E="0" F="0"><DOR A="1000"/></DET>',
         f'<DET A="{ids["connector"]}" B="2" C="6" D="Synthetic connector" E="1" F="1"><DOR A="1001"/></DET>',
         f'<DET A="{ids["header"]}" B="3" C="2" D="Synthetic header" E="2" F="1"><DOR A="1002"/><DOR A="1003"/>'
         '<DOR A="1004"/><DOR A="1005"/></DET>']
    for j, s in enumerate(ids["sections"]):
        L.append(f'<DET A="{s}" B="{10 + j}" C="4" D="Synthetic section {j}" E="{3 + j}" F="3"><DOR A="1001"/></DET>')
    L += ['<DPD A="1000" B="008D" C="1" D="9" E="Work state"/>',
          '<DPD A="1001" B="0087" C="1" D="8" E="Y offset" F="2001"/>',
          '<DPD A="1002" B="0057" C="1" D="9" E="Synthetic wet flow" F="2000"/>',
          '<DPD A="1003" B="0063" C="1" D="9" E="Synthetic moisture" F="2002"/>',
          '<DPD A="1004" B="0046" C="1" D="8" E="Synthetic width" F="2001"/>',
          '<DPD A="1005" B="008F" C="1" D="9" E="Synthetic delay"/>',
          '<DVP A="2000" B="0" C="0.0000036" D="1" E="t/h"/>', '<DVP A="2001" B="0" C="0.1" D="1" E="cm"/>',
          '<DVP A="2002" B="0" C="0.0001" D="1" E="%"/>', "</DVC>"]
    return "\n".join(L), ids


def _stamp(ms: int, tz: str = "") -> str:
    return (EPOCH + pd.Timedelta(milliseconds=int(ms))).strftime("%Y-%m-%dT%H:%M:%S") + tz


def write_set(folder: str, R: dict, *, tlg: str = "TLG00001", field: str = "Synthetic field",
              task: str = "Synthetic harvest", product: str = "Synthetic product", crop_attr=("P094_Crop_Type", 48),
              crop_type: str | None = None, moisture: bool = True, sections: bool = True, split: bool = False,
              idle_header: bool = False, gps_offset_h: float | None = 6.0, latency_s: int = LATENCY_S,
              xfr: bool = False, seeding: bool = False, connection: bool = True, density: bool = True,
              write_bin: bool = True, section_y_mm: list | None = None) -> dict:
    """One TASKDATA folder with one harvest task of the records R (R may also hold "up": the altitude of every
    record in mm; without it the log carries none). Options: crop_attr (a maker's crop code on the
    task, None for none) / crop_type (a CTP of the field); moisture False = the header logs none; sections False =
    a header without sections (actual width DDI 0043 and the work state instead of section states); split True = the
    log in two files with the same time stamps (the grain flow and the moisture in the second: a header lists at
    most 255 values); idle_header True = a second harvest device whose header never works; gps_offset_h = GNSS UTC
    time in the records, that many hours ahead of the display's clock (None: no GNSS time); xfr True = the devices
    and the task in external files; seeding True = also a seeding task with its own log (no yield value);
    connection False = no CNN element; density False = no density and reference moisture logged; write_bin False
    = the .BIN left out; section_y_mm = the Y offsets the display logs for its sections (default: the centres of
    the sections side by side, centred on the header). Returns {"folder", "ids", "parts" (the names of the log's
    files)}."""
    os.makedirs(folder, exist_ok=True)
    n = len(R["t"])
    N = R["on"].shape[1]
    hv, ids = _harvest_device(2, 10, N)
    vehicle = "\n".join([
        f'<DVC A="DVC-1" B="Synthetic vehicle" C="1.0" D="A00E840000000002" E="{SERIAL}" F="0102030406"'
        ' G="FF000000006E65">',
        '<DET A="DET-1" B="1" C="1" D="Synthetic vehicle" E="0" F="0"/>',
        '<DET A="DET-2" B="2" C="7" D="Synthetic position" E="1" F="1"><DOR A="1000"/></DET>',
        '<DET A="DET-3" B="3" C="6" D="Synthetic front connector" E="2" F="1"><DOR A="1000"/></DET>',
        '<DPD A="1000" B="0086" C="1" D="8" E="X offset"/>', "</DVC>"])
    devices = [vehicle, hv]
    ids2 = None
    if idle_header:
        hv2, ids2 = _harvest_device(3, 110, N)
        devices.append(hv2)
    # ---- the logged values: (ddi, element, values per record or one value, every record or on change)
    y_sec = [int(round(((j + 0.5) - N / 2) * SECTION_M * 1000)) for j in range(N)]
    if section_y_mm is not None:
        y_sec = [int(v) for v in section_y_mm]
    vals = [("008F", ids["header"], -1000 * latency_s, False),
            ("0046", ids["header"], int(N * SECTION_M * 1000), False),
            ("0086", "DET-3", int(X_CONNECTOR_M * 1000), False), ("0086", "DET-2", 0, False),
            ("0087", ids["connector"], 0, False), ("008D", ids["totals"], R["work"], False)]
    if density:
        vals += [("0079", ids["header"], DENSITY_MG_L, False), ("00B8", ids["header"], REF_MOISTURE_PPM, False)]
    if sections:
        vals += [(f"{0xA1 + g:04X}", ids["header"], _condensed(R["on"], g), False) for g in range((N + 15) // 16)]
        vals += [("0087", s, y, False) for s, y in zip(ids["sections"], y_sec)]
    else:
        vals.append(("0043", ids["header"], (R["on"].sum(1) * SECTION_M * 1000).astype(np.int64), False))
    grain = [("0057", ids["header"], R["flow"], True)]
    if moisture:
        grain.append(("0063", ids["header"], R["moist"], True))
    if ids2 is not None:
        vals += [("0046", ids2["header"], int(N * SECTION_M * 1000), False), ("00A1", ids2["header"], 0, False)]
        grain.append(("0057", ids2["header"], 0, True))
    parts = [(tlg, vals, grain)] if not split else [(tlg, vals, []), (tlg[:-1] + str(int(tlg[-1]) + 1), [], grain)]
    for name, static, every in parts:
        dl = static + every
        head = ['<?xml version="1.0" encoding="UTF-8"?>', '<TIM A="" D="4">',
                '<PTN A="" B="" C="" D="1"' + (' H="" I=""' if gps_offset_h is not None else "") + "/>"]
        head += [f'<DLV A="{ddi}" B="" C="{el}"/>' for ddi, el, _, _ in dl]
        head.append("</TIM>")
        with open(os.path.join(folder, name + ".XML"), "w", encoding="utf-8") as f:
            f.write("\n".join(head) + "\n")
        if not write_bin:
            continue
        cols = [np.broadcast_to(np.asarray(v, np.int64), (n,)) for _, _, v, _ in dl]
        out = io.BytesIO()
        for i in range(n):
            day, ms = divmod(int(R["t"][i]), 86_400_000)
            out.write(struct.pack("<IH", ms, day))
            out.write(struct.pack("<iii", int(round(R["lat"][i] * 1e7)), int(round(R["lon"][i] * 1e7)),
                                  int(R["up"][i]) if "up" in R else 0x7FFFFFFF))
            if gps_offset_h is not None:
                gd, gm = divmod(int(R["t"][i]) + int(gps_offset_h * 3_600_000), 86_400_000)
                out.write(struct.pack("<IH", gm, gd))
            rec = [(j, int(c[i])) for j, (c, d) in enumerate(zip(cols, dl))
                   if d[3] or i == 0 or c[i] != c[i - 1]]
            out.write(struct.pack("<B", len(rec)))
            for j, v in rec:
                out.write(struct.pack("<Bi", j, v))
        with open(os.path.join(folder, name + ".BIN"), "wb") as f:
            f.write(out.getvalue())
    # ---- TASKDATA.XML
    attr = f' {crop_attr[0]}="{crop_attr[1]}"' if crop_attr else ""
    tsk = [f'<TSK A="TSK-1" B="{task}" C="CTR-1" D="FRM-1" E="PFD-1" G="2"{attr}>']
    if connection:
        tsk.append(f'<CNN A="DVC-1" B="DET-3" C="DVC-2" D="{ids["connector"]}"/>')
    tsk += [f'<PAN A="PDT-1" E="{ids["header"]}"><ASP A="{_stamp(R["t"][0], "-06:00")}" D="4"/></PAN>',
            f'<TIM A="{_stamp(R["t"][-1], "-06:00")}" D="4"><DLV A="005A" B="1234" C="{ids["totals"]}"/></TIM>']
    tsk += [f'<TLG A="{name}" C="1"/>' for name, _, _ in parts]
    tsk.append("</TSK>")
    extra = []
    if seeding:
        # a seeding task of another device: an application rate, no yield value
        devices.append('<DVC A="DVC-9" B="Synthetic seeder" C="1.0" D="A00E840000000009" E="SYN-SEED-1" F="01"'
                       ' G="FF000000006E65">\n<DET A="DET-900" B="1" C="1" D="Synthetic seeder" E="0" F="0"/>\n</DVC>')
        with open(os.path.join(folder, "TLG00009.XML"), "w", encoding="utf-8") as f:
            f.write('<TIM A="" D="4">\n<PTN A="" B="" D="1"/>\n<DLV A="0006" B="" C="DET-900"/>\n</TIM>\n')
        with open(os.path.join(folder, "TLG00009.BIN"), "wb") as f:
            for i in range(20):
                day, ms = divmod(int(R["t"][0]) + 1000 * i, 86_400_000)
                f.write(struct.pack("<IHiiBBi", ms, day, int(LAT0 * 1e7), int(LON0 * 1e7), 1, 0, 150_000))
        extra = ['<TSK A="TSK-2" B="Synthetic seeding" C="CTR-1" D="FRM-1" E="PFD-1" G="4">',
                 '<TLG A="TLG00009" C="1"/>', "</TSK>"]
    coding = ['<CTR A="CTR-1" B="Synthetic grower"/>', '<FRM A="FRM-1" B="Synthetic farm" I="CTR-1"/>',
              f'<PDT A="PDT-1" B="{product}"/>']
    if crop_type:
        coding.append(f'<CTP A="CTP-1" B="{crop_type}"/>')
    coding.append(f'<PFD A="PFD-1" C="{field}" D="0" E="CTR-1" F="FRM-1"' + (' G="CTP-1"' if crop_type else "") + "/>")
    root_open = ('<?xml version="1.0" encoding="UTF-8"?>\n<ISO11783_TaskData VersionMajor="4" VersionMinor="2" '
                 'ManagementSoftwareManufacturer="Synthetic" ManagementSoftwareVersion="1" '
                 'TaskControllerManufacturer="Synthetic" TaskControllerVersion="1" DataTransferOrigin="2">')
    if xfr:
        for name, body in (("DVC00000", devices), ("TSK00000", tsk + extra)):
            with open(os.path.join(folder, name + ".XML"), "w", encoding="utf-8") as f:
                f.write('<?xml version="1.0" encoding="UTF-8"?>\n<XFC>\n' + "\n".join(body) + "\n</XFC>\n")
        body = coding + ['<XFR A="DVC00000" B="1"/>', '<XFR A="TSK00000" B="1"/>']
    else:
        body = coding + devices + tsk + extra
    with open(os.path.join(folder, "TASKDATA.XML"), "w", encoding="utf-8") as f:
        f.write(root_open + "\n" + "\n".join(body) + "\n</ISO11783_TaskData>\n")
    return {"folder": folder, "ids": ids, "parts": [p[0] for p in parts]}


def zip_sets(folders: list, zpath: str) -> str:
    """A zip of zipped task data sets, as a telematics portal hands a task out: <zpath> holds one
    TASKDATA-<k>.zip per folder, each with the folder's files under TASKDATA/."""
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for k, d in enumerate(folders):
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as zi:
                for f in sorted(os.listdir(d)):
                    zi.write(os.path.join(d, f), "TASKDATA/" + f)
            z.writestr(f"TASKDATA-0000000000-2026091014{k:04d}.zip", inner.getvalue())
    return zpath


def boundary(folder: str, n_pass: int = 4, n: int = 40, cut_m: float = 10.0) -> str:
    """A boundary shapefile around the made-up field that stops cut_m before the east end of the passes."""
    import geopandas as gpd
    from shapely.geometry import Polygon
    x0, x1 = -5.0, SPEED_MS * (n - 1) + X_CONNECTOR_M - cut_m
    y0, y1 = -N_SECTIONS * SECTION_M, n_pass * N_SECTIONS * SECTION_M
    k = M_PER_DEG * np.cos(np.radians(LAT0))
    poly = Polygon([(LON0 + x / k, LAT0 + y / M_PER_DEG) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))])
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "Synthetic_boundary.shp")
    gpd.GeoDataFrame({"Name": ["Synthetic field"]}, geometry=[poly], crs="EPSG:4326").to_file(path)
    return path
