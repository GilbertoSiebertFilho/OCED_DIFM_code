# -*- coding: utf-8 -*-
"""Write a CLEAN harvest back as John Deere Gen4 / G5 display logs (JD-Data/log/<year>/<guid>.jdl), the format it was
read from (engine/formats/jd_jdl.py), so that a farm program reads the clean harvest as raw monitor data.

    from tests.oced_synthetic import jd_jdl_write as W
    ok, why = W.can_write(dec["source"])                         # dec = engine.run.decode(...)
    out = W.write_clean(dec["source"], clean_shp, out_dir)
    out["zip"]                                                   # the card, ready to read as raw .jdl logs

NOT YET TESTED IN AG LEADER SMS: the cards this writer makes decode back exactly with jd_jdl (the round trip below,
run on every write), whose points="sms" mode is OCED's measured model of how SMS reads .jdl logs; no written card has
been read into SMS yet.

WHAT A READING IS IN THE FILE
A reading of jd_jdl is not a record of the file. The logs hold anchored delta streams in ~30 s data blocks: the grain
flow (DDI 510, 1 Hz), moisture (DDI 6), the logged working width (DDI 12001), the section work states (record type 4,
one record per group of 16 sections), the two header end points (type 2, 5 Hz) and the GNSS antenna (type 1, 5 Hz).
jd_jdl makes readings of them in one of two ways, and the writer marks the ones the decode it is given used:
  points="sms" (jd_jdl's default): every log file on its own; a point is a group of 5 Hz header samples; its width is
      the sections on SMS_SNAP_MS after its LAST sample (the logged width DDI 12001 in a file without section
      states); its flow and moisture the distance-weighted mean over its samples, a sample with no section on
      counting as 0. Look-ups: every header sample at t + SMS_SNAP_MS.
  points="ops_center": one reading per DDI 510 sample; sections at t + 0.8 s (the merged files of one section
      layout), width DDI 12001 at t + 1.6 s (or the sections on when that channel reads wider than the header).

WHY NOTHING IS DELETED
Deleting a flow sample changes the duration of the next reading, deleting header or GNSS samples moves the readings
around it and changes the SMS points. So no flow, moisture, position or GNSS sample is touched. A reading is removed
the way the monitor itself marks grain that is not harvested: the header is up for it.
  sections   every section off (record type 4) at the look-ups of the removed reading's own samples: in "sms" mode
             every header sample of the point (so the point has no width and no grain), in "ops_center" mode the
             reading's look-up; the original states again before the next kept look-up. Change times: in "sms" mode
             SMS_EDGE_MS before the header sample (inside the SMS_SNAP_MS window of that sample and of no other), in
             "ops_center" mode at an SMS point boundary near the flow time where one lies in the interval.
  width      DDI 12001 = 0 at the same look-ups ("sms": every file, which is the width in the files without section
             states; "ops_center": the readings whose width jd_jdl takes from that channel).
The streams are edited as jd_jdl reads them (_plan: every stream as the decoder merges it, every channel id of a DDI,
the carries of type-4 records as events at their carry time, the last of equal times winning for type 4 and the first
for type 3 in ops_center mode). The original changes that decide a kept look-up are kept; the carries and anchors of
the blocks are recomputed (a carry that repeats the last change before its block follows the new stream; one that is
the state a log starts from is planned with that log); every record, record list, block and frame length is encoded
again. Which readings are marked: in "ops_center" mode every flow sample that is not kept (the vendor's own
left-outs too, so that no non-kept flow sample keeps a width or a section on); in "sms" mode every point that was a
reading of the decode and is not kept (the points jd_jdl does not export - no width, no distance - stay as they were).

Level 2 (widths=True): a narrowed or shortened reading (Optimize Swath Widths) gets the clean width (the clean area
when the clean distance is shorter: the distance is the header's path): in the section states the contiguous run of
its sections on whose total width is nearest the clean width, centred nearest the clean strip (section steps), and in
DDI 12001 the width itself (1 mm steps). "sms" mode changes the point's last sample only (the one its width comes
from); the point keeps its position (SMS places a point by the header, see below). "ops_center" mode moves the
reading to the centre of the sections left on, as jd_jdl places it.
Level 3 (factors={machine: f}): the grain flow DDI 510 and the monitor's own yields DDI 39 / 43 of that machine times
f, rounded to their raw step (1 mg/s for the flow), the anchors with them.

THE ONE THING "sms" MODE CANNOT ALWAYS KEEP EXACT
SMS (and jd_jdl's sms mode) places every point of a log across the header by one reference: the middle of the
sections on at the log's FIRST WIDEST point (its lateral offset then says where its own sections are). When the
cleaning removed that point and the widest points left have another pattern of sections (a header run with an end
section off except for a moment, a log of another header profile), every kept point of that log is placed sideways by
the change of reference (half a section or more) and its offset moves the other way: the strip it covers, its width,
area, grain and moisture stay, its point position does not. No edit of the section states can avoid it (a point can
only lose its width by having no section on). The writer predicts it per log before writing: a narrowed reference
point keeps its original width (noted), a removed one makes write_clean raise JdlReferenceError (its .logs name each
log, its kept readings and the move) and nothing is written - unless allow_reference_shift=True, which writes the card
with the move predicted and checked per reading and named in the warnings. It arises in ordinary SMS 3-sigma
cleanings (a few logs of a field, a fraction of a section).

A file without section states takes its width from DDI 12001, which jd_jdl reads by a binary search over the samples
of every channel id of that DDI in record order. Where a second channel's sample is logged after a later sample of
the first (a session start), that order is not the time order; when such a file needs a change, its width is planned
against jd_jdl's own reading of it and written in one channel id in time order (_merge_channels).

drop_logs=True leaves out the logs with no kept reading (in ops_center mode none within FILE_MARGIN_MS); off by
default: jd_jdl's local projection origin (the median header position of the unit's logs) then moves, which changes
its distances by ~1e-6 relative. Of two copies of one log (the same GUID, one cut short by an earlier export), only
the longest is written (jd_jdl's sms mode reads only that one, SMS likewise; its ops_center mode reads the same
values from both).

New identifiers (new_ids=True): every written log gets a new GUID, in its file name, in the definitions (field 2), in
the header of every data block (field 2), in the previous / next links of the other written logs (definitions fields
35 / 36) and in version.txt, so that SMS does not refuse the card as already archived. Same length: no size change.

What else the container holds about the data (CONSISTENCY), and what the writer does with it:
  frame length (uint32 after 0x0D), protobuf lengths       re-encoded for every changed frame / message
  record type / id lists (block field 9.1 / 9.2)           re-encoded when a record is added (a block without a
                                                           record of a stream that needs a change)
  type-3 anchor value / time, type-4 carry state / time   recomputed (see above)
  block duration (block field 5)                          extended only when a change falls after a log's last block
  definitions 7 (sum of the block durations, ms)          follows a changed block duration
  definitions 8 (number of blocks), 9 (GNSS samples)       unchanged: no block and no GNSS sample is added or removed
  block sequence / start offset / bounding box / flags     unchanged (no block, position or GNSS sample changes)
  log GUID, links 35 / 36, version.txt                     new_ids (links to logs not written stay as they were)
  checksum / CRC / file size / frame count                 none exists in the container
Key from a clean row back to the raw records: (Dataset, Obj__Id) of the decoded file, checked against Time and
Machine; the original logs are decoded again with the same units, options and points mode (source), which gives every
reading's time stamp t_ms (ms) and so its samples. The round trip decodes the written logs again the same way and
compares every kept reading (all columns of the decoded file, the active sections and the lateral offset), checks
that no removed reading comes back and that every non-kept one has no width; when it fails, JdlRoundTripError is
raised and the written folder removed.

Pure engine code (no Streamlit, no app imports).
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import os
import shutil
import struct
import uuid
import zipfile

import numpy as np
import pandas as pd

from oced_clean._engine.formats import common as C
from oced_clean._engine.formats import jd_jdl as J

FORMAT_ID = J.FORMAT_ID
LABEL = "John Deere Gen4 / G5 display logs (.jdl)"
SMS_CAVEAT = ("not yet tested in Ag Leader SMS: the card decodes back exactly with OCED's model of how SMS reads .jdl "
              "logs; read it into a scratch SMS project as raw .jdl before relying on it")
SMS_CAVEAT_OPS = ("not yet tested in Ag Leader SMS: the logs decode back exactly with OCED's Operations Center model "
                  "(points=ops_center, one reading per flow sample), not with its model of how SMS reads .jdl logs; "
                  "read them into a scratch SMS project as raw .jdl before relying on them")
SMS_CAVEAT_UNVERIFIED = ("not yet tested in Ag Leader SMS and not checked by a round trip (verify=False): the written "
                         "logs were not decoded back; read them into a scratch SMS project as raw .jdl before relying "
                         "on them")
POINTS = J.POINTS
LAG_WIDTH_MS = J.POS_LAG_MS + J.WIDTH_LAG_MS     # ops_center: the width is looked up 1.6 s after the flow sample
LAG_SECTION_MS = J.POS_LAG_MS                     # and the section states 0.8 s after it
SMS_WINDOW_S = J.SMS_WINDOW_S
SMS_SNAP_MS = J.SMS_SNAP_MS
SMS_EDGE_MS = 1             # sms: a change SMS_EDGE_MS before a header sample applies to it and to no sample before
FILE_MARGIN_MS = 15_000     # ops_center, drop_logs: a log with no kept reading within 15 s of its data is left out
KEY_FLOW = (J.DDI_FLOW, "mg1sec-1")
KEY_WIDTH = (J.DDI_WIDTH, "m")
KEY_YWET = (J.DDI_YWET, "bu1ac-1")
KEY_YDRY = (J.DDI_YDRY, "bu1ac-1")
SCALED_KEYS = (KEY_FLOW, KEY_YWET, KEY_YDRY)
EDIT_KEYS = (KEY_WIDTH,) + SCALED_KEYS
T_ORIG, T_OFF, T_SET = 0, 1, 2
NEG = -(1 << 62)            # the time of the value a file carries in before its first sample (sms: type 3)
_D = struct.Struct("<d")
_I = struct.Struct("<I")
_MASK = bytes(1 if i == 1 else 0 for i in range(256))
_OFFT = bytes(0 if i == 1 else i for i in range(256))


class JdlWriteError(ValueError):
    """The input cannot be written (plain message for the user)."""


class JdlReferenceError(JdlWriteError):
    """sms mode: removing a log's first widest point would move that log's kept points sideways."""

    def __init__(self, msg, logs=None):
        super().__init__(msg)
        self.logs = logs or []


class JdlRoundTripError(JdlWriteError):
    """The written logs do not decode back as the clean readings (nothing is left on disk)."""

    def __init__(self, msg, round_trip=None):
        super().__init__(msg)
        self.round_trip = round_trip


# ================================================================================================ protobuf encoding
def _enc_varint(n: int) -> bytes:
    """Varint; a negative value as its 64-bit two's complement (how the logs store signed times)."""
    n = int(n) & ((1 << 64) - 1)
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _key(f, wt) -> bytes:
    return _enc_varint((f << 3) | wt)


def _ld(f, payload: bytes) -> bytes:
    return _key(f, 2) + _enc_varint(len(payload)) + payload


def _packed_zz(arr) -> bytes:
    return b"".join(_enc_varint((int(x) << 1) ^ (int(x) >> 63)) for x in np.asarray(arr, np.int64))


def _packed_u(arr) -> bytes:
    return b"".join(_enc_varint(int(x)) for x in arr)


def _rfields(b):
    """Shallow walk keeping the byte span of every field: [(field, wire type, value, start, end)]."""
    out = []
    i, n = 0, len(b)
    while i < n:
        s = i
        key, i = J._varint(b, i)
        f, wt = key >> 3, key & 7
        if wt == 0:
            v, i = J._varint(b, i)
        elif wt == 2:
            ln, i = J._varint(b, i)
            v = b[i:i + ln]
            i += ln
        elif wt == 1:
            v = _D.unpack_from(b, i)[0]
            i += 8
        elif wt == 5:
            v = struct.unpack_from("<f", b, i)[0]
            i += 4
        else:
            raise JdlWriteError(f"unsupported wire type {wt} (field {f})")
        if i > n:
            raise JdlWriteError("truncated message")
        out.append((f, wt, v, s, i))
    return out


def _rebuild(b, fields, repl: dict) -> bytes:
    """Message b (walked as `fields`) with field i replaced by repl[i] (bytes of the whole field; b'' deletes it)."""
    out, pos = [], 0
    for i, (_, _, _, s, e) in enumerate(fields):
        if i in repl:
            out.append(b[pos:s])
            out.append(repl[i])
            pos = e
    out.append(b[pos:])
    return b"".join(out)


# ================================================================================================ one log file
@dataclasses.dataclass
class _Block:
    frame: int            # frame index
    top: int              # index of the block field in the frame payload's fields
    bs: int               # absolute block start (ms)
    dur: int              # duration field (ms)
    fields: list          # _rfields of the block message
    v9: int               # index of field 9 in fields
    v9_fields: list       # _rfields of the field-9 message
    recs: list            # [(type, id, record bytes)]
    rec_pos: list         # index in v9_fields of each record's field 3


@dataclasses.dataclass(eq=False)
class _Rec:
    """One type-3 (channel) or type-4 (section work states) record of a block, as read and as it will be written."""
    log: int
    block: int
    ri: int | None        # index in the block's record list (None: a record this writer adds)
    typ: int
    cid: int
    anchor: object        # type 3: raw int (None: absolute record); type 4: carry state (None: no carry)
    at: int | None        # anchor / carry time (absolute ms)
    f4: bytes | None      # type 3: field 4 as written
    samples: list         # [(t ms, raw int | state bytes)] in record order (type 3: "not available" values too)
    raw: bytes | None     # the record as read
    bad: str = ""         # fields this writer does not re-encode (the record can only be copied)
    fac: float = 1.0      # type 3: channel factor / offset of the record's channel (value = raw / fac + off)
    off: float = 0.0
    dele: set = dataclasses.field(default_factory=set)
    setv: dict = dataclasses.field(default_factory=dict)
    adds: list = dataclasses.field(default_factory=list)
    new_anchor: object = None
    new_at: object = None
    anchor_set: bool = False
    derived: bool = False     # the anchor / carry repeats the stream's last sample before the block (recomputed)

    def new_samples(self):
        if not (self.dele or self.setv or self.adds):
            return self.samples
        s = [(t, self.setv.get(k, v), k) for k, (t, v) in enumerate(self.samples) if k not in self.dele]
        s += [(t, v, len(self.samples) + j) for j, (t, v) in enumerate(self.adds)]
        s.sort(key=lambda x: (x[0], x[2]))
        return [(t, v) for t, v, _ in s]

    def anchor_now(self):
        return (self.new_anchor, self.new_at) if self.anchor_set else (self.anchor, self.at)


class _Log:
    """A parsed .jdl: frames, data blocks and their records, and the edits to write back."""

    def __init__(self, meta: J.FileMeta, index: int):
        self.meta = meta
        self.index = index
        with open(meta.path, "rb") as fh:
            self.data = fh.read()
        name = os.path.basename(meta.path)
        self.frames = []                       # [(offset, payload)]
        i, n = 0, len(self.data)
        while i + 5 <= n:
            if self.data[i] != 0x0D:
                raise JdlWriteError(f"{name}: bad frame marker at byte {i}")
            ln = _I.unpack_from(self.data, i + 1)[0]
            if i + 5 + ln > n:
                raise JdlWriteError(f"{name}: truncated frame at byte {i}")
            self.frames.append((i, self.data[i + 5:i + 5 + ln]))
            i += 5 + ln
        if i != n:
            raise JdlWriteError(f"{name}: {n - i} trailing byte(s) after the last frame")
        self.blocks: list[_Block] = []
        self.defs_frame = None
        self.defs_counts = {}                  # definitions fields 7 (sum of block durations), 8 (blocks), 9 (GNSS)
        for k, (_, pay) in enumerate(self.frames):
            top = _rfields(pay)
            for j, (f, wt, v, s, e) in enumerate(top):
                if f == 5 and wt == 2 and self.defs_frame is None:
                    self.defs_frame = (k, j)
                    for f5, wt5, v5, *_ in _rfields(v):
                        if f5 in (7, 8, 9) and wt5 == 0:
                            self.defs_counts[f5] = int(v5)
                if f != 4 or wt != 2:
                    continue
                hf = _rfields(v)
                h = {a: x for a, w, x, _, _ in hf if a != 9}
                v9s = [i9 for i9, x in enumerate(hf) if x[0] == 9]
                if len(v9s) != 1:
                    raise JdlWriteError(f"{name}: block with {len(v9s)} record lists")
                vf = _rfields(hf[v9s[0]][2])
                types, ids, recs, pos = [], None, [], []
                for i3, (f3, wt3, v3, _, _) in enumerate(vf):
                    if f3 == 1:
                        if wt3 == 0:
                            types.append(v3)
                        else:
                            types.extend(J._uvarints(v3).tolist())
                    elif f3 == 2 and wt3 == 2:
                        ids = J._uvarints(v3).tolist()
                    elif f3 == 3 and wt3 == 2:
                        recs.append(v3)
                        pos.append(i3)
                if ids is None or not (len(types) == len(ids) == len(recs)):
                    raise JdlWriteError(f"{name}: block with unmatched record lists")
                self.blocks.append(_Block(frame=k, top=j, bs=meta.ref_ms + J._s64(h.get(4, 0)), dur=int(h.get(5, 0)),
                                          fields=hf, v9=v9s[0], v9_fields=vf, recs=list(zip(types, ids, recs)),
                                          rec_pos=pos))
        self.end_ms = max((b.bs + b.dur for b in self.blocks), default=None)
        self.start_ms = min((b.bs for b in self.blocks), default=None)
        self.new_dur = {}                      # block -> duration (ms)
        self.add_recs = {}                     # block -> [_Rec] added
        self.new_guid = None
        self.link_guids = {}                   # old guid of another log -> its new guid (definitions 35 / 36)
        self.r3 = {}                           # (ddi, unit) -> [_Rec] in decode order
        self.r4 = {}                           # work-state element id -> [_Rec] in decode order
        self._read_records()

    # ---- records of the streams this writer edits
    def _read_records(self):
        ch = self.meta.channels
        for bi, b in enumerate(self.blocks):
            for ri, (typ, cid, raw) in enumerate(b.recs):
                if typ == 3:
                    c = ch.get(cid)
                    if c is None or (c[0], c[1]) not in EDIT_KEYS:
                        continue
                    r = _read3(raw, self.meta.ref_ms, b.bs)
                    r.log, r.block, r.ri, r.cid, r.raw = self.index, bi, ri, cid, raw
                    r.off, r.fac = float(c[2]), float(c[3])
                    self.r3.setdefault((c[0], c[1]), []).append(r)
                elif typ == 4:
                    r = _read4(raw, self.meta.ref_ms, b.bs)
                    r.log, r.block, r.ri, r.cid, r.raw = self.index, bi, ri, cid, raw
                    self.r4.setdefault(cid, []).append(r)

    def add_record(self, typ, cid, bi, like: _Rec | None, key=None):
        """A new record of a stream in block bi (appended to the block's record list, so it comes after the block's
        other records in the decoder's order)."""
        r = _Rec(log=self.index, block=bi, ri=None, typ=typ, cid=cid, anchor=None, at=None,
                 f4=(like.f4 if like is not None and like.f4 else _key(4, 0) + _enc_varint(0)) if typ == 3 else None,
                 samples=[], raw=None, fac=like.fac if like is not None else 1.0,
                 off=like.off if like is not None else 0.0, derived=True)
        self.add_recs.setdefault(bi, []).append(r)
        lst = self.r3.setdefault(key, []) if typ == 3 else self.r4.setdefault(cid, [])
        i = next((j for j, x in enumerate(lst) if x.block > bi), len(lst))
        lst.insert(i, r)
        return r

    def records(self):
        for v in self.r3.values():
            yield from v
        for v in self.r4.values():
            yield from v

    def changed(self) -> bool:
        return bool(any(_dirty(r) for r in self.records()) or self.new_dur or self.new_guid or self.link_guids)

    def serialize(self) -> bytes:
        if not self.changed():
            return self.data
        old_g = (self.meta.guid or "").encode()
        new_g = self.new_guid.encode() if self.new_guid else None
        body = {}
        for r in self.records():
            if _dirty(r):
                body[(r.block, r.ri if r.ri is not None else id(r))] = _encode(r, self)
        by_frame = {}
        for bi, b in enumerate(self.blocks):
            by_frame.setdefault(b.frame, []).append(bi)
        out = []
        for k, (off, pay) in enumerate(self.frames):
            top = _rfields(pay)
            repl = {}
            for bi in by_frame.get(k, []):
                b = self.blocks[bi]
                blk = top[b.top][2]
                brepl = {}
                adds = self.add_recs.get(bi, [])
                if adds or any((bi, r) in body for r in range(len(b.recs))):
                    brepl[b.v9] = _ld(9, self._v9(bi, body))
                if bi in self.new_dur:
                    i5 = next(i for i, x in enumerate(b.fields) if x[0] == 5)
                    brepl[i5] = _key(5, 0) + _enc_varint(self.new_dur[bi])
                if new_g:
                    i2 = next((i for i, x in enumerate(b.fields) if x[0] == 2 and x[2] == old_g), None)
                    if i2 is not None:
                        brepl[i2] = _ld(2, new_g)
                if brepl:
                    repl[b.top] = _ld(4, _rebuild(blk, b.fields, brepl))
            if self.defs_frame and self.defs_frame[0] == k and (new_g or self.link_guids or self.new_dur):
                j = self.defs_frame[1]
                defs = top[j][2]
                df = _rfields(defs)
                drepl = {}
                d7 = sum(self.new_dur[bi] - self.blocks[bi].dur for bi in self.new_dur)
                for i, (f, wt, v, s, e) in enumerate(df):
                    if f == 2 and wt == 2 and new_g and v == old_g:
                        drepl[i] = _ld(2, new_g)
                    elif f in (35, 36) and wt == 2:
                        g = v.decode("ascii", "replace").strip("{}")
                        if g in self.link_guids:
                            drepl[i] = _ld(f, ("{" + self.link_guids[g] + "}").encode())
                    elif f == 7 and wt == 0 and d7:
                        drepl[i] = _key(7, 0) + _enc_varint(int(v) + d7)
                if drepl:
                    repl[j] = _ld(5, _rebuild(defs, df, drepl))
            p2 = _rebuild(pay, top, repl) if repl else pay
            out.append(b"\x0d" + _I.pack(len(p2)) + p2)
        return b"".join(out)

    def _v9(self, bi, body) -> bytes:
        b = self.blocks[bi]
        raw = b.fields[b.v9][2]
        adds = self.add_recs.get(bi, [])
        if not adds:
            repl = {b.rec_pos[r]: _ld(3, body[(bi, r)]) for r in range(len(b.recs)) if (bi, r) in body}
            return _rebuild(raw, b.v9_fields, repl)
        types = [t for t, _, _ in b.recs] + [r.typ for r in adds]
        ids = [i for _, i, _ in b.recs] + [r.cid for r in adds]
        recs = [body.get((bi, r), b.recs[r][2]) for r in range(len(b.recs))] + [body[(bi, id(r))] for r in adds]
        packed = any(f == 1 and wt == 2 for f, wt, *_ in b.v9_fields)
        out = _ld(1, _packed_u(types)) if packed else b"".join(_key(1, 0) + _enc_varint(t) for t in types)
        out += _ld(2, _packed_u(ids))
        return out + b"".join(_ld(3, r) for r in recs)


def _read3(raw, ref, bs) -> _Rec:
    fs = _rfields(raw)
    d = {}
    for f, wt, v, *_ in fs:
        d.setdefault(f, []).append((wt, v))
    bad = ""
    if any(f not in (1, 2, 3, 4, 12) for f in d) or any(len(v) > 1 for v in d.values()):
        bad = f"fields {sorted(d)}"
    anchor, at = None, None
    if 1 in d:
        wt, v = d[1][0]
        if wt != 1 or float(v) != round(float(v)):
            bad = bad or "anchor not an integer double"
        anchor = int(round(float(v))) if wt == 1 else int(J._num(v))
        at = ref + J._s64(d[12][0][1]) if 12 in d else None
    samples = []
    if 2 in d and 3 in d and d[2][0][0] == 2 and d[3][0][0] == 2:
        tt = bs + np.cumsum(J._zz(d[2][0][1]))
        vv = (anchor or 0) + np.cumsum(J._zz(d[3][0][1]))
        n = min(len(tt), len(vv))
        if len(tt) != len(vv):
            bad = bad or "time and value arrays of unequal length"
        samples = [(int(tt[k]), int(vv[k])) for k in range(n)]
    elif 2 in d or 3 in d:
        bad = bad or "time or value array missing"
    f4 = (_key(4, 0) + _enc_varint(d[4][0][1])) if 4 in d and d[4][0][0] == 0 else None
    if 4 in d and d[4][0][0] != 0:
        bad = bad or "field 4 not a varint"
    return _Rec(log=-1, block=-1, ri=None, typ=3, cid=-1, anchor=anchor, at=at, f4=f4, samples=samples, raw=None,
                bad=bad)


def _read4(raw, ref, bs) -> _Rec:
    fs = _rfields(raw)
    d = {}
    for f, wt, v, *_ in fs:
        d.setdefault(f, []).append((wt, v))
    bad = ""
    if any(f not in (1, 2, 3, 12) for f in d):
        bad = f"fields {sorted(d)}"

    def state(msg):
        sf = _rfields(msg)
        if len(sf) != 1 or sf[0][0] != 1 or sf[0][1] != 2:
            return None
        return bytes(sf[0][2])
    carry, at = None, None
    if 3 in d:
        carry = state(d[3][0][1])
        if carry is None:
            bad = bad or "carry state with unknown fields"
        at = ref + J._s64(d[12][0][1]) if 12 in d else None
    samples = []
    if 1 in d and 2 in d:
        tt = bs + np.cumsum(J._zz(d[1][0][1]))
        st = [state(v) for _, v in d[2]]
        if any(s is None for s in st) or len(st) != len(tt):
            bad = bad or "work states with unknown fields"
        samples = [(int(tt[k]), st[k]) for k in range(min(len(tt), len(st))) if st[k] is not None]
    return _Rec(log=-1, block=-1, ri=None, typ=4, cid=-1, anchor=carry, at=at, f4=None, samples=samples, raw=None,
                bad=bad)


def _dirty(r: _Rec) -> bool:
    return r.ri is None or bool(r.dele or r.setv or r.adds) or (r.anchor_set and (r.new_anchor, r.new_at) != (r.anchor,
                                                                                                               r.at))


def _encode(r: _Rec, lg: _Log) -> bytes:
    """A type-3 or type-4 record in the field order of the logs (type 3: 1 anchor, 2 time deltas, 3 value deltas,
    4, 12 anchor time; type 4: 1 time deltas, 2 states, 3 carry state, 12 carry time)."""
    if r.raw is not None:
        if r.bad:
            raise JdlWriteError(f"{os.path.basename(lg.meta.path)}: a record of element {r.cid} needs a change but "
                                f"its layout is not one this writer re-encodes ({r.bad})")
        if _enc_body(r, lg, r.samples, r.anchor, r.at) != r.raw:
            raise JdlWriteError(f"{os.path.basename(lg.meta.path)}: a record of element {r.cid} does not re-encode "
                                "to its own bytes: not written")
    a, at = r.anchor_now()
    return _enc_body(r, lg, r.new_samples(), a, at)


def _enc_body(r: _Rec, lg: _Log, samples, anchor, at) -> bytes:
    ref = lg.meta.ref_ms
    bs = lg.blocks[r.block].bs
    tt = np.array([t for t, _ in samples], np.int64)
    if r.typ == 3:
        out = b""
        base = 0
        if anchor is not None:
            base = int(anchor)
            out += _key(1, 1) + _D.pack(float(base))
        if len(tt):
            vv = np.array([int(v) for _, v in samples], np.int64)
            out += _ld(2, _packed_zz(np.diff(np.r_[bs, tt]))) + _ld(3, _packed_zz(np.diff(np.r_[base, vv])))
        if anchor is not None:
            out += r.f4 or (_key(4, 0) + _enc_varint(0))
            if at is not None:
                out += _key(12, 0) + _enc_varint(int(at) - ref)
        return out
    out = b""
    if len(tt):
        out += _ld(1, _packed_zz(np.diff(np.r_[bs, tt])))
        out += b"".join(_ld(2, _ld(1, v)) for _, v in samples)
    if anchor is not None:
        out += _ld(3, _ld(1, anchor))
        if at is not None:
            out += _key(12, 0) + _enc_varint(int(at) - ref)
    return out


# ================================================================================================ stream edits
def _mk(v):
    """The sections on of a work state (what jd_jdl reads: the bytes equal to 1); None = no state = none on."""
    return b"" if v is None else v.translate(_MASK).rstrip(b"\x00")


def _same4(a, b):
    return _mk(a) == _mk(b)


def _nz(v):
    return 0.0 if v is None or not np.isfinite(v) else float(v)


def _same3(a, b):
    return _nz(a) == _nz(b)


def _off4(v, width=0):
    return bytes(width) if v is None else v.translate(_OFFT)


def _slots(events, tie):
    """Events [(t, value, kind, rec, k)] in decoder order -> (times int64, values, fixed bool, [events per slot]).
    kind 's' = a sample, 'c' = a carry the stream starts from (type 4) or 'v' the value a file carries in (type 3,
    sms); fixed = holds a 'c' / 'v' event (it can take another value, it cannot be dropped). tie 'last': the last
    of equal times is the value (type 4, and type 3 in sms mode), 'first' the first (type 3 merged in ops_center)."""
    order = sorted(range(len(events)), key=lambda i: events[i][0])
    st, sv, fx, ph = [], [], [], []
    for i in order:
        e = events[i]
        if st and st[-1] == e[0]:
            ph[-1].append(e)
            if tie == "last":
                sv[-1] = e[1]
            fx[-1] = fx[-1] or e[2] != "s"
        else:
            st.append(e[0])
            sv.append(e[1])
            fx.append(e[2] != "s")
            ph.append([e])
    return np.array(st, np.int64), sv, np.array(fx, bool), ph


def _plan(st, sv, fx, q, kind, val, edge, same, off_val):
    """The fewest changes to a stream (slots st / sv / fx, as the decoder reads it: the value at a look-up time is the
    last slot at or before it) so that at every look-up q[k] its value is the original one (kind T_ORIG), none on /
    zero (T_OFF: off_val(original)) or val[k] (T_SET). A look-up k owns the slots in (q[k-1], q[k]]: original slots
    are kept where they give the wanted value, else dropped (a fixed slot takes the value instead); a new slot is put
    at edge(k, q[k-1] or None, q[k]) where one is needed. Returns (drop set, revalue dict, add dict time -> value)."""
    m = len(q)
    drop, reval, add = set(), {}, {}
    if not m:
        return drop, reval, add
    q = np.asarray(q, np.int64)
    j_hi = np.searchsorted(st, q, side="right")
    j_lo = np.r_[0, j_hi[:-1]]
    cand = np.flatnonzero(np.asarray(kind) != T_ORIG)
    todo = np.unique(np.r_[cand, cand + 1])
    todo = todo[todo < m]

    def orig(k):
        i = j_hi[k] - 1
        return sv[i] if i >= 0 else None
    cur, prev = None, -2
    for k in todo.tolist():
        if k != prev + 1:
            cur = orig(k - 1) if k > 0 else None
        prev = k
        a, b = int(j_lo[k]), int(j_hi[k])
        lo = int(q[k - 1]) if k > 0 else None
        hi = int(q[k])
        o = orig(k)
        kd = kind[k]
        if kd == T_ORIG:
            if b > a:
                cur = sv[b - 1]
            elif not same(cur, o):
                add[edge(k, lo, hi)] = o
                cur = o
            continue
        V = val[k] if kd == T_SET else off_val(o)
        if b > a and same(sv[b - 1], V):
            cur = sv[b - 1]
            continue
        if b == a:
            if not same(cur, V):
                add[edge(k, lo, hi)] = V
                cur = V
            continue
        if same(cur, V):
            for i in range(a, b):
                if not same(sv[i], V):
                    if fx[i]:
                        reval[i] = V
                    else:
                        drop.add(i)
            continue
        e = edge(k, lo, hi)
        fixed_before = [i for i in range(a, b) if fx[i] and st[i] < e]
        if cur is None and fixed_before:
            e = int(st[fixed_before[0]])                 # nothing before it to fall back on: the carry takes it
        placed = False
        for i in range(a, b):
            ti = int(st[i])
            if ti < e:
                continue
            if ti == e:
                reval[i] = V
                placed = True
            elif same(sv[i], V):
                continue
            elif fx[i]:
                reval[i] = V
            else:
                drop.add(i)
        if not placed:
            add[e] = V
        cur = V
    return drop, reval, add


def _evaluate(st, sv, q):
    """The value of slots (st, sv) at look-up times q (the last at or before; None before the first)."""
    i = np.searchsorted(st, np.asarray(q, np.int64), side="right") - 1
    return [sv[x] if x >= 0 else None for x in i]


def _new_slots(st, sv, ph, drop, reval, add, tie):
    """The slots after a plan (for the self-check)."""
    t = [int(st[i]) for i in range(len(st)) if i not in drop] + list(add)
    v = [reval.get(i, sv[i]) for i in range(len(st)) if i not in drop] + list(add.values())
    o = np.argsort(np.array(t, np.int64), kind="stable")
    return np.array(t, np.int64)[o], [v[i] for i in o]


def _apply(ph, drop, reval, add, home, to_raw):
    """Write a plan into the records: dropped samples, new values (samples and carries), added samples."""
    for i in drop:
        for (_, _, kind, rec, k) in ph[i]:
            if kind != "s":
                raise JdlWriteError("internal: a carry cannot be dropped")
            rec.dele.add(k)
    for i, v in reval.items():
        for (_, _, kind, rec, k) in ph[i]:
            if kind == "s":
                rec.setv[k] = to_raw(rec, v)
            else:
                rec.anchor_set, rec.new_anchor, rec.new_at = True, to_raw(rec, v), rec.at
    for t, v in add.items():
        rec = home(t)
        rec.adds.append((int(t), to_raw(rec, v)))


def _raw3(rec, v):
    return int(round((_nz(v) - rec.off) * rec.fac))


def _raw4(rec, v):
    return v


# ================================================================================================ the unit
@dataclasses.dataclass
class _Unit:
    uid: str
    vin: str
    logs: list            # [_Log] of the files written (one per GUID: the longest copy), ref order
    fds: list             # jd_jdl.FileData of each
    copies_left_out: int = 0
    written: set = None   # indexes of the logs written (all unless drop_logs)
    notes: dict = dataclasses.field(default_factory=dict)
    expect: dict = dataclasses.field(default_factory=dict)     # t_ms -> {column: expected value} (narrowed)


def _units(root, unit_ids):
    metas, _ = J._scan(root)
    groups = {}
    for m in metas:
        groups.setdefault(J._unit_key(m), []).append(m)
    out = []
    for uid in unit_ids:
        if uid not in groups:
            raise JdlWriteError(f"unit {uid!r} is not in the original logs any more")
        ms = sorted(groups[uid], key=lambda x: x.ref_ms)
        best = {}
        for m in ms:
            k = m.guid or m.path
            if k not in best or m.size > best[k].size:
                best[k] = m
        use = [m for m in ms if best[m.guid or m.path] is m]
        fds = [J.decode_file(m.path, m) for m in use]
        if not any(KEY_FLOW in fd.samples for fd in fds):
            continue                                       # not a combine (left out by the decoder too)
        out.append(_Unit(uid=uid, vin=use[0].vin, logs=[_Log(m, i) for i, m in enumerate(use)], fds=fds,
                         copies_left_out=len(ms) - len(use)))
    return out


def _home3(u: _Unit, files, key, t):
    """The record a new type-3 sample at time t goes into: the block that covers t (in a gap, the next file's first
    block; after the end, the last block, its duration extended) of the files, its record of the key's main channel
    (the channel id with the most samples), else of another channel of the key, else a new record."""
    files = [li for li in files if any(r.ri is not None for r in u.logs[li].r3.get(key, []))]
    li, bi = _block_for(u, files, t)
    lg = u.logs[li]
    recs = lg.r3.get(key, [])
    count = {}
    for r in recs:
        count[r.cid] = count.get(r.cid, 0) + len(r.samples)
    here = [r for r in recs if r.block == bi]
    if here:
        return max(here, key=lambda r: (count.get(r.cid, 0), -r.cid))
    like = max((r for r in recs if r.ri is not None), key=lambda r: (count.get(r.cid, 0), -r.cid))
    return lg.add_record(3, like.cid, bi, like, key)


def _home4(u: _Unit, files, cid_of, t):
    files = [li for li in files if cid_of.get(li) is not None]
    li, bi = _block_for(u, files, t)
    lg = u.logs[li]
    cid = cid_of[li]
    here = [r for r in lg.r4.get(cid, []) if r.block == bi]
    return here[0] if here else lg.add_record(4, cid, bi, None)


def _block_for(u: _Unit, files, t):
    files = sorted(files, key=lambda li: u.logs[li].meta.ref_ms)
    for li in files:
        lg = u.logs[li]
        if lg.blocks and lg.blocks[0].bs <= t <= lg.end_ms:
            bs = np.array([b.bs for b in lg.blocks], np.int64)
            return li, int(max(np.searchsorted(bs, t, side="right") - 1, 0))
    for li in files:
        if u.logs[li].blocks and u.logs[li].blocks[0].bs > t:
            return li, 0
    li = [x for x in files if u.logs[x].blocks][-1]
    lg = u.logs[li]
    bi = len(lg.blocks) - 1
    lg.new_dur[bi] = max(lg.new_dur.get(bi, lg.blocks[bi].dur), int(t - lg.blocks[bi].bs))
    return li, bi


# ---- type 4: the events of a section group over a scope of files, as jd_jdl reads them
def _t4_events(u: _Unit, files, cid_of):
    """Decoder-order events of one section group: per file (in order) per record (block order) its carry, then its
    samples. A carry that repeats an event already in the scope (the last change before its block) is 'derived'
    (recomputed when written) and not an event of its own here; the others are fixed events ('c')."""
    ev, seen = [], set()
    recs = []
    for li in files:
        cid = cid_of.get(li)
        if cid is None:
            continue
        for r in u.logs[li].r4.get(cid, []):
            recs.append(r)
            for t, v in r.samples:
                seen.add((t, v))
    for r in recs:
        if r.anchor is not None and r.at is not None:
            if (r.at, r.anchor) in seen:
                r.derived = True
            else:
                r.derived = False
                seen.add((r.at, r.anchor))
                ev.append((int(r.at), r.anchor, "c", r, -1))
        for k, (t, v) in enumerate(r.samples):
            ev.append((int(t), v, "s", r, k))
    return ev, recs


def _derive_t4(recs, u: _Unit):
    """The derived carries of a scope from the new stream: the last event before the record's first sample (or before
    the end of its block when it has none)."""
    ev = []
    for r in recs:
        if r.anchor is not None and r.at is not None and not r.derived:
            a, at = r.anchor_now()
            ev.append((int(at), a))
        for t, v in r.new_samples():
            ev.append((int(t), v))
    if not ev:
        return
    o = sorted(range(len(ev)), key=lambda i: ev[i][0])
    tt = np.array([ev[i][0] for i in o], np.int64)
    vv = [ev[i][1] for i in o]
    for r in recs:
        if not (r.derived or r.ri is None):
            continue
        if r.anchor is None and r.ri is not None:
            continue                                     # no carry as read: stays without one
        ns = r.new_samples()
        lg = u.logs[r.log]
        b = lg.blocks[r.block]
        lim = ns[0][0] if ns else b.bs + lg.new_dur.get(r.block, b.dur) + 1
        i = int(np.searchsorted(tt, lim, side="left")) - 1
        while i >= 0 and tt[i] >= lim:
            i -= 1
        if i < 0:
            na, nat = None, None
        else:
            na, nat = vv[i], int(tt[i])
        if (na, nat) != (r.anchor, r.at):
            r.anchor_set, r.new_anchor, r.new_at = True, na, nat


# ---- type 3: the samples of a channel (every channel id of the DDI) over a scope of files
def _t3_events(u: _Unit, files, key, mode):
    """Decoder-order events of one DDI over the files: its samples ('not available' values left out, as jd_jdl
    does); in sms mode (one file) also the value the file carries in ('v' at NEG: the anchor of the first record, in
    decode order, of the first channel id that has one, as jd_jdl.decode_file takes it)."""
    ev = []
    for li in files:
        recs = u.logs[li].r3.get(key, [])
        if mode == "sms":
            first = set()
            got = False
            for r in recs:
                if r.cid in first:
                    continue
                first.add(r.cid)
                if not got and r.anchor is not None and r.anchor > J.NA_RAW:
                    ev.append((NEG, r.anchor / r.fac + r.off, "v", r, -1))
                    got = True
        for r in recs:
            for k, (t, v) in enumerate(r.samples):
                if v > J.NA_RAW:
                    ev.append((int(t), v / r.fac + r.off, "s", r, k))
    return ev


def _derive_t3(u: _Unit, files, key, mode):
    """Anchors that repeat the channel's last sample before their block follow the new stream (per channel id, in
    decode order over the files: every file of the unit in ops_center mode, each file on its own in sms mode, where
    a file's first record of each channel id keeps the value the file carries in)."""
    groups = [files] if mode != "sms" else [[li] for li in files]
    for g in groups:
        last_o, last_n = {}, {}          # channel id -> (t, raw) of its last sample so far (as read / as written)
        for li in g:
            for r in u.logs[li].r3.get(key, []):
                c = r.cid
                if r.ri is None:
                    # an added record: the channel's last value before it; absolute (no anchor) when the channel has
                    # none before it in the scope, so that it can never become the value a file carries in
                    if c in last_n:
                        r.anchor_set, r.new_anchor, r.new_at = True, last_n[c][1], last_n[c][0]
                    else:
                        r.anchor_set, r.new_anchor, r.new_at = True, None, None
                elif (r.anchor is not None and not r.anchor_set and c in last_o and (r.at, r.anchor) == last_o[c]
                      and c in last_n and last_n[c] != last_o[c]):
                    r.anchor_set, r.new_anchor, r.new_at = True, last_n[c][1], last_n[c][0]
                if r.samples:
                    last_o[c] = (r.samples[-1][0], r.samples[-1][1])
                ns = r.new_samples()
                if ns:
                    last_n[c] = (ns[-1][0], ns[-1][1])


def _merge_channels(lg: _Log, key):
    """Every sample of a DDI in one channel id (the one with the most samples), in time order, block by block, so that
    the samples of the file read in record order are in time order; the other channel ids' records keep no sample."""
    recs = [r for r in lg.r3.get(key, [])]
    count = {}
    for r in recs:
        count[r.cid] = count.get(r.cid, 0) + len(r.samples)
    main = max(count, key=lambda c: (count[c], -c))
    for r in list(recs):
        if r.cid == main:
            continue
        ns = r.new_samples()
        if not ns:
            continue
        to = next((x for x in lg.r3[key] if x.cid == main and x.block == r.block), None)
        if to is None:
            to = lg.add_record(3, main, r.block, next(x for x in recs if x.cid == main and x.ri is not None), key)
        to.adds.extend((int(t), int(round(((v / r.fac + r.off) - to.off) * to.fac))) for t, v in ns)
        r.dele.update(range(len(r.samples)))
        r.adds.clear()
        r.setv.clear()


def _check(name, st_new, sv_new, q, kind, val, orig_vals, same, notes):
    """Self-check of a plan: the new stream at every look-up."""
    got = _evaluate(st_new, sv_new, q)
    bad = 0
    for k in range(len(q)):
        want = orig_vals[k] if kind[k] == T_ORIG else (val[k] if kind[k] == T_SET else None)
        if kind[k] == T_OFF:
            ok = same(got[k], None) or _nz_like_off(got[k])
        else:
            ok = same(got[k], want)
        bad += not ok
    if bad:
        raise JdlWriteError(f"internal: {name}: {bad} look-up(s) would not read the planned value")


def _nz_like_off(v):
    return v is None or (isinstance(v, (bytes, bytearray)) and not _mk(v)) or (isinstance(v, float) and _nz(v) == 0.0)


def _edit(u, name, events, tie, q, kind, val, edge, same, off_val, home, to_raw, notes):
    st, sv, fx, ph = _slots(events, tie)
    orig_vals = _evaluate(st, sv, q)
    drop, reval, add = _plan(st, sv, fx, q, kind, val, edge, same, off_val)
    st2, sv2 = _new_slots(st, sv, ph, drop, reval, add, tie)
    _check(name, st2, sv2, q, kind, val, orig_vals, same, notes)
    _apply(ph, drop, reval, add, home, to_raw)
    n = notes.setdefault("stream_edits", {})
    for k_, v_ in (("dropped", sum(len(ph[i]) for i in drop)), ("revalued", len(reval)), ("added", len(add))):
        n[k_] = n.get(k_, 0) + int(v_)
    return orig_vals


# ================================================================================================ section patterns
def _strip(on, w, y, w_new, yc):
    """Of the sections on (bool per section), the contiguous run (in their left-to-right order) whose total width is
    nearest w_new (ties: the wider) and, among those, whose centre is nearest yc -> bool per section."""
    idx = np.flatnonzero(on)
    if not len(idx):
        return on.copy()
    o = np.argsort(y[idx], kind="stable")
    idx = idx[o]
    ws, ys = w[idx], y[idx]
    cs = np.r_[0.0, np.cumsum(ws)]
    cy = np.r_[0.0, np.cumsum(ws * ys)]
    best, bk = None, None
    n = len(idx)
    for a in range(n):
        b = int(np.searchsorted(cs, cs[a] + w_new))
        for bb in (b - 1, b, b + 1):
            if a < bb <= n:
                wd = cs[bb] - cs[a]
                cen = (cy[bb] - cy[a]) / wd
                key = (round(abs(wd - w_new), 9), -round(wd, 9), round(abs(cen - yc), 9))
                if best is None or key < best:
                    best, bk = key, (a, bb)
    sel = np.zeros(len(on), bool)
    sel[idx[bk[0]:bk[1]]] = True
    return sel


def _pattern_states(state_by_grp, secs, sel):
    """Work states per group with the sections of secs not in sel switched off (the others as they were)."""
    out = {}
    for g, s in state_by_grp.items():
        if s is None:
            out[g] = None
            continue
        b = bytearray(s)
        for col in np.flatnonzero((secs.grp.values == g) & ~sel):
            ix = int(secs.cws_idx.values[col])
            if ix < len(b) and b[ix] == 1:
                b[ix] = 0
        out[g] = bytes(b)
    return out


# ================================================================================================ sms points
def _sms_samples(fd, lat0, lon0):
    """The header samples of a file as jd_jdl's sms mode groups them (jd_jdl._sms_file_points): times, point groups,
    the sections used (jd_jdl._sms_sections) and the on-pattern at every sample."""
    en = fd.ends.drop_duplicates("t_ms")
    far = np.hypot(*J._local_xy(en.lat1.values, en.lon1.values, lat0, lon0)) > J.OUTLIER_KM * 1000
    far |= np.hypot(*J._local_xy(en.lat2.values, en.lon2.values, lat0, lon0)) > J.OUTLIER_KM * 1000
    en = en[~far]
    t = en.t_ms.values.astype(np.int64)
    if not len(t):
        return None
    carried = fd.ends_carry is not None
    first, last, _ = J._sms_groups(t, fd.t0_ms if fd.t0_ms is not None else t[0], carried)
    secs = J._sms_sections(fd.meta)
    has = bool(secs is not None and len(secs) and fd.cws)
    on = np.zeros((len(t), len(secs) if secs is not None else 0), bool)
    if has:
        for col, (cid, ix) in enumerate(zip(secs.grp.values, secs.cws_idx.values)):
            if cid in fd.cws and ix < fd.cws[cid][1].shape[1]:
                o = np.argsort(fd.cws[cid][0], kind="stable")
                ct, cs = fd.cws[cid][0][o], fd.cws[cid][1][o]
                i = np.searchsorted(ct, t + SMS_SNAP_MS, side="right") - 1
                ok = i >= 0
                on[ok, col] = cs[i[ok], ix] == 1
    return {"t": t, "first": first, "last": last, "secs": secs, "has_states": has, "on": on}


def _c_ref(on_last, secs):
    """jd_jdl._sms_file_points' reference across the header: the middle of the sections on at the file's first
    widest point (0 without section states)."""
    if secs is None or not on_last.shape[1] or not len(on_last):
        return 0.0
    w = secs.width_m.values
    wl = on_last @ w
    if not wl.max() > 0:
        return 0.0
    widest = on_last[int(np.argmax(wl >= wl.max() - 1e-9))]
    if not widest.any():
        return 0.0
    y = secs.y_m.values
    return float(((y - w / 2)[widest].min() + (y + w / 2)[widest].max()) / 2)


def _sms_edge(limit):
    def edge(k, lo, hi):
        e = hi - SMS_SNAP_MS - SMS_EDGE_MS
        if lo is not None:
            e = max(e, lo + 1)
        e = min(e, hi)
        if limit is not None and e > limit and (lo is None or limit > lo):
            e = int(limit)
        return int(e)
    return edge


def _plan_sms(u: _Unit, df0, rows_u, kept_rows, narrow, widths, notes, warnings, ref_errors):
    """sms mode: every point of every written file. rows_u: df0 rows of this machine by t_ms."""
    use = [li for li in sorted(u.written) if len(u.fds[li].ends)]
    if not use:
        return
    lat0 = float(np.median(np.concatenate([u.fds[li].ends.lat1.values for li in use])))
    lon0 = float(np.median(np.concatenate([u.fds[li].ends.lon1.values for li in use])))
    n_mark = n_nar = n_nar_kept_ref = 0
    for li in use:
        fd, lg = u.fds[li], u.logs[li]
        S = _sms_samples(fd, lat0, lon0)
        if S is None:
            continue
        t, first, last, secs, on = S["t"], S["first"], S["last"], S["secs"], S["on"]
        npt = len(first)
        row = np.array([rows_u.get(int(t[x]), -1) for x in last], np.int64)
        reading = row >= 0
        kept_p = reading & np.where(reading, kept_rows[np.maximum(row, 0)], False)
        removed_p = reading & ~kept_p
        pt_of = np.repeat(np.arange(npt), last - first + 1)
        kind = np.where(removed_p[pt_of], T_OFF, T_ORIG).astype(np.int8)
        n_mark += int(removed_p.sum())
        # level 2: the narrowed kept points (their last sample)
        nar = {}
        if widths:
            for p in np.flatnonzero(kept_p):
                r = int(row[p])
                if r in narrow:
                    nar[p] = narrow[r]
        # the original reference across the header, and the patterns at every point's last sample after the edits
        on_last = on[last] if len(last) else np.zeros((0, on.shape[1]), bool)
        c0 = _c_ref(on_last, secs) if S["has_states"] else 0.0
        sel_of = {}
        if S["has_states"] and nar:
            # the decoded point sits at the header reference c0; the clean one `shift` beside it: the clean strip is
            # centred there across the header
            w, y = secs.width_m.values, secs.y_m.values
            for p, (w_new, shift) in nar.items():
                o = on_last[p]
                if o.any():
                    sel_of[p] = _strip(o, w, y, w_new, c0 + shift)
        new_last = on_last.copy()
        new_last[removed_p] = False
        for p, sel in sel_of.items():
            new_last[p] = sel
        c_fin = c0
        if S["has_states"] and npt:
            c1 = _c_ref(new_last, secs)
            if abs(c1 - c0) > 1e-9:
                wl0 = on_last @ secs.width_m.values
                ref0 = int(np.argmax(wl0 >= wl0.max() - 1e-9))
                if ref0 in sel_of:                   # a narrowed reference point keeps its width
                    del sel_of[ref0]
                    new_last[ref0] = on_last[ref0]
                    n_nar_kept_ref += 1
                    c1 = _c_ref(new_last, secs)
                if abs(c1 - c0) > 1e-9 and kept_p.any():
                    ref_errors.append({"log": os.path.basename(lg.meta.path), "machine": u.vin,
                                       "kept_readings": int(kept_p.sum()), "move_m": round(c1 - c0, 4),
                                       "first_widest_point_time_ms": int(t[last[ref0]])})
                    # what the decoder (and, by the model, SMS) then reads: every point of the log at the new
                    # reference, its lateral offset the other way (the centre of its sections on is the same)
                    for p in np.flatnonzero(kept_p):
                        u.expect.setdefault(int(t[last[p]]), {})["ref_move_m"] = c1 - c0
                    c_fin = c1
        # ---- section work states, group by group (the decoder's groups of this file)
        q = t + SMS_SNAP_MS
        edge = _sms_edge(lg.end_ms)
        if S["has_states"]:
            cid_of_grp = {int(g): int(c) for c, g in zip(lg.meta.sections.cws_id.values, lg.meta.sections.grp.values)}
            for g, cid in sorted(cid_of_grp.items()):
                if cid not in lg.r4:
                    continue
                ev, recs = _t4_events(u, [li], {li: cid})
                if not ev:
                    continue
                kd = kind.copy()
                val = [None] * len(q)
                if sel_of:
                    st, sv, _, _ = _slots(ev, "last")
                    for p, sel in sel_of.items():
                        k = int(last[p])
                        o = _evaluate(st, sv, [q[k]])[0]
                        s2 = _pattern_states({g: o}, secs, sel)[g]
                        if not _same4(s2, o):
                            kd[k] = T_SET
                            val[k] = s2
                width = max((len(e[1]) for e in ev if e[1] is not None), default=0)
                _edit(u, f"{os.path.basename(lg.meta.path)} group {g}", ev, "last", q, kd, val, edge, _same4,
                      lambda o, wd=width: _off4(o, wd), lambda tt, li=li, cid=cid: _home4(u, [li], {li: cid}, tt),
                      _raw4, notes)
                _derive_t4(recs, u)
        # ---- logged working width DDI 12001: 0 for the removed points, the clean width at a narrowed point's last
        # sample (the width jd_jdl and SMS read in a file without section states; written in every file)
        if KEY_WIDTH in lg.r3 and lg.r3[KEY_WIDTH] and ((kind != T_ORIG).any() or nar):
            ev = _t3_events(u, [li], KEY_WIDTH, "sms")
            kd = kind.copy()
            val = [None] * len(q)
            for p, (w_new, _) in nar.items():
                if not S["has_states"] or p in sel_of:
                    rec0 = lg.r3[KEY_WIDTH][0]
                    raw = int(round((w_new - rec0.off) * rec0.fac))
                    kd[int(last[p])] = T_SET
                    val[int(last[p])] = raw / rec0.fac + rec0.off
                    if not S["has_states"]:
                        sel_of.setdefault(p, None)
                        u.expect.setdefault(int(t[last[p]]), {})["width_m"] = raw / rec0.fac + rec0.off
            ts = fd.samples.get(KEY_WIDTH, (np.zeros(0, np.int64),))[0]
            unsorted = bool(len(ts) > 1 and (np.diff(ts) < 0).any())
            if unsorted and not S["has_states"]:
                # the width of these points: jd_jdl._held binary-searches the samples of every channel id of the DDI
                # in record order, which here are not in time order (a second channel's sample logged with the
                # first's): its reading is the target of every look-up, and the written file holds the DDI in one
                # channel id in time order, where that look-up is the last sample at or before the time
                exact = J._held(fd, KEY_WIDTH, t)
                for k in range(len(q)):
                    if kd[k] == T_ORIG:
                        kd[k], val[k] = T_SET, (float(exact[k]) if np.isfinite(exact[k]) else 0.0)
                notes["width_channels_merged_files"] = notes.get("width_channels_merged_files", 0) + 1
            _edit(u, f"{os.path.basename(lg.meta.path)} DDI 12001", ev, "last", q, kd, val, edge, _same3,
                  lambda o: 0.0, lambda tt, li=li: _home3(u, [li], KEY_WIDTH, tt), _raw3, notes)
            if unsorted and not S["has_states"]:
                _merge_channels(lg, KEY_WIDTH)
            _derive_t3(u, [li], KEY_WIDTH, "sms")
        elif nar and not S["has_states"]:
            warnings.append(f"{u.vin}: {os.path.basename(lg.meta.path)} has neither section states nor a width "
                            "channel: its narrowed readings keep their width")
        # the expected narrowed points (section patterns)
        if S["has_states"]:
            w, y = secs.width_m.values, secs.y_m.values
            for p, sel in sel_of.items():
                if sel is None:
                    continue
                ws = float(sel @ w)
                u.expect.setdefault(int(t[last[p]]), {}).update(
                    width_m=ws, n_act=int(sel.sum()), y_off_m=float((sel @ (w * y)) / ws) - c_fin)
        n_nar += len(sel_of)
    notes.update(points_marked_off=int(n_mark), points_narrowed=int(n_nar),
                 narrowed_reference_points_kept_at_their_width=int(n_nar_kept_ref))


def _centre(on, w, y):
    return float((on @ (w * y)) / (on @ w))


# ================================================================================================ ops_center readings
def _sms_cuts(lg: _Log, fd) -> tuple[np.ndarray, np.ndarray]:
    """Where a section change cuts no SMS point of this log: intervals (lo, hi] of change times (SMS makes its points
    of the 5 Hz header samples of every log on its own, SMS_WINDOW_S of sample intervals per point, and looks every
    sample's sections up SMS_SNAP_MS after it). Returns (lo, hi) arrays with the open ends."""
    en = fd.ends
    if en is None or not len(en):
        return np.array([-np.inf]), np.array([np.inf])
    t = np.unique(en.t_ms.values.astype(np.int64))
    first, last, _ = J._sms_groups(t, fd.t0_ms if fd.t0_ms is not None else t[0], fd.ends_carry is not None)
    lo = np.r_[-np.inf, t[np.array(last)] + SMS_SNAP_MS]
    hi = np.r_[t[np.array(first)] + SMS_SNAP_MS, np.inf]
    return lo.astype(float), hi.astype(float)


def _sms_cut_set(u: _Unit, files):
    pts = [_sms_cuts(u.logs[li], u.fds[li]) for li in sorted(files, key=lambda x: u.logs[x].meta.ref_ms)]
    if not pts:
        return np.array([-np.inf]), np.array([np.inf])
    L, H = [pts[0][0][:1]], [pts[0][1][:1]]
    for k, (lo, hi) in enumerate(pts):
        L.append(lo[1:-1])
        H.append(hi[1:-1])
        L.append(lo[-1:])
        H.append(pts[k + 1][1][:1] if k + 1 < len(pts) else hi[-1:])
    lo = np.concatenate(L).astype(float)
    hi = np.concatenate(H).astype(float)
    ok = hi > lo
    lo, hi = lo[ok], hi[ok]
    o = np.argsort(lo, kind="stable")
    return lo[o], hi[o]


def _snap(target, win_lo, win_hi, lo_c, hi_c):
    """The integer change time nearest to `target` inside (win_lo, win_hi] that cuts no SMS point (lo_c, hi_c: the
    sorted, disjoint cut intervals (lo, hi]); else target moved into the window."""
    i0 = int(np.searchsorted(hi_c, win_lo, side="right"))
    i1 = int(np.searchsorted(lo_c, win_hi, side="left"))
    best = None
    for i in range(i0, min(i1 + 1, len(lo_c))):
        a = max(lo_c[i], win_lo)
        b = min(hi_c[i], win_hi)
        if not b > a:
            continue
        lo_i = int(np.floor(a)) + 1 if np.isfinite(a) else -(1 << 62)
        hi_i = int(np.floor(b)) if np.isfinite(b) else (1 << 62)
        if hi_i < lo_i:
            continue
        c = min(max(int(round(target)), lo_i), hi_i)
        if best is None or abs(c - target) < abs(best - target):
            best = c
    if best is not None:
        return int(best), True
    return int(min(max(target, win_lo + 1), win_hi)), False


def _plan_ops(u: _Unit, df0, rows_u, kept_rows, narrow, widths, notes, warnings):
    """ops_center mode: one reading per flow sample of the written files merged."""
    files = sorted(u.written)
    fds = [u.fds[li] for li in files]
    m0 = fds[0].meta
    d, _ = J._readings(fds, m0)
    t = d.t_ms.values.astype(np.int64)
    n = len(t)
    row = np.array([rows_u.get(int(x), -1) for x in t], np.int64)
    kept = (row >= 0) & np.where(row >= 0, kept_rows[np.maximum(row, 0)], False)
    _, src_i = J._sample_files(fds, KEY_FLOW)
    src = np.array(files, np.int64)[src_i]
    samples = J._concat_files(fds)[0]
    # how jd_jdl takes the widths of this unit (the same test as jd_jdl._readings, per reading's own layout)
    keys = {li: J.layout_key(u.fds[li].meta.sections) for li in files}
    hw_r = np.array([float(u.fds[s].meta.sections.width_m.sum()) if keys[s] is not None else u.fds[s].meta.width_m
                     for s in src], float)
    if KEY_WIDTH in samples:
        tw, vw = samples[KEY_WIDTH]
        w12 = J._step_at(tw, vw, t + LAG_WIDTH_MS)
    else:
        w12 = np.full(n, np.nan)
    with np.errstate(invalid="ignore"):
        wide = (np.isfinite(hw_r) & (hw_r > 0)) & (w12 > hw_r * (1 + J.WIDTH_TOL))
    sec_w = d.sec_width_m.values if "sec_width_m" in d else np.full(n, np.nan)
    sections_mode = bool("sec_width_m" in d and (wide.any() or np.isnan(w12).all()))
    from_12001 = ~np.isfinite(sec_w) if sections_mode else np.ones(n, bool)
    notes["width_from"] = ("the sections on (DDI 12001 reads wider than the header, or is missing)" if sections_mode
                           else "DDI 12001")
    rs = d.reason.astype(str).values
    why = pd.Series(np.where(row >= 0, "a reading the cleaning removed", "left out by the decoder: ") +
                    np.where(row >= 0, "", np.where(rs == "", "position_outlier / no_moisture", rs)))[~kept]
    why = why.value_counts()
    notes.update(flow_samples=int(n), kept=int(kept.sum()), marked_off=int((~kept).sum()),
                 marked_off_by_reason={str(k): int(v) for k, v in why.items()})
    # level 2
    nar = {}
    if widths:
        for i in np.flatnonzero(kept):
            r = int(row[i])
            if r in narrow:
                nar[int(i)] = narrow[r]
    # ---- section work states, per section layout (jd_jdl merges the files of one layout)
    sel_of = {}
    by_key = {}
    for li in files:
        by_key.setdefault(keys[li], []).append(li)
    for key, scope in by_key.items():
        if key is None:
            continue
        rows_s = np.flatnonzero(np.isin(src, scope))
        if not len(rows_s):
            continue
        meta = u.fds[scope[0]].meta
        secs = meta.sections
        cid_of_grp = {int(g): int(c) for c, g in zip(secs.cws_id.values, secs.grp.values)}
        P = t[rows_s] + LAG_SECTION_MS
        lo_c, hi_c = _sms_cut_set(u, scope)
        snapped = [0, 0]

        def edge(k, lo, hi, rows_s=rows_s, lo_c=lo_c, hi_c=hi_c, snapped=snapped):
            e, ok = _snap(float(t[rows_s[k]]), float(lo) if lo is not None else -np.inf, float(hi), lo_c, hi_c)
            snapped[0 if ok else 1] += 1
            return e
        kd0 = np.where(kept[rows_s], T_ORIG, T_OFF).astype(np.int8)
        streams = {}
        for g, _ in sorted(cid_of_grp.items()):
            cid_of = {li: next((int(c) for c, gg in zip(u.logs[li].meta.sections.cws_id.values,
                                                         u.logs[li].meta.sections.grp.values) if int(gg) == g), None)
                      for li in scope}
            ev, recs = _t4_events(u, scope, cid_of)
            if ev:
                streams[g] = (ev, recs, cid_of)
        # the narrowed readings: their pattern over this layout
        if nar:
            w, y = secs.width_m.values, secs.y_m.values
            at = {g: _slots(s[0], "last") for g, s in streams.items()}
            for jj, i in enumerate(rows_s):
                if int(i) not in nar:
                    continue
                st_by = {g: _evaluate(a[0], a[1], [P[jj]])[0] for g, a in at.items()}
                on = np.zeros(len(secs), bool)
                for col, (g, ix) in enumerate(zip(secs.grp.values, secs.cws_idx.values)):
                    s0 = st_by.get(int(g))
                    on[col] = s0 is not None and ix < len(s0) and s0[ix] == 1
                if not on.any():
                    continue
                w_new, shift = nar[int(i)]
                y_act = _centre(on, w, y)
                sel = _strip(on, w, y, w_new, y_act + shift)
                sel_of[int(i)] = (sel, _pattern_states(st_by, secs, sel), float(sel @ w), _centre(sel, w, y), y_act)
        for g, (ev, recs, cid_of) in streams.items():
            kd = kd0.copy()
            val = [None] * len(rows_s)
            for jj, i in enumerate(rows_s):
                if int(i) in sel_of:
                    kd[jj] = T_SET
                    val[jj] = sel_of[int(i)][1][g]
            wdt = max((len(e[1]) for e in ev if e[1] is not None), default=0)
            _edit(u, f"section group {g}", ev, "last", P, kd, val, edge, _same4, lambda o, wd=wdt: _off4(o, wd),
                  lambda tt, scope=scope, cid_of=cid_of: _home4(u, scope, cid_of, tt), _raw4, notes)
            _derive_t4(recs, u)
        notes["span_edges_at_sms_point_boundaries"] = notes.get("span_edges_at_sms_point_boundaries", 0) + snapped[0]
        notes["span_edges_cutting_an_sms_point"] = notes.get("span_edges_cutting_an_sms_point", 0) + snapped[1]
    # ---- logged working width DDI 12001 (the width jd_jdl reads where it does not take the sections)
    have12 = any(u.logs[li].r3.get(KEY_WIDTH) for li in files)
    if have12:
        L = t + LAG_WIDTH_MS
        kd = np.where(from_12001 & ~kept, T_OFF, T_ORIG).astype(np.int8)
        val = [None] * n
        rec0 = next(r for li in files for r in u.logs[li].r3.get(KEY_WIDTH, []))
        for i, (w_new, _) in nar.items():
            if from_12001[i]:
                raw = int(round((w_new - rec0.off) * rec0.fac))
                kd[i] = T_SET
                val[i] = raw / rec0.fac + rec0.off
                u.expect.setdefault(int(t[i]), {})["width_m"] = val[i]

        def edge3(k, lo, hi):
            e = int(t[k]) + LAG_SECTION_MS
            if lo is not None:
                e = max(e, lo + 1)
            return int(min(e, hi))
        ev = _t3_events(u, files, KEY_WIDTH, "ops_center")
        _edit(u, "DDI 12001", ev, "first", L, kd, val, edge3, _same3, lambda o: 0.0,
              lambda tt: _home3(u, files, KEY_WIDTH, tt), _raw3, notes)
        _derive_t3(u, files, KEY_WIDTH, "ops_center")
    elif sections_mode is False:
        warnings.append(f"{u.vin}: no working-width channel (DDI 12001) to mark")
    # the expected narrowed readings
    heading = d.heading.values
    for i, (sel, _, ws, yc_new, y_act) in sel_of.items():
        e = u.expect.setdefault(int(t[i]), {})
        e.update(n_act=int(sel.sum()), y_off_m=yc_new, move_m=yc_new - y_act, heading=float(heading[i]))
        if not from_12001[i]:
            e["width_m"] = ws
    notes["readings_narrowed"] = len(sel_of)


def _choose_logs(u: _Unit, mode, kept_t: np.ndarray) -> set:
    """drop_logs: the logs that are written: every log with a kept reading (ops_center: within FILE_MARGIN_MS of its
    data), and the first log of the unit (its definitions name the unit's field and crop for the decoder)."""
    w = {0}
    margin = 1000 if mode == "sms" else FILE_MARGIN_MS
    for li, lg in enumerate(u.logs):
        if not lg.blocks:
            continue
        i = np.searchsorted(kept_t, lg.start_ms - margin, side="left")
        if i < len(kept_t) and kept_t[i] <= lg.end_ms + margin:
            w.add(li)
    if mode != "sms":
        # the decoder reads the sections only while some flow sample sees a DDI 12001 wider than the header: keep the
        # logs that hold such a sample
        for li, lg in enumerate(u.logs):
            secs = lg.meta.sections
            hw = float(secs.width_m.sum()) if secs is not None and len(secs) else lg.meta.width_m
            for r in lg.r3.get(KEY_WIDTH, []):
                if any(v > J.NA_RAW and v / r.fac + r.off > hw * (1 + J.WIDTH_TOL) for _, v in r.samples):
                    w.add(li)
                    break
    return w


def _scale(u: _Unit, factor: float, notes: dict):
    """Level 3: every value of the grain flow and of the monitor's yields times factor, rounded to the raw step; the
    anchors with them; "not available" values stay."""
    def f(v):
        return int(v) if v is None or int(v) <= J.NA_RAW else int(round(int(v) * factor))
    n = 0
    for li in sorted(u.written):
        for key in SCALED_KEYS:
            for r in u.logs[li].r3.get(key, []):
                for k, (_, v) in enumerate(r.samples):
                    v2 = f(r.setv.get(k, v))
                    if v2 != v:
                        r.setv[k] = v2
                n += len(r.samples)
                if r.anchor is not None:
                    r.anchor_set, r.new_anchor, r.new_at = True, f(r.anchor), r.at
    notes["flow_factor"] = {"factor": float(factor), "values_scaled": int(n)}


# ================================================================================================ source and clean
def _source_params(source):
    """(input root, unit ids, DecodeOptions, decode report, points mode) of a decoded input (engine.run.decode's
    'source')."""
    if not isinstance(source, dict):
        raise JdlWriteError("no decoded source: the clean file must come from a decode of raw .jdl logs in this app")
    fmt = source.get("format")
    if fmt != FORMAT_ID:
        raise JdlWriteError(f"this writer writes {FORMAT_ID} logs; the source is {fmt or 'not a raw decode'}")
    rep = {}
    rj = source.get("decode_report")
    if rj and os.path.exists(rj):
        with open(rj, encoding="utf-8") as fh:
            rep = json.load(fh)
    root = source.get("input_root") or rep.get("input_root")
    units = source.get("unit_ids") or rep.get("units")
    o = source.get("options") or rep.get("options") or {}
    if not root or not os.path.isdir(root):
        raise JdlWriteError("the original logs are not on disk any more: open the raw input again")
    if not units:
        raise JdlWriteError("the source does not say which units were decoded")
    kw = source.get("decoder_kwargs") or rep.get("decoder_kwargs") or {}
    mode = rep.get("points") or kw.get("points") or ("ops_center" if rep else None)
    if mode not in POINTS:
        raise JdlWriteError(f"unknown points mode {mode!r} of the decode")
    fields = {f.name for f in dataclasses.fields(C.DecodeOptions)}
    opts = C.DecodeOptions(**{k: v for k, v in o.items() if k in fields})
    return os.path.abspath(root), list(units), opts, rep, mode


def can_write(source: dict | None) -> tuple[bool, str]:
    """(True, '') when write_clean can write this source back as .jdl logs, else (False, the reason)."""
    try:
        _source_params(source)
    except JdlWriteError as e:
        return False, str(e)
    return True, ""


def _inside(path, folder):
    a, b = os.path.normcase(os.path.abspath(path)), os.path.normcase(os.path.abspath(folder))
    try:
        return os.path.commonpath([a, b]) == b
    except ValueError:
        return False


def _read_clean(clean_shp):
    """The clean points with lon / lat (WGS 84) from their geometry."""
    import pyogrio
    if not clean_shp or not os.path.exists(clean_shp):
        raise JdlWriteError("the clean file is not on disk")
    g = pyogrio.read_dataframe(clean_shp)
    if g.crs is not None and g.crs.to_epsg() != 4326:
        g = g.to_crs(4326)
    df = pd.DataFrame(g.drop(columns="geometry"))
    ok = g.geometry.notna().values
    df["lon"] = np.where(ok, g.geometry.x.values, np.nan) if len(g) else np.zeros(0)
    df["lat"] = np.where(ok, g.geometry.y.values, np.nan) if len(g) else np.zeros(0)
    need = ["Dataset", "Obj__Id", "Time", "Machine"]
    miss = [c for c in need if c not in df.columns]
    if miss:
        raise JdlWriteError(f"the clean file has no {', '.join(miss)} column: it is not a decoded {FORMAT_ID} file")
    if "Src_Fmt" in df.columns and len(df) and not (df.Src_Fmt.astype(str) == FORMAT_ID).all():
        raise JdlWriteError(f"the clean file holds readings of another format ({sorted(set(df.Src_Fmt.astype(str)))})")
    return df


def _match(clean: pd.DataFrame, df0: pd.DataFrame):
    """Rows of the clean file -> rows of the decode of the original logs, by (Dataset, Obj__Id); Time and Machine must
    agree."""
    k0 = pd.Series(np.arange(len(df0)), index=pd.MultiIndex.from_arrays(
        [df0.Dataset.astype(str).str.slice(0, C.STR_WIDTH["Dataset"]).values, df0.Obj__Id.astype(np.int64).values]))
    if not k0.index.is_unique:
        raise JdlWriteError("(Dataset, Obj__Id) is not unique in the decode of the original logs")
    keys = pd.MultiIndex.from_arrays([clean.Dataset.astype(str).values, clean.Obj__Id.astype(np.int64).values])
    pos = k0.reindex(keys)
    if pos.isna().any():
        n = int(pos.isna().sum())
        raise JdlWriteError(f"{n:,} clean reading(s) are not readings of the original logs (Dataset + Obj__Id): the "
                            "clean file was not made from this decode (same units, options and points)")
    pos = pos.astype(np.int64).values
    t_ok = (df0.Time.astype(str).values[pos] == clean.Time.astype(str).values)
    m_ok = (df0.Machine.astype(str).values[pos] == clean.Machine.astype(str).values)
    if not (t_ok.all() and m_ok.all()):
        raise JdlWriteError(f"{int((~t_ok).sum()):,} clean reading(s) differ in Time and {int((~m_ok).sum()):,} in "
                            "Machine from the reading of the same Dataset and Obj__Id: not the same decode")
    if len(np.unique(pos)) != len(pos):
        raise JdlWriteError("the clean file holds a reading twice")
    return pos


def _quiet(_m):
    pass


# ================================================================================================ round trip
COMPARE = ["Field", "Product", "Time", "Duration_s", "Elevation_", "Speed_mph_", "Distance_f", "Track_deg_",
           "Swth_Wdth_", "Moisture__", "Yld_Mass_W", "Yld_Mass_D", "Yld_Vol_We", "Yld_Vol_Dr", "lon", "lat", "n_act",
           "Y_Offset_f"]
PER_AREA = ("Yld_Mass_W", "Yld_Mass_D", "Yld_Vol_We", "Yld_Vol_Dr")


def _expected(df0k: pd.DataFrame, units, factors) -> tuple[pd.DataFrame, np.ndarray]:
    """The kept readings as the decoder must return them from the written logs: the narrowed ones at their written
    width / sections (per-area values scaled by old / new width; ops_center: moved to the centre of the sections
    left on), the factor on the per-area values. Returns (expected, moved: bool per row)."""
    e = df0k.copy().reset_index(drop=True)
    moved = np.zeros(len(e), bool)
    exp = {}
    for u in units:
        for tm, x in u.expect.items():
            exp[(u.vin, tm)] = x
    r = np.ones(len(e))
    mach = e.Machine.astype(str).values
    tms = e.t_ms.values.astype(np.int64)
    for j in range(len(e)):
        x = exp.get((mach[j], int(tms[j])))
        if not x:
            continue
        if "width_m" in x:
            w1 = x["width_m"] * C.FT_PER_M
            r[j] = float(e.at[j, "Swth_Wdth_"]) / w1
            e.at[j, "Swth_Wdth_"] = w1
        if "n_act" in x:
            e.at[j, "n_act"] = x["n_act"]
        if "y_off_m" in x:
            e.at[j, "Y_Offset_f"] = x["y_off_m"] * C.FT_PER_M
        if "ref_move_m" in x:
            # sms: the log's reference across the header moved; the point with it, its offset the other way
            mv = x["ref_move_m"]
            if "y_off_m" not in x:
                e.at[j, "Y_Offset_f"] = float(e.at[j, "Y_Offset_f"]) - mv * C.FT_PER_M
            x = dict(x, move_m=mv + x.get("move_m", 0.0), heading=float(e.at[j, "Track_deg_"]))
        if "move_m" in x and abs(x["move_m"]) > 0:
            h = np.radians(x["heading"])
            lat, lon = J._local_ll(np.cos(h) * x["move_m"], -np.sin(h) * x["move_m"], float(e.at[j, "lat"]),
                                   float(e.at[j, "lon"]))
            e.at[j, "lat"], e.at[j, "lon"] = float(lat), float(lon)
            moved[j] = True
    f = e.Machine.map(lambda v: factors.get(str(v), 1.0)).astype(float).values
    for c in PER_AREA:
        e[c] = e[c].values * r * f
    return e, moved


def compare(exp: pd.DataFrame, got: pd.DataFrame, rel_tol: dict | None = None, abs_tol: dict | None = None) -> dict:
    """Readings expected vs decoded from the written logs, matched by (Machine, t_ms): counts and, per column, the
    number of readings that differ, the number beyond the tolerance (relative rel_tol or absolute abs_tol) and the
    largest difference."""
    rel_tol = rel_tol or {}
    abs_tol = abs_tol or {}
    a = exp.set_index(["Machine", "t_ms"])
    b = got.set_index(["Machine", "t_ms"])
    both = a.index.intersection(b.index)
    out = {"n_expected": int(len(a)), "n_decoded": int(len(b)), "n_matched": int(len(both)),
           "n_missing": int(len(a.index.difference(b.index))), "n_extra": int(len(b.index.difference(a.index))),
           "columns": {}}
    a, b = a.loc[both], b.loc[both]
    for c in COMPARE:
        if c not in a.columns or c not in b.columns:
            continue
        x, y = a[c].values, b[c].values
        if x.dtype.kind in "fiu" and y.dtype.kind in "fiu":
            x, y = x.astype(float), y.astype(float)
            same = (x == y) | (np.isnan(x) & np.isnan(y))
            d = np.abs(x - y)
            rel = d / np.maximum(np.abs(x), 1e-12)
            tol, at = rel_tol.get(c), abs_tol.get(c)
            bad = ~(same | ((rel <= tol) if tol is not None else False) | ((d <= at) if at is not None else False))
            out["columns"][c] = {"n_differ": int((~same).sum()), "n_beyond_tol": int(bad.sum()),
                                 "max_abs": float(np.nanmax(np.where(same, 0, d))) if len(d) else 0.0,
                                 "max_rel": float(np.nanmax(np.where(same, 0, rel))) if len(d) else 0.0}
        else:
            ne = x.astype(str) != y.astype(str)
            out["columns"][c] = {"n_differ": int(ne.sum()), "n_beyond_tol": int(ne.sum())}
    out["ok"] = (out["n_missing"] == 0 and out["n_extra"] == 0
                 and all(v["n_beyond_tol"] == 0 for v in out["columns"].values()))
    return out


def _non_kept_check(out_root, unit_ids, opts, mode, df0, kept_rows, units):
    """Every reading of the original decode that is not kept, in the written logs: with no width and no section on
    (sms: the point; ops_center: the flow sample), and the points the decoder did not export still not exported."""
    metas, _ = J._scan(out_root)
    groups = {}
    for m in metas:
        groups.setdefault(J._unit_key(m), []).append(m)
    res = {"n_not_kept": 0, "n_with_width": 0, "n_with_sections_on": 0, "n_absent": 0}
    for u in units:
        ms = sorted(groups.get(u.uid, []), key=lambda x: x.ref_ms)
        if not ms:
            continue
        fds = [J.decode_file(m.path, m) for m in ms]
        d = (J._sms_points(fds, ms[0]) if mode == "sms" else J._readings(fds, ms[0]))[0]
        rows = np.flatnonzero(df0.Machine.astype(str).values == u.vin)
        nk = df0.t_ms.values[rows[~kept_rows[rows]]].astype(np.int64)
        if mode != "sms":
            # every flow sample of the unit that is not a kept reading
            kt = set(df0.t_ms.values[rows[kept_rows[rows]]].astype(np.int64).tolist())
            nk = np.array([x for x in d.t_ms.values.astype(np.int64) if int(x) not in kt], np.int64)
        dd = d.drop_duplicates("t_ms").set_index("t_ms")
        hit = dd.index.intersection(nk)
        res["n_not_kept"] += int(len(nk))
        res["n_absent"] += int(len(nk) - len(hit))
        if len(hit):
            w = dd.loc[hit, "width_m"].values.astype(float)
            res["n_with_width"] += int((np.nan_to_num(w) > 0).sum())
            na = dd.loc[hit, "n_act"].values.astype(float)
            res["n_with_sections_on"] += int((na > 0).sum())
    res["ok"] = res["n_with_width"] == 0 and res["n_with_sections_on"] == 0
    return res


def _reference_of_written(all_logs, root, out_root, unit_ids, opts, mode, kept0):
    """drop_logs: the kept readings as jd_jdl reads the ORIGINAL logs of the files written (hard links, else copies,
    in a folder next to the written one, removed after), and how far they are from the decode of every log."""
    ref = out_root + "_reference"
    try:
        for _, _, lg in all_logs:
            dst = os.path.join(ref, os.path.relpath(lg.meta.path, root))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            try:
                os.link(lg.meta.path, dst)
            except OSError:
                shutil.copyfile(lg.meta.path, dst)
        dw, _ = J.decode_detailed(ref, unit_ids, opts, log=_quiet, points=mode)
    finally:
        shutil.rmtree(ref, ignore_errors=True)
    k = pd.MultiIndex.from_arrays([kept0.Machine.astype(str).values, kept0.t_ms.values.astype(np.int64)])
    w = dw.set_index([dw.Machine.astype(str), dw.t_ms.astype(np.int64)])
    miss = k.difference(w.index)
    if len(miss):
        raise JdlWriteError(f"internal: {len(miss):,} kept reading(s) are not readings of the logs written")
    base = w.loc[k].reset_index(drop=True)
    eff = {}
    for c in ("Distance_f", "Yld_Vol_Dr"):
        x, y = kept0[c].values.astype(float), base[c].values.astype(float)
        with np.errstate(invalid="ignore", divide="ignore"):
            eff[c] = float(np.nanmax(np.abs(y / x - 1))) if len(x) else 0.0
    dx, dy = J._local_xy(base.lat.values, base.lon.values, kept0.lat.values, kept0.lon.values)
    eff["position_m"] = float(np.nanmax(np.hypot(dx, dy))) if len(dx) else 0.0
    return base, eff


def _totals(df):
    if not len(df):
        return {"n": 0, "area_ac": 0.0, "wet_lb": 0.0, "dry_bu": 0.0, "bu_ac": None}
    a = pd.to_numeric(df.Swth_Wdth_, errors="coerce").values * pd.to_numeric(df.Distance_f, errors="coerce").values \
        / 43560.0
    wet = float(np.nansum(pd.to_numeric(df.Yld_Mass_W, errors="coerce").values * a))
    dry = float(np.nansum(pd.to_numeric(df.Yld_Vol_Dr, errors="coerce").values * a))
    area = float(np.nansum(a))
    return {"n": int(len(df)), "area_ac": area, "wet_lb": wet, "dry_bu": dry, "bu_ac": dry / area if area > 0 else None}


# ================================================================================================ the writer
def write_clean(source: dict, clean_shp: str, out_dir: str, *, widths: bool = True, factors: dict | None = None,
                new_ids: bool = True, log=print, drop_logs: bool = False, verify: bool = True,
                allow_reference_shift: bool = False, out_name: str | None = None) -> dict:
    """Write the readings of `clean_shp` (a subset of the decoded readings of `source`) as John Deere .jdl logs.

    source: engine.run.decode(...)["source"] (format, decode report with the input root, units, options, points);
    clean_shp: the clean point file (its rows keep Dataset, Obj__Id, Time and Machine of the decoded file);
    widths: write the clean swath width of each narrowed / shortened reading (level 2);
    factors: {machine (VIN): factor} on the grain flow (level 3); new_ids: new GUIDs (SMS refuses archived logs);
    drop_logs: leave out the logs with no kept reading; verify: decode the written logs again and compare (on by
    default; a failed round trip raises JdlRoundTripError and leaves nothing on disk);
    allow_reference_shift (sms points only): when the cleaning removed a log's first widest point and its kept points
    would be placed sideways (see the module docstring), write anyway, with that move predicted and checked per
    reading and named in the warnings (default False: JdlReferenceError, nothing written);
    out_name: the name of the folder in out_dir that holds the logs (default <clean file name>_jdl; a time stamp is
    added when that folder or its zip exists).
    Returns {path, zip, n_kept, n_removed, files_changed, notes, warnings, round_trip, check, expected_summary,
    clean_summary, details, bytes, logs_written, logs_in_units, points}. Raises JdlWriteError (a ValueError) with a
    plain message when the input cannot be written (empty clean file, clean file not from this source, input gone,
    out_dir inside the input, a log's reference across the header would move in sms mode)."""
    root, unit_ids, opts, rep, mode = _source_params(source)
    out_abs = os.path.abspath(out_dir)
    if _inside(out_abs, root):
        raise JdlWriteError("the output folder is inside the input folder: choose another folder (the original logs "
                            "are never written over)")
    clean = _read_clean(clean_shp)
    if not len(clean):
        raise JdlWriteError("the clean file holds no reading: there is nothing to write")
    log(f"jdl writer: decoding the original logs again ({len(unit_ids)} unit(s), points={mode})")
    df0, _ = J.decode_detailed(root, unit_ids, opts, log=_quiet, points=mode)
    pos = _match(clean, df0)
    kmt = pd.MultiIndex.from_arrays([df0.Machine.astype(str).values, df0.t_ms.values.astype(np.int64)])
    if not kmt.is_unique:
        raise JdlWriteError("two readings of one machine share a time stamp (t_ms): the readings cannot be told apart")
    kept_rows = np.zeros(len(df0), bool)
    kept_rows[pos] = True
    warnings, notes = [], {}
    factors = {str(k): float(v) for k, v in (factors or {}).items() if v is not None}
    units = _units(root, unit_ids)
    vins = {u.vin for u in units}
    for k in factors:
        if k not in vins:
            warnings.append(f"factor for {k!r}: no such machine in these logs ({', '.join(sorted(vins))})")
    # ---- level 2: the narrowed / shortened kept readings (Optimize Swath Widths), df0 row -> (width m, shift m)
    narrow, n_area = {}, 0
    if widths and "Swth_Wdth_" in clean.columns:
        w1 = pd.to_numeric(clean.Swth_Wdth_, errors="coerce").values
        d1 = pd.to_numeric(clean.Distance_f, errors="coerce").values if "Distance_f" in clean.columns else \
            np.full(len(clean), np.nan)
        for j, r in enumerate(pos):
            w0 = float(df0.Swth_Wdth_.values[r])
            d0 = float(df0.Distance_f.values[r])
            if not np.isfinite(w1[j]):
                continue
            cut = np.isfinite(d1[j]) and d0 > 0 and d1[j] < d0 * (1 - 1e-9)
            if w1[j] >= w0 - 1e-9 and not cut:
                continue
            w_ft = w1[j] * (d1[j] / d0 if cut else 1.0)
            n_area += bool(cut)
            h = np.radians(float(df0.Track_deg_.values[r]))
            dx, dy = J._local_xy(clean.lat.values[j], clean.lon.values[j], float(df0.lat.values[r]),
                                 float(df0.lon.values[r]))
            shift = float(dx * np.cos(h) - dy * np.sin(h)) if np.isfinite(dx) else 0.0   # + = right of travel
            narrow[int(r)] = (w_ft / C.FT_PER_M, shift)
    n_kept = int(kept_rows.sum())
    n_removed = int(len(df0) - n_kept)
    # ---- the logs to write and the edits
    ref_errors = []
    for u in units:
        rows = np.flatnonzero(df0.Machine.astype(str).values == u.vin)
        rows_u = dict(zip(df0.t_ms.values[rows].astype(np.int64).tolist(), rows.tolist()))
        kt = np.sort(df0.t_ms.values[rows[kept_rows[rows]]].astype(np.int64))
        u.written = _choose_logs(u, mode, kt) if drop_logs else set(range(len(u.logs)))
        nu = {"logs": len(u.logs), "logs_written": len(u.written), "copies_left_out": u.copies_left_out,
              "readings": int(len(rows)), "kept": int(kept_rows[rows].sum())}
        if mode == "sms":
            _plan_sms(u, df0, rows_u, kept_rows, narrow, widths, nu, warnings, ref_errors)
        else:
            _plan_ops(u, df0, rows_u, kept_rows, narrow, widths, nu, warnings)
        if factors.get(u.vin) is not None and abs(factors[u.vin] - 1.0) > 1e-12:
            _scale(u, factors[u.vin], nu)
        if u.copies_left_out:
            warnings.append(f"{u.vin}: {u.copies_left_out} shorter cop(y/ies) of a log (the same GUID) left out of "
                            "the card: the longest copy holds the same data")
        notes[u.uid] = nu
        log(f"jdl writer: {u.vin}: {nu['kept']:,} of {nu['readings']:,} readings kept; {len(u.written)} of "
            f"{len(u.logs)} logs written")
    if ref_errors:
        n = sum(e["kept_readings"] for e in ref_errors)
        moves = ", ".join(sorted({f"{abs(e['move_m']):.3f} m" for e in ref_errors}))
        if allow_reference_shift:
            warnings.append(f"{n:,} kept reading(s) in {len(ref_errors)} log(s) whose first widest point the cleaning "
                            f"removed are placed {moves} sideways (SMS's reference across the header: the sections "
                            "on at a log's first widest point); their lateral offset moves the other way, so the "
                            "strip they cover, their width, area, grain and moisture are unchanged: "
                            + "; ".join(f"{e['machine']} log {e['log']} ({e['kept_readings']:,} kept)"
                                        for e in ref_errors))
            notes["reference_moves"] = ref_errors
            ref_errors = []
    if ref_errors:
        n = sum(e["kept_readings"] for e in ref_errors)
        raise JdlReferenceError(
            f"not written: in {len(ref_errors)} log(s) the cleaning removed the first widest point, and the widest "
            "points left have another pattern of sections. SMS (and OCED's sms points) place every point of a log "
            "across the header by the sections on at its first widest point, so the "
            f"{n:,} kept reading(s) of those logs would move sideways (by "
            + ", ".join(sorted({f'{abs(e['move_m']):.3f} m' for e in ref_errors})) + "): "
            + "; ".join(f"{e['machine']} log {e['log']} ({e['kept_readings']:,} kept)" for e in ref_errors[:5])
            + (" ..." if len(ref_errors) > 5 else "") + ". Write it anyway with allow_reference_shift=True (the move "
            "is then predicted and checked per reading).", ref_errors)
    # ---- new identifiers, paths, version.txt
    if out_name is not None and (not str(out_name).strip() or any(c in str(out_name) for c in '/\\:')):
        raise JdlWriteError(f"the output folder name {out_name!r} is not a plain folder name")
    name = str(out_name) if out_name else os.path.splitext(os.path.basename(clean_shp))[0] + "_jdl"
    out_root = os.path.join(out_abs, name)
    if os.path.exists(out_root) or os.path.exists(out_root + ".zip"):
        out_root = out_root + "_" + _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    all_logs = [(u, li, u.logs[li]) for u in units for li in sorted(u.written)]
    new_guid = {}
    if new_ids:
        for _, _, lg in all_logs:
            new_guid.setdefault(lg.meta.guid, str(uuid.uuid4()))
        for _, _, lg in all_logs:
            lg.new_guid = new_guid[lg.meta.guid]
            lg.link_guids = dict(new_guid)
    files_changed, versions = [], {}
    total = {"bytes_in": 0, "bytes_out": 0, "logs_unchanged": 0, "records_changed": 0, "records_added": 0,
             "durations_extended": 0}
    try:
        for u, li, lg in all_logs:
            rel = os.path.relpath(lg.meta.path, root)
            base = (lg.new_guid + ".jdl") if lg.new_guid else os.path.basename(rel)
            dst = os.path.join(out_root, os.path.dirname(rel), base)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            data = lg.serialize()
            with open(dst, "wb") as fh:
                fh.write(data)
            total["bytes_in"] += len(lg.data)
            total["bytes_out"] += len(data)
            total["records_changed"] += sum(1 for r in lg.records() if _dirty(r) and r.ri is not None)
            total["records_added"] += sum(len(v) for v in lg.add_recs.values())
            total["durations_extended"] += len(lg.new_dur)
            if data == lg.data:
                total["logs_unchanged"] += 1
            else:
                files_changed.append(os.path.relpath(dst, out_root))
            rr = _record_root(lg.meta.path)
            versions.setdefault(rr, []).append((lg.meta.guid, lg.new_guid or lg.meta.guid))
        for rr, pairs in versions.items():
            src = os.path.join(rr, "version.txt")
            if not os.path.exists(src):
                continue
            with open(src, "rb") as fh:
                raw = fh.read()
            nl = b"\r\n" if b"\r\n" in raw else b"\n"
            keep = {o: n for o, n in pairs}
            outl = []
            for ln in [x for x in raw.split(nl) if x.strip()]:
                g = ln.split(b",")[0].decode("ascii", "replace").strip()
                if g in keep:
                    outl.append(keep[g].encode() + ln[len(g):])
            dst = os.path.join(out_root, os.path.relpath(src, root))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            data = nl.join(outl) + (nl if raw.endswith(nl) else b"")
            with open(dst, "wb") as fh:
                fh.write(data)
            if data != raw:
                files_changed.append(os.path.relpath(dst, out_root))
        res = {"path": out_root, "zip": out_root + ".zip", "n_kept": n_kept, "n_removed": n_removed,
               "files_changed": files_changed, "notes": [], "warnings": warnings, "round_trip": None, "check": None,
               "details": notes, "bytes": total, "logs_written": len(all_logs), "points": mode,
               "logs_in_units": int(sum(len(u.logs) for u in units)), "expected_summary": None,
               "clean_summary": _totals(clean)}
        res["notes"].append(
            f"{len(all_logs)} of {res['logs_in_units']} log(s) written, {total['logs_unchanged']} of them byte for "
            f"byte; {total['records_changed']:,} record(s) changed, {total['records_added']} added"
            + (f", {total['durations_extended']} block duration(s) extended (definitions field 7 with them)"
               if total["durations_extended"] else "") + (" and new GUIDs" if new_ids else ""))
        if n_area:
            res["notes"].append(f"{n_area} reading(s) whose clean distance is shorter: the width written gives the "
                                "clean area (the distance is the header's path and cannot be written)")
        # ---- round trip
        if verify:
            df1, _ = J.decode_detailed(out_root, unit_ids, opts, log=_quiet, points=mode)
            base = df0[kept_rows]
            if res["logs_written"] < res["logs_in_units"]:
                base, eff = _reference_of_written(all_logs, root, out_root, unit_ids, opts, mode, base)
                res["drop_logs_effect"] = eff
                res["notes"].append(
                    "logs left out: jd_jdl places and measures the readings in a local plane around the median header "
                    "position of the logs it reads, which moves with them; the round trip compares with the original "
                    "logs of the written files decoded alike, and against the full card the kept readings differ by "
                    f"up to {eff['Distance_f']:.2g} relative in distance, {eff['Yld_Vol_Dr']:.2g} in yield and "
                    f"{eff['position_m']:.2g} m in position")
            e, moved = _expected(base, units, factors)
            tol = {}
            if factors:
                tol.update({c: 2e-6 for c in PER_AREA})
            nar_t = {(u.vin, tm) for u in units for tm, x in u.expect.items() if "width_m" in x}
            atol = {}
            if nar_t:
                tol.update({"Swth_Wdth_": 1e-12, **{c: max(tol.get(c, 0), 1e-9) for c in PER_AREA}})
                atol["Y_Offset_f"] = 1e-9
            if moved.any():
                tol.update({"lon": max(tol.get("lon", 0), 1e-10), "lat": max(tol.get("lat", 0), 1e-10)})
                atol["Y_Offset_f"] = 1e-9
            rt = compare(e, df1, tol, atol)
            nk = _non_kept_check(out_root, unit_ids, opts, mode, df0, kept_rows, units)
            rt["non_kept"] = nk
            rt["ok"] = rt["ok"] and nk["ok"]
            ds = int((e.set_index(["Machine", "t_ms"]).Dataset.reindex(df1.set_index(["Machine", "t_ms"]).index)
                      .astype(str).values != df1.Dataset.astype(str).values).sum()) if len(df1) else 0
            rt["datasets_renamed"] = ds
            res["round_trip"] = res["check"] = rt
            msg = (f"round trip through {FORMAT_ID} (points={mode}): {rt['n_matched']:,} of {n_kept:,} kept readings "
                   f"decoded again, {rt['n_missing']} missing, {rt['n_extra']} extra; "
                   + ("every compared value as expected" if rt["ok"] and not nk["n_with_width"] else
                      "DIFFERENCES: " + ", ".join(f"{c} {v['n_beyond_tol']}" for c, v in rt["columns"].items()
                                                   if v["n_beyond_tol"])
                      + f"; non-kept with a width {nk['n_with_width']}, with sections on {nk['n_with_sections_on']}"))
            log("jdl writer: " + msg)
            if not rt["ok"]:
                raise JdlRoundTripError("not written: the written logs do not decode back as the clean readings: "
                                        + msg, rt)
            res["notes"].append(msg)
            if ds:
                res["notes"].append(f"{ds:,} kept reading(s) carry another Dataset name in the written logs: the "
                                    "decoder names a dataset by its first reading, which the cleaning removed")
            kt_df = df1
            res["expected_summary"] = {"all": _totals(kt_df), "by_machine": {
                str(m): _totals(g) for m, g in kt_df.groupby(kt_df.Machine.astype(str))}}
            cs = res["clean_summary"]
            es = res["expected_summary"]["all"]
            res["notes"].append(
                f"the written logs read as {es['n']:,} readings, {es['area_ac']:,.2f} ac, {es['wet_lb']:,.0f} lb wet, "
                f"{es['dry_bu']:,.1f} bu dry; the clean file: {cs['n']:,}, {cs['area_ac']:,.2f} ac, "
                f"{cs['wet_lb']:,.0f} lb, {cs['dry_bu']:,.1f} bu"
                + (" (narrowed widths in section steps)" if any(u.expect for u in units) else ""))
            res["notes"].append(SMS_CAVEAT if mode == "sms" else SMS_CAVEAT_OPS)
        else:                                            # nothing decoded back: no 'decodes back exactly' claim
            res["notes"].append("round trip not run (verify=False)")
            res["notes"].append(SMS_CAVEAT_UNVERIFIED)
        zpath = res["zip"]
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for dp, _, fn in os.walk(out_root):
                for f in sorted(fn):
                    p = os.path.join(dp, f)
                    z.write(p, os.path.relpath(p, os.path.dirname(out_root)))
    except BaseException:
        shutil.rmtree(out_root, ignore_errors=True)
        if os.path.exists(out_root + ".zip"):
            os.remove(out_root + ".zip")
        raise
    return res


def _record_root(path):
    parts = os.path.normpath(path).split(os.sep)
    low = [x.lower() for x in parts]
    return os.sep.join(parts[:low.index("jd-data")]) if "jd-data" in low else os.path.dirname(path)
