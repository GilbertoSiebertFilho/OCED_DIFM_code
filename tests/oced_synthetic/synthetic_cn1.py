# -*- coding: utf-8 -*-
"""A synthetic Case IH / New Holland AFS Pro 700 card (.cn1) for the tests: every name and number is made up.

    from tests.oced_synthetic.synthetic_cn1 import make_card
    C = make_card(folder)       # {"root", "card", "tasks": {task id: n records}, "vehicle"}

One combine, one grower / farm / field, two harvest tasks of east-west passes 9 m apart with a 30 ft header at about
2 m/s, one record per second: task SYNTSK01 (300 records, the first five with the header up, one invalid flow
sample) and task SYNTSK02 (200 records, the display restarts after 120 of them and goes back 20 counters). The
records carry the 8-bit header checksum of the format (engine.formats.cnh_cn1_write.checksum).
make_card(folder, upper=True) writes every name inside the card folder in upper case (INDEX.VY1, SHARED/...FMD/
...FLD/*.TLO, COMBINES/...), as a newer display software does.
"""
from __future__ import annotations

import os
import struct

import numpy as np

LAT0, LON0 = 51.80, -114.10            # a made-up position
OLAT, OLON = 51, -114                  # the task's integer-degree origin
M_PER_DEG_LAT = 111_320.0
VEHICLE = "SYNVH0000001"
CROP = 48                              # crop number; its table entry is a 60 lb/bu, 13.5 % crop (a wheat guess)
CURVE = [1000, 1500, 2000, 2500, 3000, 3500, 4000, 4500, 5000, 5500, 6000]
COMP = 75
KIND = {".vy1": (0x00, 1), ".gws": (0x25, 2), ".fms": (0x0A, 2), ".fls": (0x08, 3), ".ycs": (0x01, 5),
        ".ycc": (0x0D, 3), ".tso": (0x14, 4), ".tsh": (0x09, 5), ".tst": (0xF0, 3), ".tlo": (0x22, 2),
        ".tlh": (0x0E, 2), ".tlt": (0xF1, 1)}


def record(ext: str, rid: bytes, parent: bytes, t6, payload: bytes) -> bytes:
    """One CNH1 record with its checksum: magic, length, [8] checksum, [9] file-type constant, [10] type, id, parent,
    time (year - 1970, month, day, hour, minute, second), payload."""
    k9, typ = KIND[ext]
    h = bytearray(b"CNH1" + struct.pack("<I", len(payload)) + bytes([0, k9, typ]) + rid.ljust(8, b"\0")[:8]
                  + parent.ljust(8, b"\0")[:8] + bytes(t6))
    rec = h + payload
    rec[8] = (rec[9] + sum(rec[10:])) & 0xFF
    return bytes(rec)


def _t6(ts):
    return [ts.year - 1970, ts.month, ts.day, ts.hour, ts.minute, ts.second]


def _i32(v):
    return struct.pack("<i", int(round(v)))


def _flow(hA, M):
    u = hA / 1000.0 * (1 + COMP / 1e4 * (14.0 - M))
    i = min(int(hA // 1000), 9)
    x = CURVE[i] + (u - i) * (CURVE[i + 1] - CURVE[i])
    return max(0.0, (x - 1000) / 100.0)


def _task(folder, task, field_id, start, n, counters, seed):
    import pandas as pd
    rng = np.random.default_rng(seed)
    base = os.path.join(folder, task)
    tid = task.encode()
    m_per_deg_lon = M_PER_DEG_LAT * np.cos(np.radians(LAT0))
    tlo, tlh, tlt = [], [], []
    per_pass = 150
    n_log = n_off = 0
    area = dist_cm = 0.0
    secs, cnts = [0] * 10, [0] * 10
    kg = msum = 0.0
    for k in range(n):
        p, j = divmod(k, per_pass)
        east = p % 2 == 0
        x = (j if east else per_pass - 1 - j) * 2.0
        y = p * 9.0 + (seed % 3) * 200.0
        ts = start + pd.Timedelta(seconds=k)
        lat, lon = LAT0 + y / M_PER_DEG_LAT, LON0 + x / m_per_deg_lon
        half = 4.572 / M_PER_DEG_LAT
        logging = 0 if k < 5 else 1
        dist = int(2000 + rng.integers(-150, 150))
        width = 9144 if k % 37 else 6096
        hA = int(2500 + 1200 * np.sin(k / 15.0) + rng.integers(-200, 200))
        hB = int(140 + rng.integers(-8, 8))
        flow_bad = k == 50
        o = bytearray(41)
        o[0] = logging
        o[1] = 10
        o[2:4] = struct.pack("<H", dist)
        o[4:6] = struct.pack("<H", width)
        o[6:14] = _i32((lat - OLAT) * 1e8 + 30) + _i32((lon - OLON) * 1e8 - 20)
        o[14:18] = _i32(1040.0 * 1000 + k)
        o[18] = 4
        o[19:23] = _i32((ts.hour * 3600 + ts.minute * 60 + ts.second + 6 * 3600) * 1000 % 86_400_000)
        o[23:25] = struct.pack("<H", 900 if east else 2700)
        o[25:33] = _i32((lat + half - OLAT) * 1e8) + _i32((lon - OLON) * 1e8)
        o[33:41] = _i32((lat - half - OLAT) * 1e8) + _i32((lon - OLON) * 1e8)
        h = bytearray(17)
        h[1:3] = struct.pack("<H", hA)
        h[5] = hB
        h[11] = 255 if flow_bad else 0
        t = bytes(range(14))
        rid = struct.pack("<I", counters[k]) + b"\0\0\0\0"
        tlo.append(record(".tlo", rid, tid, _t6(ts), bytes(o)))
        tlh.append(record(".tlh", rid, tid, _t6(ts), bytes(h)))
        tlt.append(record(".tlt", rid, tid, _t6(ts), t))
        if logging:
            n_log += 1
            area += dist * width / 1e6 * 100
            dist_cm += dist / 10
            if not flow_bad:
                seg = min(hA // 1000, 9)
                secs[seg] += 1
                cnts[seg] += hA
                f = _flow(hA, hB / 10.0)
                kg += f / 2.20462262185
                msum += f / 2.20462262185 * hB / 10.0
        else:
            n_off += 1
    end = start + pd.Timedelta(seconds=n)
    for ext, recs in ((".tlo", tlo), (".tlh", tlh), (".tlt", tlt)):
        with open(base + ext, "wb") as fh:
            fh.write(b"".join(recs))
    so = bytearray(199)
    so[0:19] = start.strftime("%y/%m/%d-%H:%M:%S").encode().ljust(19, b"\0")
    so[19:25] = bytes(_t6(start))
    so[33:43] = b"Grain Harv"
    so[52] = CROP
    so[53:65] = VEHICLE.encode()
    so[120:132] = b"SYNSEAS02026"
    struct.pack_into("<hh", so, 159, OLAT, OLON)
    struct.pack_into("<IIII", so, 163, n_log, n_off, n_log - 1, 123)
    struct.pack_into("<II", so, 183, int(dist_cm), 4567)
    struct.pack_into("<I", so, 195, int(area))
    sh = bytearray(209)
    sh[4] = 6
    struct.pack_into("<11H", sh, 5, *CURVE)
    struct.pack_into("<H", sh, 27, COMP)
    struct.pack_into("<10H", sh, 29, *secs)
    struct.pack_into("<10I", sh, 49, *cnts)
    sh[95] = 100
    struct.pack_into("<II", sh, 96, int(kg), int(msum))
    st = bytes(89)
    for ext, pl in ((".tso", so), (".tsh", sh), (".tst", st)):
        with open(base + ext, "wb") as fh:
            fh.write(record(ext, tid, field_id, _t6(end), bytes(pl)))
    return n


def _upper_inside(card: str) -> None:
    """Rename every file and folder inside the card folder to upper case (the card folder keeps its name)."""
    for d, dirs, files in os.walk(card, topdown=False):
        for x in files + dirs:
            if x != x.upper():
                os.rename(os.path.join(d, x), os.path.join(d, x.upper()))


def make_card(folder: str, name: str = "SYN0001A", upper: bool = False) -> dict:
    import pandas as pd
    card = os.path.join(folder, name + ".cn1")
    veh = VEHICLE[:5]
    t0 = (126 - 70, 9, 1, 8, 0, 0)
    os.makedirs(os.path.join(card, "COMBINES", veh), exist_ok=True)
    fld = os.path.join(card, "shared", "SYNFARM1.fmd", "SYNFLD01.fld")
    os.makedirs(fld, exist_ok=True)
    with open(os.path.join(card, "index.vy1"), "wb") as fh:
        fh.write(record(".vy1", b"", b"", t0, b"\0" + veh.encode()))
    ycs = bytearray(244)
    struct.pack_into("<H", ycs, 2, 600)
    ycs[4], ycs[6] = 135, 135
    ycc = bytearray(65)
    struct.pack_into("<11H", ycc, 0, *CURVE)
    ycc[46] = COMP
    with open(os.path.join(card, "COMBINES", veh, veh + ".ycs"), "wb") as fh:
        fh.write(record(".ycs", struct.pack("<I", CROP), b"", t0, bytes(ycs)))
    with open(os.path.join(card, "COMBINES", veh, veh + ".ycc"), "wb") as fh:
        fh.write(record(".ycc", struct.pack("<I", CROP), b"", t0, bytes(ycc)))
    with open(os.path.join(card, "shared", "growers.gws"), "wb") as fh:
        fh.write(record(".gws", b"SYNGRW01", b"", t0, b"Grower A".ljust(18, b"\0")))
    with open(os.path.join(card, "shared", "SYNFARM1.fms"), "wb") as fh:
        fh.write(record(".fms", b"SYNFARM1", b"SYNGRW01", t0, b"Farm A".ljust(44, b"\0")))
    with open(os.path.join(card, "shared", "SYNFARM1.fmd", "SYNFLD01.fls"), "wb") as fh:
        fh.write(record(".fls", b"SYNFLD01", b"SYNFARM1", t0, b"Field 1".ljust(22, b"\0")))
    with open(os.path.join(fld, "SYNGUIDE.ags"), "wb") as fh:
        fh.write(record(".fls", b"SYNGUIDE", b"SYNFLD01", t0, b"guidance".ljust(30, b"\0")))
    tasks = {}
    tasks["SYNTSK01"] = _task(fld, "SYNTSK01", b"SYNFLD01", pd.Timestamp("2026-09-14 13:00:00"), 300,
                              list(range(1000, 1300)), seed=1)
    ctr = list(range(5000, 5120)) + list(range(5100, 5180))          # a display restart after 120 records
    tasks["SYNTSK02"] = _task(fld, "SYNTSK02", b"SYNFLD01", pd.Timestamp("2026-09-14 14:00:00"), 200, ctr, seed=2)
    if upper:
        _upper_inside(card)
    return {"root": folder, "card": card, "tasks": tasks, "vehicle": VEHICLE, "upper": bool(upper),
            "field_dir": os.path.join(card, *(("SHARED", "SYNFARM1.FMD", "SYNFLD01.FLD") if upper else
                                              ("shared", "SYNFARM1.fmd", "SYNFLD01.fld")))}
