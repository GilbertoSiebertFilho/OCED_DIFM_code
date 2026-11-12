# -*- coding: utf-8 -*-
"""ISOXML task data of a combine (ISO 11783-10: TASKDATA.XML with its TLGnnnnn.XML / .BIN time logs) -> standard
readings. Any maker's task data: nothing here is read by position or by a maker's names, everything through the
task data's own references (task -> time log -> logged value -> device element -> device) and the data dictionary
of ISO 11783-11 (DDI_TABLE).

METHOD (the rules of "points as SMS" were measured against Ag Leader SMS Advanced on the harvest task data of one
combine display, every point of every time log compared; the logs and the comparison scripts are not part of this
repository; tests: tests/test_isoxml.py on synthetic task data)

Task data. One input holds any number of TASKDATA folders (a telematics portal hands a running task out in slices, one
zipped TASKDATA set per slice; the app unpacks zips, nested ones included). TASKDATA.XML holds the coding data
(customer CTR, farm FRM, field PFD, product PDT, crop type CTP / variety CVT), the device descriptions (DVC: device
elements DET with their type and parent, the process data DPD and properties DPT they refer to through DOR, the
presentations DVP) and the tasks (TSK: time logs TLG, totals TIM, device and product allocations DAN / PAN, the
connection CNN of the vehicle to the implement). Elements kept in external files (XFR -> <name>.XML with an XFC
root) are read from there. A TLG is two files: the .XML header names what every binary record holds (TIM and PTN
attributes with an empty value, and the list of logged values DLV: DDI, device element, for DDI DFFE the PGN and
its bits), the .BIN holds the records, little-endian: time (uint32 ms since midnight, uint16 days since 1980-01-01),
then the position attributes of the header (latitude and longitude int32 1e-7 deg, altitude int32 mm, status uint8,
PDOP and HDOP uint16 0.1, satellites uint8, GNSS UTC time uint32 and date uint16), a uint8 count and that many
(uint8 index into the header's DLV list, int32 value). A value holds until it is logged again. A header lists at
most 255 values, so a display with more splits one log over several TLG files with the same time stamps: those are
put together record by record (never counted twice). The order of the values differs from file to file: always the
file's own header.

Values (DDI_TABLE; raw integers in the dictionary's units, whatever the DVP presentation says: a DVP only tells how
the display shows a value, and is quoted in the report):
    0057 (87)  mass per time yield, mg/s: the WET grain flow       0054 (84)  mass per area yield, mg/m2
    0063 (99)  crop moisture, ppm (1e-4 %)                         00B8 (184) reference moisture for dry mass, ppm
    0079 (121) product density, mg/l                               008F (143) actual value time latency, ms
    00A1-00B0 (161-176) condensed work state, 2 bits per section (01 = on), 16 sections per DDI
    0046 (70) maximum, 0043 (67) actual, 0042 (66) setpoint, 0044 (68) default working width, mm
    0086 / 0087 / 0088 (134-136) device element offsets X (forward) / Y (right) / Z, mm       008D (141) work state
    00B6 (182) / 00B5 (181) dry mass per time / per area, 0056 (86) / 0053 (83) volume per time / per area
A time log is a harvest log when it logs a yield value (YIELD_DDIS) on some device element; that element is the
header. A log without one (a planter, a sprayer, a setup) is not a harvest log and is not listed. A combine can
describe several headers: each one that measured grain in a log is read by the rules below, one that measured none
is passed over.

Points as SMS (decode(time_logs="sms"), the default). SMS reads every time log on its own (one dataset per log):
  1 a record followed by the next one in less than MERGE_S is put together with that next one, which keeps its own
    time, position and sections; its duration and distance are measured from the last record kept and its flow and
    moisture are the time-weighted mean of the two (a record whose clock steps back is left out);
  2 Duration = time since the previous record (the first record of a log: FIRST_DURATION_S); Distance = step from the
    previous record on a flat earth of M_PER_DEG metres per degree, the longitude scaled by the cosine of the
    previous record's latitude (the first record: the step of the second); Track = the bearing of that step;
  3 Swath = the sections on (condensed work state) times their width, a section's width being the header's maximum
    working width / number of sections (the section's own width when the task data gives one). The setpoint width
    the display logs is not used, nor the work state of the device;
  4 flow and moisture of a record = those of the record NEAREST in time to (its time - latency), the latency being
    the record's own DDI 008F (negative: the grain cut now is measured later); a record with latency 0 keeps its
    own values; when the log ends before that time there is no point, so the last seconds of every log give none;
  5 a point = a record with swath > 0 and flow > 0. It sits at the logged position moved forward along Track by the
    X offset of the connector the header hangs on, and sideways to the header's reference: the centre of the
    sections on in the widest state of the log, from the Y offsets the display logs for its sections (once per
    log; + = right of travel). Y_Offset_f = centre of the sections on - that reference, which SMS's Optimize Swath
    Widths moves the point by (engine.clean.sms3sigma S2). The offsets are used as SMS uses them (as the centres
    of the sections, each one section wide) as long as they lie side by side: every step from a section to the
    next is one section, but for the SECTION_END_STEPS outermost steps at either end, which may be shorter (seen:
    a header set narrower than its maximum width, whose whole header then reads a fraction of a section beside the
    middle; SMS places its points there too). Offsets that do not lie side by side (seen on a later software
    version of the same display: values logged while the header was still being laid out, crowded to one side or
    with an odd one among them, which would put every point of the log metres beside the strip it cut) are not
    the centres of anything: they are passed over, the sections taken side by side, centred on the header, and
    the logs are counted in a warning (details[..]["sections"]: "logged" | "side by side" | "no offsets"). What
    SMS makes of such a log was not compared;
  6 wet mass = flow x Duration, area = Distance x Swath; the monitor's own per-area yields are not used.
SMS's import then leaves out the points under its minimum yield (engine/sms_import.py), as for any source.
Because of rule 4 the grain of the first seconds of a log file is given to no point and the last seconds of a file
are no points: a task handed out in slices loses that at every slice. time_logs="joined" reads the logs of a task
that follow each other without a gap (JOIN_GAP_S) as one log, so nothing is lost at the slice ends; it is NOT what
SMS holds (more points, more grain).

Without section states (a header with no sections) the width is the actual working width (0043), else the setpoint
(0042), the default (0044) or the maximum (0046), on the records whose work state (008D of the header, else of its
device) is 1. Without a mass flow the wet mass comes from the mass per area (0054), else from the dry mass or the
volume channels (CHANNELS, in that order; the report names the channel and says that these routes were not compared
with SMS).

Crop, weight to volume and dry moisture (report["monitor_product"], used by engine/sms_import.py). SMS does not take
the density and the reference moisture the log carries (DDI 0079 / 00B8): it gives the product of an ISOXML task its
own values for the CROP, ISO_SMS_CROPS (measured for wheat and canola), with a minimum of 5 and a maximum of 10,000
bu/ac. The crop is the maker's crop code where the task data has one (MAKER_CROP_CODES: the crop numbers of that
maker's cards; product names typed on a display are unreliable), else the crop type (CTP) or variety of the field,
else the product allocated to the task. Four cases:
    a crop with an SMS value (wheat, canola)        SMS's values for it (basis "sms_crop");
    another crop OCED knows (barley, peas ...)      no SMS value established: SMS's product table applies;
    a crop code or name OCED does not know          the density and the reference moisture the log carries (basis
                                                    None, "from the file"); what they read like is said as a guess
                                                    (monitor_product["guess"], the unit's label) and a warning asks
                                                    to check the product. What SMS gives such a task is not known;
    no crop and no density in the log               what SMS stores for a dataset it could give no product ('NO
                                                    Product'): ISO_SMS_NO_PRODUCT, with a warning (basis
                                                    "sms_no_product").
The decoder's own yields use the same values (DecodeOptions > SMS's for the crop > the log's own > the crop table >
SMS's values for no product).

Time. The Time column is the log's own clock (TIM), as SMS shows it: local time on the displays seen, UTC on others.
Where the log carries the GNSS UTC time the difference is measured (report time_note) and the basis reported ("UTC"
when the log clock is UTC, "local" otherwise, "unknown" without GNSS time); DecodeOptions.tz_offset_h moves the
times to UTC + that offset when the difference is known.

Damaged or partial sets never stop the others: a TASKDATA.XML that is not XML, a missing .BIN or header, a log
that ends inside a record or names a value its header does not list are reported in the warnings and the readable
part is read (probe / list_units / decode; the units count the records that can be read); a .BIN whose record
times are not times in order (a file of something else under the log's name) is named as damaged and not listed;
explain() says why a set gives no harvest data. Records that give no reading for want of a position or because the
clock stepped back are counted in a warning of the decode.
"""
from __future__ import annotations

import dataclasses
import os
import time as _time
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd

from . import common as C

FORMAT_ID = "isoxml"
LABEL = "ISOXML task data (ISO 11783 TASKDATA.XML with TLG time logs)"
DEFAULT_SHRINK = "one_sided"          # SMS's import (expand dry moisture off)
PROBE_NOTES = True                    # detect() lists what the probe could not read (missing or damaged files)
TIME_LOGS = ("sms", "joined")         # decode(time_logs=...): each time log on its own (SMS) | consecutive logs joined
MERGE_S = 0.9                         # SMS: a record followed by the next one in less than this is put together with it
                                      # (bracketed, not pinned: in the logs compared every pair of records a few
                                      # hundredths of a second closer than this was one SMS point, every pair a few
                                      # hundredths further apart two; no pair lay in between)
FIRST_DURATION_S = 1.0                # SMS: the Duration of the first record of a log
M_PER_DEG = 111_120.0                 # SMS's flat-earth metric (60 x 1852 m per degree)
JOIN_GAP_S = 2.5                      # time_logs "joined": the next log starts within this of the previous one's end
LONG_DURATION_S = 10.0                # a point lasting longer than this is counted in a warning
SECTION_STEP_TOL = 0.05               # sections side by side: a step between neighbours within this of one section
SECTION_END_STEPS = 2                 # ... but for this many steps at either end, which may be shorter
DAMAGED_BACK, DAMAGED_TIME = 0.2, 0.01    # a .BIN is no time log when more than these shares of its records step
                                          # back in time / carry no time of day
EPOCH = pd.Timestamp("1980-01-01")
NA_I32 = 0x7FFFFFFF
BU_M3 = 0.0352390704                  # US bushel, m3
_SKIP_DIRS = {"__MACOSX", ".git", "$RECYCLE.BIN", "System Volume Information"}

# ISO 11783-11 data dictionary entries this reader uses: DDI -> (key, name, unit of the raw integer)
DDI_TABLE = {
    0x0042: ("setpoint_width", "Setpoint Working Width", "mm"),
    0x0043: ("actual_width", "Actual Working Width", "mm"),
    0x0044: ("default_width", "Default Working Width", "mm"),
    0x0046: ("max_width", "Maximum Working Width", "mm"),
    0x0053: ("volume_per_area", "Actual Volume Per Area Yield", "ml/m2"),
    0x0054: ("mass_per_area", "Actual Mass Per Area Yield", "mg/m2"),
    0x0056: ("volume_per_time", "Actual Volume Per Time Yield", "ml/s"),
    0x0057: ("mass_per_time", "Actual Mass Per Time Yield", "mg/s"),
    0x0059: ("total_volume", "Yield Total Volume", "l"),
    0x005A: ("total_mass", "Yield Total Mass", "kg"),
    0x0063: ("moisture", "Actual Crop Moisture", "ppm"),
    0x0074: ("total_area", "Total Area", "m2"),
    0x0075: ("total_distance", "Effective Total Distance", "mm"),
    0x0077: ("total_time", "Effective Total Time", "s"),
    0x0079: ("density", "Product Density Mass Per Volume", "mg/l"),
    0x0086: ("offset_x", "Device Element Offset X", "mm"),
    0x0087: ("offset_y", "Device Element Offset Y", "mm"),
    0x0088: ("offset_z", "Device Element Offset Z", "mm"),
    0x008D: ("work_state", "Actual Work State", ""),
    0x008F: ("latency", "Physical Actual Value Time Latency", "ms"),
    0x00B5: ("dry_mass_per_area", "Dry Mass Per Area Yield", "mg/m2"),
    0x00B6: ("dry_mass_per_time", "Dry Mass Per Time Yield", "mg/s"),
    0x00B7: ("total_dry_mass", "Yield Total Dry Mass", "kg"),
    0x00B8: ("reference_moisture", "Reference Moisture For Dry Mass", "ppm"),
    0xDFFE: ("pgn", "PGN Based Data", ""),
}
DDI = {v[0]: k for k, v in DDI_TABLE.items()}
CONDENSED_FIRST, CONDENSED_LAST = 0x00A1, 0x00B0     # Actual Condensed Work State 1-16 ... 241-256
# the grain of a reading, in the order they are used: (key, per "time" or "area", wet or dry mass, or volume)
CHANNELS = (("mass_per_time", "time", "wet"), ("mass_per_area", "area", "wet"), ("dry_mass_per_time", "time", "dry"),
            ("dry_mass_per_area", "area", "dry"), ("volume_per_time", "time", "volume"),
            ("volume_per_area", "area", "volume"))
YIELD_DDIS = tuple(DDI[k] for k, _, _ in CHANNELS)
WIDTH_KEYS = ("actual_width", "setpoint_width", "default_width", "max_width")     # a header without sections
DET_DEVICE, DET_FUNCTION, DET_SECTION, DET_CONNECTOR, DET_NAVIGATION = "1", "2", "4", "6", "7"

# What Ag Leader SMS gives the product of an ISOXML harvest task: its own weight to volume (lb/bu) and dry moisture
# (%) for the crop, not the density and reference moisture the log carries (a wheat log saying 13.5 % and a canola
# log saying 8.5 % are read at 13.5 % and 10.0 %). A crop not listed is not established: SMS's product table applies.
ISO_SMS_CROPS = {"wheat": (60.0, 13.5), "canola": (50.0, 10.0)}
ISO_MIN_YIELD, ISO_MAX_YIELD = 5.0, 10_000.0          # SMS's Min. / Max. Yield Volume of these datasets (bu/ac)
# what SMS stores for the dataset of an ISOXML task it could give no product ('NO Product'): lb/bu, dry moisture %
ISO_SMS_NO_PRODUCT = (56.0, 13.0)
# a maker's own crop code on the task or its product: attribute -> {code: key of common.CROPS}. The codes are the
# crop numbers of the same maker's cards (the density and reference moisture the logs carry for a code are those of
# the card's crop table for that number): 48 and 10 seen with their SMS product in ISOXML task data, the others with
# their SMS product on cards (engine/formats/cnh_cn1.py CROP_NAMES)
MAKER_CROP_CODES = {"P094_Crop_Type": {48: "wheat", 10: "canola", 47: "durum", 17: "barley", 40: "peas"}}
# (lb/bu, reference moisture %) a log carries -> the crop it reads like, for a crop code or name OCED does not know
# (a guess, shown with a "?"; the same pairs as the maker's cards, cnh_cn1.CROP_GUESS)
CROP_GUESS = {(48.0, 14.5): "barley", (50.0, 8.5): "canola", (60.0, 13.5): "wheat", (32.0, 14.0): "oats"}
REASON_TEXT = {"no_position": "without a usable position", "time_step": "after a step back of the clock",
               "put_together": "put together with the record after them", "sections_off": "with every section off",
               "log_end": "at the end of a log, whose grain arrives after its last record",
               "no_flow": "with no grain flow"}
VALIDATION_NOTE = ("time_logs='sms': compared with the points Ag Leader SMS holds after reading the same task data "
                   "(one combine display, one software version): the same records, with the same duration, distance, "
                   "swath, flow, moisture and offset on every point, and the SMS Summary of the field met. Not "
                   "compared with SMS: a log with two working headers, a header without sections, a log without a "
                   "mass flow, section offsets that do not lie side by side, a crop code OCED does not know, other "
                   "makers' harvest task data, time_logs='joined'")


class IsoxmlError(ValueError):
    """The task data cannot be read (a plain message for the user)."""


def default_options() -> C.DecodeOptions:
    return C.DecodeOptions(shrink=DEFAULT_SHRINK)


# ================================================================================================ TASKDATA.XML
@dataclasses.dataclass
class Element:
    id: str
    obj: str
    type: str
    name: str
    number: int
    parent: str
    refs: list
    device: str


@dataclasses.dataclass
class Device:
    id: str
    name: str
    software: str
    client: str
    serial: str
    elements: dict
    dpd: dict            # object id -> {"ddi", "name", "dvp"}
    dpt: dict            # object id -> {"ddi", "value", "name", "dvp"}
    dvp: dict            # object id -> {"offset", "scale", "decimals", "unit"}

    def element_by_obj(self, obj) -> Element | None:
        return next((e for e in self.elements.values() if e.obj == obj), None)

    def children(self, el: Element, typ: str | None = None) -> list:
        out = [e for e in self.elements.values() if e.parent == el.obj and e.id != el.id
               and (typ is None or e.type == typ)]
        return sorted(out, key=lambda e: (e.number, _num_tail(e.id)))

    def prop(self, el: Element, ddi: int):
        """The value of a device property (DPT) of this element for a DDI, or None."""
        for r in el.refs:
            p = self.dpt.get(r)
            if p and p["ddi"] == ddi and p["value"] is not None:
                return p["value"]
        return None

    def present(self, el: Element, ddi: int) -> str:
        """How the display names and shows a DDI of this element ('<name>, shown in <unit>'), '' when not described."""
        for r in el.refs:
            p = self.dpd.get(r) or self.dpt.get(r)
            if p and p["ddi"] == ddi:
                v = self.dvp.get(p.get("dvp") or "")
                return (p.get("name") or "") + (f", shown in {v['unit']}" if v and v.get("unit") else "")
        return ""


@dataclasses.dataclass
class Task:
    id: str
    name: str
    attrs: dict
    customer: str
    farm: str
    field: str
    logs: list           # TLG names
    products: list       # [{"id", "name", "attrs", "element"}] of the product allocations
    connections: list    # [(vehicle device, its connector element, implement device, its connector element)]
    totals: list         # [{ddi, value, element}] of the last TIM
    crop_type: str       # the field's crop type / variety designator


@dataclasses.dataclass
class TaskSet:
    folder: str
    version: str
    software: str
    devices: dict
    elements: dict       # element id -> Element (all devices)
    tasks: list
    files: dict          # upper-case name -> name in the folder
    warnings: list


def _num_tail(s) -> int:
    d = "".join(ch for ch in str(s) if ch.isdigit())
    return int(d[-12:]) if d else 0


def _int(v, default=None):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return default


def _hex(v):
    try:
        return int(str(v).strip(), 16)
    except (TypeError, ValueError):
        return None


def _float(v, default=None):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


def _parse_xml(path: str):
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError, UnicodeError) as e:
        raise IsoxmlError(f"{os.path.basename(path)} is not readable XML ({type(e).__name__}: {e})") from None


def read_taskdata(folder: str) -> TaskSet:
    """The coding data, device descriptions and tasks of one TASKDATA folder (external files of XFR included)."""
    files = {f.upper(): f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f))}
    if "TASKDATA.XML" not in files:
        raise IsoxmlError(f"no TASKDATA.XML in {folder}")
    root = _parse_xml(os.path.join(folder, files["TASKDATA.XML"]))
    warns = []
    for x in root.findall("XFR"):
        name = (x.get("A") or "").upper() + ".XML"
        if name not in files:
            warns.append(f"{x.get('A')}.XML, which TASKDATA.XML refers to, is missing")
            continue
        try:
            root.extend(list(_parse_xml(os.path.join(folder, files[name]))))
        except IsoxmlError as e:
            warns.append(str(e))
    names = {tag: {e.get("A"): e for e in root.findall(tag)} for tag in ("CTR", "FRM", "PFD", "PDT", "CTP")}
    varieties = {v.get("A"): v.get("B") or "" for c in root.findall("CTP") for v in c.findall("CVT")}
    devices, elements = {}, {}
    for d in root.findall("DVC"):
        dev = Device(id=d.get("A") or "", name=d.get("B") or "", software=d.get("C") or "", client=d.get("D") or "",
                     serial=(d.get("E") or "").strip(), elements={}, dpd={}, dpt={}, dvp={})
        for e in d.findall("DET"):
            el = Element(id=e.get("A") or "", obj=e.get("B") or "", type=e.get("C") or "", name=e.get("D") or "",
                         number=_int(e.get("E"), 0), parent=e.get("F") or "",
                         refs=[r.get("A") for r in e.findall("DOR")], device=dev.id)
            dev.elements[el.id] = el
            elements[el.id] = el
        for p in d.findall("DPD"):
            dev.dpd[p.get("A")] = {"ddi": _hex(p.get("B")), "name": p.get("E") or "", "dvp": p.get("F")}
        for p in d.findall("DPT"):
            dev.dpt[p.get("A")] = {"ddi": _hex(p.get("B")), "value": _int(p.get("C")), "name": p.get("D") or "",
                                   "dvp": p.get("E")}
        for p in d.findall("DVP"):
            dev.dvp[p.get("A")] = {"offset": _float(p.get("B"), 0.0), "scale": _float(p.get("C"), 1.0),
                                   "decimals": _int(p.get("D"), 0), "unit": p.get("E") or ""}
        devices[dev.id] = dev
    tasks = []
    for t in root.findall("TSK"):
        pfd = names["PFD"].get(t.get("E"))
        ctr = names["CTR"].get(t.get("C")) if t.get("C") else (names["CTR"].get(pfd.get("E")) if pfd is not None
                                                                 else None)
        frm = names["FRM"].get(t.get("D")) if t.get("D") else (names["FRM"].get(pfd.get("F")) if pfd is not None
                                                                 else None)
        crop_type = ""
        if pfd is not None:
            ctp = names["CTP"].get(pfd.get("G"))
            crop_type = (ctp.get("B") if ctp is not None else "") or varieties.get(pfd.get("H"), "") or ""
        prods = []
        for p in t.findall("PAN"):
            pdt = names["PDT"].get(p.get("A"))
            prods.append({"id": p.get("A"), "name": (pdt.get("B") if pdt is not None else "") or "",
                          "attrs": dict(pdt.attrib) if pdt is not None else {}, "element": p.get("E")})
        tims = t.findall("TIM")
        totals = [{"ddi": _hex(v.get("A")), "value": _int(v.get("B")), "element": v.get("C")}
                  for v in (tims[-1].findall("DLV") if tims else [])]
        tasks.append(Task(
            id=t.get("A") or "", name=t.get("B") or "", attrs=dict(t.attrib),
            customer=(ctr.get("B") if ctr is not None else "") or "",
            farm=(frm.get("B") if frm is not None else "") or "",
            field=(pfd.get("C") if pfd is not None else "") or "",
            logs=[g.get("A") for g in t.findall("TLG") if g.get("A")], products=prods,
            connections=[(c.get("A"), c.get("B"), c.get("C"), c.get("D")) for c in t.findall("CNN")],
            totals=totals, crop_type=crop_type))
    return TaskSet(folder=folder, version=f"{root.get('VersionMajor') or '?'}.{root.get('VersionMinor') or '?'}",
                   software=" ".join(x for x in (root.get("TaskControllerManufacturer"),
                                                 root.get("TaskControllerVersion")) if x),
                   devices=devices, elements=elements, tasks=tasks, files=files, warnings=warns)


# ================================================================================================ time logs
_PTN_FIELDS = (("A", "lat", "<i4"), ("B", "lon", "<i4"), ("C", "up", "<i4"), ("D", "status", "u1"),
               ("E", "pdop", "<u2"), ("F", "hdop", "<u2"), ("G", "nsat", "u1"), ("H", "gps_ms", "<u4"),
               ("I", "gps_day", "<u2"))


@dataclasses.dataclass
class LogHeader:
    dtype: np.dtype      # the fixed part of a record
    values: list         # [(ddi, element id, pgn or None, first bit or None)] in the order of the header
    status: int | None   # a position status given once in the header (not in the records)


def read_log_header(xml_path: str) -> LogHeader:
    """What every record of a time log holds, from its .XML header."""
    root = _parse_xml(xml_path)
    tim = root if root.tag == "TIM" else root.find(".//TIM")
    if tim is None:
        raise IsoxmlError(f"{os.path.basename(xml_path)} has no TIM element: not a time log header")
    if tim.get("A") != "":
        raise IsoxmlError(f"{os.path.basename(xml_path)}: the records carry no time (TIM A is not empty)")
    fields = [("ms", "<u4"), ("day", "<u2")]
    if tim.get("B") == "":
        fields += [("stop_ms", "<u4"), ("stop_day", "<u2")]
    if tim.get("C") == "":
        fields.append(("duration", "<u4"))
    if tim.get("D") == "":
        fields.append(("type", "u1"))
    ptn = tim.find("PTN")
    status = None
    if ptn is not None:
        for key, name, dt in _PTN_FIELDS:
            if ptn.get(key) == "":
                fields.append((name, dt))
        status = _int(ptn.get("D")) if ptn.get("D") not in (None, "") else None
    vals = []
    for v in tim.findall("DLV"):
        vals.append((_hex(v.get("A")), v.get("C") or "", _int(v.get("D")), _int(v.get("E"))))
    return LogHeader(dtype=np.dtype(fields), values=vals, status=status)


def _walk(buf: bytes, fixed: int) -> tuple:
    """(offset of every complete record, its number of values, bytes left over at the end)."""
    n = len(buf)
    offs, cnt = [], []
    pos = 0
    while pos + fixed + 1 <= n:
        k = buf[pos + fixed]
        end = pos + fixed + 1 + 5 * k
        if end > n:
            break
        offs.append(pos)
        cnt.append(k)
        pos = end
    return np.asarray(offs, np.int64), np.asarray(cnt, np.int64), n - pos


@dataclasses.dataclass
class Log:
    head: np.ndarray     # the fixed parts (structured)
    t: np.ndarray        # int64 ms since 1980-01-01 on the log's clock
    values: list         # the header's value list (parts appended)
    vrec: np.ndarray     # record of every logged value
    vidx: np.ndarray     # its index into values
    vval: np.ndarray     # the value
    status: int | None
    warnings: list

    def __len__(self):
        return len(self.t)

    def index(self, ddi: int, element: str) -> int | None:
        for j, v in enumerate(self.values):
            if v[0] == ddi and v[1] == element and v[2] is None:
                return j
        return None

    def column(self, j: int | None) -> np.ndarray:
        """A logged value on every record (held until logged again; NaN before its first value)."""
        n = len(self.t)
        col = np.full(n, np.nan)
        if j is None or not n:
            return col
        m = self.vidx == j
        if not m.any():
            return col
        col[self.vrec[m]] = self.vval[m]
        last = np.where(np.isfinite(col), np.arange(n), -1)
        np.maximum.accumulate(last, out=last)
        ok = last >= 0
        col[ok] = col[last[ok]]
        return col

    def first(self, j: int | None):
        """The first value logged for an entry, or None."""
        if j is None:
            return None
        m = np.flatnonzero(self.vidx == j)
        return float(self.vval[m[0]]) if len(m) else None


def read_log(xml_path: str, bin_path: str, header: LogHeader | None = None) -> Log:
    """One time log file pair. A log that ends inside a record, or names a value its header does not list, is read
    up to there (Log.warnings says so)."""
    h = header or read_log_header(xml_path)
    name = os.path.basename(bin_path)
    try:
        with open(bin_path, "rb") as fh:
            buf = fh.read()
    except OSError as e:
        raise IsoxmlError(f"{name} cannot be read ({type(e).__name__}: {e})") from None
    fixed = h.dtype.itemsize
    offs, cnt, left = _walk(buf, fixed)
    warns = []
    arr = np.frombuffer(buf, np.uint8)
    n = len(offs)
    if n:
        total = int(cnt.sum())
        first = offs + fixed + 1
        within = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        vpos = np.repeat(first, cnt) + 5 * within
        vidx = arr[vpos].astype(np.int64)
        vrec = np.repeat(np.arange(n), cnt)
        bad = np.flatnonzero(vidx >= len(h.values))
        if len(bad):
            # a value the header does not list: the file is not what its header says from that record on
            n = int(vrec[bad[0]])
            warns.append(f"{name}: record {n + 1:,} names a value its header does not list; read up to there "
                         f"({n:,} of {len(offs):,} records)")
            keep = vrec < n
            vpos, vidx, vrec = vpos[keep], vidx[keep], vrec[keep]
            offs, cnt = offs[:n], cnt[:n]
        elif left:
            warns.append(f"{name}: the file ends inside a record ({left} byte(s) left over): the {n:,} complete "
                         "records are read")
        vval = arr[vpos[:, None] + 1 + np.arange(4)[None, :]].copy().view("<i4").ravel().astype(np.int64)
        head = arr[offs[:, None] + np.arange(fixed)[None, :]].copy().view(h.dtype).ravel()
    else:
        head = np.zeros(0, h.dtype)
        vidx = vrec = vval = np.zeros(0, np.int64)
        if len(buf):
            warns.append(f"{name}: no complete record ({len(buf)} byte(s))")
    t = head["day"].astype(np.int64) * 86_400_000 + head["ms"].astype(np.int64)
    return Log(head=head, t=t, values=list(h.values), vrec=vrec, vidx=vidx, vval=vval, status=h.status, warnings=warns)


def _merge_parts(parts: list) -> Log:
    """The files of one log split because a header lists at most 255 values: the values of the later files are added
    to the records of the first with the same time stamp."""
    base = parts[0]
    values = list(base.values)
    vrec, vidx, vval = [base.vrec], [base.vidx], [base.vval]
    warns = list(base.warnings)
    for p in parts[1:]:
        pos = np.searchsorted(base.t, p.t)
        pos_c = np.clip(pos, 0, max(len(base.t) - 1, 0))
        hit = (base.t[pos_c] == p.t) if len(base.t) else np.zeros(len(p.t), bool)
        ok = hit[p.vrec] if len(p.vrec) else np.zeros(0, bool)
        vrec.append(pos_c[p.vrec][ok])
        vidx.append(p.vidx[ok] + len(values))
        vval.append(p.vval[ok])
        if (~ok).any():
            warns.append(f"{int((~ok).sum()):,} value(s) of a further file of the log have no record with their time "
                         "in its first file: not used")
        values += p.values
        warns += p.warnings
    return Log(head=base.head, t=base.t, values=values, vrec=np.concatenate(vrec), vidx=np.concatenate(vidx),
               vval=np.concatenate(vval), status=base.status, warnings=warns)


# ================================================================================================ scan
@dataclasses.dataclass
class LogInfo:
    """One time log of a task (its files put together), described without decoding its values."""
    folder: str
    task: str
    task_name: str
    names: list          # TLG names of its files
    headers: list
    element: str         # the header: the device element with a yield value (the first one)
    yield_elements: list
    machine: str
    field: str
    customer: str
    farm: str
    product: str
    crop: str | None     # key of common.CROPS
    crop_label: str      # what the unit shows as its crop
    crop_code: tuple | None
    n: int
    t0: int
    t1: int
    size: int
    software: str
    grain: bool = True   # some yield value above 0 in the log
    own: tuple = (None, None)    # (lb/bu, reference moisture %) the log carries on its header, None when it logs none


def find_sets(root: str) -> list:
    """Every folder under root that holds a TASKDATA.XML."""
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in _SKIP_DIRS and not d.startswith("."))
        if any(f.upper() == "TASKDATA.XML" for f in fn):
            out.append(dp)
    return sorted(out)


def _crop_of(task: Task) -> tuple:
    """(crop key or None, label, (attribute, code) or None, product name) of a task."""
    prod = next((p for p in task.products if p["name"]), task.products[0] if task.products else None)
    pname = prod["name"] if prod else ""
    for attr, table in MAKER_CROP_CODES.items():
        raw = task.attrs.get(attr)
        if raw is None and prod is not None:
            raw = prod["attrs"].get(attr)
        code = _int(raw)
        if code is not None:
            key = table.get(code)
            return key, key or f"crop type {code}", (attr, code), pname
    for text in (task.crop_type, pname):
        key = C.crop_key_of(text)
        if key:
            return key, key, None, pname
    return None, (task.crop_type or pname or ""), None, pname


def _span(bin_path: str, h: LogHeader) -> dict:
    """What a .BIN holds, from its record lengths and time stamps: {"n" records its header can read, "n_all" complete
    records, "t0", "t1" (first and last time), "size", "grain" (some yield value above 0), "own" ((lb/bu, reference
    moisture %) the largest density and reference moisture logged give, None without), "damaged" (why the file is
    no time log of its header, '' when it is), "cut" (the first record that names a value the header does not
    list, None when every record is read)}."""
    with open(bin_path, "rb") as fh:
        buf = fh.read()
    fixed = h.dtype.itemsize
    offs, cnt, _ = _walk(buf, fixed)
    out = {"n": 0, "n_all": int(len(offs)), "t0": 0, "t1": 0, "size": len(buf), "grain": False, "own": (None, None),
           "damaged": "", "cut": None}
    if not len(offs):
        return out
    arr = np.frombuffer(buf, np.uint8)
    ms = arr[offs[:, None] + np.arange(4)[None, :]].copy().view("<u4").ravel().astype(np.int64)
    day = arr[offs[:, None] + 4 + np.arange(2)[None, :]].copy().view("<u2").ravel().astype(np.int64)
    t = day * 86_400_000 + ms
    no_time = float((ms >= 86_400_000).mean())
    back = float((np.diff(t) < 0).mean()) if len(t) > 1 else 0.0
    if no_time > DAMAGED_TIME or back > DAMAGED_BACK:
        out["damaged"] = (f"{100 * no_time:.0f} % of its records carry no time of day" if no_time > DAMAGED_TIME else
                          f"{100 * back:.0f} % of its records step back in time")
        return out
    n = len(offs)
    total = int(cnt.sum())
    if total:
        within = np.arange(total) - np.repeat(np.cumsum(cnt) - cnt, cnt)
        vpos = np.repeat(offs + fixed + 1, cnt) + 5 * within
        vidx = arr[vpos]
        bad = np.flatnonzero(vidx >= len(h.values))
        if len(bad):                                        # read up to the record before (as read_log does)
            n = int(np.repeat(np.arange(len(offs)), cnt)[bad[0]])
            out["cut"] = n
            keep = vpos < offs[n]
            vpos, vidx = vpos[keep], vidx[keep]

        def largest(ddi_set):
            idx = [j for j, v in enumerate(h.values) if v[0] in ddi_set and v[2] is None]
            m = np.isin(vidx, idx) if idx else np.zeros(len(vidx), bool)
            if not m.any():
                return None
            return int(arr[vpos[m][:, None] + 1 + np.arange(4)[None, :]].copy().view("<i4").max())
        g = largest(set(YIELD_DDIS))
        out["grain"] = bool(g is not None and g > 0)
        dens, refm = largest({DDI["density"]}), largest({DDI["reference_moisture"]})
        out["own"] = (dens / 1000.0 * C.LB_PER_KG * BU_M3 if dens and dens > 0 else None,
                      refm / 1e4 if refm and refm > 0 else None)
    out["n"] = n
    if n:
        out["t0"], out["t1"] = int(t[:n].min()), int(t[:n].max())
    return out


def _scan_set(folder: str) -> tuple:
    """(harvest logs, other logs, warnings) of one TASKDATA folder."""
    ts = read_taskdata(folder)
    warns = [f"{os.path.basename(os.path.dirname(folder)) or folder}: {w}" for w in ts.warnings]
    where = os.path.basename(folder.rstrip("\\/"))
    parent = os.path.basename(os.path.dirname(folder.rstrip("\\/")))
    tag = f"{parent}/{where}" if parent else where
    harvest, other = [], 0
    named = {n.upper() for t in ts.tasks for n in t.logs}
    orphans = sorted(f[:-4] for f in ts.files if f.startswith("TLG") and f.endswith(".BIN") and f[:-4] not in named)
    tasks = list(ts.tasks)
    if orphans:
        tasks.append(Task(id="", name="", attrs={}, customer="", farm="", field="", logs=orphans, products=[],
                          connections=[], totals=[], crop_type=""))
        warns.append(f"{tag}: {len(orphans)} time log(s) that no task of TASKDATA.XML names are read without a task")
    for task in tasks:
        found = []
        for name in task.logs:
            u = name.upper()
            if u + ".XML" not in ts.files or u + ".BIN" not in ts.files:
                miss = [e for e in (".XML", ".BIN") if u + e not in ts.files]
                warns.append(f"{tag}: {name}{' and '.join(miss)} is missing: that time log is not read")
                continue
            try:
                found.append((name, read_log_header(os.path.join(folder, ts.files[u + ".XML"]))))
            except IsoxmlError as e:
                warns.append(f"{tag}: {e}: that time log is not read")
        if not any(v[0] in YIELD_DDIS and v[2] is None for _, h in found for v in h.values):
            other += len(found)                                # no yield value in any header: not a harvest task
            continue                                           # (its logs, which can be very large, are not opened)
        heads = []
        own = (None, None)
        for name, h in found:
            try:
                sp = _span(os.path.join(folder, ts.files[name.upper() + ".BIN"]), h)
            except OSError as e:
                warns.append(f"{tag}: {name}.BIN cannot be read ({type(e).__name__}: {e}): that time log is not read")
                continue
            if sp["damaged"]:
                warns.append(f"{tag}: {name}.BIN is damaged, not the time log its header describes "
                             f"({sp['damaged']}): that time log is not read")
                continue
            if sp["cut"] is not None:
                warns.append(f"{tag}: {name}.BIN: record {sp['cut'] + 1:,} names a value its header does not list"
                             + (f": read up to there ({sp['n']:,} of {sp['n_all']:,} records)" if sp["n"] else
                                ": that time log is not read (the header and the .BIN are not of the same log)"))
            if sp["n"]:
                heads.append((name, h, sp["n"], sp["t0"], sp["t1"], sp["size"], sp["grain"]))
                own = tuple(a if a is not None else b for a, b in zip(own, sp["own"]))
            elif sp["cut"] is None:
                warns.append(f"{tag}: {name}.BIN holds no complete record")
        # the files of one log split over several headers share their time stamps: same start and end
        groups = []
        for x in heads:
            g = next((g for g in groups if abs(g[0][2] - x[2]) <= 2 and abs(g[0][3] - x[3]) <= 2000
                      and abs(g[0][4] - x[4]) <= 2000), None)
            if g is not None:
                g.append(x)
            else:
                groups.append([x])
        groups = [sorted(g, key=lambda x: -x[2]) for g in groups]      # the file with every record first
        crop, crop_label, code, pname = _crop_of(task)
        for g in groups:
            vals = [v for x in g for v in x[1].values]
            yel = []
            for ddi, el, pgn, _ in vals:
                if ddi in YIELD_DDIS and pgn is None and el not in yel:
                    yel.append(el)
            if not yel:
                other += 1
                continue
            el0 = ts.elements.get(yel[0])
            dev = ts.devices.get(el0.device) if el0 else None
            machine = (dev.serial or dev.client[-8:] or dev.name) if dev else ""
            if not machine:
                machine = next((d.serial for d in ts.devices.values() if d.serial), "") or "unknown"
            harvest.append(LogInfo(
                folder=folder, task=task.id, task_name=task.name, names=[x[0] for x in g],
                headers=[x[1] for x in g], element=yel[0], yield_elements=yel, machine=machine,
                field=task.field or task.name, customer=task.customer, farm=task.farm, product=pname, crop=crop,
                crop_label=crop_label, crop_code=code, n=g[0][2], t0=g[0][3], t1=g[0][4],
                size=sum(x[5] for x in g), software=ts.software, grain=any(x[6] for x in g), own=own))
    return harvest, other, warns


_CACHE: dict = {}


def _signature(sets: list) -> tuple:
    sig = []
    for d in sets:
        try:
            for f in sorted(os.listdir(d)):
                u = f.upper()
                if u == "TASKDATA.XML" or u.startswith("TLG") or u.endswith(".XML"):
                    st = os.stat(os.path.join(d, f))
                    sig.append((d, f, st.st_size, st.st_mtime_ns))
        except OSError:
            sig.append((d, "?", 0, 0))
    return tuple(sig)


def scan(root: str) -> dict:
    """What the task data under root holds: {"sets": folders, "logs": [LogInfo of the harvest logs, the same log found
    twice listed once], "other": time logs without a yield value, "warnings", "unreadable": {folder: why}}."""
    root = os.path.abspath(root)
    sets = find_sets(root)
    sig = _signature(sets)
    hit = _CACHE.get(os.path.normcase(root))
    if hit and hit[0] == sig:
        return hit[1]
    logs, other, warns, bad = [], 0, [], {}
    for d in sets:
        try:
            h, o, w = _scan_set(d)
        except IsoxmlError as e:
            bad[d] = str(e)
            continue
        logs += h
        other += o
        warns += w
    seen, uniq, dup = set(), [], 0
    for L in sorted(logs, key=lambda x: (x.machine, x.t0, x.folder)):
        key = (L.machine, L.t0, L.t1, L.n, L.size)
        if key in seen:
            dup += 1
            continue
        seen.add(key)
        uniq.append(L)
    if dup:
        warns.append(f"{dup} time log(s) are in the input twice (the same machine, times, records and size): read once")
    out = {"sets": sets, "logs": uniq, "other": other, "warnings": warns, "unreadable": bad}
    if len(_CACHE) > 8:
        _CACHE.clear()
    _CACHE[os.path.normcase(root)] = (sig, out)
    return out


def _year(ms: int) -> int:
    return int((EPOCH + pd.Timedelta(milliseconds=int(ms))).year)


def _unit_key(L: LogInfo) -> str:
    return "|".join((L.machine, L.field, L.crop_label, str(_year(L.t0))))


def explain(root: str) -> str:
    """Why the ISOXML task data under root gives no harvest data ('' when it does, or when there is none)."""
    S = scan(root)
    if not S["sets"] or S["logs"]:
        return ""
    if S["unreadable"] and len(S["unreadable"]) == len(S["sets"]):
        return "its TASKDATA.XML cannot be read (" + next(iter(S["unreadable"].values())) + ")"
    if S["other"]:
        return (f"none of its {S['other']:,} time log(s) holds a yield value (the mass or volume per time or per "
                "area of ISO 11783-11): the task data of a planter, a sprayer or another operation, not a harvest. "
                "For those, open the as-applied POINT export (a shapefile)")
    miss = [w for w in S["warnings"] if "missing" in w or "no complete record" in w or "not read" in w
            or "is damaged" in w]
    if miss:
        return "its time logs cannot be read (" + "; ".join(miss[:3]) + (" ..." if len(miss) > 3 else "") + ")"
    return ("it holds no time log (TLG files): a setup, guidance or prescription set, with no logged data to read")


def probe(root: str) -> C.Probe | None:
    """Recognise ISOXML harvest task data anywhere under root (the XML headers and the record lengths only)."""
    S = scan(root)
    logs = S["logs"]
    if not logs:
        return None
    machines = sorted({L.machine for L in logs})
    fields = sorted({L.field for L in logs if L.field})
    crops = sorted({L.crop_label for L in logs if L.crop_label})
    clients = sorted({x for L in logs for x in (L.customer, L.farm) if x})
    folders = sorted({L.folder for L in logs})
    summary = (f"{len(logs):,} harvest time log(s) in {len(folders):,} TASKDATA set(s), {len(machines)} machine(s) "
               f"({', '.join(machines)}); field: {', '.join(fields) or '(none)'}; "
               f"crop: {', '.join(crops) or '(none)'}; client/farm: {', '.join(clients) or '(none)'}")
    warns = list(S["warnings"])
    if S["other"]:
        warns.append(f"{S['other']:,} time log(s) without a yield value (another operation) are not read")
    for d, why in S["unreadable"].items():
        warns.append(f"{os.path.basename(os.path.dirname(d)) or d}: {why}: that set is not read")
    try:
        top = os.path.commonpath([os.path.dirname(f.rstrip("\\/")) or f for f in folders])
    except ValueError:
        top = os.path.abspath(root)
    if not os.path.normcase(top).startswith(os.path.normcase(os.path.abspath(root))):
        top = os.path.abspath(root)
    return C.Probe(format_id=FORMAT_ID, label=LABEL, root=top, summary=summary, n_files=len(logs),
                   machines=machines, warnings=warns)


def _stamp(ms: int) -> pd.Timestamp:
    return EPOCH + pd.Timedelta(milliseconds=int(ms))


def guess_crop(tw_lb_bu, ref_moisture_pct) -> str | None:
    """The crop a log's own density (as lb/bu) and reference moisture read like (CROP_GUESS), or None."""
    if not tw_lb_bu or ref_moisture_pct is None:
        return None
    return CROP_GUESS.get((round(float(tw_lb_bu), 1), round(float(ref_moisture_pct), 1)))


def list_units(root: str) -> list:
    """One unit per machine x field x crop x season (every time log of it, all days). The units of the latest season
    that hold grain (a yield value above 0 in some log) are pre-selected."""
    logs = scan(root)["logs"]
    groups = {}
    for L in logs:
        groups.setdefault(_unit_key(L), []).append(L)
    latest = max((_year(L.t0) for L in logs), default=None)
    units = []
    for key, ls in sorted(groups.items()):
        ls = sorted(ls, key=lambda x: x.t0)
        L0 = ls[0]
        t0, t1 = _stamp(min(x.t0 for x in ls)), _stamp(max(x.t1 for x in ls))
        grain = any(x.grain for x in ls)
        prods = sorted({x.product for x in ls if x.product})
        # a crop OCED cannot name: what the density and reference moisture of the logs read like, as a guess
        gs = {guess_crop(*x.own) for x in ls if x.own[0]} if L0.crop is None else set()
        guess = f" ({next(iter(gs))}?)" if len(gs) == 1 and None not in gs else ""
        label = (f"{L0.field or '(no field)'} · {_year(L0.t0)} · {(L0.crop_label or '?') + guess} · {L0.machine} · "
                 f"{t0:%d %b %Y %H:%M} - {t1:%d %b %Y %H:%M} · {len(ls):,} time log(s)"
                 + (f" · product {prods[0]}" if len(prods) == 1 and prods[0] != L0.crop_label else "")
                 + ("" if grain else " · no grain flow in the log(s)"))
        units.append(C.Unit(id=key, label=label, field=L0.field, crop=L0.crop_label, year=_year(L0.t0),
                            start=t0.isoformat(), end=t1.isoformat(), machine=L0.machine,
                            n_records=int(sum(x.n for x in ls)), default=grain and _year(L0.t0) == latest))
    return units


# ================================================================================================ one header's records
@dataclasses.dataclass
class Series:
    """What the rules need of one header in one time log (or in several joined), per record."""
    t: np.ndarray            # ms on the log clock
    lat: np.ndarray
    lon: np.ndarray
    pos_ok: np.ndarray
    alt_m: np.ndarray
    gps_t: np.ndarray        # GNSS UTC ms since 1980 (NaN without)
    grain: np.ndarray        # the channel's raw value (NaN -> 0)
    moist: np.ndarray        # %
    has_moist: bool
    latency: np.ndarray      # ms
    width: np.ndarray        # m, of the sections on (or of the header while working)
    y_centre: np.ndarray     # m, centre of the sections on (NaN when none)
    n_on: np.ndarray
    state: np.ndarray | None   # the section states (records x sections), None without sections
    sec_y: np.ndarray | None
    sec_w: np.ndarray | None
    x_off: float
    y_base: float
    channel: tuple           # (key, per, basis)
    density: np.ndarray      # mg/l (NaN / 0 when not logged)
    ref_moist: np.ndarray    # %
    geometry: tuple          # what must be equal for two logs to be joined
    notes: list
    start_new: np.ndarray | None = None   # joined logs: True on the first record of each log after the first
    layout: str | None = None             # the sections' Y offsets: "logged" | "side by side" | "no offsets"


def _load(L: LogInfo) -> Log:
    parts = []
    files = {f.upper(): f for f in os.listdir(L.folder)}
    for name, h in zip(L.names, L.headers):
        u = name.upper()
        parts.append(read_log(os.path.join(L.folder, files[u + ".XML"]), os.path.join(L.folder, files[u + ".BIN"]), h))
    return parts[0] if len(parts) == 1 else _merge_parts(parts)


def _static(log: Log, dev: Device | None, el: Element | None, key: str):
    """A value logged once (or a device property) of an element: the first logged value, else its DPT, else None."""
    if el is None:
        return None
    v = log.first(log.index(DDI[key], el.id))
    if v is None and dev is not None:
        v = dev.prop(el, DDI[key])
    return None if v is None else float(v)


def side_by_side(sec_y: np.ndarray, sec_w: np.ndarray) -> bool:
    """True when the Y offsets of a header's sections can be the centres of sections lying side by side (module
    docstring, rule 5): in the order of the sections every step is one section (within SECTION_STEP_TOL and the
    millimetre the offsets are logged in), but for the SECTION_END_STEPS outermost steps at either end, which only
    have to be above 0 and no longer than a section."""
    y, w = np.asarray(sec_y, float), np.asarray(sec_w, float)
    if len(y) < 2:
        return True
    step = np.diff(y)
    if np.median(step) < 0:                                 # sections numbered from the right
        step = -step
    one = (w[:-1] + w[1:]) / 2.0
    tol = SECTION_STEP_TOL * one + 0.002
    k = min(SECTION_END_STEPS, len(step))
    ends = np.zeros(len(step), bool)
    ends[:k] = ends[len(step) - k:] = True
    ok = np.where(ends, (step > 0) & (step <= one + tol), np.abs(step - one) <= tol)
    return bool(ok.all())


def _series(L: LogInfo, ts: TaskSet, log: Log, element: str) -> Series | None:
    """The records of one header in one log; None for a header that measured no grain in it (a second header of
    the combine that is not on, defined with or without its geometry)."""
    el = ts.elements.get(element)
    dev = ts.devices.get(el.device) if el else None
    if el is None:
        raise IsoxmlError(f"{'/'.join(L.names)}: the device element {element} its yield value belongs to is not "
                          "described in TASKDATA.XML (the device description, DVC, is missing): the header and its "
                          "sections cannot be read")
    n = len(log)
    notes = []
    h = log.head
    names = h.dtype.names or ()
    lat = h["lat"].astype(float) * 1e-7 if "lat" in names else np.full(n, np.nan)
    lon = h["lon"].astype(float) * 1e-7 if "lon" in names else np.full(n, np.nan)
    raw_ok = np.ones(n, bool)
    if "lat" in names and "lon" in names:
        raw_ok = ~(((h["lat"] == -1) & (h["lon"] == -1)) | (h["lat"] == NA_I32) | (h["lon"] == NA_I32))
    pos_ok = raw_ok & np.isfinite(lat) & np.isfinite(lon) & (np.abs(lat) <= 90) & (np.abs(lon) <= 180) \
        & ~((lat == 0) & (lon == 0))
    if "status" in names:
        pos_ok &= ~np.isin(h["status"], (0, 14, 15))
    if "up" in names:
        up = h["up"].astype(float)
        alt = np.where((h["up"] == NA_I32) | ~raw_ok, np.nan, up / 1000.0)
    else:
        alt = np.full(n, np.nan)
    if "gps_ms" in names and "gps_day" in names:
        gps = np.where((h["gps_ms"] == 0xFFFFFFFF) | (h["gps_day"] == 0xFFFF), np.nan,
                       h["gps_day"].astype(float) * 86_400_000 + h["gps_ms"].astype(float))
    else:
        gps = np.full(n, np.nan)

    def col(key, e=el):
        return log.column(log.index(DDI[key], e.id)) if e is not None else np.full(n, np.nan)
    channel = next((c for c in CHANNELS if log.index(DDI[c[0]], element) is not None), None)
    if channel is None:
        raise IsoxmlError(f"{'/'.join(L.names)}: no yield value on {element}")
    grain = np.nan_to_num(col(channel[0]), nan=0.0)
    if not (grain > 0).any():
        return None
    jm = log.index(DDI["moisture"], element)
    moist = col("moisture") / 1e4
    latency = np.nan_to_num(col("latency"), nan=0.0)
    if log.index(DDI["latency"], element) is None:
        v = _static(log, dev, el, "latency")
        latency = np.full(n, float(v or 0.0))
    # ---- the sections of the header and their states
    secs = dev.children(el, DET_SECTION) if dev is not None else []
    cond = sorted((v[0], j) for j, v in enumerate(log.values) if v[1] == element and v[2] is None
                  and v[0] is not None and CONDENSED_FIRST <= v[0] <= CONDENSED_LAST)
    state = sec_y = sec_w = None
    if secs and cond:
        N = len(secs)
        blocks = []
        for ddi, j in cond:
            first_sec = 16 * (ddi - CONDENSED_FIRST)
            if first_sec >= N:
                continue
            v = np.nan_to_num(log.column(j), nan=0.0).astype(np.int64) & 0xFFFFFFFF
            b = np.stack([((v >> (2 * k)) & 3) == 1 for k in range(16)], axis=1)
            blocks.append((first_sec, b))
        state = np.zeros((n, N), bool)
        for first_sec, b in blocks:
            m = min(16, N - first_sec)
            state[:, first_sec:first_sec + m] = b[:, :m]
        ys, ws = [], []
        for s in secs:
            y = _static(log, dev, s, "offset_y")
            ys.append(np.nan if y is None else y / 1000.0)
            w = next((x for x in (_static(log, dev, s, k) for k in ("actual_width", "max_width", "default_width"))
                      if x), None)
            ws.append(np.nan if w is None else w / 1000.0)
        sec_y, sec_w = np.asarray(ys, float), np.asarray(ws, float)
        if not np.isfinite(sec_w).all():
            wmax = _static(log, dev, el, "max_width") or _static(log, dev, el, "default_width") \
                or _static(log, dev, el, "actual_width")
            if not wmax:
                raise IsoxmlError(f"{'/'.join(L.names)}: the header {element} has sections but no working width "
                                  "(DDI 0046) to give them a width")
            sec_w = np.full(N, wmax / 1000.0 / N)
        layout = "logged"
        if not np.isfinite(sec_y).all() or not side_by_side(sec_y, sec_w):
            layout = "side by side" if np.isfinite(sec_y).all() else "no offsets"
            right_to_left = np.isfinite(sec_y).all() and np.median(np.diff(sec_y)) < 0
            edges = np.r_[0.0, np.cumsum(sec_w)]
            sec_y = (edges[:-1] + edges[1:]) / 2.0 - edges[-1] / 2.0
            if right_to_left:
                sec_y = -sec_y
            notes.append("the sections carry no Y offset: taken side by side, centred on the header"
                         if layout == "no offsets" else
                         "section offsets that do not lie side by side were passed over (the sections taken side "
                         "by side, centred on the header)")
        width = (state * sec_w[None, :]).sum(1)
        n_on = state.sum(1)
        with np.errstate(invalid="ignore", divide="ignore"):
            y_centre = np.where(n_on > 0, (state * (sec_w * sec_y)[None, :]).sum(1) / np.where(width > 0, width, 1.0),
                                np.nan)
        width_how = (f"sections on (condensed work state) x section width ({N} sections of "
                     f"{float(np.median(sec_w)):.3f} m)")
    else:
        wkey = next((k for k in WIDTH_KEYS if log.index(DDI[k], element) is not None), None)
        if wkey is not None:
            w = np.nan_to_num(col(wkey), nan=0.0) / 1000.0
        else:
            v = next((x for x in (_static(log, dev, el, k) for k in WIDTH_KEYS) if x), None)
            if not v:
                raise IsoxmlError(f"{'/'.join(L.names)}: the header {element} has neither section states nor a "
                                  "working width (DDIs 0043, 0042, 0044, 0046)")
            w = np.full(n, v / 1000.0)
        # working or not: the header's own work state, else its device's
        ws_el = el
        jw = log.index(DDI["work_state"], element)
        if jw is None and dev is not None:
            ws_el = next((e for e in dev.elements.values() if e.type == DET_DEVICE
                          and log.index(DDI["work_state"], e.id) is not None), None)
            jw = log.index(DDI["work_state"], ws_el.id) if ws_el is not None else None
        working = (np.nan_to_num(log.column(jw), nan=0.0) == 1) if jw is not None else np.ones(n, bool)
        width = np.where(working, w, 0.0)
        n_on = (width > 0).astype(int)
        y_centre = np.where(width > 0, 0.0, np.nan)
        layout = None
        width_how = (f"DDI {DDI[wkey or 'max_width']:04X} ({DDI_TABLE[DDI[wkey or 'max_width']][1]})"
                     + (" while the work state is 1" if jw is not None else "")
                     + " - the header logs no section states; not compared with SMS")
    notes.append("width: " + width_how)
    # ---- where the header is from the logged position
    x_off, y_base = _offsets(L, ts, log, el, dev)
    key = (element, None if sec_y is None else tuple(np.round(sec_y, 4)),
           None if sec_w is None else tuple(np.round(sec_w, 4)), round(x_off, 4), round(y_base, 4), channel[0])
    return Series(t=log.t, lat=lat, lon=lon, pos_ok=pos_ok, alt_m=alt, gps_t=gps, grain=grain, moist=moist,
                  has_moist=jm is not None, latency=latency, width=width, y_centre=y_centre, n_on=n_on, state=state,
                  sec_y=sec_y, sec_w=sec_w, x_off=x_off, y_base=y_base, channel=channel,
                  density=col("density"), ref_moist=col("reference_moisture") / 1e4, geometry=key, notes=notes,
                  layout=layout)


def _offsets(L: LogInfo, ts: TaskSet, log: Log, el: Element, dev: Device | None) -> tuple:
    """(forward, right) in m of the header's own reference from the logged position: the connector of the vehicle the
    header's device hangs on (the task's CNN), less the vehicle's navigation reference and the header device's own
    connector, plus the header element's own offset. Without a CNN: the connector of another device with the largest
    forward offset logged."""
    def xy(e):
        if e is None:
            return 0.0, 0.0
        d = ts.devices.get(e.device)
        return ((_static(log, d, e, "offset_x") or 0.0) / 1000.0, (_static(log, d, e, "offset_y") or 0.0) / 1000.0)
    task = next((t for t in ts.tasks if t.id == L.task), None)
    veh = imp = None
    for a_dev, a_el, b_dev, b_el in (task.connections if task else []):
        if dev is not None and b_dev == dev.id:
            veh, imp = ts.elements.get(a_el), ts.elements.get(b_el)
        elif dev is not None and a_dev == dev.id:
            veh, imp = ts.elements.get(b_el), ts.elements.get(a_el)
    if veh is None and dev is not None:
        best = 0.0
        for j, v in enumerate(log.values):
            e = ts.elements.get(v[1])
            if v[0] == DDI["offset_x"] and v[2] is None and e is not None and e.type == DET_CONNECTOR \
                    and e.device != dev.id:
                x = log.first(j)
                if x is not None and x / 1000.0 > best:
                    best, veh = x / 1000.0, e
    vx, vy = xy(veh)
    nav = None
    if veh is not None:
        vdev = ts.devices.get(veh.device)
        nav = next((e for e in vdev.elements.values() if e.type == DET_NAVIGATION), None) if vdev else None
    nx, ny = xy(nav)
    ix, iy = xy(imp)
    hx, hy = xy(el)
    return vx - nx - ix + hx, vy - ny - iy + hy


_SETS: dict = {}


def read_taskdata_cached(folder: str) -> TaskSet:
    key = os.path.normcase(os.path.abspath(folder))
    try:
        st = os.stat(os.path.join(folder, next(f for f in os.listdir(folder) if f.upper() == "TASKDATA.XML")))
        sig = (st.st_size, st.st_mtime_ns)
    except (OSError, StopIteration):
        sig = None
    hit = _SETS.get(key)
    if hit and hit[0] == sig:
        return hit[1]
    ts = read_taskdata(folder)
    if len(_SETS) > 512:
        _SETS.clear()
    _SETS[key] = (sig, ts)
    return ts


def _join(parts: list) -> Series:
    """Series of consecutive logs as one (the record a log repeats from the end of the previous one is dropped)."""
    keep = []
    last = None
    for s in parts:
        k = np.ones(len(s.t), bool)
        if last is not None:
            k &= s.t > last
        keep.append(k)
        if k.any():
            last = int(s.t[k][-1])

    def cat(name):
        return np.concatenate([getattr(s, name)[k] for s, k in zip(parts, keep)])
    p0 = parts[0]
    firsts = []
    for i, k in enumerate(keep):
        f = np.zeros(int(k.sum()), bool)
        if i and len(f):
            f[0] = True
        firsts.append(f)
    start_new = np.concatenate(firsts)
    return Series(t=cat("t"), lat=cat("lat"), lon=cat("lon"), pos_ok=cat("pos_ok"), alt_m=cat("alt_m"),
                  gps_t=cat("gps_t"), grain=cat("grain"), moist=cat("moist"),
                  has_moist=any(s.has_moist for s in parts), latency=cat("latency"), width=cat("width"),
                  y_centre=cat("y_centre"), n_on=cat("n_on"),
                  state=None if p0.state is None else np.concatenate([s.state[k] for s, k in zip(parts, keep)]),
                  sec_y=p0.sec_y, sec_w=p0.sec_w, x_off=p0.x_off, y_base=p0.y_base, channel=p0.channel,
                  density=cat("density"), ref_moist=cat("ref_moist"), geometry=p0.geometry, notes=list(p0.notes),
                  start_new=start_new, layout=p0.layout)


REASONS = ["no_position", "time_step", "put_together", "sections_off", "log_end", "no_flow"]


def sms_points(S: Series) -> tuple:
    """The points SMS makes of one header's records (module docstring, rules 1-6): (DataFrame of the points with
    rec, t_ms, lon, lat, lon_gps, lat_gps, alt_m, gps_t, dur_s, dist_m, width_m, y_off_m, track_deg, grain (the
    channel's value at the point), moist; {reason: records that are no point})."""
    n = len(S.t)
    pre = {k: 0 for k in REASONS}
    if not n:
        return pd.DataFrame(), pre
    t = S.t.astype(np.int64)
    # a clock that steps back: the record is left out (SMS's rule for it is not known); a record with the time of
    # the one before it is put together with it by rule 1
    run_max = np.maximum.accumulate(t)
    fwd = np.r_[True, t[1:] >= run_max[:-1]]
    pre["time_step"] = int((~fwd).sum())
    idx = np.flatnonzero(fwd)
    t, lat, lon = t[idx], S.lat[idx], S.lon[idx]
    m = len(idx)
    dt_next = np.r_[np.diff(t), np.inf] / 1000.0
    alive = ~(dt_next < MERGE_S)                                # rule 1: put together with the next record
    ar = np.arange(m)
    last_alive = np.maximum.accumulate(np.where(alive, ar, -1))
    prev = np.r_[-1, last_alive[:-1]]                           # the last record kept before this one
    has_prev = prev >= 0
    pc = np.clip(prev, 0, None)
    dur = np.where(has_prev, (t - t[pc]) / 1000.0, FIRST_DURATION_S)
    x = (lon - lon[pc]) * M_PER_DEG * np.cos(np.radians(lat[pc]))
    y = (lat - lat[pc]) * M_PER_DEG
    dist = np.where(has_prev, np.hypot(x, y), np.nan)
    trk = np.where(has_prev, np.arctan2(x, y) % (2 * np.pi), 0.0)
    step_ok = S.pos_ok[idx] & np.where(has_prev, S.pos_ok[idx][pc], True)
    f = S.grain[idx].astype(float).copy()
    mo = np.nan_to_num(S.moist[idx].astype(float), nan=0.0)
    for i in np.flatnonzero(~alive):                            # rare: the time-weighted mean goes to the next record
        w0, w1 = dur[i], dt_next[i]
        f[i + 1] = (f[i] * w0 + f[i + 1] * w1) / (w0 + w1)
        mo[i + 1] = (mo[i] * w0 + mo[i + 1] * w1) / (w0 + w1)
    k0 = np.flatnonzero(alive)
    if len(k0) > 1:
        dist[k0[0]] = dist[k0[1]]                               # rule 2: the first record takes the second's step
        step_ok[k0[0]] = step_ok[k0[1]]
    ta = t[k0]
    T = t - S.latency[idx]                                      # rule 4: the record nearest to (time - latency)
    p = np.searchsorted(ta, T)
    p0, p1 = np.clip(p - 1, 0, len(ta) - 1), np.clip(p, 0, len(ta) - 1)
    j = np.where(np.abs(ta[p0] - T) <= np.abs(ta[p1] - T), p0, p1)
    in_log = T <= ta[-1]
    fv, mv = f[k0][j], mo[k0][j]
    width = S.width[idx]
    on = width > 0
    # rule 5: the header's reference across the machine = the centre of the sections on in the widest state
    if S.state is not None and on.any():
        st = S.state[idx]
        used = st[int(np.argmax(S.n_on[idx]))]
        y_ref = float((S.sec_w[used] * S.sec_y[used]).sum() / S.sec_w[used].sum())
    else:
        y_ref = 0.0
    y_off = np.where(on, np.nan_to_num(S.y_centre[idx], nan=y_ref) - y_ref, 0.0)
    side = S.y_base + y_ref
    pt = alive & in_log & on & (fv > 0) & step_ok
    pre["put_together"] = int((~alive).sum())
    pre["sections_off"] = int((alive & ~on).sum())
    pre["log_end"] = int((alive & on & ~in_log).sum())
    pre["no_flow"] = int((alive & on & in_log & ~(fv > 0)).sum())
    pre["no_position"] = int((alive & on & in_log & (fv > 0) & ~step_ok).sum())
    lat_p = lat + (S.x_off * np.cos(trk) - side * np.sin(trk)) / M_PER_DEG
    lon_p = lon + (S.x_off * np.sin(trk) + side * np.cos(trk)) / (M_PER_DEG * np.cos(np.radians(lat)))
    out = pd.DataFrame({
        "rec": idx, "t_ms": t, "lon": lon_p, "lat": lat_p, "lon_gps": lon, "lat_gps": lat, "alt_m": S.alt_m[idx],
        "gps_t": S.gps_t[idx], "dur_s": dur, "dist_m": dist, "width_m": width, "y_off_m": y_off,
        "track_deg": np.degrees(trk), "grain": fv, "moist": mv if S.has_moist else np.full(m, np.nan),
        "density": S.density[idx], "ref_moist": S.ref_moist[idx]})[pt].reset_index(drop=True)
    out.attrs.update(y_ref_m=y_ref, x_off_m=float(S.x_off), n_records=int(n))
    return out, pre


def _split_runs(S: Series) -> list:
    """time_logs 'joined': the stretches of a joined Series that are one continuous log (a new stretch where the
    next log does not start within JOIN_GAP_S of the previous one's last record)."""
    if S.start_new is None or not S.start_new.any():
        return [np.arange(len(S.t))]
    gap = np.r_[0.0, np.diff(S.t) / 1000.0]
    cut = np.flatnonzero(S.start_new & (gap > JOIN_GAP_S))
    return np.split(np.arange(len(S.t)), cut)


def _take(S: Series, idx: np.ndarray) -> Series:
    d = {f.name: getattr(S, f.name) for f in dataclasses.fields(Series)}
    for k, v in d.items():
        if isinstance(v, np.ndarray) and k not in ("sec_y", "sec_w") and len(v) == len(S.t):
            d[k] = v[idx]
    d["start_new"] = None
    return Series(**d)


def _wet_kg(P: pd.DataFrame, channel: tuple, tw_lb_bu: float, std_m: float) -> np.ndarray:
    """The wet grain mass (kg) of every point from the channel's value."""
    key, per, basis = channel
    v = P.grain.to_numpy(float)
    amount = v * P.dur_s.to_numpy(float) if per == "time" else v * (P.dist_m * P.width_m).to_numpy(float)
    if basis == "volume":                                  # ml -> kg with the logged density, else the test weight
        rho = np.where(np.nan_to_num(P.density.to_numpy(float)) > 0, P.density.to_numpy(float) / 1000.0,
                       tw_lb_bu / C.LB_PER_KG / BU_M3)   # kg/m3
        return amount / 1e6 * rho
    kg = amount / 1e6
    if basis == "dry":                                     # dry mass at the reference moisture -> wet (one-sided)
        ref = np.where(np.nan_to_num(P.ref_moist.to_numpy(float)) > 0, P.ref_moist.to_numpy(float), std_m)
        mm = np.nan_to_num(P.moist.to_numpy(float), nan=0.0)
        kg = kg * np.where(mm > ref, (100.0 - ref) / np.maximum(100.0 - mm, 1e-9), 1.0)
    return kg


# ================================================================================================ decode
def decode(root: str, unit_ids=None, opts: C.DecodeOptions | None = None, log=print, time_logs: str = "sms"):
    """Decode the selected units (None = all) -> (DataFrame in the standard schema + lon / lat + Y_Offset_f, report).
    opts None = default_options() (one-sided shrink, as SMS's import). time_logs: "sms" (default) = every time log
    on its own, as Ag Leader SMS reads the task data (the file then holds what SMS holds after its import); "joined"
    = the time logs of a unit that follow each other without a gap are read as one log, so the readings at the end of
    a log file are not lost (more readings and grain than SMS holds). The point sits at SMS's reference across the
    header; Y_Offset_f is where the centre of the sections on lies from it (see the module docstring)."""
    t_start = _time.time()
    opts = opts or default_options()
    if time_logs not in TIME_LOGS:
        raise IsoxmlError(f"time_logs must be one of {TIME_LOGS}, not {time_logs!r}")
    S = scan(root)
    groups = {}
    for L in S["logs"]:
        groups.setdefault(_unit_key(L), []).append(L)
    if not groups:
        why = explain(root)
        raise IsoxmlError("no harvest time log in the ISOXML task data under " + str(root)
                          + (f": {why}" if why else ""))
    missing = [u for u in (unit_ids or []) if u not in groups]
    if missing:
        raise IsoxmlError(f"unknown unit id(s): {missing}")
    sel = sorted(groups) if not unit_ids else list(unit_ids)
    report = {k: None for k in C.DECODE_REPORT_KEYS}
    report.update(format=FORMAT_ID, label=LABEL, units=list(sel), warnings=list(S["warnings"]), notes=[])
    warns = report["warnings"]
    pre_total = {k: 0 for k in REASONS}
    frames, details, cal_units, n_read = [], [], [], 0
    offsets, wet_all, long_n = [], 0.0, 0
    resolved = None
    file_vals = {}
    refused, n_logs, n_idle, passed_over = [], 0, 0, 0
    basis_units = set()
    for uid in sel:
        ls = sorted(groups[uid], key=lambda x: (x.t0, x.folder))
        L0 = ls[0]
        log(f"decoding {L0.machine} | {L0.field or '(no field)'} | {L0.crop_label or '?'}: {len(ls):,} time log(s)")
        series = []                                         # (LogInfo, element, Series)
        for L in ls:
            n_logs += 1
            try:
                ts = read_taskdata_cached(L.folder)
                lg = _load(L)
            except IsoxmlError as e:
                warns.append(f"{os.path.basename(os.path.dirname(L.folder))}: {e}: that time log is left out")
                refused.append(str(e))
                continue
            for w in lg.warnings:
                if w not in warns:
                    warns.append(w)
            n_read += len(lg)
            for element in L.yield_elements:
                try:
                    s = _series(L, ts, lg, element)
                except IsoxmlError as e:
                    if str(e) not in warns:
                        warns.append(str(e))
                    refused.append(str(e))
                    continue
                if s is None:                                # a header with no grain in this log: nothing to read
                    n_idle += 1
                    details.append(dict(time_log="/".join(L.names), task=L.task_name, element=element,
                                        n_records=int(len(lg)), n_points=0, pre_step={}, channel=None,
                                        set=os.path.basename(os.path.dirname(L.folder)), y_ref_m=None, x_off_m=None,
                                        sections=None))
                    continue
                passed_over += s.layout == "side by side"
                series.append((L, element, s))
        # the logs to read as one: each on its own (SMS), or the consecutive ones of the same header joined
        jobs = []
        if time_logs == "joined":
            by_geo = {}                                     # the same header and geometry, in the same task
            for L, element, s in series:
                by_geo.setdefault((s.geometry, L.task_name), []).append((L, s))
            for (geo, _), items in by_geo.items():
                J = _join([s for _, s in items])
                for r in _split_runs(J):
                    if len(r):
                        jobs.append((items[0][0], geo[0], _take(J, r)))
        else:
            jobs = series
        # crop, weight to volume, dry moisture: option > SMS's values for the crop > the log's own > the crop table
        dens = np.concatenate([s.density[np.isfinite(s.density) & (s.density > 0)] for _, _, s in series]) \
            if series else np.zeros(0)
        refm = np.concatenate([s.ref_moist[np.isfinite(s.ref_moist) & (s.ref_moist > 0)] for _, _, s in series]) \
            if series else np.zeros(0)
        log_tw = float(np.median(dens)) / 1000.0 * C.LB_PER_KG * BU_M3 if len(dens) else None
        log_sm = float(np.median(refm)) if len(refm) else None
        sms_tw, sms_sm = ISO_SMS_CROPS.get(L0.crop, (None, None))
        override = bool(opts.crop) and L0.crop is not None and opts.crop.strip().lower() != L0.crop
        basis = "sms_crop" if sms_tw else None
        if override:
            warns.append(f"{L0.machine}: the task data's crop {L0.crop!r} replaced by {opts.crop!r}; its own weight "
                         "and reference moisture are not used")
            crop, tw, sm = opts.resolve(None, None, None)
        else:
            try:
                crop, tw, sm = opts.resolve(L0.crop, sms_tw or log_tw, sms_sm if sms_sm is not None else log_sm)
            except ValueError:
                # nothing names a crop and the log carries no density: what SMS gives a dataset without a product
                crop, tw, sm = opts.resolve(None, log_tw or ISO_SMS_NO_PRODUCT[0],
                                            log_sm if log_sm is not None else ISO_SMS_NO_PRODUCT[1])
                basis = "sms_no_product"
                msg = (f"{L0.field or L0.machine}: the task data names no crop OCED knows "
                       f"({L0.crop_label or 'none'}) and logs no density and reference moisture: weight to volume "
                       f"{tw:g} lb/bu and dry moisture {sm:g} % are what SMS gives a dataset without a product; "
                       "choose the product in the import settings")
                if msg not in warns:
                    warns.append(msg)
        guess = None
        if L0.crop is None and not opts.crop and basis is None and opts.test_weight_lb_bu is None \
                and opts.std_moisture_pct is None:
            # a crop OCED cannot name: the log's own values are used; what they read like is said, as a guess
            guess = guess_crop(log_tw, log_sm)
            code = f"{L0.crop_code[0]} {L0.crop_code[1]}" if L0.crop_code else (L0.crop_label or "no crop")
            msg = (f"{L0.field or L0.machine}: the crop of the task data ({code}) is not one OCED knows: weight to "
                   f"volume {tw:.2f} lb/bu and dry moisture {sm:g} % are the density and the reference moisture "
                   "the log carries" + (f" (they read like {guess})" if guess else "")
                   + "; what SMS gives such a task is not known: check the product in the import settings")
            if msg not in warns:
                warns.append(msg)
        basis_units.add(basis)
        if sms_sm is not None and log_sm is not None and abs(sms_sm - log_sm) > 0.05 and not override \
                and opts.std_moisture_pct is None:
            msg = (f"dry moisture {sm:g} % (what SMS gives this crop) differs from the reference moisture the task "
                   f"data logs ({log_sm:g} %): the import uses {sm:g} %; to use the log's value, set the dry moisture "
                   "in the import settings")
            if msg not in warns:
                warns.append(msg)
        # a difference that shows in the two decimals printed (a density that converts to SMS's round weight but
        # for its fourth decimal is none)
        if resolved and (abs(resolved[1] - tw) >= 0.005 or abs(resolved[2] - sm) >= 0.005):
            warns.append(f"{L0.machine}: weight to volume / dry moisture differ between the units decoded "
                         f"({resolved[1]:.2f} / {resolved[2]:.2f} vs {tw:.2f} / {sm:.2f})")
        resolved = (crop, tw, sm)
        file_vals = {"crop_code": list(L0.crop_code) if L0.crop_code else None, "product": L0.product,
                     "test_weight_lb_bu": None if log_tw is None else round(log_tw, 4), "std_moisture_pct": log_sm,
                     "guess": guess}
        unit_n = 0
        for L, element, s in jobs:
            P, pre = sms_points(s)
            for k, v in pre.items():
                pre_total[k] += v
            dtr = np.r_[FIRST_DURATION_S, np.diff(s.t) / 1000.0]
            if s.channel[1] == "time" and s.channel[2] == "wet":
                wet_all += float(np.nansum(s.grain * np.clip(dtr, 0, LONG_DURATION_S)) / 1e6 * C.LB_PER_KG)
            det = dict(time_log="/".join(L.names), task=L.task_name, element=element, n_records=int(len(s.t)),
                       n_points=int(len(P)), pre_step=pre, set=os.path.basename(os.path.dirname(L.folder)),
                       channel=s.channel[0], y_ref_m=None, x_off_m=round(float(s.x_off), 4), sections=s.layout)
            if not len(P):
                details.append(det)
                continue
            det["y_ref_m"] = round(float(P.attrs["y_ref_m"]), 4)
            wet_lb = _wet_kg(P, s.channel, tw, sm) * C.LB_PER_KG
            area_ac = (P.dist_m * P.width_m).to_numpy(float) / 4046.8564224
            moist = P.moist.to_numpy(float)
            yc = C.yield_columns(wet_lb, area_ac, np.where(np.isfinite(moist), moist, sm), tw, sm, opts.shrink)
            off = None
            g = P.gps_t.to_numpy(float)
            if np.isfinite(g).any():
                off = float(np.round(np.nanmedian(P.t_ms.to_numpy(float) - g) / 3_600_000.0 * 4) / 4)
                offsets.append(off)
            tt = EPOCH + pd.to_timedelta(P.t_ms.to_numpy(np.int64), unit="ms")
            if opts.tz_offset_h is not None and off is not None:
                tt = tt + pd.to_timedelta(opts.tz_offset_h - off, unit="h")
            start = _stamp(int(s.t[0]))
            long_n += int((P.dur_s > LONG_DURATION_S).sum())
            frames.append(pd.DataFrame({
                "Field": L.field, "Dataset": C.dataset_name(f"{L.field} {L.machine[-6:]}".strip(), start),
                "Product": crop if override else (L.product or L.crop_label), "Obj__Id": np.arange(1, len(P) + 1),
                "Time": pd.Series(tt).dt.strftime("%Y-%m-%d %H:%M:%S.%f").str.slice(0, 23).to_numpy(),
                "Duration_s": P.dur_s.to_numpy(float), "Elevation_": P.alt_m.to_numpy(float) * C.FT_PER_M,
                "Speed_mph_": (P.dist_m / P.dur_s).to_numpy(float) * C.MPH_PER_MS,
                "Distance_f": P.dist_m.to_numpy(float) * C.FT_PER_M, "Track_deg_": P.track_deg.to_numpy(float),
                "Swth_Wdth_": P.width_m.to_numpy(float) * C.FT_PER_M, "Moisture__": moist, **yc,
                "Machine": L.machine, "Src_Fmt": FORMAT_ID, "lon": P.lon.to_numpy(float),
                "lat": P.lat.to_numpy(float), "Y_Offset_f": P.y_off_m.to_numpy(float) * C.FT_PER_M}))
            unit_n += len(P)
            details.append(det)
            for note in s.notes:
                if note not in report["notes"]:
                    report["notes"].append(note)
            if not s.has_moist and "no moisture" not in " ".join(warns):
                warns.append(f"{L.machine}: the header logs no moisture (DDI 0063): the dry yield equals the wet "
                             "yield and the Moisture column is empty")
            if s.channel != CHANNELS[0]:
                msg = (f"{L.machine}: no wet mass flow (DDI 0057) in the log; the grain comes from DDI "
                       f"{DDI[s.channel[0]]:04X} ({DDI_TABLE[DDI[s.channel[0]]][1]}), a route not compared with SMS")
                if msg not in warns:
                    warns.append(msg)
        cal_units.append({"id": uid, "crop_used": crop, "crop_label": L0.crop_label, "test_weight_lb_bu": tw,
                          "std_moisture_pct": sm, "n_time_logs": len(ls), "n_readings": int(unit_n), "basis": basis,
                          "log_test_weight_lb_bu": None if log_tw is None else round(log_tw, 4),
                          "log_reference_moisture_pct": log_sm})
    cols = C.STANDARD_NAMES + ["lon", "lat", "Y_Offset_f"]
    df = pd.concat(frames, ignore_index=True)[cols] if frames else pd.DataFrame(columns=cols)
    rows, tot = C.summarize(df) if len(df) else ([], {})
    offs = sorted(set(offsets))
    if opts.tz_offset_h is not None:
        basis = "local" if offs else "unknown"
        if not offs:
            warns.append("no GNSS time in the logs to measure the clock's offset: Time left on the log's own clock "
                         "(tz_offset_h ignored)")
    else:
        basis = "unknown" if not offs else ("UTC" if all(abs(o) < 0.01 for o in offs) else "local")
    if passed_over:
        warns.append(f"{passed_over:,} of {len([d for d in details if d['sections']]):,} time log(s): the section "
                     "positions the display logged do not lie side by side (they are not the centres of the "
                     "sections: used as such they would put the points beside the strip the header cut); the "
                     "sections were taken side by side, centred on the header. What SMS makes of such a log was not "
                     "compared")
    beside = sorted({d["y_ref_m"] for d in details if d.get("sections") == "logged" and d.get("y_ref_m")})
    if beside:
        report["notes"].append(
            "the header's reference lies " + " / ".join(f"{abs(v):.3f} m to the {'right' if v > 0 else 'left'}"
                                                        for v in beside)
            + " of the middle of the header (the centre of the sections on in the widest state of a log, from the "
            "positions the display logs for its sections): the points sit there, as SMS places them; the cleaning "
            "measures and reports such a lateral offset (S2, swath_bias)")
    if pre_total["no_position"]:
        warns.append(f"{pre_total['no_position']:,} record(s) with the header on and grain have no position (no "
                     "GNSS fix on them or on the record before): they give no reading")
    if pre_total["time_step"]:
        warns.append(f"{pre_total['time_step']:,} record(s) come after the clock of the log steps back: they are "
                     "left out (what SMS makes of them is not known)")
    if long_n:
        warns.append(f"{long_n:,} reading(s) last longer than {LONG_DURATION_S:g} s (the first record after a stop "
                     "of the logging takes the whole stop as its duration, as in SMS): their yield is far too high "
                     "and the import's maximum or the cleaning removes them")
    crop, tw, sm = resolved if resolved else (None, None, None)
    names = sorted({u["crop_label"] for u in cal_units if u.get("crop_label")})
    keys = sorted({u["crop_used"] for u in cal_units if u.get("crop_used")})
    el0 = None
    L_any = next((L for u in sel for L in groups[u]), None)
    if L_any is not None:
        try:
            ts0 = read_taskdata_cached(L_any.folder)
            el0 = ts0.elements.get(L_any.element)
            dev0 = ts0.devices.get(el0.device) if el0 else None
        except IsoxmlError:
            dev0 = None
    shown = {}
    if el0 is not None and dev0 is not None:
        for k in ("mass_per_time", "moisture", "latency", "max_width", "density", "reference_moisture"):
            txt = dev0.present(el0, DDI[k])
            if txt:
                shown[f"{DDI[k]:04X}"] = txt
    report.update(n_records_read=int(n_read), n_readings=int(len(df)), pre_step=pre_total, machines=rows, totals=tot,
                  time_basis=basis)
    report["time_logs"] = time_logs
    report["calibration"] = {
        "channel": "DDI 0057 mass per time yield (mg/s, the monitor's calibrated wet grain flow) at the record "
                   "nearest to the reading's time less its latency (DDI 008F) x the reading's duration = wet mass",
        "area": "width of the sections on x the step from the previous record on SMS's flat earth (as SMS)",
        "crop": crop, "test_weight_lb_bu": tw, "std_moisture_pct": sm, "shrink": opts.shrink, "units": cal_units,
        "display": shown,
        "wet_lb_all_records": round(wet_all, 1),
        "validation": VALIDATION_NOTE}
    if offs:
        report["calibration"]["time_note"] = (
            "Time = the log's own clock; log clock - GNSS UTC = " + ", ".join(f"{o:+.2f}" for o in offs) + " h"
            + (f"; shifted to UTC{opts.tz_offset_h:+g} h on request" if opts.tz_offset_h is not None else ""))
    report["details"] = details
    one = len(keys) == 1 and len(names) == 1
    key = keys[0] if one else None
    sms_v = ISO_SMS_CROPS.get(key)
    mp = {"crop": key, "test_weight_lb_bu": sms_v[0] if sms_v else None,
          "dry_moisture_pct": sms_v[1] if sms_v else None, "min_yield": ISO_MIN_YIELD, "max_yield": ISO_MAX_YIELD,
          "basis": "sms_crop" if sms_v else None, "file": dict(file_vals), "guess": file_vals.get("guess")}
    if sms_v:
        mp["source"] = ("ISOXML task data: the crop of the task (" + (
            f"{file_vals['crop_code'][0]} {file_vals['crop_code'][1]}" if file_vals.get("crop_code") else
            "its crop type / product") + "); weight to volume and dry moisture = what SMS gives this crop "
            "(isoxml.ISO_SMS_CROPS), not the density and reference moisture the log carries")
    elif key:
        mp["source"] = ("ISOXML task data: the crop of the task; no SMS value established for this crop in ISOXML "
                        "task data: the product table applies")
    elif basis_units == {"sms_no_product"}:
        mp.update(test_weight_lb_bu=ISO_SMS_NO_PRODUCT[0], dry_moisture_pct=ISO_SMS_NO_PRODUCT[1],
                  basis="sms_no_product")
        mp["source"] = ("ISOXML task data: no crop and no density or reference moisture in the log; weight to volume "
                        "and dry moisture = what SMS gives a dataset without a product (isoxml.ISO_SMS_NO_PRODUCT)")
    else:
        # a crop OCED cannot name: the log's own density and reference moisture stand in for a product
        own = {(u["log_test_weight_lb_bu"], u["log_reference_moisture_pct"]) for u in cal_units}
        if len(own) == 1:
            mp.update(test_weight_lb_bu=next(iter(own))[0], dry_moisture_pct=next(iter(own))[1])
        mp["source"] = ("ISOXML task data: no crop OCED knows (" + (", ".join(names) or "none") + "); the density "
                        "(DDI 0079) and the reference moisture (DDI 00B8) the log carries"
                        + (f", which read like {mp['guess']}" if mp.get("guess") else "")
                        + "; what SMS gives such a task is not known")
    report["monitor_product"] = mp
    report["crop_name"] = key if one else (names[0] if len(names) == 1 else None)
    report["decode_seconds"] = round(_time.time() - t_start, 1)
    if not len(df):
        why = [f"{v:,} record(s) {REASON_TEXT[k]}" for k, v in pre_total.items() if v]
        if not why and n_idle and not refused:
            why = [f"none of the {n_logs:,} time log(s) read holds grain (no grain flow above 0 on a header: the "
                   "combine was driven or set up without cutting)"]
        why += list(dict.fromkeys(refused))[:3]
        raise IsoxmlError("the selected units hold no harvest reading (sections on with grain flow): "
                          + ("; ".join(why) or f"nothing could be read of the {n_logs:,} time log(s)"))
    probs = C.check_standard(df)
    if probs:
        raise IsoxmlError("decoded table fails the standard schema: " + "; ".join(probs))
    log(f"decoded {len(df):,} readings from {n_read:,} records in {report['decode_seconds']} s")
    return df, report
