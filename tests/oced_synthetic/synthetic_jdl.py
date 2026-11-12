# -*- coding: utf-8 -*-
"""A synthetic John Deere Gen4 log card (JD-Data/log/<year>/<guid>.jdl + version.txt) for the tests: one combine, two
passes of a 9 m header (6 sections of 1.5 m in one work-state group) joined by a header-up turn, split over two log
files whose second one carries the streams of the first (anchors). Made-up names and positions only.

Options (make_card): width2 - a second DDI 12001 channel (another element id) with one sample of the full width in
the first pass, as some displays log; state_times / states - the times (s after the start) of the section changes
and the states (default: on, off at the turn, on again); layout2 - the second file defines another header layout
(3 sections of 3 m in a work-state element of its own, as when another header profile is chosen on the display) and
starts a new session (no carry of the section states); counts - the definitions carry fields 7 / 8 / 9 (sum of the
block durations, number of blocks, GNSS samples); width2_s - the time of the second width channel's sample;
states2=False - the second file logs no section states (its width is then the logged width DDI 12001)."""
from __future__ import annotations

import os
import struct
import uuid

import numpy as np

from tests.oced_synthetic.jd_jdl_write import _enc_varint, _key, _ld, _packed_u, _packed_zz

VIN = "1SYNTH000TEST0001"
REF_MS = 1_780_000_000_000
LAT0, LON0 = 52.0, -113.0
M_PER_DEG_LAT = 111_250.0
M_PER_DEG_LON = 68_500.0
IDS = {"gnss": 11, "header": 12, "cws": 13, "flow": 21, "moist": 22, "width": 23, "tw": 24, "width2": 25,
       "cws2": 14}
SECTIONS = [(31 + k, 1.5, -3750.0 + 1500.0 * k) for k in range(6)]          # id, width m, y mm (left to right)
SECTIONS2 = [(41 + k, 3.0, -3000.0 + 3000.0 * k) for k in range(3)]


def _d(f, v):
    return _key(f, 1) + struct.pack("<d", float(v))


def _v(f, v):
    return _key(f, 0) + _enc_varint(v)


def _s(f, text):
    return _ld(f, text.encode())


def _name(f, text):
    return _ld(f, _ld(1, _s(2, text)))


def definitions(guid, ref_ms, prev=None, nxt=None, width2=False, layout2=False, counts=None) -> bytes:
    sections, cws = (SECTIONS2, IDS["cws2"]) if layout2 else (SECTIONS, IDS["cws"])
    secs = b"".join(_ld(4, _v(1, sid) + _d(2, w) + _ld(3, _d(2, y)) + _v(7, cws) + _v(16, k))
                    for k, (sid, w, y) in enumerate(sections))
    chans = b""
    lst = [(IDS["flow"], 510, "mg1sec-1", 1.0), (IDS["moist"], 6, "ppm", 1.0), (IDS["width"], 12001, "m", 1000.0),
           (IDS["tw"], 8, "lb1bu-1", 1.0)]
    if width2:
        lst.append((IDS["width2"], 12001, "m", 1000.0))
    for cid, ddi, unit, fac in lst:
        delay = -14.0 if ddi in (510, 6) else 0.0
        chans += _ld(5, _v(1, cid) + _v(2, ddi) + _d(3, 0.0) + _d(4, fac) + _s(5, unit) + _d(9, delay))
    element = _ld(7, _ld(3, _v(1, IDS["header"]) + _ld(3, _d(1, 4000.0) + _d(2, 0.0) + _d(3, 0.0))) + secs + chans)
    machine = _ld(2, _s(4, VIN) + _s(5, "S999") + _d(6, 9.0) + _v(26, IDS["gnss"]) + element)
    receiver = _ld(3, _v(1, IDS["gnss"]) + _ld(3, _d(1, 0.0) + _d(2, 0.0) + _d(3, 3.0)))
    crop = _ld(21, _ld(1, _s(2, "Wheat (Test)") + _ld(5, _d(1, 60.0) + _s(3, "lb1bu-1"))
                       + _ld(6, _d(1, 13.5) + _s(3, "prcnt"))))
    out = _v(1, 5) + _s(2, guid) + _v(3, ref_ms) + _v(4, 2) + _s(5, "TESTDISPLAY")
    if counts:
        out += _v(7, counts[0]) + _v(8, counts[1]) + _v(9, counts[2])
    out += (_ld(10, receiver + machine) + _name(11, "Test Client") + _name(12, "Test Farm") + _name(13, "Test Field")
            + _v(17, 2026) + crop + _s(24, "1.0.0"))
    if prev:
        out += _s(35, "{" + prev + "}")
    if nxt:
        out += _s(36, "{" + nxt + "}")
    return _v(1, 1) + _v(2, 161) + _ld(5, out)


def _frame(payload: bytes) -> bytes:
    return b"\x0d" + struct.pack("<I", len(payload)) + payload


class _Streams:
    """The samples of every stream (absolute ms, raw values), cut into blocks with anchors as the logs do."""

    def __init__(self, track):
        self.t = track

    def block(self, guid, seq, bs, be, ref, last, first_block, cws_id=None, states_key="states", new_session=False,
              states=True):
        """Records of the samples with bs < t <= be (the block); `last` keeps the last sample of every stream.
        Returns (frame payload, number of GNSS samples)."""
        recs = []
        tr = self.t
        cws_id = IDS["cws"] if cws_id is None else cws_id

        def rng(ts):
            return np.flatnonzero((ts > bs) & (ts <= be))

        # GNSS (type 1): lat, lon x 1e9 deg, alt mm; anchors 1, 2, 4 / deltas 12, 13, 16, time 10
        k = rng(tr["gt"])
        n_gnss = len(k)
        if len(k):
            vals = [np.round(tr["glat"][k] * 1e9).astype(np.int64), np.round(tr["glon"][k] * 1e9).astype(np.int64),
                    np.round(tr["galt"][k] * 1000).astype(np.int64)]
            body = b""
            prev = last.get("gnss")
            base = prev[1] if prev is not None and not first_block else [0, 0, 0]
            if prev is not None and not first_block:
                body += _d(1, base[0]) + _d(2, base[1]) + _d(4, base[2])
            body += _ld(10, _packed_zz(np.diff(np.r_[bs, tr["gt"][k]])))
            for f, x, b0 in zip((12, 13, 16), vals, base):
                body += _ld(f, _packed_zz(np.diff(np.r_[b0, x])))
            recs.append((1, IDS["gnss"], body))
            last["gnss"] = (int(tr["gt"][k[-1]]), [int(x[-1]) for x in vals])
        # header end points (type 2): anchors 1-4 / deltas 17-20, time 15
        k = rng(tr["et"])
        if len(k):
            vals = [np.round(tr[c][k] * 1e9).astype(np.int64) for c in ("lat1", "lon1", "lat2", "lon2")]
            prev = last.get("ends")
            base = prev[1] if prev is not None and not first_block else [0, 0, 0, 0]
            body = b""
            if prev is not None and not first_block:
                body += b"".join(_d(f, b0) for f, b0 in zip((1, 2, 3, 4), base)) + _v(11, 0) + _v(12, prev[0] - ref)
            body += _ld(15, _packed_zz(np.diff(np.r_[bs, tr["et"][k]])))
            for f, x, b0 in zip((17, 18, 19, 20), vals, base):
                body += _ld(f, _packed_zz(np.diff(np.r_[b0, x])))
            recs.append((2, IDS["header"], body))
            last["ends"] = (int(tr["et"][k[-1]]), [int(x[-1]) for x in vals])
        # channels (type 3)
        for name, ts, vs in (("flow", tr["ft"], tr["flow"]), ("moist", tr["ft"], tr["moist"]),
                             ("width", tr["wt"], tr["width"]), ("tw", tr["twt"], tr["tw"]),
                             ("width2", tr.get("w2t"), tr.get("width2"))):
            if ts is None:
                continue
            k = rng(ts)
            prev = last.get(name)
            body = b""
            if prev is not None:
                body += _d(1, prev[1])
            if len(k):
                base = prev[1] if prev is not None else 0
                body += _ld(2, _packed_zz(np.diff(np.r_[bs, ts[k]]))) + _ld(3, _packed_zz(np.diff(np.r_[base, vs[k]])))
            if prev is not None:
                body += _v(4, 0) + _v(12, prev[0] - ref)
            if body:
                recs.append((3, IDS[name], body))
            if len(k):
                last[name] = (int(ts[k[-1]]), int(vs[k[-1]]))
        # section work states (type 4): one group
        k = rng(tr["st"]) if states else np.zeros(0, int)
        prev = None if new_session else last.get("cws")
        body = b""
        if len(k):
            body += _ld(1, _packed_zz(np.diff(np.r_[bs, tr["st"][k]])))
            body += b"".join(_ld(2, _ld(1, bytes(tr[states_key][i]))) for i in k)
        if prev is not None and states:
            body += _ld(3, _ld(1, bytes(prev[1]))) + _v(12, prev[0] - ref)
        if body:
            recs.append((4, cws_id, body))
        if len(k):
            last["cws"] = (int(tr["st"][k[-1]]), bytes(tr[states_key][k[-1]]))
        v9 = b"".join(_v(1, t) for t, _, _ in recs) + _ld(2, _packed_u([i for _, i, _ in recs]))
        v9 += b"".join(_ld(3, r) for _, _, r in recs)
        blk = (_v(1, 1) + _s(2, guid) + _v(3, seq) + _v(4, bs - ref) + _v(5, be - bs) + _ld(9, v9) + _v(11, 1))
        return _v(1, 2) + _v(2, 161) + _ld(4, blk), n_gnss


def track(seed=3, width2=False, state_times=(0.12, 60.1, 80.1), states=None, width2_s=20.5):
    """Two 60 s passes 9 m apart joined by a 20 s header-up turn; 5 Hz positions, 1 Hz flow and moisture."""
    rng = np.random.default_rng(seed)
    t0 = REF_MS + 500
    et = t0 + np.arange(0, 140_000, 200, dtype=np.int64)
    s = (et - t0) / 1000.0
    north = np.where(s < 60, 2.0 * s, np.where(s < 80, 120.0 + 0.0 * s, 120.0 - 2.0 * (s - 80)))
    east = np.where(s < 60, 0.0, np.where(s < 80, 9.0 * (s - 60) / 20.0, 9.0))
    north = north + np.where((s >= 60) & (s < 80), 4.0 * np.sin(np.pi * (s - 60) / 20.0), 0.0)
    heading_n = np.where(s < 70, 1.0, -1.0)            # +1 north, -1 south
    lat = LAT0 + north / M_PER_DEG_LAT
    lon = LON0 + east / M_PER_DEG_LON
    half = 4.5 / M_PER_DEG_LON
    tr = {"et": et, "lat1": lat, "lat2": lat, "lon1": lon - half * heading_n, "lon2": lon + half * heading_n,
          "gt": et.copy(), "glat": lat, "glon": lon, "galt": 900.0 + 0.01 * s}
    ft = t0 + 300 + np.arange(0, 140_000, 1000, dtype=np.int64)
    fs = (ft - t0) / 1000.0
    work = (fs < 60) | (fs >= 80)
    tr["ft"] = ft
    tr["flow"] = np.where(work, np.round(rng.normal(8_000_000, 900_000, len(ft)) / 500) * 500, 0).astype(np.int64)
    tr["moist"] = np.round(rng.normal(140_000, 6_000, len(ft)) / 100).astype(np.int64) * 100
    tr["wt"] = np.array([t0 + 100, t0 + 60_900, t0 + 80_900], np.int64)
    tr["width"] = np.array([9000, 0, 9000], np.int64)
    tr["twt"] = np.array([t0 + 50], np.int64)
    tr["tw"] = np.array([60], np.int64)
    if width2:
        tr["w2t"] = np.array([t0 + int(width2_s * 1000)], np.int64)    # a second DDI 12001 channel
        tr["width2"] = np.array([9000], np.int64)
    tr["st"] = t0 + np.round(np.array(state_times) * 1000).astype(np.int64)
    tr["states"] = [list(x) for x in states] if states is not None else [[1] * 6, [0] * 6, [1] * 6]
    tr["states2"] = [[1] * 3 if all(x) else [0] * 3 if not any(x) else [1, 1, 0] for x in tr["states"]]
    return tr


def make_card(folder: str, split_s: float = 70.0, seed: int = 3, width2: bool = False,
              state_times=(0.12, 60.1, 80.1), layout2: bool = False, counts: bool = False, states=None,
              width2_s: float = 20.5, states2: bool = True) -> dict:
    """Write the card under folder/<VIN>_<date>/ and return {root, files, guids}. states: the section states (one
    list of 6 values per time of state_times; default all on, all off, all on)."""
    tr = track(seed, width2=width2, state_times=state_times, states=states, width2_s=width2_s)
    root = os.path.join(folder, f"{VIN}_01012026")
    logd = os.path.join(root, "JD-Data", "log", "2026")
    os.makedirs(logd, exist_ok=True)
    g = [str(uuid.UUID(int=0x5EED0000 + k)) for k in range(2)]
    st = _Streams(tr)
    last = {}
    split = REF_MS + int(split_s * 1000)
    end = REF_MS + 141_000
    files = []
    for k, (a, b) in enumerate(((REF_MS, split), (split, end))):
        ref = a
        alt = layout2 and k == 1
        blocks, n_g, dur = [], 0, 0
        seq = 1
        bs = a
        while bs < b:
            be = min(bs + 30_000, b)
            fr, ng = st.block(g[k], seq, bs, be, ref, last, first_block=(k == 0 and seq == 1),
                              cws_id=IDS["cws2"] if alt else IDS["cws"], states_key="states2" if alt else "states",
                              new_session=alt and seq == 1, states=states2 or k == 0)
            blocks.append(_frame(fr))
            n_g += ng
            dur += be - bs
            seq += 1
            bs = be
        out = _frame(_v(1, 3) + _v(2, 161) + _ld(3, _v(1, 4) + _v(2, 10607242) + _v(3, 1) + _s(4, "0")))
        kw = {}                         # only the options in use (tests elsewhere replace definitions())
        if width2:
            kw["width2"] = True
        if alt:
            kw["layout2"] = True
        if counts:
            kw["counts"] = (dur, len(blocks), n_g)
        out += _frame(definitions(g[k], ref, prev=g[k - 1] if k else None, nxt=g[k + 1] if k + 1 < 2 else None,
                                  **kw))
        out += b"".join(blocks)
        out += _frame(_v(1, 4) + _v(2, 161))
        p = os.path.join(logd, g[k] + ".jdl")
        with open(p, "wb") as fh:
            fh.write(out)
        files.append(p)
    with open(os.path.join(root, "version.txt"), "wb") as fh:
        fh.write(b"\n".join(f"{x},{1780000500 + k}".encode() for k, x in enumerate(g)))
    return {"root": folder, "files": files, "guids": g}
