# -*- coding: utf-8 -*-
"""The inputs that are not a raw card, on synthetic data (every number is made up): a point export with the John
Deere Operations Center column names through the import as SMS, a file without a moisture column, an Operations
Center section-element export, a zip whose paths are too long for Windows with zips that duplicate their unpacked
copies, and ISOXML task data.

    python -m pytest -q tests
"""
from __future__ import annotations

import io
import json
import os
import sys
import zipfile

import numpy as np
import pandas as pd
import pyogrio
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from oced_clean._engine import run as er  # noqa: E402
from oced_clean._engine import sms_import as si  # noqa: E402
from oced_clean._engine.formats import detect  # noqa: E402

APP = os.path.join(ROOT, "app", "oced.py")
LAT0, LON0, M_PER_DEG = 51.80, -114.10, 111_320.0
OPS_TW, OPS_DRY = 59.0, 12.5             # a made-up basis for the file's own dry yield (the import recomputes it)


def _ops_frame(n=400, seed=3):
    """n one-second readings of one combine with the Operations Center column names (wheat, 40 ft header)."""
    rng = np.random.default_rng(seed)
    moist = 13.0 + rng.normal(0, 1.2, n)                      # about a third above 13.5 %
    wet = 4000 + rng.normal(0, 300, n)                        # lb/ac
    wet[:10] = 100.0                                          # 1.7 bu/ac: below the 5 bu/ac minimum
    t = pd.Timestamp("2026-09-14 13:00:00") + pd.to_timedelta(np.arange(n), unit="s")
    df = pd.DataFrame({
        "DISTANCE": 6.6, "SWATHWIDTH": 40.0, "WetMass": wet, "Moisture": moist,
        "VRYIELDVOL": wet * np.where(moist > OPS_DRY, (100 - moist) / (100 - OPS_DRY), 1.0) / OPS_TW,
        "Crop": 25, "Time": t.strftime("%m/%d/%Y %I:%M:%S %p"), "IsoTime": t.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "Heading": 90.0, "Machine": 1, "VEHICLSPEE": 4.5})
    df["x"], df["y"] = np.arange(n) * 2.0, 0.0
    return df


def _write_points(df, shp):
    import geopandas as gpd
    lon = LON0 + df["x"].to_numpy() / (M_PER_DEG * np.cos(np.radians(LAT0)))
    lat = LAT0 + df["y"].to_numpy() / M_PER_DEG
    os.makedirs(os.path.dirname(shp), exist_ok=True)
    g = gpd.GeoDataFrame(df.drop(columns=["x", "y"]), geometry=gpd.points_from_xy(lon, lat), crs="EPSG:4326")
    g.to_file(shp, engine="pyogrio")
    return shp


def _deere_metadata(shp, crop="Wheat (Hard Red Spring)", units=None):
    u = {"DISTANCE": "ft", "SWATHWIDTH": "ft", "VRYIELDVOL": "bu1ac-1", "WetMass": "lb1ac-1", "VEHICLSPEED": "mi1hr-1",
         **(units or {})}
    meta = {"Version": "1.0", "Operation": "Harvest", "CropId": 25, "CropName": crop,
            "DataAttributes": [{"Name": k, "Unit": v} for k, v in u.items()] + [{"Name": "Time"}]}
    with open(os.path.splitext(shp)[0] + "-Deere-Metadata.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh)


def _imported(shp, out, S, crop="wheat"):
    rep = si.apply(shp, out, si.resolve(si.settings(S), crop))
    return pyogrio.read_dataframe(rep["path"], read_geometry=False), rep


def test_import_operations_center_names(tmp_path):
    """The import as SMS on a file with the Operations Center names: the dry yield again from the wet mass with the
    product's weight and dry moisture (not the file's own VRYIELDVOL), the limits on that dry yield, and the manual
    swath, the manual moisture and the flow shift reach the yields."""
    df = _ops_frame()
    shp = _write_points(df, str(tmp_path / "ops" / "Synthetic_Harvest_2026-09-14_00.shp"))
    wet, moist = df["WetMass"].to_numpy(), df["Moisture"].to_numpy()
    assert si.yield_columns(pyogrio.read_info(shp)["fields"]) == {
        "wet_mass": "WetMass", "dry_mass": None, "wet_vol": None, "dry_vol": "VRYIELDVOL"}
    # SMS's wheat: 60 lb/bu, 13.5 %, one-sided; the 10 readings below 5 bu/ac left out
    e, rep = _imported(shp, str(tmp_path / "a.shp"), {})
    f = np.where(moist > 13.5, (100 - moist) / 86.5, 1.0)
    assert rep["left_out"] == {"below the min. yield volume": 10} and rep["changed"] == ["VRYIELDVOL"]
    assert not rep["notes"]
    assert np.allclose(e["VRYIELDVOL"].to_numpy(float), (wet * f / 60.0)[10:], rtol=1e-9)
    assert np.allclose(e["WetMass"].to_numpy(float), wet[10:], rtol=1e-12)
    keep_all = {"min_yield": None, "max_yield": 1e5}
    # the totals of SMS: area, wet weight and dry volume of every reading
    e, rep = _imported(shp, str(tmp_path / "b.shp"), keep_all)
    area = 6.6 * 40.0 / 43560.0
    assert rep["n_out"] == len(df)
    assert float((e["VRYIELDVOL"] * area).sum()) == pytest.approx(float((wet * f).sum() * area / 60.0), rel=1e-9)
    # a manual swath of half the logged width doubles every per-area value: the grain mass is kept
    e, rep = _imported(shp, str(tmp_path / "c.shp"), {**keep_all, "use_manual_swath": True, "manual_swath_ft": 20.0})
    assert set(rep["changed"]) == {"SWATHWIDTH", "WetMass", "VRYIELDVOL"}
    assert np.allclose(e["SWATHWIDTH"], 20.0) and np.allclose(e["WetMass"].to_numpy(float), 2 * wet, rtol=1e-9)
    assert np.allclose(e["VRYIELDVOL"].to_numpy(float), 2 * wet * f / 60.0, rtol=1e-9)
    # a manual moisture of 20 % dries every reading by (100 - 20) / (100 - 13.5)
    e, rep = _imported(shp, str(tmp_path / "d.shp"), {**keep_all, "use_manual_moisture": True,
                                                     "manual_moisture_pct": 20.0})
    assert np.allclose(e["Moisture"], 20.0)
    assert np.allclose(e["VRYIELDVOL"].to_numpy(float) * 60.0 / e["WetMass"].to_numpy(float), 80 / 86.5, rtol=1e-9)
    # a flow shift of 2 s: the grain of the reading logged 2 s later (the last two readings have none: no yield)
    e, rep = _imported(shp, str(tmp_path / "e.shp"), {**keep_all, "flow_shift_s": 2.0})
    assert rep["left_out"] == {"no yield": 2} and any(n.startswith("flow shift +2 s") for n in rep["notes"])
    assert np.allclose(e["WetMass"].to_numpy(float), wet[2:], rtol=1e-9)


def test_import_without_moisture_column(tmp_path):
    """A file with no moisture column: each reading keeps its own dry / wet ratio (the dry yield does not become the
    wet yield), which a note says; with 'Use manual moisture' the dry yield is computed with the manual moisture,
    which a note says too."""
    df = _ops_frame(200)
    wet = df["WetMass"].to_numpy()
    d = pd.DataFrame({"Distance_f": 6.6, "Swth_Wdth_": 40.0, "Duration_s": 1.0, "Yld_Mass_W": wet,
                      "Yld_Vol_We": wet / 60.0, "Yld_Mass_D": wet * 0.95, "Yld_Vol_Dr": wet * 0.95 / 60.0,
                      "Track_deg_": 90.0, "Dataset": "d1", "Obj__Id": np.arange(1, len(df) + 1),
                      "x": df["x"], "y": df["y"]})
    shp = _write_points(d, str(tmp_path / "sms" / "No_moisture.shp"))
    keep_all = {"min_yield": None, "max_yield": 1e5}
    e, rep = _imported(shp, str(tmp_path / "a.shp"), keep_all)
    assert rep["changed"] == [] and any(n.startswith("no moisture column") for n in rep["notes"])
    assert np.allclose(e["Yld_Vol_Dr"].to_numpy(float) / e["Yld_Vol_We"].to_numpy(float), 0.95, rtol=1e-6)
    # another weight to volume reaches both volumes; the dry mass of the file stays (its dry / wet ratio)
    e, rep = _imported(shp, str(tmp_path / "b.shp"), {**keep_all, "test_weight_lb_bu": 57.0})
    assert rep["changed"] == ["Yld_Vol_We", "Yld_Vol_Dr"]
    assert np.allclose(e["Yld_Vol_We"].to_numpy(float), wet / 57.0, rtol=1e-6)
    assert np.allclose(e["Yld_Vol_Dr"].to_numpy(float), wet * 0.95 / 57.0, rtol=1e-6)
    assert np.allclose(e["Yld_Mass_D"].to_numpy(float), wet * 0.95, rtol=1e-6)
    e, rep = _imported(shp, str(tmp_path / "c.shp"), {**keep_all, "use_manual_moisture": True,
                                                     "manual_moisture_pct": 20.0})
    assert set(rep["changed"]) == {"Yld_Mass_D", "Yld_Vol_Dr"}
    assert any(n.startswith("manual moisture: the file has no moisture column") for n in rep["notes"])
    assert np.allclose(e["Yld_Vol_Dr"].to_numpy(float) / e["Yld_Vol_We"].to_numpy(float), 80 / 86.5, rtol=1e-6)
    # no wet mass and no wet volume: a flow shift cannot be applied, and the note says so
    d2 = d.drop(columns=["Yld_Mass_W", "Yld_Vol_We", "Yld_Mass_D"])
    shp2 = _write_points(d2, str(tmp_path / "dry" / "Dry_only.shp"))
    _, rep = _imported(shp2, str(tmp_path / "d.shp"), {**keep_all, "flow_shift_s": 2.0})
    assert any(n.startswith("flow shift: the file has no wet mass") for n in rep["notes"])


def test_import_limits_on_the_recomputed_dry_yield(tmp_path):
    """Wet mass and moisture but no dry-volume column: the yield volume limits act on the dry yield the import
    computes (wet 300 bu/ac at 18 % is 284.39 bu/ac dry at 13.5 %: kept under a maximum of 290), not on the wet one."""
    n = 60
    moist = np.where(np.arange(n) % 2 == 0, 18.0, 13.0)
    d = pd.DataFrame({"Distance_f": 6.6, "Swth_Wdth_": 40.0, "Duration_s": 1.0, "Yld_Mass_W": 300.0 * 60.0,
                      "Moisture__": moist, "Track_deg_": 90.0, "Dataset": "d1", "Obj__Id": np.arange(1, n + 1),
                      "x": np.arange(n) * 2.0, "y": 0.0})
    shp = _write_points(d, str(tmp_path / "wet" / "Wet_only.shp"))
    assert si.yield_columns(pyogrio.read_info(shp)["fields"])["dry_vol"] is None
    _, rep = _imported(shp, str(tmp_path / "a.shp"), {"min_yield": None, "max_yield": 290.0})
    assert rep["left_out"] == {"above the max. yield volume": n // 2}            # the 13 % readings: 300 bu/ac dry
    e = pyogrio.read_dataframe(rep["path"], read_geometry=False)
    assert np.allclose(e["Moisture__"], 18.0) and len(e) == n // 2
    assert 300.0 * 82.0 / 86.5 == pytest.approx(284.393, abs=1e-3)


def _section_export(folder, crop="Barley", units=None):
    """An Operations Center section-element export: two combines, 120 seconds each, 8 header elements of 5 ft a
    second, the outer two switched off (no area) on every fourth second; one second of combine 2 with no area at
    all. Returns (shp, seconds with an area, area in acres, wet weight in lb)."""
    rng = np.random.default_rng(5)
    rows, area, mass, seconds = [], 0.0, 0.0, 0
    for m in (1, 2):
        for s in range(120):
            t = pd.Timestamp("2026-07-15 15:00:00") + pd.Timedelta(seconds=s)
            dist = 6.0 + 0.5 * np.sin(s / 9.0)
            dead = m == 2 and s == 50
            for k in range(8):
                on = not dead and not (s % 4 == 0 and k in (0, 7))
                w, dd = (5.0, dist) if on else (0.0, dist)
                wm = 3800.0 + 400.0 * rng.random() if on else 0.0
                mo = 15.2 if s % 3 == 0 else 13.8
                rows.append({"DISTANCE": dd, "SWATHWIDTH": w, "VRYIELDVOL": wm / 48.0, "SECTIONID": 100 + k, "Crop": 2,
                             "WetMass": wm, "Moisture": mo, "Time": t.strftime("%m/%d/%Y %I:%M:%S %p"),
                             "Heading": 90.0 if m == 1 else 270.0, "Elevation": 3300.0,
                             "IsoTime": t.strftime("%Y-%m-%dT%H:%M:%S.250Z"), "Machine": m, "VEHICLSPEE": dist / 1.4667,
                             "x": (s if m == 1 else 119 - s) * 1.9, "y": (m - 1) * 12.2 + (k - 3.5) * 1.524})
                area += dd * w / 43560.0
                mass += wm * dd * w / 43560.0
            seconds += not dead
    shp = _write_points(pd.DataFrame(rows), os.path.join(folder, "doc", "Synthetic_Field_Harvest_2026-07-15_00.shp"))
    _deere_metadata(shp, crop, units)
    return shp, seconds, area, mass


def test_section_export_collapse_and_import(tmp_path):
    """A section-element export through the engine: recognised, collapsed to one reading per combine and second
    (the area and the wet weight kept, the second without an area dropped), the crop from the Deere metadata, and
    the import as SMS computes the dry yield of the product (barley: 48 lb/bu, 14.5 %, one-sided)."""
    shp, seconds, area, mass = _section_export(str(tmp_path / "in"))
    work = str(tmp_path / "work")
    cands = er.open_input([os.path.dirname(shp)], work, log=lambda m: None)
    assert len(cands) == 1 and cands[0].get("sections") and cands[0]["crop"] == "Barley"
    assert not any("metadata gives" in n for n in cands.notes)
    cand = er.use_candidate(cands[0], work, log=lambda m: None)
    d = pyogrio.read_dataframe(cand["path"], read_geometry=False)
    a = d["DISTANCE"] * d["SWATHWIDTH"] / 43560.0
    assert len(d) == seconds == 239 and "SECTIONID" not in d
    assert float(a.sum()) == pytest.approx(area, rel=1e-9)
    assert float((d["WetMass"] * a).sum()) == pytest.approx(mass, rel=1e-9)
    R = si.resolve(si.settings({"min_yield": None, "max_yield": 1e5}), cand["crop"])
    assert (R["product"], R["test_weight_lb_bu"], R["dry_moisture_pct"]) == ("barley", 48.0, 14.5)
    rep = si.apply(cand["path"], str(tmp_path / "imported.shp"), R)
    e = pyogrio.read_dataframe(rep["path"], read_geometry=False)
    f = np.where(e["Moisture"] > 14.5, (100 - e["Moisture"]) / 85.5, 1.0)
    assert rep["changed"] == ["VRYIELDVOL"] and (f < 1).sum() == 80
    assert np.allclose(e["VRYIELDVOL"].to_numpy(float), e["WetMass"].to_numpy(float) * f / 48.0, rtol=1e-9)


def test_operations_center_units_note(tmp_path):
    """An Operations Center export whose metadata declares metric units is read as imperial: the note says so."""
    df = _ops_frame(50)
    shp = _write_points(df, str(tmp_path / "in" / "Metric_Harvest_2026-09-14_00.shp"))
    _deere_metadata(shp, units={"DISTANCE": "m", "WetMass": "kg1ha-1"})
    cands = detect.detect(str(tmp_path / "in"))
    assert len(cands) == 1 and cands[0]["crop"] == "Wheat (Hard Red Spring)"
    note = next(n for n in cands.notes if "metadata gives" in n)
    assert "DISTANCE in m" in note and "WetMass in kg1ha-1" in note and "SWATHWIDTH" not in note


def test_app_operations_center_export(tmp_path, monkeypatch):
    """The app on an Operations Center export: the product comes from the Deere metadata (barley), so the import
    computes the dry yield with what SMS gives a John Deere barley (UPDATE 4: 48 lb/bu, 15.0 %, 5-10,000 bu/ac)
    instead of leaving the file's own, and says that the weight and moisture are SMS's for the crop, not the file's."""
    from streamlit.testing.v1 import AppTest
    df = _ops_frame(300)
    shp = _write_points(df, str(tmp_path / "in" / "Synthetic_Harvest_2026-09-14_00.shp"))
    _deere_metadata(shp, crop="Barley")
    monkeypatch.setenv("OCED_WORKSPACE", str(tmp_path / "ws"))
    at = AppTest.from_file(APP, default_timeout=900)
    at.run()
    at.text_input(key="path_in").input(shp)
    at.button(key="open_btn").click().run()
    assert not at.exception, at.exception
    rd = next(m for m in at.metric if m.label == "Readings")
    assert "product barley; weight to volume 48.00 lb/bu; dry moisture 15.00 %" in rd.help
    assert "yield volume 5.000-10,000.00 bu/ac" in rd.help
    assert "recomputed: VRYIELDVOL" in rd.help and "not recognised" not in rd.help
    assert ("weight to volume 48.00 lb/bu (SMS's value for this John Deere crop, not the file's own)" in rd.help
            and "(from the file)" not in rd.help)
    mx = next(w for w in at.number_input if str(w.key or "").startswith("fi_max_"))
    assert mx.value == 10_000.0 and "From: SMS for John Deere files." in mx.help
    assert "When SMS holds another maximum for the product" in mx.help and "200 bu/ac" not in mx.help


# ------------------------------------------------------------------------------------------------ unpacking
def _download_zip(path, top, machines, loose=True, n_files=3):
    """A zip as a cloud drive hands out a folder of per-machine JD-Data zips: <top>/<machine>.zip and, with loose,
    the same files unpacked beside each zip in <top>/<machine>/. The log files are made-up bytes."""
    rng = np.random.default_rng(1)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for k, m in enumerate(machines):
            names = [f"JD-Data/log/2026/{k:08d}-0000-4000-8000-{i:012d}.jdl" for i in range(n_files)]
            blobs = [rng.bytes(500 + 10 * i) for i in range(n_files)]
            if loose:
                for nm, b in zip(names, blobs):
                    z.writestr(f"{top}/{m}/{nm}", b)
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, "w", zipfile.ZIP_DEFLATED) as zi:
                for nm, b in zip(names, blobs):
                    zi.writestr(nm, b)
            z.writestr(f"{top}/{m}.zip", inner.getvalue())
    return path


def _files(root):
    """Every file under root: walked through the extended-length form of the path, given back without its prefix."""
    pre = len(detect._long(root)) - len(os.path.abspath(root))
    return sorted(os.path.join(dp, f)[pre:] for dp, _, fn in os.walk(detect._long(root)) for f in fn)


def test_long_paths(tmp_path, monkeypatch):
    """A zip whose files would land beyond what Windows opens (260 characters) under <zip stem>/ goes into a short
    folder, said in the log; zips that lie beside their own unpacked copy are not unpacked again; nothing is called
    a damaged zip. With no room even in the short folder the error says the paths are too long."""
    monkeypatch.setattr(detect, "PATH_LIMITED", True)            # the same behaviour where paths have no limit
    machines = ["SYNTHCOMBINE00001_07152026", "SYNTHCOMBINE00002_07152026"]
    log_name = "JD-Data/log/2026/00000000-0000-4000-8000-000000000000.jdl"
    dest = str(tmp_path / "w" / "input")
    # the longest top folder that still fits in the short folder: <dest>/z1/<top>/<machine>/<log>
    room = detect.PATH_LIMIT - 5 - len(os.path.join(dest, "z1", "", machines[0], *log_name.split("/")))
    if room < 8:
        pytest.skip("the temporary folder of the tests is too deep for this layout")
    top = "Raw_" + "x" * (room - 4)
    zp = _download_zip(str(tmp_path / (top + "-20000101T000000Z-1-001.zip")), top, machines)
    stem_dir = os.path.join(dest, os.path.splitext(os.path.basename(zp))[0])
    assert len(os.path.join(stem_dir, top, machines[0], *log_name.split("/"))) > 260
    log = []
    roots = detect.unpack([zp], dest, log=log.append)
    assert roots == [os.path.abspath(dest)]
    files = _files(dest)
    jdl = [f for f in files if f.endswith(".jdl")]
    assert len(jdl) == 6                                          # each log once: the nested zips repeat them
    assert max(len(f) for f in files) <= detect.PATH_LIMIT
    assert os.path.isdir(os.path.join(dest, "z1")) and not os.path.exists(stem_dir)
    text = " | ".join(log)
    assert "unpacked into the short folder z1" in text and text.count("not unpacked again") == 2
    # the same zips without their unpacked copies: every nested zip is unpacked, within the limit too
    zp2 = _download_zip(str(tmp_path / (top + "-20000101T000000Z-1-002.zip")), top, machines, loose=False)
    dest2 = str(tmp_path / "v" / "input")
    log2 = []
    detect.unpack([zp2], dest2, log=log2.append)
    assert len([f for f in _files(dest2) if f.endswith(".jdl")]) == 6
    assert any("unpacked into the short folder" in m for m in log2)
    assert max(len(f) for f in _files(dest2)) <= detect.PATH_LIMIT
    # no room even in the short folder: the real cause, not "damaged"
    deep = str(tmp_path / "deep.zip")
    with zipfile.ZipFile(deep, "w") as z:
        z.writestr("/".join(["folder_" + "y" * 40] * 6) + "/log.jdl", b"0123")
    with pytest.raises(ValueError) as err:
        detect.unpack([deep], str(tmp_path / "u" / "input"), log=lambda m: None)
    assert "too long for Windows" in str(err.value) and "not damaged" in str(err.value)


def test_unpack_zip_beside_its_copy(tmp_path):
    """A folder holding a zip beside its own unpacked copy, read in place and copied: the zip is not unpacked
    again, so every log file is listed once."""
    names = [f"JD-Data/log/2026/{i:08d}-0000-4000-8000-000000000000.jdl" for i in range(3)]
    blobs = [bytes([i]) * (400 + i) for i in range(3)]
    top = tmp_path / "card"
    for nm, b in zip(names, blobs):
        f = top / "M1" / nm
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b)
    with zipfile.ZipFile(top / "M1.zip", "w") as z:
        for nm, b in zip(names, blobs):
            z.writestr(nm, b)

    def jdl(roots):
        return sorted(os.path.relpath(os.path.join(dp, f), r) for r in roots for dp, _, fn in os.walk(r)
                      for f in fn if f.endswith(".jdl"))
    for copy in (False, True):
        log = []
        dest = str(tmp_path / f"work_{copy}" / "input")
        roots = detect.unpack([str(top)], dest, copy_folders=copy, log=log.append)
        assert roots == ([os.path.abspath(dest)] if copy else [os.path.abspath(dest), str(top)])
        assert len(jdl(roots)) == 3, jdl(roots)
        assert any("the folder beside it holds the same files, not unpacked" in m for m in log)
        assert not any(m.startswith("unpacking") for m in log)
        assert not os.path.exists(os.path.join(dest, "card__zips"))


def test_unpack_messages_say_what_failed(tmp_path):
    """A member that cannot be inflated is a damaged zip; a member the file system refuses is not (here a folder
    that cannot be made because a file has its name); a zip is kept in its own folder when the paths fit."""
    ok = str(tmp_path / "ok.zip")
    with zipfile.ZipFile(ok, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("a/b.txt", b"hello " * 400)
    dest = str(tmp_path / "work" / "input")
    detect.unpack([ok], dest, log=lambda m: None)
    assert os.path.isfile(os.path.join(dest, "ok", "a", "b.txt"))
    raw = bytearray(open(ok, "rb").read())
    at = raw.find(b"a/b.txt") + len(b"a/b.txt")
    raw[at:at + 12] = b"\xff" * 12                               # the compressed data of the member, overwritten
    bad = str(tmp_path / "bad.zip")
    with open(bad, "wb") as fh:
        fh.write(bytes(raw))
    with pytest.raises(ValueError) as err:
        detect.unpack([bad], str(tmp_path / "work2" / "input"), log=lambda m: None)
    assert "damaged" in str(err.value) and "file system" not in str(err.value)
    clash = str(tmp_path / "clash.zip")
    with zipfile.ZipFile(clash, "w") as z:
        z.writestr("a", b"a file")
        z.writestr("a/b.txt", b"needs a folder with the name of that file")
    with pytest.raises(ValueError) as err:
        detect.unpack([clash], str(tmp_path / "work3" / "input"), log=lambda m: None)
    assert "the file system refused" in str(err.value) and "damaged" not in str(err.value)


def test_isoxml_is_named(tmp_path):
    """ISOXML task data that holds no readable harvest log (here empty sets inside nested zips; a readable one is a
    raw dataset: tests/test_isoxml.py): the message says that it was found and why nothing is read from it, instead
    of 'nothing usable was recognised'; beside a readable export it is a note."""
    zp = str(tmp_path / "Synthetic ISOXML.zip")
    with zipfile.ZipFile(zp, "w") as z:
        for k in range(3):
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, "w") as zi:
                zi.writestr("TASKDATA/TASKDATA.XML", b"<ISO11783_TaskData/>")
                zi.writestr("TASKDATA/TLG00001.XML", b"<TIM/>")
                zi.writestr("TASKDATA/TLG00001.BIN", b"\x00" * 32)
            z.writestr(f"TASKDATA-0000000000-2000010100{k:04d}.zip", inner.getvalue())
    cands = er.open_input([zp], str(tmp_path / "work"), log=lambda m: None)
    assert len(cands) == 0
    assert cands.message.startswith("ISOXML task data was found (3 TASKDATA.XML with 3 TLG log file(s)")
    assert "no harvest data is read from it: its time logs cannot be read" in cands.message
    assert "does not read ISOXML yet" not in cands.message and "Supported inputs" in cands.message
    assert "Nothing usable" not in cands.message
    plain = er.open_input([str(tmp_path)], str(tmp_path / "work_empty"), log=lambda m: None)   # the zip, unpacked
    assert plain.message.startswith("ISOXML")
    _write_points(_ops_frame(30), str(tmp_path / "exp" / "Synthetic_Harvest_2026-09-14_00.shp"))
    both = er.open_input([zp, str(tmp_path / "exp")], str(tmp_path / "work_both"), log=lambda m: None)
    assert len(both) == 1 and not both.message and any(n.startswith("ISOXML task data was found") for n in both.notes)
