# -*- coding: utf-8 -*-
"""The library API: raw yield-monitor data + a field boundary -> the clean point shapefile and a cleaning summary.

    import oced_clean
    result = oced_clean.clean_yield(["upload.zip"], "boundary.geojson", "work/job_123")
    result.difm_zip         # the shapefile for DIFM, zipped: the yield file of the existing pipeline
    result.difm_yield       # the yield in it: "monitor", or "adjusted" (combine factor and / or grower's weight)
    result.clean_zip        # the full clean shapefile, with OCED's extra columns
    result.summary          # a dict: what was read, the import settings, every step with its limits and counts

The route is OCED's own, with OCED's engine (oced_clean/_engine, copied unchanged from the OCED repository):

    1. unpack and detect      engine.run.open_input        zips, folders, files -> the datasets found
    2. choose the units       engine.run.units_inside_aoi  the tasks / logs of a raw card that lie in the boundary
    3. decode                 engine.run.decode            raw monitor data -> a point table in the SMS column names
    4. import as SMS          engine.run.import_as_sms     the Processing Settings SMS gives this source
    5. SMS 3-sigma cleaning   engine.run.clean             clip, Optimize Swath Widths, moisture, speed, dry yield
    6. the clean shapefile    written by the engine        byte copy of the kept records, cells changed by step 2 only
    7. the shapefile for DIFM engine.difm.write_zip        the same readings in the layout of an SMS shapefile export

Two adjustments can follow the cleaning, both OCED's and both off unless asked for: the combine factor (several
combines) and the level of the grower's weight. They decide which yield the shapefile for DIFM carries (difm_yield).

clean_yield() below is the sequence of OCED's command line (engine.run._cli) without its options for other things
(another cleaning method, planter and sprayer files). tests/test_parity.py runs both on the same synthetic inputs
and compares the clean shapefiles byte for byte.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import shutil
from typing import Callable, Iterable

from . import _lean
from . import summary as _summary
from ._engine import run as _run
from ._engine.formats import detect as _detect

_lean.install()

GROUP_MODES = ("auto", "machine", "task", "field")
DIFM_YIELDS = ("adjusted", "monitor")                    # the yield in the shapefile for DIFM
FACTOR_METHODS = ("auto", "cells", "track", "point", "machine")     # how OCED computes the combine factor
WEIGHT_UNITS = tuple(_run.TICKET_UNITS)                  # lb, kg, bu, t (metric tonne), bu/ac
WEIGHT_BASES = ("wet", "dry")
_LOG = logging.getLogger("oced_clean")


class CleaningError(ValueError):
    """A problem with the input the caller or the user can fix; the message says what. (The engine reports such
    problems as plain ValueError: catch ValueError to get both.)"""


class SeveralDatasetsError(CleaningError):
    """The upload holds several datasets and none was chosen. `datasets` lists them (index, kind, label ...): call
    clean_yield again with candidate=<index>."""

    def __init__(self, message: str, datasets: list):
        super().__init__(message)
        self.datasets = datasets


@dataclasses.dataclass
class CleanResult:
    """What clean_yield returns. Every path is absolute."""
    clean_shp: str            # the full clean point shapefile (WGS 84), with OCED's extra columns; for checking
    clean_zip: str            # the same five files zipped
    flagged_shp: str          # every reading that went into the cleaning, with Keep (1/0) and Rem_By (the step)
    flagged_zip: str
    yield_column: str         # the column of the clean file to map as the dry yield, bu/ac
    summary: dict             # the cleaning summary (also written to summary_json)
    summary_json: str
    engine_json: str          # the engine's full record of the run (every setting, limit, count, warning)
    out_dir: str              # the work folder of the run
    difm_zip: str | None = None   # the shapefile for DIFM, zipped: the kept readings in the layout of an SMS shapefile
    #                               export; the yield upload of the DIFM pipeline
    difm_yield: str | None = None  # the yield in it: "adjusted" (combine factor and / or grower's weight) or "monitor"

    def text(self) -> str:
        """The summary as a short plain-text report."""
        return _summary.as_text(self.summary)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _logger(log) -> Callable[[str], None]:
    target = log if callable(log) else _LOG.info

    def emit(message):
        if _lean.keep_log_line(message):
            target(str(message))
    return emit


def _paths(inputs) -> list:
    if isinstance(inputs, (str, os.PathLike)):
        inputs = [inputs]
    out = [os.fspath(p) for p in inputs]
    if not out:
        raise CleaningError("no input given: pass the uploaded file(s), folder(s) or zip(s)")
    return out


def _dataset_row(i: int, c: dict) -> dict:
    kind = c.get("kind")
    row = {"index": i, "kind": kind, "label": c.get("label"),
           "format": c.get("decoder") if kind == "raw" else ("jd_ops_center_sections" if c.get("sections")
                                                             else "point_export"),
           "harvest": True if kind == "raw" else c.get("yield"), "n_points": c.get("n")}
    if kind != "raw":
        row["file"] = os.path.basename(str(c.get("source_path") or c.get("path") or ""))
    return row


def _unit_row(u, share=None) -> dict:
    return {"id": u.id, "label": u.label, "field": u.field, "crop": u.crop, "year": u.year, "machine": u.machine,
            "start": u.start, "end": u.end, "n_records": int(u.n_records or 0), "latest_season": bool(u.default),
            "share_inside_boundary": share}


def _pick(cands, candidate, log):
    """The dataset to clean, chosen as OCED's command line does: the one asked for; the only one; the only raw one."""
    if candidate is not None:
        if not 1 <= int(candidate) <= len(cands):
            raise CleaningError(f"candidate must be 1..{len(cands)} (the upload holds {len(cands)} dataset(s))")
        return cands[int(candidate) - 1]
    raw = [c for c in cands if c["kind"] == "raw"]
    if len(cands) == 1:
        return cands[0]
    if len(raw) == 1:
        log(f"using dataset {cands.index(raw[0]) + 1}, the raw monitor dataset (another one with candidate=N)")
        return raw[0]
    rows = [_dataset_row(i, c) for i, c in enumerate(cands, 1)]
    raise SeveralDatasetsError(
        f"{len(cands)} datasets were found: choose one with candidate=N ("
        + "; ".join(f"{r['index']} = {r['label']}" for r in rows) + ")", rows)


def _read_boundary(boundary, feature):
    plan = _run.read_plan(os.path.abspath(os.fspath(boundary)), feature=feature)
    if plan.get("needs_choice"):
        raise CleaningError(
            f"{os.path.basename(os.fspath(boundary))} holds {plan['n_polygons']} field boundaries, not a trial design: "
            "choose the field with boundary_feature=N, or [N, M] for a field drawn in several polygons ("
            + "; ".join(f"{f['index']} = {f['name'] or 'polygon ' + str(f['index'])} ({f['area_ac']:,.2f} ac)"
                        for f in plan.get("fields") or []) + ")")
    return plan


def _number(value, name: str, low: float = 0.0, high: float | None = None) -> float:
    """`value` as a number above `low` (and below `high`), else CleaningError naming the option."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = float("nan")
    if not (v == v and v > low and (high is None or v < high)):
        raise CleaningError(f"{name} must be a number above {low:g}" + (f" and below {high:g}" if high else "")
                            + f", not {value!r}")
    return v


def _adjustments(grower_weight, weight_unit, weight_basis, weight_moisture, weight_area_ac, combine_factor,
                 reference_combine, difm_yield) -> tuple:
    """(the engine's scale_ticket setting or None, the engine's machines setting) from the options of clean_yield,
    checked before any file is read. Both are the settings OCED's own command line builds from its --scale-ticket ...
    and --factor-method / --reference-machine options."""
    if difm_yield is not None and difm_yield not in DIFM_YIELDS:
        raise CleaningError(f"difm_yield must be one of {', '.join(DIFM_YIELDS)} (or None), not {difm_yield!r}")
    method = None
    if combine_factor is True:
        method = "auto"
    elif isinstance(combine_factor, str) and combine_factor.strip().lower() in FACTOR_METHODS:
        method = combine_factor.strip().lower()
    elif combine_factor not in (None, False):
        raise CleaningError(f"combine_factor must be True, False or one of {', '.join(FACTOR_METHODS)}, not "
                            f"{combine_factor!r}")
    if reference_combine and not method:
        raise CleaningError("reference_combine names the reference of the combine factor: switch the factor on with "
                            "combine_factor=True (or a method)")
    machines = {"harmonise": False}                      # the SMS sequence alone: no combine factor
    if method:
        machines = {"harmonise": True, "method": method}
        if reference_combine:
            machines["reference"] = str(reference_combine)
    ticket = None
    if grower_weight is None:
        if weight_moisture is not None or weight_area_ac is not None:
            raise CleaningError("weight_moisture and weight_area_ac describe the grower's weight: give grower_weight too")
    else:
        if weight_unit not in WEIGHT_UNITS:
            raise CleaningError(f"weight_unit must be one of {', '.join(WEIGHT_UNITS)}, not {weight_unit!r}")
        if weight_basis not in WEIGHT_BASES:
            raise CleaningError(f"weight_basis must be one of {', '.join(WEIGHT_BASES)}, not {weight_basis!r}")
        if weight_moisture is not None and weight_basis == "dry":
            raise CleaningError("weight_moisture only goes with weight_basis='wet': a dry-basis weight is already at "
                                "the dry moisture")
        if weight_area_ac is not None and weight_unit != "bu/ac":
            raise CleaningError("weight_area_ac only goes with weight_unit='bu/ac' (the acres the grower's yield is "
                                "over)")
        ticket = {"value": _number(grower_weight, "grower_weight"), "unit": weight_unit, "basis": weight_basis,
                  "moisture_pct": (None if weight_moisture is None else _number(weight_moisture, "weight_moisture",
                                                                               high=100.0)),
                  "note": None,
                  "area_ac": None if weight_area_ac is None else _number(weight_area_ac, "weight_area_ac")}
    if difm_yield == "adjusted" and ticket is None and not method:
        raise CleaningError("difm_yield='adjusted' needs an adjustment: give the grower's weight (grower_weight) and / "
                            "or switch the combine factor on (combine_factor)")
    return ticket, machines


def _write_difm(clean_shp: str, R: dict, source: dict | None, which: str, adjusted_column: str | None) -> dict:
    """The shapefile for DIFM of the clean file, written by the engine (engine/difm.py write_zip) next to it:
    <clean file>_DIFM.zip with the monitor's yield, <clean file>_adjusted_DIFM.zip with the adjusted one. Returns the
    writer's own record ({zip, n, fields, days, notes, warnings ...}).

    PROVISIONAL, written before OCED published the adjusted file: the monitor call is the writer's as it stood on
    11 November 2026; how the adjusted file is asked for (the `adjusted` keyword below) is to be matched with the
    engine when it is copied in. tests/test_difm_file.py fails until it does, which is the reminder."""
    from ._engine import difm as _difm
    kw = {"source": source, "columns": R.get("columns")}
    if which == "adjusted":
        kw.update(map_column=adjusted_column, adjusted=True)
    W = _difm.write_zip(clean_shp, **kw)
    if not (W.get("zip") and os.path.isfile(W["zip"])):
        raise RuntimeError("the engine wrote no shapefile for DIFM")
    return W


def inspect_upload(inputs, work_dir, boundary=None, *, boundary_feature=None, log=None) -> dict:
    """What an upload holds, without cleaning anything: for a page that lets the user choose, or to check an upload.

    inputs: the uploaded file(s), folder(s) or zip(s). work_dir: a folder for the unpacked files. boundary (optional):
    with it, every unit of a raw dataset's latest season gets the share of its readings that lie inside the boundary
    + 50 m (each unit is decoded once for that, which takes time on a full card; clean_yield in the same folder
    reuses those positions).

    Returns {"datasets": [...], "notes": [...], "message": str}. A dataset: {"index" (1-based, the `candidate` of
    clean_yield), "kind" ("raw" | "export"), "format", "label", "harvest" (False = a planter / sprayer file: not
    cleaned here), "n_points", "units": [...]}. A unit of a raw dataset: {"id", "label", "field", "crop", "year",
    "machine", "start", "end", "n_records", "latest_season", "share_inside_boundary"}."""
    emit = _logger(log)
    work = os.path.abspath(os.fspath(work_dir))
    os.makedirs(work, exist_ok=True)
    cands = _run.open_input(_paths(inputs), work, log=emit)
    plan = _read_boundary(boundary, boundary_feature) if boundary else None
    out = []
    for i, c in enumerate(cands, 1):
        row = _dataset_row(i, c)
        row["units"] = []
        if c["kind"] == "raw":
            units = _run.list_units(c)
            shares = {}
            if plan is not None:
                shares = _run.units_inside_aoi(c, units, _run.plan_union_wgs84(plan),
                                               _detect.default_options(c["decoder"]), work, log=emit)
            row["units"] = [_unit_row(u, shares.get(u.id)) for u in units]
        out.append(row)
    return {"datasets": out, "notes": list(cands.notes), "message": cands.message or ""}


def clean_yield(inputs, boundary, out_dir, *, boundary_feature=None, candidate: int | None = None,
                units: str | Iterable[str] | None = None, field: str | None = None, year: int | None = None,
                allow_mixed: bool = False, product: str | None = None, weight_to_volume: float | None = None,
                dry_moisture: float | None = None, min_yield: float | None = None, max_yield: float | None = None,
                expand_dry_moisture: bool = False, group_by: str | None = None,
                grower_weight: float | None = None, weight_unit: str = "lb", weight_basis: str = "wet",
                weight_moisture: float | None = None, weight_area_ac: float | None = None,
                combine_factor: bool | str = False, reference_combine: str | None = None,
                difm_yield: str | None = None, tag: str | None = None, cleanup: bool = False,
                log=None) -> CleanResult:
    """Raw yield-monitor data -> the clean shapefile, with OCED's rules.

    inputs      the uploaded file(s), folder(s) or zip(s): a Case IH / New Holland .cn1 card, a John Deere GreenStar 3
                card, John Deere Gen4 .jdl logs (a JD-Data folder), a John Deere Operations Center export, or a yield
                point shapefile in the SMS column names. A zip of any of them, zips inside zips included.
    boundary    the field boundary or the trial design: a polygon file (.shp, a .zip of it, .geojson, .gpkg, .kml,
                .kmz). It is the Clip To Field Boundary of the cleaning and, for a raw card, it chooses the tasks to
                read (those with 20 % or more of their readings inside it). None = no clip (the summary says so) and
                the units of a raw card are chosen by `field` / `year` / `units`.
    out_dir     the work folder of this run (made if missing): input/ (the upload unpacked), decoded/ (the readings
                as read and as imported), output/ (the clean file, the flagged file, the JSON records). Use one
                folder per run.

    boundary_feature  when the boundary file holds several field boundaries (not a trial design): the 1-based index
                of the field's polygon, or a list of indices for a field drawn in several polygons.
    candidate   when the upload holds several datasets: the 1-based index of the one to clean (inspect_upload lists
                them; without it SeveralDatasetsError lists them too).
    units       raw data only: None = chosen by the boundary (or, without one, the field with the most records in the
                latest season); "all" = every unit left by `field` / `year`; a list of unit ids = exactly these.
    field, year raw data only: keep the units of this monitor field name (exact, else a substring) / of this season.
    allow_mixed raw data only: let units of several fields, crops or seasons into one file (refused by default).

    Import as SMS (None = what SMS gives this source: the crop definition of a John Deere file, SMS's product table
    for a Case IH / New Holland card; see README):
    product           the crop: wheat, durum, barley, canola, oats, peas, lentils, flax, rye, corn, soybeans, or an
                      SMS product name ("Wheat - Spring", "Barley - Fall" ...). None = the crop of the data.
    weight_to_volume  lb/bu.          dry_moisture  %.
    min_yield, max_yield  bu/ac; readings outside are left out at the import (0 = no minimum).
    expand_dry_moisture   True raises grain drier than the dry moisture to it (SMS's default, and this one, is off).

    group_by    where the mean and SD of the moisture, speed and yield steps are computed: None or "auto" = one group
                per combine over all its tasks and days (one group for the field when one combine cut it); "task" =
                one group per task; "machine"; "field" = one group for everything.

    Adjustments after the cleaning (none by default: the SMS 3-sigma sequence alone). The cleaning itself, the
    readings kept and the monitor's columns are the same with or without them; they add a column to the full clean
    file and decide which yield the shapefile for DIFM carries:
    grower_weight     the weight the grower got for the field (the scale ticket), in weight_unit: the map is leveled
                      to it (level factor = the weight / the monitor's total; column Yld_Level).
    weight_unit       lb (default), kg, t (metric tonne), bu, or bu/ac (the grower's yield per acre).
    weight_basis      "wet" (default: the scale weighed the grain as harvested) or "dry" (the weight is already at
                      the dry moisture).
    weight_moisture   %, the moisture of the weighed grain when the ticket gives it (wet basis only).
    weight_area_ac    with bu/ac: the acres the grower's yield is over (default: the boundary's area).
    combine_factor    several combines: True (or "auto") computes OCED's combine factor where the combines met on
                      interleaved or mixed passes and applies it where it is proven (column Yld_Harm); "cells",
                      "track", "point" or "machine" choose the method. False = no factor.
    reference_combine the combine the others are brought to (default: the one with the most kept readings).
    difm_yield        the yield in the shapefile for DIFM: "monitor" = the SMS 3-sigma sequence alone; "adjusted" =
                      with the combine factor and the grower's weight; None = adjusted when the cleaning adjusted
                      something, monitor otherwise. "adjusted" without grower_weight or combine_factor is an error;
                      asked for but nothing was applied (a factor that is not proven), the file is the monitor one
                      and the result says so (CleanResult.difm_yield, summary["difm_file"]).

    tag         name prefix of the output files (default: the field and year of a raw card, the file name otherwise).
    cleanup     True removes input/, decoded/ and sections/ from out_dir at the end (the unpacked upload can be large);
                output/ stays.
    log         a function taking one text line (default: logging.getLogger("oced_clean").info).

    Returns a CleanResult. Raises ValueError (CleaningError, SeveralDatasetsError, or the engine's own) for a problem
    with the input that the user can fix: nothing recognised, no reading inside the boundary, a file that is not a
    harvest, several datasets and none chosen, fewer than 30 readings left."""
    emit = _logger(log)
    if group_by is not None and group_by not in GROUP_MODES:
        raise CleaningError(f"group_by must be one of {', '.join(GROUP_MODES)} (or None), not {group_by!r}")
    ticket, machines = _adjustments(grower_weight, weight_unit, weight_basis, weight_moisture, weight_area_ac,
                                    combine_factor, reference_combine, difm_yield)
    work = os.path.abspath(os.fspath(out_dir))
    for sub in ("input", "decoded", "output"):
        os.makedirs(os.path.join(work, sub), exist_ok=True)
    cands = _run.open_input(_paths(inputs), work, log=emit)
    if not cands:
        raise CleaningError(cands.message)
    cand = _pick(cands, candidate, emit)
    _run.use_candidate(cand, work, log=emit)
    all_units = _run.list_units(cand) if cand["kind"] == "raw" else []
    plan = _read_boundary(boundary, boundary_feature) if boundary else None
    if cand["kind"] != "raw" and cand.get("yield") is False:
        raise CleaningError(f"{cand.get('label')}: not a harvest file ({cand.get('op') or 'another operation'}); this "
                            "package cleans yield data only")

    imp_set = {"on": True}
    for key, value in (("product", product), ("test_weight_lb_bu", weight_to_volume),
                       ("dry_moisture_pct", dry_moisture), ("min_yield", min_yield), ("max_yield", max_yield)):
        if value is not None:
            imp_set[key] = value
    if expand_dry_moisture:
        imp_set["expand_dry_moisture"] = True

    dec, sel, unit_note, shares = None, [], "", {}
    if cand["kind"] == "raw":
        everything = isinstance(units, str) and units.strip().lower() == "all"
        ids = None
        if units is not None and not everything:
            ids = [units] if isinstance(units, str) else [str(u) for u in units]
        opts = _detect.default_options(cand["decoder"])
        opts = dataclasses.replace(opts, crop=opts.crop, test_weight_lb_bu=None, std_moisture_pct=None,
                                   shrink=opts.shrink)
        if plan is not None and ids is None and not everything and not field:
            # the boundary chooses the units: 20 % or more of the readings inside the boundary + 50 m
            emit(f"measuring the share of every latest-season unit inside the boundary + {_run.AOI_BUFFER_M:g} m ...")
            shares = _run.units_inside_aoi(cand, all_units, _run.plan_union_wgs84(plan), opts, work, log=emit)
            pool = [u for u in all_units if (shares.get(u.id) or 0.0) >= _run.AOI_PRESELECT_SHARE]
            if year:
                pool = [u for u in pool if u.year == year]
            if not pool:
                dist = _run.units_aoi_distance_m(cand, all_units, _run.plan_union_wgs84(plan), opts, work)
                near = min(dist.values()) if dist else float("nan")
                raise CleaningError(
                    f"no unit of the raw data has {100 * _run.AOI_PRESELECT_SHARE:.0f} % of its readings inside the "
                    f"boundary; the nearest unit is {near:,.0f} m away (the wrong boundary or the wrong upload; or "
                    "choose the units with units=[...])")
            sel = pool
            unit_note = (f"{len(pool)} unit(s) with {100 * _run.AOI_PRESELECT_SHARE:.0f} % or more of their readings "
                         "inside the boundary")
        else:
            sel, unit_note = _run.select_units(all_units, field, year, ids, everything)
        if not sel:
            raise CleaningError("no unit left to read: inspect_upload lists the units of the upload")
        if unit_note:
            emit("units: " + unit_note)
        for u in sel:
            emit("  " + u.label)
        crops = sorted({str(u.crop or "").strip().lower() for u in sel} - {""})
        imp_crop = product or (crops[0] if crops else None)
        # the decoder gets the weight and moisture of the import, as OCED's extract does
        opts, _ = _run.decoder_options_for_import(opts, imp_set, imp_crop, fmt=cand["decoder"])
        dec = _run.decode(cand, [u.id for u in sel], opts, work, log=emit, tag=tag, allow_mixed=allow_mixed,
                          aoi=plan is not None)
        rep = dec["report"]
        for w in rep["warnings"]:
            emit("decoder warning: " + str(w))
        shp = dec["shp"]
        imp_mon, imp_fmt = rep.get("monitor_product"), rep.get("format") or cand["decoder"]
        imp_crop = product or rep.get("crop_name") or imp_crop
    else:
        shp = cand["path"]
        imp_crop = product or _run.export_crop_guess(shp)
        imp_mon, imp_fmt = cand.get("monitor_product"), _run.import_format(None, cand)

    tag_i = tag or (dec["tag"] if dec else os.path.splitext(os.path.basename(shp))[0])
    shp, _n_imported, imp_rec = _run.import_as_sms(shp, tag_i, os.path.join(work, "decoded"), imp_set, crop=imp_crop,
                                                   log=emit, monitor=imp_mon, fmt=imp_fmt)
    if imp_rec:
        emit("import values from: " + imp_rec["sources_text"])

    settings = {}
    if boundary:
        settings.setdefault("aoi", {})["file"] = os.path.abspath(os.fspath(boundary))
        if boundary_feature is not None:
            settings["aoi"]["feature"] = _run.feature_setting(boundary_feature)
    if tag or dec:
        settings.setdefault("tag", tag or dec["tag"])
    settings["method"] = "sms3sigma"
    settings["machines"] = machines                      # {"harmonise": False} unless combine_factor asks for it
    if ticket is not None:
        settings["scale_ticket"] = ticket
    if group_by:
        settings["sms3sigma"] = dict(settings.get("sms3sigma") or {}, group_by=group_by)
    # as OCED: the import's weight, moisture and minimum, and the SMS clip of the boundary
    settings = _run.sms_clean_defaults(settings, imp_rec)
    out = _run.free_output_dir(work)
    R = _run.clean(shp, out, settings, log=emit, source=dec["source"] if dec else None)

    files = dict(R["files"])
    for key in ("report_pdf", "figures"):            # not made by this package (oced_clean/_lean.py)
        files.pop(key, None)
    # the shapefile for DIFM: the adjusted yield when the cleaning adjusted something, unless the monitor's is asked
    adjusted_column = _summary.adjusted_column(R)
    which = "adjusted" if (adjusted_column and difm_yield != "monitor") else "monitor"
    W = _write_difm(files["difm_ready_shp"], R, dec["source"] if dec else None, which, adjusted_column)
    S = _summary.build(R, files=files, dataset=_dataset_row(cands.index(cand) + 1, cand),
                       units=[_unit_row(u, shares.get(u.id)) for u in sel], unit_note=unit_note,
                       decode_report=dec["report"] if dec else None, import_record=imp_rec,
                       boundary=os.fspath(boundary) if boundary else None, notes=list(cands.notes),
                       difm=W, difm_yield=which, difm_yield_asked=difm_yield)
    sj = os.path.join(out, f"{R['tag']}_summary.json")
    with open(sj, "w", encoding="utf-8") as f:
        json.dump(S, f, indent=1)
    if cleanup:
        for name in os.listdir(work):
            if name == "input" or name.startswith("input_") or name in ("decoded", "sections"):
                shutil.rmtree(os.path.join(work, name), ignore_errors=True)
        S["files"]["intermediate_files_removed"] = True
        with open(sj, "w", encoding="utf-8") as f:
            json.dump(S, f, indent=1)
    emit(f"shapefile for DIFM ({which} yield): {W['zip']}")
    emit(f"full clean file: {files['difm_ready_zip']} (its yield to map: {S['result']['yield_column']})")
    return CleanResult(clean_shp=files["difm_ready_shp"], clean_zip=files["difm_ready_zip"],
                       flagged_shp=files["flagged_shp"], flagged_zip=files["flagged_zip"],
                       yield_column=S["result"]["yield_column"], summary=S, summary_json=sj,
                       engine_json=files["json"], out_dir=work, difm_zip=W["zip"], difm_yield=which)
