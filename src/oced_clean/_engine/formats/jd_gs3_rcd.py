# -*- coding: utf-8 -*-
"""Decoder for John Deere GreenStar 3 (2630) RCD harvest logs: ``RCD/EIC/Documentation/<xx>/<guid>.fdd`` + ``.fdl``.

Reverse engineered (no official description), extending an earlier internal reader. The rules below were measured
against Ag Leader SMS (the data and the comparison scripts are not part of this repository).

Files
  <guid>.fdl  XML RCDLogFile = one display session; one RCDLogBlock per monitor load. Each block carries the setup
              (client/farm/field, crop with crop weight and standard payable moisture, machine and header, receiver
              offsets), the meters (vrHarvestMoisture %, vrYieldWetMass kg, dtHeaderStatus, one dtRecordingStatus per
              section with its width and lateral offset), two navigation references (dtiVehicleGPS = receiver,
              dtiComputedImplement = header point), the UTC start (LogTaskRegion@timeStamp) and its byte range in the
              .fdd (filePosition). The GUID file name starts with the session start in hex UTC seconds
              (<8 hex>-0000-1xxx-<4 hex>-<12 hex>; a writer that gives the files new ids changes the third group).
  <guid>.fdd  per block: header with the column list, then 1-Hz records ("EvenByteDelta"), end marker u16 4.
  <guid>.fdr  RCDLoadFile: load number and name, referenced by the block's LoadIdentityRef.

Record grammar (read_columns / decode_block): u16 2, u8 n_groups, three bitmaps of ceil(ncols/8) bytes giving each
column a state (none: delta in the dense part; bitmap 0: no value; bitmap 1: unchanged; bitmap 2: value in a TLV),
dense int16/int32 deltas, then n_groups x (u16 3, u16 L, mask) + TLVs. Corrections to the previous reader, each proven
on the data: (1) the preamble is these bitmaps, not a fixed signature -> blocks of other machine set-ups no longer come
out empty (every block ends exactly on its end marker); (2) enumerations (header status, section states) hold
absolute indices, not deltas -> the header state is right in every block (the old cumulative sum made it wrong in
some blocks); (3) after a record with no value the chain restarts from 0 -> no moisture offset after a logging pause
(the old reader shifted the moisture of whole loads); (4) a TLV on a delta column restarts its chain at that
absolute value (heading wrap).

Reading = one record at the header point, aligned as Ag Leader SMS aligns these logs (alignment "sms", the default;
measured on SMS point exports and SMS totals: nearly every point of an SMS export lies on a decoded
reading)
  position  the dtiComputedImplement point (receiver + inline offset of the .fdl) - the SMS export of the same logs
            sits on it. With only part of the header recording, the centre of the recording sections (the point moved
            sideways by their mean lateral offset), where nearly all such SMS points whose width SMS left as logged
            lie. Fallback when the implement point is absent: receiver projected forward by GPS-to-axle -
            axle-to-connection (equal to the logged offset wherever both exist).
  sessions  one copy per session: a session (.fdl + .fdd) is named by its GUID, which carries the display's node id and
            the session start, so two files with the same name are the same session (the same zip uploaded twice,
            or a folder next to its own zip). find_sessions keeps the copy with the largest .fdd (ties: the first by
            path) and the probe and the decode report say how many copies were left out; SMS refuses a file it has
            already archived, so it never reads a session twice either.
  kept      every record with the header down, at least one section recording and a valid GNSS position (not the
            0/0 position of signal type Unknown). Nothing else is left out here: SMS also holds the readings of a
            standing combine and those whose grain record fell into a pause of the log (its Min. / Max. Yield
            Volume then remove what they remove: engine/sms_import.py). OPEN ITEM: after the same minimum SMS holds
            a few readings fewer than this per field (a small fraction of a percent; its wet mass is met). Most
            likely they are among the readings that pass the minimum only through the carried grain below; no rule
            tried reproduces SMS's counts, so none is applied (the SMS rule is not established: one raw SMS point
            export of a GreenStar 3 card would settle it).
  duration  the log interval (1 s) for every reading, also the first one after a pause (SMS: 1.0 on every point).
  mass      sms_align: the records are matched to the positions one to one in time order; a record can be taken by
            a position when it was logged more than FLOW_DELAY_S - 1 = 13.0 s after it and the moisture that goes
            with it more than MOISTURE_DELAY_S - 1 = 17.0 s after it, and no record is matched across a pause.
            On an unbroken log this is the record 14 s later; after a pause SMS takes the record 13 or 14 s later,
            and this rule gives the one SMS takes.
  moisture  logged 4 s after the record that gives the mass (18 s after the position): the first record at least
            3.5 s later, and 0 % when that record is 5.5 s or more later, i.e. when the log paused in between (SMS
            keeps such a reading with 0 %: the area-weighted moisture SMS shows is met with them and is too high
            without). Equal to the SMS value on nearly every point.
  carried   SMS loses no grain: its total wet mass is the sum of every record of the card (less the grain of the
            readings its minimum yield removes). The grain of a record
            taken by a position that is not harvesting, or passed over because it came too early for the position
            waiting (the first seconds of the flow after the header went down), is carried to the readings next to
            it: each reading receives 1 - PENDING_KEEP of what is pending and the rest moves on (SMS: the surplus
            over the matched record falls by the factor 0.68645 from one reading to the next). WHERE the grain
            enters
            is only approximated (sms_align, PENDING_SPREAD is a chosen value): its total is right, its place can be
            a few readings off.
  area      DeltaDistance of the implement point x active width (sections recording On).
  yield     common.yield_columns(wet lb, area, moisture, test weight, standard moisture, shrink); the crop weight
            (lb/bu, or kg/bu converted) and standard payable moisture come from the .fdl unless DecodeOptions
            overrides them (a crop chosen in the options that differs from the monitor's replaces the .fdl values
            and the Product); default shrink one-sided. The report carries the card's own two values as
            "monitor_product" (with min_yield 5 and max_yield MONITOR_MAX_YIELD = 10,000 bu/ac, SMS's Processing
            Settings for these cards): SMS made its product with them (its dry volume is met with the card's own
            standard moisture, not with the one of SMS's own wheat product), and engine.sms_import.resolve uses them
            in place of the product table WHEN the caller passes
            monitor=report["monitor_product"]. A unit whose median
            moisture is more than MOISTURE_PROFILE_TOL = 2 points above the resolved standard moisture gets a
            warning: the monitor's crop profile probably is not the harvested crop (another crop's profile left on the
            display; SMS uses the profile the card gives).
Alignment "shift" (the earlier versions, kept as an option of decode): a reading takes the record logged FLOW_DELAY_S
later and the moisture logged MOISTURE_DELAY_S later (within 0.6 s), and is left out when there is none or when the
combine moves slower than MIN_SPEED_MS; the grain of the records no reading takes is lost and the duration is the
logged interval.
The .fdl states a vrLatency for each meter; it is reported, not used.
Time basis: UTC (LogTaskRegion timeStamp + ElapsedTime ms); DecodeOptions.tz_offset_h gives local time.
"""
from __future__ import annotations

import math
import os
import re
import struct
import time
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from oced_clean._engine.formats import common as C

FORMAT_ID = "jd_gs3_rcd"
LABEL = "John Deere GreenStar 3 (2630) RCD card (Documentation/*.fdd + .fdl)"

# ------------------------------------------------------------------------------------------------- binary layer
_DELTA_FMT = {1: "h", 3: "h", 4: "i"}          # dense delta width by column type (enum, 16-bit, 32-bit)
_REC_TYPE = 2                                  # u16 record marker
_GROUP_TYPE = 3                                # u16 sparse-group marker
_END_TYPE = 4                                  # u16 end-of-block marker (the last 2 bytes of every block)
# column state of a record, from the three bitmaps of its preamble
ST_DELTA, ST_NONE, ST_HOLD, ST_TLV = 0, 1, 2, 4


class FormatError(ValueError):
    """The bytes do not follow the layout this decoder knows."""


def read_columns(blk: bytes) -> tuple[list[dict], int]:
    """Column list of one log block and the offset of its first record.

    Block header: u16 1, u16 L, L bytes (GUID strings: data-format GUID, session, task, field), then u16 1, u16 2,
    u16 ncols, and ncols column definitions: u16 type, u16 size, u16 name length, name (latin-1), u8 sparse flag, then
      type 1 (enumeration): u16 count, u16 bytes, count x (u16 length, text);
      type 3/4/5 (numbers): u16 24, 3 doubles = resolution, limit, initial value.
    """
    one, hl = struct.unpack_from("<HH", blk, 0)
    a, b, nc = struct.unpack_from("<HHH", blk, 4 + hl)
    if one != 1 or (a, b) != (1, 2) or not 0 < nc < 512:
        raise FormatError(f"unknown block header ({one}, {a}, {b}, {nc})")
    i = 4 + hl + 6
    cols = []
    for _ in range(nc):
        t, _size, nl = struct.unpack_from("<HHH", blk, i)
        name = blk[i + 6:i + 6 + nl].decode("latin1")
        if not re.match(r"^[A-Za-z]\w*$", name):
            raise FormatError(f"column list broken at byte {i}")
        j = i + 6 + nl
        c = {"name": name, "type": t, "sparse": blk[j]}
        if t == 1:
            cnt, _ = struct.unpack_from("<HH", blk, j + 1)
            k, names = j + 5, []
            for _ in range(cnt):
                ln = struct.unpack_from("<H", blk, k)[0]
                names.append(blk[k + 2:k + 2 + ln].decode("latin1"))
                k += 2 + ln
            c["enum"], i = names, k
        elif t in (3, 4, 5):
            size = struct.unpack_from("<H", blk, j + 1)[0]
            c["res"], c["limit"], c["init"] = struct.unpack_from("<ddd", blk, j + 3)
            i = j + 3 + size
        else:
            raise FormatError(f"unknown column type {t} ({name})")
        cols.append(c)
    return cols, i


def decode_block(blk: bytes) -> tuple[dict, dict]:
    """All records of one log block as absolute values.

    Record: u16 2, u8 n_groups, then three bitmaps of ceil(ncols/8) bytes (bit c = column c):
      bitmap 0 -> state 1 "no value in this record", bitmap 1 -> state 2 "unchanged", bitmap 2 -> state 4 "value given
      in a TLV"; a column in none of them (state 0) has its delta in the dense part. Dense part: the state-0 columns in
      column order, int16 (types 1 and 3) or int32 (type 4) DELTAS from the previous record. Then n_groups x
      {u16 3, u16 L, L-byte mask} and, for each column whose mask bit is 0, a TLV: u16 type, u16 length, value
      (double for numbers, int16 for enumerations). A TLV on a delta column restarts its chain at that absolute value.
    Block end: u16 4.

    Returns ({column name: float array (NaN = no value)}, info) where info has n_records, the columns, and a count of
    states / anomalies.
    """
    cols, p = read_columns(blk)
    nc = len(cols)
    nb = (nc + 7) // 8
    pre = 3 + 3 * nb
    end = len(blk)
    cache: dict[bytes, tuple] = {}
    rows_by_key: dict[bytes, list] = {}
    idx_by_key: dict[bytes, list] = {}
    tlv_rec, tlv_col, tlv_val = [], [], []
    unpack_hh = struct.Struct("<HH").unpack_from
    unpack_d = struct.Struct("<d").unpack_from
    unpack_h = struct.Struct("<h").unpack_from
    n = 0
    ended = False
    while p + 2 <= end:
        typ = blk[p] | (blk[p + 1] << 8)
        if typ == _END_TYPE:
            ended = True
            p += 2
            break
        if typ != _REC_TYPE:
            raise FormatError(f"record marker expected at byte {p}, found {typ}")
        ng = blk[p + 2]
        key = blk[p + 3:p + pre]
        ent = cache.get(key)
        if ent is None:
            bm = [int.from_bytes(key[k * nb:(k + 1) * nb], "little") for k in range(3)]
            st = [((bm[0] >> c) & 1) | (((bm[1] >> c) & 1) << 1) | (((bm[2] >> c) & 1) << 2) for c in range(nc)]
            if any(s not in (ST_DELTA, ST_NONE, ST_HOLD, ST_TLV) for s in st):
                raise FormatError(f"column in two bitmaps at byte {p}")
            dense = [c for c in range(nc) if st[c] == ST_DELTA]
            if any(cols[c]["sparse"] or cols[c]["type"] == 5 for c in dense):
                raise FormatError(f"sparse column with a dense delta at byte {p}")
            s = struct.Struct("<" + "".join(_DELTA_FMT[cols[c]["type"]] for c in dense))
            ent = cache[key] = (st, dense, s)
            rows_by_key[key], idx_by_key[key] = [], []
        st, dense, s = ent
        rows_by_key[key].append(s.unpack_from(blk, p + pre))
        idx_by_key[key].append(n)
        q = p + pre + s.size
        for _ in range(ng):
            t, ln = unpack_hh(blk, q)
            if t != _GROUP_TYPE:
                raise FormatError(f"sparse group expected at byte {q}")
            mask = int.from_bytes(blk[q + 4:q + 4 + ln], "little")
            q += 4 + ln
            for c in range(nc):
                if not (mask >> c) & 1:
                    t2, l2 = unpack_hh(blk, q)
                    if l2 == 8:
                        v = unpack_d(blk, q + 4)[0]
                    elif l2 == 2:
                        v = float(unpack_h(blk, q + 4)[0])
                    else:
                        raise FormatError(f"TLV of length {l2} at byte {q}")
                    tlv_rec.append(n)
                    tlv_col.append(c)
                    tlv_val.append(v)
                    q += 4 + l2
        p = q
        n += 1
    # ---- assemble (vectorised per column)
    delta = np.zeros((n, nc), np.int64)
    state = np.zeros((n, nc), np.int8)
    for key, rows in rows_by_key.items():
        st, dense, _ = cache[key]
        ii = np.asarray(idx_by_key[key])
        if dense:
            delta[np.ix_(ii, dense)] = np.asarray(rows, np.int64)
        state[ii, :] = np.asarray(st, np.int8)
    tr, tc, tv = np.asarray(tlv_rec, int), np.asarray(tlv_col, int), np.asarray(tlv_val, float)
    out = {}
    anomalies = {}
    for c, col in enumerate(cols):
        sc = state[:, c]
        sel = tc == c
        if col["sparse"] or col["type"] == 5:
            v = np.full(n, np.nan)
            v[tr[sel]] = tv[sel]
            v = pd.Series(v).ffill().to_numpy(copy=True)          # state 2 = unchanged: hold the last value
            v[sc == ST_NONE] = np.nan
        else:
            if col["type"] == 1:                            # enumerations hold the absolute index, not a delta
                base = delta[:, c].astype(float)
                bad = (base < 0) | (base >= len(col["enum"]))
                if bad.any():
                    anomalies[col["name"] + ":enum_out_of_range"] = int(bad.sum())
                    base[bad] = np.nan
            else:
                base = col["init"] + np.cumsum(delta[:, c]) * col["res"]
            none = np.flatnonzero(sc == ST_NONE)
            if col["type"] != 1 and (sel.any() or len(none)):
                # restarts of the chain: a TLV gives the absolute value; a record with no value counts as 0 (the next
                # delta is taken from 0, not from the last value: proven against an SMS export)
                off = np.full(n, np.nan)
                off[none] = 0.0 - base[none]
                off[tr[sel]] = tv[sel] - base[tr[sel]]
                off = pd.Series(off).ffill().fillna(0.0).to_numpy(copy=True)
                base = base + off
                if sel.any():
                    anomalies[col["name"] + ":absolute"] = int(sel.sum())
            v = np.round(base.astype(float), 9)              # float noise of init + k x res (e.g. -4e-16 kg)
            v[sc == ST_NONE] = np.nan
            nn = int((sc == ST_NONE).sum())
            if nn:
                anomalies[col["name"] + ":no_value"] = nn
        out[col["name"]] = v
    info = {"n_records": n, "columns": cols, "ended": ended, "trailing_bytes": end - p, "patterns": len(cache),
            "anomalies": anomalies}
    return out, info


# ------------------------------------------------------------------------------------------------- .fdl metadata
_UOM_M = {"m": 1.0, "mm": 0.001, "cm": 0.01, "in": 0.0254, "ft": 0.3048}
_UOM_LB_BU = {"lb1bu-1": 1.0, "kg1bu-1": C.LB_PER_KG}      # crop weight per bushel: the card gives either
# the display's id: <session start, hex UTC seconds>-0000-1xxx-<4>-<12> (1000 on the display; a writer that gives the
# files new ids, as SMS refuses a file it has archived, changes the 3rd group and keeps the start)
_HEX_GUID = re.compile(r"^([0-9a-f]{8})-0000-1[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _strip_ns(root):
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.rsplit("}", 1)[1]
    return root


def _get(el, path, attr):
    if el is None:
        return None
    x = el.find(path)
    return x.get(attr) if x is not None else None


def _metres(el) -> float:
    """Value of a vr* element converted to metres (NaN when absent or in an unknown unit)."""
    if el is None or el.get("value") is None:
        return math.nan
    f = _UOM_M.get(el.get("sourceUOM", ""))
    return float(el.get("value")) * f if f is not None else math.nan


def _fval(el) -> float:
    try:
        return float(el.get("value"))
    except (AttributeError, TypeError, ValueError):
        return math.nan


def guid_time(name: str):
    """UTC time encoded in a GreenStar GUID file name ('70dc8140-0000-1000-...' -> 2030-01-01 12:00:00 UTC)."""
    m = _HEX_GUID.match(os.path.splitext(os.path.basename(name))[0])
    return pd.Timestamp(int(m.group(1), 16), unit="s", tz="UTC") if m else None


def read_fdl(fdl_path: str) -> list[dict]:
    """One dict per RCDLogBlock of a session file: setup (client/farm/field, crop, machine, header), the column ids
    and what they represent, section geometry, navigation references, start time and the block's byte range."""
    root = _strip_ns(ET.parse(fdl_path).getroot())
    fdd_path = os.path.splitext(fdl_path)[0] + ".fdd"
    fdd_size = os.path.getsize(fdd_path) if os.path.exists(fdd_path) else -1
    out = []
    for k, blk in enumerate(root.findall("RCDLogBlock")):
        setup = blk.find("ConfigBlock/Setup")
        region = blk.find("ConfigBlock/LogTaskRegion")
        ebd = blk.find("LogDataBlock/EvenByteDelta")
        if setup is None or region is None or ebd is None:
            continue
        m = {"fdl": fdl_path, "fdd": fdd_path, "session": os.path.basename(fdl_path)[:8], "block": k,
             "file_position": int(ebd.get("filePosition", "0")),
             "fdd_name": (ebd.get("filePathName") or "").replace("\\", "/").split("/")[-1]}
        fld = setup.find("Field")
        m["field"] = fld.get("name", "") if fld is not None else ""
        m["field_erid"] = (fld.get("erid", "") if fld is not None else "").strip("{}")
        farm, client = setup.find("Farm"), setup.find("Participant/Client")
        m["farm"] = farm.get("name", "") if farm is not None else ""
        m["client"] = client.get("name", "") if client is not None else ""
        op = region.find("Operation")
        m["op_class"] = _get(op, "dtOperationClass", "value")
        prod_ref = (_get(op, "ProductRef", "eridRef") or "").strip("{}")
        m["load_ref"] = (_get(op, "LoadIdentityRef", "idRef") or "").strip("{}")
        crop = None
        for cr in setup.findall("Products/Crop"):
            if crop is None or any((v.get("erid") or "").strip("{}") == prod_ref for v in cr.findall("CropVariety")):
                crop = cr
        m["crop"] = crop.get("name", "") if crop is not None else ""
        var = [v for v in (crop.findall("CropVariety") if crop is not None else [])
               if (v.get("erid") or "").strip("{}") == prod_ref]
        m["variety"] = var[0].get("name", "") if var else ""
        cw = crop.find("CropWeight") if crop is not None else None
        m["crop_weight_lb_bu"] = (_fval(cw) * _UOM_LB_BU.get(cw.get("sourceUOM", ""), math.nan)
                                  if cw is not None else math.nan)
        m["std_moisture_pct"] = _fval(crop.find("StandardPayableMoisture")) if crop is not None else math.nan
        mach = setup.find("Equipment/Machine")
        m["machine"] = mach.get("name", "") if mach is not None else ""
        m["machine_serial"] = mach.get("serialNumber", "") if mach is not None else ""
        model = setup.find("MachineModel")
        m["machine_model"] = model.get("name", "") if model is not None else ""
        imp = setup.find("Equipment/Implement")
        m["implement_serial"] = imp.get("serialNumber", "") if imp is not None else ""
        imodel = setup.find("ImplementModel")
        m["implement_model"] = imodel.get("name", "") if imodel is not None else ""
        m["physical_width_m"] = _metres(setup.find(".//vrPhysicalImplementWidth"))
        m["gps_to_axle_m"] = _metres(setup.find(".//vrGPSToNonSteeringAxleOffset"))
        m["axle_to_connection_m"] = _metres(setup.find(".//vrNonSteeringAxleToConnectionOffset"))
        m["start_utc"] = pd.Timestamp(region.get("timeStamp")).tz_convert("UTC") if region.get("timeStamp") else None
        m["time_col"] = region.get("timeColumnID", "ElapsedTime")
        m["log_interval_ms"] = float(region.get("logInterval", "nan"))
        m["crop_year"] = int(region.get("cropYear")) if (region.get("cropYear") or "").isdigit() else None
        meters = {mt.get("id"): mt for eq in blk.findall("ConfigBlock/EquipmentConfiguration") for mt in eq.findall("Meter")}
        used = {r.get("idRef") for r in region.iter("MeterRef")}
        m.update(moisture_col=None, mass_col=None, header_col=None, mass_uom=None, mass_calibration=None,
                 latency_mass_s=math.nan, latency_moisture_s=math.nan)
        targets = {}
        for mid, mt in meters.items():
            me = mt.find("MeasuredElement")
            if me is not None and mid in used:
                vn, dt = me.find("VariableNumberColumn"), me.find("DefinedTypeColumn")
                rep = vn.get("variableRepresentation") if vn is not None else (
                    dt.get("definedTypeRepresentation") if dt is not None else None)
                lat = _fval(me.find("vrLatency"))
                if rep == "vrHarvestMoisture":
                    m["moisture_col"], m["latency_moisture_s"] = vn.get("columnID"), lat
                elif rep == "vrYieldWetMass":
                    m["mass_col"], m["mass_uom"], m["latency_mass_s"] = vn.get("columnID"), vn.get("sourceUOM"), lat
                    m["mass_calibration"] = me.get("calibration")
                elif rep == "dtHeaderStatus":
                    m["header_col"] = dt.get("columnID")
            te = mt.find("TargetElement")
            if te is not None and te.find("VariableNumberColumn") is not None:
                vn = te.find("VariableNumberColumn")
                targets[vn.get("variableRepresentation")] = (vn.get("columnID"), vn.get("sourceUOM"))
        m["targets"] = targets
        secs = []
        for eq in blk.findall("ConfigBlock/EquipmentConfiguration"):
            for s in eq.findall("Section"):
                rs = s.find("RecordingStateElement/DefinedTypeColumn")
                secs.append({"id": s.get("id"), "col": rs.get("columnID") if rs is not None else None,
                             "width_m": _metres(s.find("vrSectionWidth")), "lateral_m": _metres(s.find("vrLateralOffset")),
                             "inline_m": _metres(s.find("vrInlineOffset"))})
        m["sections"] = secs
        m["width_m"] = float(np.nansum([s["width_m"] for s in secs])) if secs else math.nan
        navs = []
        for eq in blk.findall("ConfigBlock/EquipmentConfiguration"):
            for nr in eq.findall("NavigationReference"):
                navs.append({"type": _get(nr, "dtNavigationType", "value"),
                             "inline_m": _metres(nr.find("vrInlineOffset")), "lateral_m": _metres(nr.find("vrLateralOffset")),
                             "dd_uom": _get(nr, "vrDeltaDistance", "sourceUOM"),
                             **{k: _get(nr, k, "columnID") for k in ("vrLatitude", "vrLongitude", "vrHeading",
                                                                      "vrDeltaDistance", "vrElevation", "dtSignalType",
                                                                      "vrGPSAccuracy", "vrGPSVerticalAccuracy")}})
        m["nav"] = navs
        out.append(m)
    out.sort(key=lambda d: d["file_position"])
    for i, d in enumerate(out):
        d["end_position"] = out[i + 1]["file_position"] if i + 1 < len(out) else fdd_size
    return out


def session_files(root: str) -> tuple[list[str], list[str]]:
    """(sessions, copies left out): every .fdl under ``root`` that has its .fdd next to it, ONE per session. The file
    name (case ignored) is the session's GUID, so two pairs with the same name are the same session (a zip uploaded
    twice, a folder next to its own zip): the copy with the largest .fdd is kept (a later export of a card can hold
    a session that grew), ties to the first by path. Both lists sorted by path."""
    pairs = []
    for d, _, files in os.walk(root):
        low = {f.lower(): f for f in files}
        for f in files:
            if f.lower().endswith(".fdl") and f.lower()[:-4] + ".fdd" in low:
                fdd = os.path.join(d, low[f.lower()[:-4] + ".fdd"])
                try:
                    size = os.path.getsize(fdd)
                except OSError:
                    size = -1
                pairs.append((os.path.join(d, f), size))
    best = {}
    for p, size in sorted(pairs):
        k = os.path.basename(p).lower()
        if k not in best or size > best[k][1]:
            best[k] = (p, size)
    keep = sorted(p for p, _ in best.values())
    kept = set(keep)
    return keep, sorted(p for p, _ in pairs if p not in kept)


def find_sessions(root: str) -> list[str]:
    """Every .fdl under ``root`` that has its .fdd next to it, one copy per session (session_files), sorted by path."""
    return session_files(root)[0]


def _copies_note(skipped: list[str]) -> str:
    return (f"{len(skipped)} duplicate GreenStar 3 session(s) (the same .fdl / .fdd name twice: e.g. a zip uploaded "
            "twice, or a folder next to its own zip) were left out: each session is read once, from its largest copy")


def read_loads(root: str, refs) -> dict:
    """{load GUID: (load number, load name)} from the .fdr files (RCDLoadFile) named after the load GUIDs."""
    want = {r.lower() for r in refs if r}
    paths = {}
    for d, _, files in os.walk(root):
        for f in files:
            if f.lower().endswith(".fdr") and f[:-4].lower() in want:
                paths[f[:-4].lower()] = os.path.join(d, f)
    out = {}
    for g, p in paths.items():
        try:
            r = _strip_ns(ET.parse(p).getroot())
        except ET.ParseError:
            continue
        li = r.find("LoadIdentity")
        if li is None:
            continue
        names = {(u.get("erid") or "").strip("{}"): u.get("name", "") for u in r.iter("UserDefinedTypeInstance")}
        out[g] = (li.get("loadNumber", ""), names.get((li.get("loadNameRef") or "").strip("{}"), ""))
    return out


# ------------------------------------------------------------------------------------------------- records -> channels
def _enum_is(v: np.ndarray, col: dict | None, name: str) -> np.ndarray:
    """Boolean array: enumeration value equals ``name`` (False where there is no value)."""
    if col is None or "enum" not in col or name not in col["enum"]:
        return np.zeros(len(v), bool)
    return np.asarray(v) == col["enum"].index(name)


def block_frame(meta: dict, data: bytes) -> tuple[pd.DataFrame, dict]:
    """Every record of one log block with named channels (no filtering). Positions in WGS 84 degrees, distances in m."""
    v, info = decode_block(data)
    n = info["n_records"]
    cols = {c["name"]: c for c in info["columns"]}
    nan = np.full(n, np.nan)
    get = lambda cid: v[cid] if cid in v else nan.copy()  # noqa: E731
    t_s = get(meta["time_col"]) / 1000.0
    start = meta["start_utc"] if meta["start_utc"] is not None else guid_time(meta["fdd"])
    df = pd.DataFrame({"session": meta["session"], "block": meta["block"], "elapsed_s": t_s})
    df["t_utc"] = start + pd.to_timedelta(t_s, unit="s")
    # the same time as whole milliseconds since 1970 (ElapsedTime is in ms): the matching compares them exactly
    df["t_ms"] = int(round(start.timestamp() * 1000.0)) + np.round(np.nan_to_num(t_s) * 1000.0).astype(np.int64)
    ivl = meta.get("log_interval_ms", math.nan) / 1000.0
    df["ivl_s"] = ivl if np.isfinite(ivl) and ivl > 0 else 1.0
    df["dt_s"] = np.r_[np.nan, np.diff(t_s)]
    df["moisture_pct"] = get(meta["moisture_col"])
    kg = get(meta["mass_col"])
    if meta["mass_uom"] not in ("kg", None):
        raise FormatError(f"wet mass in {meta['mass_uom']!r}, only kg is known")
    df["wet_kg"] = kg
    hc = meta["header_col"]
    df["header_on"] = _enum_is(get(hc), cols.get(hc), "dtiHeaderStatusOn")
    df["header_known"] = ~np.isnan(get(hc))
    on_w = np.zeros(n)
    on_wl = np.zeros(n)
    n_on = np.zeros(n, int)
    for s in meta["sections"]:
        on = _enum_is(get(s["col"]), cols.get(s["col"]), "dtiRecordingStatusOn")
        on_w += np.where(on, s["width_m"], 0.0)
        on_wl += np.where(on, s["width_m"] * (s["lateral_m"] if np.isfinite(s["lateral_m"]) else 0.0), 0.0)
        n_on += on
    df["sections_on"] = n_on
    df["active_width_m"] = on_w
    # centre of the recording sections, metres to the right of the machine centre-line (0 with all of them on)
    df["active_lateral_m"] = np.divide(on_wl, on_w, out=np.zeros(n), where=on_w > 0)
    navs = {n["type"]: n for n in reversed(meta["nav"])}           # first reference of each type
    nav0 = navs.get("dtiVehicleGPS") or (meta["nav"][0] if meta["nav"] else None)
    if nav0 is None:
        raise FormatError("no navigation reference in the .fdl")
    for tag, nav in (("gps", nav0), ("imp", navs.get("dtiComputedImplement"))):
        if nav is None:
            continue
        f = _UOM_M.get(nav["dd_uom"] or "", np.nan)
        df[f"lat_{tag}"] = get(nav["vrLatitude"])
        df[f"lon_{tag}"] = get(nav["vrLongitude"])
        df[f"head_{tag}"] = get(nav["vrHeading"]) % 360.0          # chains may sit a few 1/100 deg below 0
        df[f"dd_{tag}_m"] = get(nav["vrDeltaDistance"]) * f
    meta["imp_fallback"] = "dtiComputedImplement" not in navs
    if meta["imp_fallback"]:
        # no computed implement point logged: project the receiver forward by the machine offsets of the .fdl
        # (inline = GPS-to-axle - axle-to-connection; equal to the logged implement offset wherever both exist)
        off = meta["gps_to_axle_m"] - meta["axle_to_connection_m"]
        off = 0.0 if not np.isfinite(off) else off
        h = np.radians(df["head_gps"].to_numpy())
        lat0 = np.radians(df["lat_gps"].to_numpy())
        df["lat_imp"] = df["lat_gps"] + np.degrees(off * np.cos(h) / _EARTH_R_M)
        df["lon_imp"] = df["lon_gps"] + np.degrees(off * np.sin(h) / (_EARTH_R_M * np.cos(lat0)))
        df["head_imp"] = df["head_gps"]
        df["dd_imp_m"] = df["dd_gps_m"]
    df["elev_m"] = get(nav0.get("vrElevation"))
    sc = nav0.get("dtSignalType")
    sig = get(sc)
    names = cols[sc]["enum"] if sc in cols and "enum" in cols[sc] else []
    df["signal"] = pd.Series(sig).map(lambda x: names[int(x)] if x == x and 0 <= int(x) < len(names) else "")
    df["gps_accuracy"] = get(nav0.get("vrGPSAccuracy"))
    df["vertical_accuracy_mm"] = get(nav0.get("vrGPSVerticalAccuracy"))
    tg = meta["targets"]
    df["speed_kmh"] = get(tg.get("vrVehicleSpeed", (None,))[0])
    df["fuel_l_h"] = get(tg.get("vrFuelRatePerHour", (None,))[0])
    return df, info


# ------------------------------------------------------------------------------------------------- units
def _crop_key(name: str) -> str | None:
    """Monitor crop name -> key of common.CROPS ('Wheat (Hard Red Spri' -> 'wheat'); None when unknown."""
    low = (name or "").strip().lower()
    for k in C.CROPS:
        if low.startswith(k) or low.startswith(k.rstrip("s")):
            return k
    return None


def _unit_id(m: dict) -> str:
    return f"{m['field_erid'] or m['field']}|{m['crop']}|{m['start_utc'].year}"


def _harvest_blocks(root: str) -> list[dict]:
    blocks = []
    for f in find_sessions(root):
        try:
            blocks += [m for m in read_fdl(f) if m["op_class"] == "dtiHarvest" and m["mass_col"]]
        except ET.ParseError:
            continue
    return sorted(blocks, key=lambda m: (m["start_utc"], m["session"], m["block"]))


def list_units(root: str) -> list[C.Unit]:
    """One unit per field x crop x calendar year (UTC) of the harvest log blocks that carry a wet-mass column.
    The units of the most recent year are pre-selected."""
    blocks = _harvest_blocks(root)
    groups: dict[str, list] = {}
    for m in blocks:
        groups.setdefault(_unit_id(m), []).append(m)
    last_year = max((m["start_utc"].year for m in blocks), default=None)
    units = []
    for uid, ms in groups.items():
        m0 = ms[0]
        days = sorted({m["start_utc"].strftime("%d %b") for m in ms})
        machines = sorted({m["machine_serial"] or m["machine"] for m in ms})
        n_rec = 0
        for m in ms:                                   # cheap size estimate: ~60 bytes per record
            n_rec += max(0, (m["end_position"] - m["file_position"]) // 60)
        label = (f"{m0['field']} · {m0['start_utc'].year} · {m0['crop']}"
                 f"{(' ' + m0['variety']) if m0['variety'] else ''} · {len(ms)} loads · "
                 f"{', '.join(days[:4])}{' ...' if len(days) > 4 else ''}")
        units.append(C.Unit(id=uid, label=label, field=m0["field"], crop=m0["crop"], year=m0["start_utc"].year,
                            start=ms[0]["start_utc"].isoformat(), end=ms[-1]["start_utc"].isoformat(),
                            machine=", ".join(machines), n_records=int(n_rec),
                            default=m0["start_utc"].year == last_year))
    return sorted(units, key=lambda u: (u.start, u.field))


def probe(root: str) -> C.Probe | None:
    """Cheap recognition: .fdl files that start as an RCDLogFile with their .fdd next to them, anywhere below root.
    Reads only the first 16 kB of each .fdl (machine serial); never decodes the .fdd."""
    t0 = time.time()
    sessions, skipped = session_files(root)
    good = []
    serials, crops, warnings = set(), set(), []
    if skipped:
        warnings.append(_copies_note(skipped))
    n_read = 0
    for f in sessions:
        if good and time.time() - t0 > 4.0:
            # the format is recognised: count the other .fdl + .fdd pairs by name (a slow disk must not change what
            # the probe reports, only how many headers it reads for the machine / crop summary)
            rest = sessions[n_read:]
            warnings.append(f"machine serials and crops read from the first {n_read} of {len(sessions)} sessions "
                            f"(time limit); the other {len(rest)} .fdl + .fdd pairs were counted by name")
            good += rest
            break
        n_read += 1
        try:
            with open(f, "rb") as fh:
                head = fh.read(16384).decode("latin1", "replace")
        except OSError:
            continue
        if "<RCDLogFile" not in head[:600]:
            continue
        good.append(f)
        s = re.search(r'<rcdsetup:Machine [^>]*serialNumber="([^"]*)"', head)
        if s:
            serials.add(s.group(1))
        c = re.search(r'<rcdsetup:Crop [^>]*name="([^"]*)"', head)
        if c:
            crops.add(c.group(1))
    if not good:
        return None
    # the root holds every copy, so that decode() sees the copies too and says it left them out
    rec_root = os.path.commonpath([os.path.dirname(f) for f in good + skipped])
    times = [t for t in (guid_time(f) for f in good) if t is not None]
    span = f", {min(times):%d %b %Y} - {max(times):%d %b %Y} (UTC, from the file names)" if times else ""
    return C.Probe(format_id=FORMAT_ID, label=LABEL, root=rec_root,
                   summary=f"{len(good)} GreenStar 3 log sessions (.fdl + .fdd){span}; crops: "
                           f"{', '.join(sorted(crops)) or 'unknown'}",
                   n_files=2 * len(good), machines=sorted(serials), warnings=warnings)


# ------------------------------------------------------------------------------------------------- records of units
def read_records(root: str, unit_ids=None, log=print) -> tuple[pd.DataFrame, list[dict], dict]:
    """Every record of the selected units (all harvest units when unit_ids is None), unfiltered, in time order, with
    the block metadata columns (field, crop, load, machine). Returns (records, block metas, {block key: info})."""
    blocks = _harvest_blocks(root)
    if unit_ids is not None:
        want = set(unit_ids)
        unknown = want - {_unit_id(m) for m in blocks}
        if unknown:
            raise KeyError(f"unknown unit id(s): {sorted(unknown)}")
        blocks = [m for m in blocks if _unit_id(m) in want]
    loads = read_loads(root, [m["load_ref"] for m in blocks])
    frames, infos, cache = [], {}, {}
    for m in blocks:
        if m["fdd"] not in cache:
            cache.clear()
            with open(m["fdd"], "rb") as fh:
                cache[m["fdd"]] = fh.read()
        data = cache[m["fdd"]][m["file_position"]:m["end_position"]]
        d, info = block_frame(m, data)
        ln = loads.get(m["load_ref"].lower())
        m["load"] = f"L{ln[0]}:{ln[1]}" if ln else f"Load {m['load_ref'][:8]}"
        m["load_number_known"] = bool(ln)
        m["machine_serial"] = m["machine_serial"] or m["machine"] or "unknown machine"   # key of the delay alignment
        for k in ("field", "crop", "variety", "load", "load_ref", "machine_serial", "machine"):
            d[k] = m[k]
        d["unit"] = _unit_id(m)
        frames.append(d)
        infos[f"{m['session']}/{m['block']}"] = info
        log(f"  {m['session']}/{m['block']}  {m['load']:<14} {m['field'][:28]:<28} {m['start_utc']:%Y-%m-%d %H:%M:%S}Z "
            f"{info['n_records']:>6} records")
    recs = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(recs):
        recs = recs.sort_values(["machine_serial", "t_utc"], kind="stable").reset_index(drop=True)
    return recs, blocks, infos


# ------------------------------------------------------------------------------------------------- decode
FLOW_DELAY_S = 14.0        # wet mass logged at t belongs to the position logged 14 s earlier (SMS)
MOISTURE_DELAY_S = 18.0    # moisture: 18 s earlier, i.e. logged 4 s after the wet mass of the same reading (SMS)
MATCH_TOL_S = 1.0          # a position takes a record logged more than delay - 1 s and less than delay + 1 s after it
LOG_BREAK = 2.5            # a pause of the log: the next record comes more than 2.5 log intervals later
PENDING_KEEP = 0.68645     # share of the grain not yet placed that moves on at each reading (SMS: 0.68645)
PENDING_SPREAD = 4         # readings over which the grain of a record that came too early enters: a CHOSEN value of the
#                            approximation, not a measured SMS rule (SMS spreads it over about twice as many readings
#                            as there are such records; its rule is not established)
MIN_SPEED_MS = 0.1         # alignment "shift": below 0.1 m/s (0.36 km/h) the combine is standing
MOISTURE_PROFILE_TOL = 2.0  # warn when a unit's median moisture is this many points above the profile's standard
MONITOR_MAX_YIELD = 10_000.0  # Max. Yield Volume (bu/ac) SMS stores with a GreenStar 3 card's product
MONITOR_MIN_YIELD = 5.0       # Min. Yield Volume (bu/ac) SMS stores with it
ALIGNMENTS = ("sms", "shift")
_EARTH_R_M = 6371008.8


def sms_align(t_ms, mass, moisture, harvest, flow_delay_s: float = FLOW_DELAY_S,
              moisture_delay_s: float = MOISTURE_DELAY_S, interval_s: float = 1.0,
              pending: str = "redistribute") -> dict:
    """Grain mass and moisture of the readings of ONE machine as Ag Leader SMS holds them after reading the card.

    t_ms (integers, ascending), mass (kg, NaN = the record has none), moisture (%, NaN = none) and harvest (True =
    header down, a section recording, valid position) are the records of the machine in time order. Returns
    {"wet_kg", "moisture_pct"} per record (NaN where harvest is False) and the counts of the report.

    Matching (the rules SMS follows, as measured; the parameters are in the module docstring):
      * a pause of the log (the next record more than LOG_BREAK intervals later) separates runs, and no record is
        matched across a pause;
      * the records are taken one to one in time order. Every record is a position, harvesting or not. The record
        k can be taken by the position p when it was logged more than flow_delay - 1 s after it AND the moisture
        that goes with it more than moisture_delay - 1 s after it; a record that is too early for the position
        waiting is passed over, and a position takes nothing when the next record is flow_delay + 1 s or more later;
      * the moisture of the record k is the one logged moisture_delay - flow_delay = 4 s after it: the first record
        with a moisture at least 3.5 s later, and 0 % when that record is 5.5 s or more later (a pause in between);
        a reading that took no record has 0 kg and 0 %.
    Grain of the records no reading took (pending "redistribute": SMS loses none of it; "drop": left out):
      * each reading receives 1 - PENDING_KEEP of the grain pending and the rest moves on to the next reading (in
        SMS the surplus over the matched record falls by 0.68645 from one reading to the next); what is still
        pending after the last reading goes to that reading.
      * where the grain enters is an APPROXIMATION of what SMS does (its rule is not fully resolved): the grain of
        a record taken by a position that is not harvesting enters at the reading logged flow_delay + 1 s before
        that position (a larger first share, measured in SMS, is not modelled); the grain of a record passed over
        enters flow_delay + 1 s after the position it was meant for, i.e. 1 s after it was logged, or earlier by
        what the pause before the run is shorter than that, in equal parts over PENDING_SPREAD readings (in SMS it
        enters over about twice as many readings as there are such records).
    """
    t = np.asarray(t_ms, np.int64)
    x = np.asarray(mass, float)
    m = np.asarray(moisture, float)
    harvest = np.asarray(harvest, bool)
    n = len(t)
    out = {"wet_kg": np.full(n, np.nan), "moisture_pct": np.full(n, np.nan), "kg_logged": float(np.nansum(x)),
           "n_no_record": 0, "n_no_moisture": 0, "n_not_harvesting": 0, "kg_not_harvesting": 0.0, "n_too_early": 0,
           "kg_too_early": 0.0, "kg_carried": 0.0}
    if pending not in ("redistribute", "drop"):
        raise ValueError(f"pending must be 'redistribute' or 'drop', not {pending!r}")
    if not n:
        return out
    ivl, fd, md = (int(round(v * 1000.0)) for v in (interval_s, flow_delay_s, moisture_delay_s))
    tol = int(round(MATCH_TOL_S * 1000.0))
    far = np.iinfo(np.int64).max // 4
    brk = np.r_[True, np.diff(t) > LOG_BREAK * ivl]
    run = np.cumsum(brk) - 1
    first = np.flatnonzero(brk)
    pause = np.r_[far, t[first[1:]] - t[first[1:] - 1]]            # pause before each run (none before the first)
    sens = np.flatnonzero(np.isfinite(x))                           # records that carry a mass
    moist_rec = np.flatnonzero(np.isfinite(m))
    lag = md - fd
    if len(sens) and len(moist_rec):
        j = np.searchsorted(t[moist_rec], t[sens] + lag - ivl // 2, side="left")
        q = np.where(j < len(moist_rec), moist_rec[np.minimum(j, len(moist_rec) - 1)], -1)
    else:
        q = np.full(len(sens), -1, np.int64)
    tq = np.where(q >= 0, t[np.maximum(q, 0)], far)                 # time of the moisture that goes with each record
    rec_moist = np.where((q >= 0) & (tq - t[sens] < lag + ivl + ivl // 2), m[np.maximum(q, 0)], 0.0)

    # ---- one-to-one matching in time order (plain Python integers: one pass over the records)
    take = np.full(n, -1, np.int64)                                 # index into sens of the record each position took
    taken = np.zeros(len(sens), bool)
    ts, rs, tql, tl, rl = t[sens].tolist(), run[sens].tolist(), tq.tolist(), t.tolist(), run.tolist()
    lo_mass, lo_moist, hi_mass = fd - tol, md - tol, fd + tol
    k, n_sens = 0, len(sens)
    for p in range(n):
        tp, rp = tl[p], rl[p]
        while k < n_sens and (rs[k] < rp or (rs[k] == rp and (ts[k] - tp <= lo_mass or tql[k] - tp <= lo_moist))):
            k += 1
        if k < n_sens and rs[k] == rp and ts[k] - tp < hi_mass:
            take[p] = k
            taken[k] = True
            k += 1
    got = take >= 0
    rec_of = sens[np.maximum(take, 0)] if len(sens) else np.zeros(n, np.int64)     # record each position took
    wet = np.where(got, x[rec_of], 0.0) if len(sens) else np.zeros(n)
    moist = np.where(got, rec_moist[np.maximum(take, 0)], 0.0) if len(sens) else np.zeros(n)
    hp = np.flatnonzero(harvest)
    out["n_no_record"] = int((harvest & ~got).sum())
    out["n_no_moisture"] = int((harvest & got & (moist == 0.0)).sum())
    not_harv = np.flatnonzero(got & ~harvest & (wet > 0))           # positions that took grain without harvesting
    early = sens[~taken]                                            # records passed over ...
    early = early[x[early] > 0]                                     # ... that carry grain
    out["n_not_harvesting"], out["n_too_early"] = int(len(not_harv)), int(len(early))
    out["kg_not_harvesting"], out["kg_too_early"] = float(wet[not_harv].sum()), float(x[early].sum())
    wet_h = wet[hp]

    # ---- the grain no reading took, carried to the readings next to it
    if pending == "redistribute" and len(hp) and (len(not_harv) or len(early)):
        thp, rhp = t[hp], run[hp]
        enter = np.zeros(len(hp))
        shift = fd + ivl
        for p0 in not_harv.tolist():
            i = int(np.searchsorted(thp, t[p0] - shift + ivl // 2, side="right")) - 1
            if i < 0 or rhp[i] != run[p0]:                          # no reading that early in the run: the next one
                i = min(int(np.searchsorted(hp, p0)), len(hp) - 1)
            enter[i] += wet[p0]
        for r in early.tolist():
            t_in = t[r] - fd + min(shift, int(pause[run[r]]))
            i = min(int(np.searchsorted(thp, t_in - ivl // 2, side="left")), len(hp) - 1)
            j = i + 1
            while j < len(hp) and j - i < PENDING_SPREAD and rhp[j] == rhp[i]:
                j += 1
            enter[i:j] += x[r] / (j - i)
        carried = np.zeros(len(hp))
        pend = 0.0
        for i, v in enumerate(enter.tolist()):
            pend += v
            carried[i] = (1.0 - PENDING_KEEP) * pend
            pend *= PENDING_KEEP
        carried[-1] += pend
        wet_h = wet_h + carried
        out["kg_carried"] = float(carried.sum())
    out["wet_kg"][hp] = wet_h
    out["moisture_pct"][hp] = moist[hp]
    return out


def _shift_right(lat, lon, heading_deg, offset_m):
    """WGS 84 positions moved offset_m to the right of the heading (to the left when negative; 0 where unknown)."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    h = np.radians(np.nan_to_num(np.asarray(heading_deg, float)))
    off = np.nan_to_num(np.asarray(offset_m, float))
    with np.errstate(invalid="ignore", divide="ignore"):
        return (lat - np.degrees(off * np.sin(h) / _EARTH_R_M),
                lon + np.degrees(off * np.cos(h) / (_EARTH_R_M * np.cos(np.radians(lat)))))


def _shift_in_time(t: np.ndarray, machine: np.ndarray, v: np.ndarray, delay: float, tol: float = 0.6) -> np.ndarray:
    """Alignment "shift": v taken ``delay`` seconds later on the same machine's continuous record series (across load
    boundaries); NaN where no record lies within ``tol`` s of t + delay. The search runs on each machine's own records
    in time order: the records come sorted by machine, so the time array of several combines is not monotonic and one
    binary search over all of them lands in the other machine's block (every reading of the first machine was lost)."""
    out = np.full(len(t), np.nan)
    if not len(t) or delay == 0:
        return v.astype(float).copy()
    for m in np.unique(machine):
        idx = np.flatnonzero(machine == m)
        idx = idx[np.argsort(t[idx], kind="stable")]
        tm, vm = t[idx], v[idx]
        j = np.searchsorted(tm, tm + delay - tol)
        jj = np.minimum(j, len(tm) - 1)
        ok = (j < len(tm)) & (np.abs(tm[jj] - (tm + delay)) <= tol)
        out[idx[ok]] = vm[jj[ok]]
    return out


def _monitor_product(blocks: list[dict]) -> dict:
    """What engine.sms_import.resolve needs to import the decoded loads as SMS does: the crop (key of common.CROPS,
    None when unknown), its weight to volume (lb/bu) and its dry moisture (%) as the card holds them - the one
    value all the loads share (the weight within 0.02 lb/bu: a card can state it in lb/bu in some loads and in
    kg/bu in others), None where they differ or the card has none - and max_yield MONITOR_MAX_YIELD, the Max. Yield
    Volume SMS stores with these cards (10,000 bu/ac in its Processing Settings; with the 300 / 200 bu/ac of SMS's own
    wheat / barley products part of the wet mass would be left out), min_yield MONITOR_MIN_YIELD, and source
    (where on the card the values are). The import uses them only when the caller passes this dict as monitor= to
    sms_import.resolve."""
    crops = {_crop_key(m["crop"]) for m in blocks}
    tw = sorted({round(m["crop_weight_lb_bu"], 4) for m in blocks if np.isfinite(m["crop_weight_lb_bu"])})
    sm = sorted({round(m["std_moisture_pct"], 4) for m in blocks if np.isfinite(m["std_moisture_pct"])})
    return {"crop": crops.pop() if len(crops) == 1 else None,
            "test_weight_lb_bu": float(tw[0]) if tw and tw[-1] - tw[0] <= 0.02 else None,
            "dry_moisture_pct": float(sm[0]) if len(sm) == 1 else None,
            "min_yield": MONITOR_MIN_YIELD, "max_yield": MONITOR_MAX_YIELD,
            "source": (f"GreenStar 3 card: .fdl RCDLogBlock/ConfigBlock/Setup/Products/Crop of {len(blocks)} load(s) "
                       "(CropWeight in lb1bu-1 or kg1bu-1, StandardPayableMoisture), the values SMS made its "
                       "product with; min / max yield = SMS's Processing Settings for these cards")}


def decode(root: str, unit_ids, opts: C.DecodeOptions | None = None, log=print,
           flow_delay_s: float | None = None, moisture_delay_s: float | None = None,
           alignment: str = "sms") -> tuple[pd.DataFrame, dict]:
    """Decode the selected units into the STANDARD schema (one row = one 1-s reading at the header centre).

    opts None -> DecodeOptions(shrink="one_sided"), the John Deere / SMS convention (dry mass never above wet).
    flow_delay_s / moisture_delay_s override the delays (seconds) between the position and the grain-mass / moisture
    channels (defaults FLOW_DELAY_S / MOISTURE_DELAY_S, the delays the vendor software applied to these logs).
    alignment "sms" (default): the readings, their grain and their moisture as Ag Leader SMS holds them after reading
    the card (sms_align and the module docstring). "shift": the fixed time shift of the earlier versions - a reading
    takes the record logged flow_delay_s later, is left out when there is none or the combine is standing, and the
    grain of the records no reading takes is lost.
    """
    t0 = time.time()
    opts = opts or C.DecodeOptions(shrink="one_sided")
    if alignment not in ALIGNMENTS:
        raise ValueError(f"alignment must be one of {ALIGNMENTS}, not {alignment!r}")
    fd = FLOW_DELAY_S if flow_delay_s is None else float(flow_delay_s)
    md = MOISTURE_DELAY_S if moisture_delay_s is None else float(moisture_delay_s)
    warnings: list[str] = []
    units = list(unit_ids) if unit_ids is not None else [u.id for u in list_units(root) if u.default]
    skipped = session_files(root)[1]
    if skipped:
        warnings.append(_copies_note(skipped))
    R, blocks, infos = read_records(root, units, log=log)
    if not len(R):
        raise ValueError("no harvest records in the selected units")
    n_read = len(R)
    mach = R.machine_serial.astype(str).to_numpy()
    lat, lon = R.lat_imp.to_numpy(), R.lon_imp.to_numpy()
    ivl = R.ivl_s.to_numpy().astype(float)

    # ---- readings the vendor software holds, every drop counted by reason (first reason wins)
    steps = [
        ("header_up", ~R.header_on.to_numpy()),
        ("sections_off", R.sections_on.to_numpy() == 0),
        ("no_gnss", ~np.isfinite(lat) | ~np.isfinite(lon) | ((np.abs(lat) < 1e-6) & (np.abs(lon) < 1e-6))
         | (np.abs(lat) > 90) | (np.abs(lon) > 180) | (R.signal.to_numpy() == "dtiSignalTypeUnknown")),
    ]
    aligned = {"method": alignment}
    if alignment == "sms":
        harvest = ~(steps[0][1] | steps[1][1] | steps[2][1])
        wet_al, moist_al = np.full(n_read, np.nan), np.full(n_read, np.nan)
        t_ms = R.t_ms.to_numpy()
        for m in np.unique(mach):
            idx = np.flatnonzero(mach == m)
            a = sms_align(t_ms[idx], R.wet_kg.to_numpy()[idx], R.moisture_pct.to_numpy()[idx], harvest[idx], fd, md,
                          float(np.median(ivl[idx])))
            wet_al[idx], moist_al[idx] = a.pop("wet_kg"), a.pop("moisture_pct")
            for k, v in a.items():
                aligned[k] = aligned.get(k, 0) + v
        R["wet_al"], R["moist_al"] = wet_al, moist_al
        dur = ivl                                       # the log interval, also after a pause (SMS: 1.0 everywhere)
        gap = np.zeros(n_read, bool)
        # with part of the header recording the reading sits at the centre of the recording sections
        lat, lon = _shift_right(lat, lon, R.head_imp.to_numpy(), R.active_lateral_m.to_numpy())
    else:
        t = R.t_ms.to_numpy() / 1000.0
        R["wet_al"] = _shift_in_time(t, mach, R.wet_kg.to_numpy(), fd)
        R["moist_al"] = _shift_in_time(t, mach, R.moisture_pct.to_numpy(), md)
        dur = R.dt_s.to_numpy().copy()
        gap = ~np.isfinite(dur) | (dur < 0.5 * ivl) | (dur > 2.5 * ivl)
        dur[gap] = ivl[gap]
        steps += [
            ("stationary", ~(R.dd_imp_m.to_numpy() / dur >= MIN_SPEED_MS)),
            ("no_mass", ~np.isfinite(R.wet_al.to_numpy())),
            ("no_moisture", ~np.isfinite(R.moist_al.to_numpy())),
        ]
    keep = np.ones(n_read, bool)
    pre = {}
    for name, bad in steps:
        pre[name] = int((keep & bad).sum())
        keep &= ~bad
    n_gap = int((keep & gap).sum())
    if n_gap:
        warnings.append(f"{n_gap} kept readings follow a logging gap or an irregular interval; their duration is set "
                        f"to the 1-s log interval (distance is the logged distance)")
    K = R[keep].copy()
    K["dur"] = dur[keep]

    # ---- crop, test weight and standard moisture per unit
    tw_by_unit, sm_by_unit, crop_by_unit, replaced = {}, {}, {}, set()
    for u in units:
        mu = [m for m in blocks if _unit_id(m) == u]
        if not mu:
            continue
        m0 = mu[0]
        mon_key = _crop_key(m0["crop"])
        same = opts.crop is None or (mon_key is not None and opts.crop.strip().lower() == mon_key)
        if not same:
            warnings.append(f"unit {m0['field']} {m0['start_utc'].year}: monitor crop {m0['crop']!r} replaced by "
                            f"{opts.crop!r}; the monitor's crop weight ({m0['crop_weight_lb_bu']} lb/bu) and standard "
                            f"moisture ({m0['std_moisture_pct']} %) are not used")
        crop, tw, sm = opts.resolve(mon_key if same else None,
                                    m0["crop_weight_lb_bu"] if same and np.isfinite(m0["crop_weight_lb_bu"]) else None,
                                    m0["std_moisture_pct"] if same and np.isfinite(m0["std_moisture_pct"]) else None)
        tw_by_unit[u], sm_by_unit[u], crop_by_unit[u] = tw, sm, crop
        if not same:
            replaced.add(u)
        # a monitor crop profile that does not fit the grain: a median moisture well above the standard moisture
        # (another crop's profile left on the display)
        mm = float(np.nanmedian(K.moist_al.to_numpy()[(K.unit == u).to_numpy()])) if (K.unit == u).any() else np.nan
        if np.isfinite(mm) and mm > sm + MOISTURE_PROFILE_TOL:
            warnings.append(f"unit {m0['field']} {m0['start_utc'].year}: median grain moisture {mm:.1f} % is more than "
                            f"{MOISTURE_PROFILE_TOL:g} points above the standard moisture {sm:g} % of the crop profile "
                            f"{crop!r} (monitor crop {m0['crop']!r}): check that the monitor's crop is the harvested "
                            "crop, or choose the crop / standard moisture before decoding")
        tws = {round(m["crop_weight_lb_bu"], 2) for m in mu if np.isfinite(m["crop_weight_lb_bu"])}
        if len(tws) > 1:
            warnings.append(f"unit {m0['field']}: crop weight changes between loads {sorted(tws)}; the first is used")
        if any(m["crop_year"] and m["crop_year"] != m["start_utc"].year for m in mu):
            warnings.append(f"unit {m0['field']} {m0['start_utc'].year}: the monitor's cropYear attribute "
                            f"({sorted({m['crop_year'] for m in mu})}) differs from the log date (informational)")

    # ---- standard columns
    tz = opts.tz_offset_h
    tloc = K.t_utc + pd.to_timedelta(tz or 0.0, unit="h")
    dist_ft = K.dd_imp_m.to_numpy() * C.FT_PER_M
    width_ft = K.active_width_m.to_numpy() * C.FT_PER_M
    wet_lb = K.wet_al.to_numpy() * C.LB_PER_KG
    area_ac = dist_ft * width_ft / C.SQFT_PER_AC
    load_start = R.groupby("load_ref").t_utc.min()
    ds_start = K.load_ref.map(load_start) + pd.to_timedelta(tz or 0.0, unit="h")
    out = pd.DataFrame({
        "Field": K.field.to_numpy(),
        "Dataset": [C.dataset_name(l, s) for l, s in zip(K.load, ds_start)],
        "Product": np.where(K.unit.isin(replaced).to_numpy(), K.unit.map(crop_by_unit).astype(str).to_numpy(),
                            np.where(K.variety.astype(str).str.len() > 0, K.variety, K.crop)),
        "Obj__Id": 0,
        "Time": tloc.dt.strftime("%Y-%m-%d %H:%M:%S.") + ((tloc.dt.microsecond // 100000).astype(str)),
        "Duration_s": K.dur.to_numpy(),
        "Elevation_": K.elev_m.to_numpy() * C.FT_PER_M,
        "Speed_mph_": K.dd_imp_m.to_numpy() / K.dur.to_numpy() * C.MPH_PER_MS,
        "Distance_f": dist_ft,
        "Track_deg_": K.head_imp.to_numpy(),
        "Swth_Wdth_": width_ft,
        "Moisture__": K.moist_al.to_numpy(),
    })
    yc = {k: np.full(len(K), np.nan) for k in ("Yld_Mass_W", "Yld_Mass_D", "Yld_Vol_We", "Yld_Vol_Dr")}
    for u in tw_by_unit:
        sel = (K.unit == u).to_numpy()
        cols = C.yield_columns(wet_lb[sel], area_ac[sel], K.moist_al.to_numpy()[sel], tw_by_unit[u], sm_by_unit[u],
                               opts.shrink)
        for k, v in cols.items():
            yc[k][sel] = v
    for k, v in yc.items():
        out[k] = v
    out["Machine"] = K.machine_serial.to_numpy()
    out["Src_Fmt"] = FORMAT_ID
    out["lon"], out["lat"] = lon[keep], lat[keep]
    out["_t"] = K.t_utc.to_numpy()
    out = out.sort_values(["Dataset", "_t"], kind="stable").drop(columns="_t").reset_index(drop=True)
    out["Obj__Id"] = out.groupby("Dataset").cumcount() + 1
    probs = C.check_standard(out)
    if probs:
        raise ValueError("decoded table does not follow the standard schema: " + "; ".join(probs))

    # ---- report
    machines, totals = C.summarize(out)
    if not all(m["load_number_known"] for m in blocks):
        warnings.append("load files (.fdr) not found for some loads: the dataset is named 'Load <GUID>' instead of "
                        "'L<number>:<name>'")
    n_fb = sum(m.get("imp_fallback", False) for m in blocks)
    if n_fb:
        warnings.append(f"{n_fb} log blocks have no computed implement point: the header point was projected from "
                        f"the receiver with the machine offsets of the .fdl")
    offs = sorted({round(m["gps_to_axle_m"] - m["axle_to_connection_m"], 3) for m in blocks
                   if np.isfinite(m["gps_to_axle_m"] - m["axle_to_connection_m"])})
    if len(offs) > 1:
        warnings.append(f"the receiver-to-header offset entered on the display changes between loads ({offs} m); the "
                        f"positions follow what the monitor logged")
    cal_vals = sorted({m["mass_calibration"] for m in blocks if m["mass_calibration"]})
    lat_vals = sorted({m["latency_mass_s"] for m in blocks if np.isfinite(m["latency_mass_s"])})
    if alignment == "sms":
        lost = 100.0 * (aligned["kg_logged"] - float(np.nansum(K.wet_al.to_numpy()))) / max(aligned["kg_logged"], 1e-9)
        aligned["pct_of_logged_mass_not_on_a_reading"] = round(lost, 4)
        if aligned["n_no_moisture"] + aligned["n_no_record"]:
            warnings.append(f"{aligned['n_no_moisture']} readings have no moisture record {md - fd:g} s after their "
                            f"grain record and {aligned['n_no_record']} have no grain record {fd:g} s later (the log "
                            "paused): they are kept with 0 % / 0 kg, as SMS keeps them")
        mass_txt = (f"sms_align: the record logged {fd:g} s after the position (one-to-one matching in time order, "
                    f"more than {fd - MATCH_TOL_S:g} s later and its moisture more than {md - MATCH_TOL_S:g} s later); "
                    f"the grain of the {aligned['n_not_harvesting'] + aligned['n_too_early']} records no reading took "
                    f"({aligned['kg_not_harvesting'] + aligned['kg_too_early']:.0f} kg) carried to the readings next "
                    f"to them (approximation of where SMS places it)")
    else:
        n_lag_edge = pre["no_mass"] + pre["no_moisture"]
        if n_lag_edge:
            warnings.append(f"{n_lag_edge} readings at the end of the machine's log have no mass/moisture "
                            f"{fd:g}/{md:g} s later and were dropped")
        mass_txt = f"the record logged {fd:g} s later (fixed time shift; the grain of the other records is lost)"
    report = {
        "format": FORMAT_ID, "label": LABEL, "units": units,
        "n_records_read": int(n_read), "n_readings": int(len(out)), "pre_step": pre,
        "machines": machines, "totals": totals,
        "time_basis": "local" if tz is not None else "UTC",
        "alignment": aligned,
        "monitor_product": _monitor_product(blocks),
        "crop_name": (lambda n: n[0] if len(n) == 1 else None)(sorted({m["crop"] for m in blocks if m["crop"]})),
        "sessions_left_out": len(skipped),
        "calibration": {
            "mass_channel": "vrYieldWetMass (MeasuredElement of the harvest Meter), kg of wet grain per 1-s record, "
                            "already calibrated by the monitor (calibration factor attribute(s) "
                            f"{cal_vals}); converted to lb x {C.LB_PER_KG}; " + mass_txt,
            "moisture_channel": "vrHarvestMoisture, % wet basis",
            "area": "distance travelled by the computed implement point (DeltaDistance of the dtiComputedImplement "
                    "navigation reference) x active width (sum of the widths of the sections whose dtRecordingStatus "
                    "is On)",
            "position": "dtiComputedImplement navigation reference = header connection point on the machine "
                        "centre-line (inline offset from the receiver given in the .fdl), the point the vendor export "
                        "uses" + ("; with part of the header recording, the centre of the recording sections"
                                  if alignment == "sms" else ""),
            "receiver_to_header_m": offs,
            "flow_delay_s": fd, "moisture_delay_s": md, "fdl_latency_s": lat_vals,
            "shrink": opts.shrink,
            "test_weight_lb_bu": {u: tw_by_unit[u] for u in tw_by_unit},
            "std_moisture_pct": {u: sm_by_unit[u] for u in sm_by_unit},
            "crop": {u: crop_by_unit[u] for u in crop_by_unit},
            "validation": "checked against Ag Leader SMS point exports and the SMS RAW Summary of GreenStar 3 cards: "
                          "nearly every SMS export point on a decoded reading, with the same duration, "
                          "distance and moisture; the grain mass of the readings that carry grain SMS moved from "
                          "records no reading took is placed here by an approximation. Imported with the card's "
                          "weight to volume and dry moisture and a 10,000 bu/ac maximum (report['monitor_product'], "
                          "SMS's Processing Settings for these cards), the readings meet the SMS RAW Summary in area, "
                          "wet mass and dry volume; SMS holds a few readings fewer (open: rule not established).",
        },
        "warnings": warnings,
    }
    log(f"decoded {len(out)} readings from {n_read} records in {time.time() - t0:.1f} s; dropped {pre}")
    return out, report
