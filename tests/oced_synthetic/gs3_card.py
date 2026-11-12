# -*- coding: utf-8 -*-
"""engine/formats/jd_gs3_rcd.py on synthetic logs (every number made up; no grower data): the alignment of the grain
and moisture records to the readings as Ag Leader SMS does it (sms_align), the decode of a small synthetic GreenStar 3
card written here, and the settings engine/sms_import.py takes from the card.

    python -m pytest -q tests
"""
from __future__ import annotations

import os
import struct
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from oced_clean._engine import sms_import as si  # noqa: E402
from oced_clean._engine.formats import common as C  # noqa: E402
from oced_clean._engine.formats import jd_gs3_rcd as G  # noqa: E402

KEEP = G.PENDING_KEEP


def _log(n, mass=5.0):
    """n records one second apart: times (ms), mass (kg), moisture (% = 10 + record number / 100), all harvesting."""
    return np.arange(n) * 1000, np.full(n, mass), 10.0 + np.arange(n) / 100.0, np.ones(n, bool)


def test_align_unbroken_log_takes_14_s_and_18_s_and_loses_no_grain():
    t, x, m, h = _log(80)
    x = 5.0 + np.arange(80) / 10.0                         # a different mass in every record
    a = G.sms_align(t, x, m, h, pending="drop")
    # every position takes the record 14 s later and the moisture logged 4 s after that record
    assert np.allclose(a["wet_kg"][:66], x[14:80]) and np.allclose(a["moisture_pct"][:62], m[18:80])
    # the last 14 positions have no record (0 kg, 0 %); the four before them a record without its moisture (0 %)
    assert np.all(a["wet_kg"][66:] == 0) and np.all(a["moisture_pct"][62:] == 0)
    assert (a["n_no_record"], a["n_no_moisture"], a["n_too_early"]) == (14, 4, 14)
    assert a["kg_too_early"] == pytest.approx(x[:14].sum()) and a["kg_carried"] == 0
    # with the grain of the first 14 records carried on, nothing is lost
    b = G.sms_align(t, x, m, h)
    assert np.nansum(b["wet_kg"]) == pytest.approx(x.sum()) == pytest.approx(b["kg_logged"])
    assert np.allclose(b["wet_kg"][:1], x[14:15])           # the first reading is before any of it enters
    assert np.all(b["wet_kg"][1:66] > x[15:80]) and np.allclose(b["moisture_pct"], a["moisture_pct"])


def test_align_grain_of_a_position_that_is_not_harvesting_goes_15_readings_back():
    t, x, m, h = _log(120)
    x[:14] = 0.0                                           # no grain before the first reading can take it
    h[60] = False                                          # the header is up for one record
    a = G.sms_align(t, x, m, h)
    assert np.isnan(a["wet_kg"][60]) and np.isnan(a["moisture_pct"][60])
    assert (a["n_not_harvesting"], a["kg_not_harvesting"], a["n_too_early"]) == (1, 5.0, 0)
    extra = a["wet_kg"] - 5.0
    assert np.allclose(extra[:45], 0)                       # it enters at the reading logged 15 s before ...
    k = np.arange(15)
    assert np.allclose(extra[45:60], 5.0 * (1 - KEEP) * KEEP ** k)       # ... and falls by 0.68645 per reading,
    assert extra[61] == pytest.approx(5.0 * (1 - KEEP) * KEEP ** 15)     # the record of the header up not counted
    assert np.nansum(a["wet_kg"]) == pytest.approx(x.sum())
    # "drop": the reading keeps what its own record holds
    d = G.sms_align(t, x, m, h, pending="drop")
    assert np.allclose(d["wet_kg"][:60], 5.0) and np.nansum(d["wet_kg"]) == pytest.approx(x.sum() - 5.0)
    with pytest.raises(ValueError):
        G.sms_align(t, x, m, h, pending="keep")


@pytest.mark.parametrize("dt13, dt17, taken", [(13001, 17001, 13), (13001, 17000, 14), (13000, 17001, 14),
                                               (12999, 16999, 14)])
def test_align_first_reading_after_a_pause(dt13, dt17, taken):
    """After a pause the first position takes the record 13 s later only when it was logged MORE than 13.000 s after
    it and its moisture more than 17.000 s after it; otherwise the next one (the rule SMS follows after a pause)."""
    t0 = np.arange(40) * 1000
    t1 = 100_000 + np.arange(60) * 1000                    # the log pauses for 61 s
    t1[13], t1[17] = 100_000 + dt13, 100_000 + dt17
    t = np.r_[t0, t1]
    x = 3.0 + np.arange(100) / 100.0
    m = 10.0 + np.arange(100) / 100.0
    a = G.sms_align(t, x, m, np.ones(100, bool), pending="drop")
    assert a["wet_kg"][40] == pytest.approx(x[40 + taken]) and a["moisture_pct"][40] == pytest.approx(m[40 + taken + 4])
    assert a["wet_kg"][41] == pytest.approx(x[41 + 14])     # one to one: the next position takes the next record
    # no record is matched across the pause: the last 14 positions before it have none
    assert np.all(a["wet_kg"][26:40] == 0) and a["wet_kg"][25] == pytest.approx(x[39])


def test_align_moisture_is_zero_when_its_record_fell_into_a_pause():
    t = np.r_[np.arange(40) * 1000, 43_000 + np.arange(40) * 1000]      # a pause of 4 s after record 39
    x = np.full(80, 4.0)
    m = 10.0 + np.arange(80) / 100.0
    a = G.sms_align(t, x, m, np.ones(80, bool), pending="drop")
    # position 21 takes record 35; its moisture record 39 is 4 s later: kept. Position 22 takes record 36, whose
    # moisture would be logged at 40 s: the first record after it is 7 s later (5.5 s or more): 0 %, as SMS has it
    assert a["wet_kg"][21] == 4.0 and a["moisture_pct"][21] == pytest.approx(m[39])
    assert a["wet_kg"][22] == 4.0 and a["moisture_pct"][22] == 0.0
    # a pause of 1 s more than the interval is still inside the 5.5 s: the record after it gives the moisture
    t2 = np.r_[np.arange(40) * 1000, 40_000 + np.arange(40) * 1000]
    t2[40:] += 1000                                        # records 40.. are 2 s after record 39
    b = G.sms_align(t2, x, m, np.ones(80, bool), pending="drop")
    assert b["moisture_pct"][22] == pytest.approx(m[40])    # record 36 + 5 s


# ------------------------------------------------------------------------------------------------- a synthetic card
def _column(name, typ, enum=None, res=1.0, init=0.0):
    b = struct.pack("<HHH", typ, 0, len(name)) + name.encode("latin1") + b"\x00"
    if typ == 1:
        names = b"".join(struct.pack("<H", len(e)) + e.encode("latin1") for e in enum)
        return b + struct.pack("<HH", len(enum), len(names)) + names
    return b + struct.pack("<H", 24) + struct.pack("<ddd", res, 0.0, init)


def make_card(folder: str, n: int = 110) -> dict:
    """A GreenStar 3 card with one log block of n one-second records, written in the layout the decoder reads: a
    6 m header in two sections heading east at 2 m/s, 5 kg of grain in every record, the moisture rising by 0.01
    point per record, the header up for records 60-64 and the right-hand section off for records 80-84."""
    doc = os.path.join(folder, "RCD", "EIC", "Documentation", "AA")
    os.makedirs(doc, exist_ok=True)
    name = "6a570000-0000-1000-aaaa-000000000001"
    on_off = lambda kind: [f"dti{kind}StatusOn", f"dti{kind}StatusOff"]  # noqa: E731
    lat0, lon0 = 51.8, -114.1
    cols = [("Moist", 3, None, 0.01, 14.0), ("Mass", 3, None, 0.01, 0.0), ("Header", 1, on_off("Header"), 0, 0),
            ("Sec1", 1, on_off("Recording"), 0, 0), ("Sec2", 1, on_off("Recording"), 0, 0),
            ("Lat", 4, None, 1e-9, lat0), ("Lon", 4, None, 1e-9, lon0), ("Head", 3, None, 0.01, 0.0),
            ("DD", 3, None, 1.0, 0.0), ("Elev", 3, None, 0.001, 0.0), ("Time", 4, None, 1.0, 0.0)]
    blk = struct.pack("<HH", 1, 8) + b"synthetc" + struct.pack("<HHH", 1, 2, len(cols))
    blk += b"".join(_column(c[0], c[1], c[2], c[3], c[4]) for c in cols)
    nb = (len(cols) + 7) // 8
    dlon = int(round(2.0 / (111_320.0 * np.cos(np.radians(lat0))) * 1e9))     # 2 m east, in 1e-9 degrees
    for i in range(n):
        header = 1 if 60 <= i <= 64 else 0
        sec2 = 1 if 80 <= i <= 84 or header else 0
        dense = struct.pack("<hhhhhiihhhi", 1 if i else 0, 500 if i == 0 else 0, header, header, sec2,
                            0, dlon if i else 0, 9000 if i == 0 else 0, 200 if i == 0 else 0, 0, 1000 if i else 0)
        blk += struct.pack("<HB", 2, 0) + bytes(3 * nb) + dense
    blk += struct.pack("<H", 4)
    with open(os.path.join(doc, name + ".fdd"), "wb") as fh:
        fh.write(blk)
    section = ('<Section id="{i}"><vrLateralOffset value="{lat}" sourceUOM="mm"/><RecordingStateElement>'
               '<DefinedTypeColumn columnID="Sec{k}"/></RecordingStateElement>'
               '<vrSectionWidth value="3000" sourceUOM="mm"/></Section>')
    fdl = f"""<?xml version="1.0"?>
<RCDLogFile><RCDLogBlock><ConfigBlock>
<Setup>
 <Participant><Client name="Synthetic client"/></Participant><Farm name="Synthetic farm"/>
 <Field name="Synthetic field" erid="{{00000000-0000-0000-0000-00000000f1e1}}"/>
 <Products><Crop name="Wheat" id="1"><CropWeight value="27.22" sourceUOM="kg1bu-1"/>
  <StandardPayableMoisture value="13.0" sourceUOM="prcnt"/>
  <CropVariety erid="{{00000000-0000-0000-0000-0000000000aa}}" name="Synthetic variety"/></Crop></Products>
 <Equipment><Machine name="Synthetic combine" serialNumber="SYNTH0001"/><Implement serialNumber=""/></Equipment>
 <MachineModel name="S000"/><ImplementModel name="600D"/>
</Setup>
<EquipmentConfiguration>
 <Meter id="1"><MeasuredElement calibration="500"><VariableNumberColumn columnID="Moist" sourceUOM="prcnt"
   variableRepresentation="vrHarvestMoisture"/><vrLatency value="3"/></MeasuredElement></Meter>
 <Meter id="2"><MeasuredElement calibration="500"><VariableNumberColumn columnID="Mass" sourceUOM="kg"
   variableRepresentation="vrYieldWetMass"/><vrLatency value="3"/></MeasuredElement></Meter>
 <Meter id="3"><MeasuredElement><DefinedTypeColumn columnID="Header" definedTypeRepresentation="dtHeaderStatus"/>
  </MeasuredElement></Meter>
 {section.format(i=10, lat=-1500, k=1)}{section.format(i=11, lat=1500, k=2)}
 <NavigationReference><vrInlineOffset value="0" sourceUOM="in"/><vrLateralOffset value="0" sourceUOM="in"/>
  <vrLatitude columnID="Lat"/><vrLongitude columnID="Lon"/><vrHeading columnID="Head"/>
  <vrDeltaDistance columnID="DD" sourceUOM="cm"/><vrElevation columnID="Elev"/>
  <dtNavigationType value="dtiComputedImplement"/></NavigationReference>
</EquipmentConfiguration>
<LogTaskRegion timeStamp="2026-07-15T18:00:00.400Z" timeColumnID="Time" logInterval="1000" cropYear="2026">
 <Operation><dtOperationClass value="dtiHarvest"/><ProductRef eridRef="{{00000000-0000-0000-0000-0000000000aa}}"/>
  <MeterRef idRef="1"/><MeterRef idRef="2"/><MeterRef idRef="3"/>
  <LoadIdentityRef idRef="{{00000000-0000-0000-0000-00000000010a}}"/></Operation>
</LogTaskRegion></ConfigBlock>
<LogDataBlock><EvenByteDelta filePathName="Documentation/AA/{name}.fdd" filePosition="0"/></LogDataBlock>
</RCDLogBlock></RCDLogFile>
"""
    with open(os.path.join(doc, name + ".fdl"), "w", encoding="latin1") as fh:
        fh.write(fdl)
    return {"root": folder, "n": n, "lat0": lat0, "lon0": lon0}


@pytest.fixture(scope="module")
def card(tmp_path_factory):
    return make_card(str(tmp_path_factory.mktemp("card")))


def test_decode_synthetic_card_as_sms(card):
    assert G.probe(card["root"]) is not None
    df, rep = G.decode(card["root"], None, log=lambda m: None)
    n = card["n"]
    # every record with the header down and a section recording is a reading, one second each
    assert len(df) == n - 5 and rep["pre_step"] == {"header_up": 5, "sections_off": 0, "no_gnss": 0}
    assert np.all(df.Duration_s == 1.0) and np.allclose(df.Distance_f, 2.0 * C.FT_PER_M)
    assert np.allclose(df.Speed_mph_, 2.0 * C.MPH_PER_MS)
    area = df.Distance_f * df.Swth_Wdth_ / C.SQFT_PER_AC
    # no grain is lost: the readings hold the 5 kg of every one of the n records
    assert float((df.Yld_Mass_W * area).sum()) == pytest.approx(n * 5.0 * C.LB_PER_KG, rel=1e-9)
    A = rep["alignment"]
    assert (A["method"], A["n_too_early"], A["n_not_harvesting"], A["n_no_record"]) == ("sms", 14, 5, 14)
    assert A["kg_logged"] == pytest.approx(n * 5.0) and A["pct_of_logged_mass_not_on_a_reading"] == pytest.approx(0, abs=1e-6)
    # the first reading: the grain logged 14 s and the moisture logged 18 s after it
    assert float(df.Yld_Mass_W[0] * area[0]) == pytest.approx(5.0 * C.LB_PER_KG) and df.Moisture__[0] == pytest.approx(14.18)
    # half the header recording (the left section): 10 ft wide, the reading 1.5 m to the left of the header centre
    half = df[np.isclose(df.Swth_Wdth_, 3.0 * C.FT_PER_M)]
    full = df[np.isclose(df.Swth_Wdth_, 6.0 * C.FT_PER_M)]
    assert len(half) == 5 and len(full) == n - 10
    assert np.allclose((half.lat - card["lat0"]) * 111_195.0, 1.5, atol=0.01) and np.allclose(full.lat, card["lat0"])
    # the card's own product: 27.22 kg/bu = 60.01 lb/bu, 13.0 % dry, maximum 10,000 bu/ac (as SMS imports these cards)
    mp = rep["monitor_product"]
    assert mp["crop"] == "wheat" and mp["test_weight_lb_bu"] == pytest.approx(60.0098, abs=1e-4)
    assert mp["dry_moisture_pct"] == 13.0 and mp["max_yield"] == G.MONITOR_MAX_YIELD == 10_000.0
    assert np.allclose(df.Yld_Mass_W / df.Yld_Vol_We, 27.22 * C.LB_PER_KG)
    assert not C.check_standard(df)


def test_decode_synthetic_card_fixed_shift_is_still_there(card):
    """alignment="shift": the earlier behaviour - a reading needs the records 14 s and 18 s later, and the grain of
    the records no reading takes is lost."""
    df, rep = G.decode(card["root"], None, log=lambda m: None, alignment="shift")
    n = card["n"]
    assert len(df) == n - 18 - 5 and rep["alignment"] == {"method": "shift"}
    assert rep["pre_step"]["no_mass"] == 14 and rep["pre_step"]["no_moisture"] == 4
    area = df.Distance_f * df.Swth_Wdth_ / C.SQFT_PER_AC
    assert float((df.Yld_Mass_W * area).sum()) == pytest.approx(len(df) * 5.0 * C.LB_PER_KG, rel=1e-9)
    assert np.allclose(df.lat, card["lat0"])                # the header centre, also with half of it recording
    with pytest.raises(ValueError):
        G.decode(card["root"], None, log=lambda m: None, alignment="nearest")


def test_sms_import_takes_the_card_product():
    """engine.sms_import.resolve: the monitor's weight, dry moisture and 'no maximum' stand in for the product table
    when no product is chosen or the product is the monitor's crop; what the user sets always wins."""
    mon = {"crop": "wheat", "test_weight_lb_bu": 60.01, "dry_moisture_pct": 13.0, "max_yield": None}
    R = si.resolve(si.settings(), "Wheat (Hard Red Spring)", mon)
    assert (R["product"], R["test_weight_lb_bu"], R["dry_moisture_pct"], R["max_yield"]) == ("wheat", 60.01, 13.0, None)
    assert R["manual_moisture_pct"] == 13.0 and "no maximum" in si.describe(R)
    R = si.resolve(si.settings({"product": "wheat"}), None, mon)
    assert (R["test_weight_lb_bu"], R["dry_moisture_pct"], R["max_yield"]) == (60.01, 13.0, None)
    R = si.resolve(si.settings({"product": "barley"}), "wheat", mon)          # another product: its own table
    assert (R["test_weight_lb_bu"], R["dry_moisture_pct"], R["max_yield"]) == (48.0, 14.5, 200.0)
    R = si.resolve(si.settings({"dry_moisture_pct": 14.5, "max_yield": 250.0}), "wheat", mon)
    assert (R["test_weight_lb_bu"], R["dry_moisture_pct"], R["max_yield"]) == (60.01, 14.5, 250.0)
    R = si.resolve(si.settings(), "wheat", {"crop": "wheat", "test_weight_lb_bu": None, "dry_moisture_pct": None})
    assert (R["test_weight_lb_bu"], R["dry_moisture_pct"], R["max_yield"]) == (60.0, 13.5, 300.0)
    assert si.resolve(si.settings(), "wheat") == si.resolve(si.settings(), "wheat", None)
    # the product a decoded card reports: SMS's 10,000 bu/ac maximum, said as a range
    R = si.resolve(si.settings(), "wheat", dict(mon, max_yield=G.MONITOR_MAX_YIELD))
    assert R["max_yield"] == 10_000.0 and "5.000-10,000.00 bu/ac" in si.describe(R)


def _blk(crop="Barley", tw=47.50, sm=14.2):
    return {"crop": crop, "crop_weight_lb_bu": tw, "std_moisture_pct": sm}


def test_monitor_product_is_the_one_value_all_loads_share():
    """_monitor_product: a card that gives the crop weight in lb/bu in some loads and the same weight in kg/bu,
    rounded to 0.01 kg, in others (made-up 47.50 lb/bu and 21.55 kg/bu = 47.5096 lb/bu) still has one weight; loads
    that disagree give None, not a guess."""
    mp = G._monitor_product([_blk(), _blk(tw=21.55 * C.LB_PER_KG), _blk()])
    assert {k: mp[k] for k in ("crop", "test_weight_lb_bu", "dry_moisture_pct", "min_yield", "max_yield")} == {
        "crop": "barley", "test_weight_lb_bu": 47.50, "dry_moisture_pct": 14.2, "min_yield": 5.0, "max_yield": 10_000.0}
    assert "StandardPayableMoisture" in mp["source"]
    assert G._monitor_product([_blk(), _blk(tw=48.5)])["test_weight_lb_bu"] is None
    assert G._monitor_product([_blk(), _blk(sm=14.5)])["dry_moisture_pct"] is None
    assert G._monitor_product([_blk(), _blk(crop="Wheat")])["crop"] is None
    mp = G._monitor_product([_blk(tw=float("nan"), sm=float("nan"))])
    assert mp["test_weight_lb_bu"] is None and mp["dry_moisture_pct"] is None
    # the .fdl unit of the crop weight: kg per bushel converted, an unknown unit gives no weight
    assert G._UOM_LB_BU["kg1bu-1"] == C.LB_PER_KG and G._UOM_LB_BU["lb1bu-1"] == 1.0


def test_align_carried_grain_reaches_a_reading_whose_own_record_is_empty():
    """The open item of the module docstring: a reading whose own grain record is 0 kg (the flow has not started)
    receives grain carried from the records no reading took, and can pass the minimum yield that way."""
    t, x, m, h = _log(60)
    x[:14] = 0.0
    x[14:30] = 6.0
    x[30:] = 0.0                                           # positions 16.. take empty records: the flow stopped
    h[30:] = False                                         # header up at position 30
    x[40:44] = 3.0                                         # draining grain, taken by positions 26-29 (harvesting)
    x[44:50] = 2.0                                         # ... and by positions 30-35 (not harvesting): carried
    own = G.sms_align(t, x, m, h, pending="drop")["wet_kg"]
    a = G.sms_align(t, x, m, h)
    assert np.allclose(own[:16], 6.0) and np.all(own[16:26] == 0) and np.allclose(own[26:30], 3.0)
    assert a["n_not_harvesting"] == 6 and a["kg_not_harvesting"] == pytest.approx(12.0)
    # the 12 kg enter 15 readings before the positions that took them (readings 15-20) and fall by PENDING_KEEP per
    # reading: readings 16-25, whose own records are empty, now hold grain; nothing is lost
    assert np.all(a["wet_kg"][16:26] > 0) and a["wet_kg"][16] > 1.0
    assert np.nansum(a["wet_kg"]) == pytest.approx(x.sum())
