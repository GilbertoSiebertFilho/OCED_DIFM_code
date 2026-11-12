# -*- coding: utf-8 -*-
"""The cleaning summary: a short, JSON-ready account of one run, made from the engine's own record.

build() reads the numbers; it computes none of its own except sums of the engine's counts. The engine's full record
(every setting, limit, count and warning) stays beside it as <tag>_cleaning.json.

Layout of the summary (README has the same list with an example):
    package     this package's version and the OCED engine it carries (version, commit)
    input       the dataset cleaned, its format, the units read (raw cards), the combines, the crop
    import      Import as SMS: the settings used, where each value came from, readings in / imported / left out
    boundary    the file, its area, whether it is a trial design
    cleaning    the method's fixed rules, the groups, and `steps`: one row per step in the order they ran
    result      the clean file: readings, area, mean and area-weighted average of the yield column, the column to map
    adjustment  what was done after the cleaning: the combine factor and the grower's weight, when asked for
    difm_file   the shapefile for DIFM: which yield it carries (adjusted | monitor), its fields, the writer's notes
    columns     which column of the full clean file holds what
    files       the paths
    warnings, checks, notes   what the engine wants a person to read
"""
from __future__ import annotations

import json
import os

from . import __about__

STEP_NAMES = {"S0": "Import check", "S1": "Clip to field boundary", "S2": "Optimize Swath Widths"}
DIFM_WARNING = "shapefile for DIFM: "       # how a warning of the DIFM writer starts in the summary's warnings
ATTRIBUTE_STEP = {"moisture": "Moisture", "speed": "Speed", "dry yield": "Dry yield"}


def _plain(v):
    """JSON-ready: numpy scalars to Python numbers, NaN / inf to None, tuples to lists."""
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_plain(x) for x in v]
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        try:
            v = v.item()
        except (ValueError, AttributeError):
            return str(v)
    if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
        return None
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    return str(v)


def engine_source() -> dict:
    """Which OCED the copied engine is: {"oced_version", "commit", "commit_subject", "commit_date", "repository"}."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_engine", "ENGINE_SOURCE.json")
    try:
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return {"oced_version": None, "commit": None, "commit_subject": None, "commit_date": None, "repository": None}
    return {"oced_version": m.get("oced_version"), "commit": m.get("commit"),
            "commit_subject": m.get("commit_subject"), "commit_date": m.get("commit_date"),
            "repository": m.get("source_repository")}


def _find(fields, *starts) -> str | None:
    """The first field whose lower-case name starts with one of `starts`."""
    for s in starts:
        for f in fields:
            if str(f).lower().startswith(s):
                return f
    return None


def _columns(R: dict, fields: list, adjusted: str | None = None) -> dict:
    """Which column of the clean file holds what. Units are the ones the engine reads the file in (the SMS / Operations
    Center export convention). adjusted = the column of the adjusted yield, when the cleaning adjusted something."""
    C = R.get("columns") or {}
    ch = (R.get("sms3sigma") or {}).get("difm_ready_changes") or {}
    width = C.get("width_ft") or ("Wdth_Opt" if "Wdth_Opt" in (R.get("difm_ready_added_columns") or []) else None)

    def col(name, unit, note=None):
        if not name or (fields and name not in fields):
            return None
        d = {"column": name, "unit": unit}
        if note:
            d["note"] = note
        return d
    out = {
        "yield_volume_dry": col(C.get("yield") or R.get("yield_column_for_difm"), "bu/ac",
                                "the monitor's yield as cleaned: dry volume at the dry moisture of the import, as SMS "
                                "exports it after Optimize Swath Widths"),
        "yield_volume_dry_adjusted": col(adjusted, "bu/ac",
                                         "the same x the combine factor and / or the level factor of the grower's "
                                         "weight"),
        "yield_mass_dry": col(_find(fields, "yld_mass_d", "drymass", "dry_mass"), "lb/ac"),
        "yield_volume_wet": col(C.get("yield_wet"), "bu/ac"),
        "yield_mass_wet": col(C.get("wet_mass_lbac"), "lb/ac"),
        "moisture": col(C.get("moisture"), "%"),
        "swath_width": col(width, "ft", "the width after Optimize Swath Widths"
                           + ("; the logged width is in Wdth_Raw" if "Wdth_Raw" in (ch.get("added") or []) else "")),
        "distance": col(C.get("distance_ft"), "ft"),
        "speed": col(C.get("speed_mph"), "mph"),
        "heading": col(C.get("heading"), "degrees from north"),
        "duration": col(C.get("duration_s"), "s"),
        "time": col(C.get("time"), None),
        "elevation": col(_find(fields, "elevation", "elev"), "ft"),
        "harvester_id": col(C.get("machine"), None, "one value per combine"),
        "dataset": col(C.get("dataset"), None, "the task / log of the reading"),
    }
    return {k: v for k, v in out.items() if v is not None}


def _steps(R: dict) -> list:
    """One row per step in the order they ran: the clip (S1), the import check (S0), the swath step (S2), then for
    every group the moisture, speed and dry-yield passes with the mean, SD and limits they were made with. The step
    ids are the engine's (the Rem_By codes of the flagged file). The engine sets the readings outside the boundary
    aside before anything else, which is why the clip comes first; those readings are not in the flagged file."""
    M = R.get("sms3sigma") or {}
    clip = M.get("clip") or {}
    by = {r.get("filter"): r for r in R.get("cascade") or []}
    rows = []
    left = int(R.get("n_read") or 0)
    for sid in ("S1", "S0", "S2"):
        r = by.get(sid) or {}
        removed = int(r.get("removed") or 0)
        if sid == "S1":
            removed = int(clip.get("sms_clip_equivalent", removed) or 0)
        left -= removed
        row = {"step": sid, "name": STEP_NAMES[sid], "group": None, "attribute": None, "unit": None,
               "readings_before": left + removed, "mean": None, "sd": None, "lower": None, "upper": None,
               "removed": removed, "remaining": left}
        if sid == "S1":
            row["note"] = ("no boundary given: nothing clipped" if not R.get("aoi") else
                           "readings whose logged point is outside the boundary (a point on the edge is inside)")
        elif sid == "S0":
            row["note"] = "no position, no yield, or a dry yield below the minimum of the import"
        else:
            sw = M.get("swath") or {}
            row["narrowed"] = int(sw.get("n_narrowed") or 0)
            row["shortened"] = int(sw.get("n_shortened") or 0)
            row["note"] = "fully overlapped readings removed; partly overlapped ones narrowed, their per-area values scaled"
        rows.append(row)
    for s in M.get("steps") or []:
        sid = s.get("step")
        attr = s.get("attribute")
        name = ATTRIBUTE_STEP.get(attr, str(attr))
        if str(sid).startswith("S5"):
            name += f", pass {' abcdefghij'.index(str(sid)[2:3] or 'a')}"
        row = {"step": sid, "name": name, "group": s.get("group"), "attribute": attr, "unit": s.get("unit"),
               "readings_before": s.get("n_before"), "mean": s.get("mean"), "sd": s.get("sd"),
               "lower": s.get("lo"), "upper": s.get("hi"), "removed": int(s.get("removed") or 0),
               "remaining": s.get("n_after"), "sd_after": s.get("sd_after"), "sd_drop_pct": s.get("sd_drop_pct")}
        if s.get("no_value"):
            row["no_value"] = int(s["no_value"])
        if s.get("note"):
            row["note"] = s["note"]
        rows.append(row)
    return rows


def _combines(R: dict) -> list:
    """Per combine: readings into the cleaning, readings kept, mean dry yield of the kept ones (from the engine's
    harvest sessions, one per combine and day)."""
    by = {}
    for t in (R.get("sessions") or {}).get("table") or []:
        m = str(t.get("machine"))
        d = by.setdefault(m, {"combine": m, "readings": 0, "kept": 0, "days": [], "_y": 0.0})
        n, k = int(t.get("n_readings") or 0), int(t.get("n_kept") or 0)
        d["readings"] += n
        d["kept"] += k
        if t.get("day") and t["day"] not in d["days"]:
            d["days"].append(t["day"])
        if t.get("mean_dry_yield") is not None and k:
            d["_y"] += float(t["mean_dry_yield"]) * k
    out = []
    for d in by.values():
        y = d.pop("_y")
        d["mean_dry_yield"] = (y / d["kept"]) if d["kept"] else None
        out.append(d)
    return out


def _adjustment(R: dict, adj: dict | None) -> dict:
    """What was done after the cleaning, from the engine's record. combine_factor (None when it was not asked for):
    the method asked for and used, the reference combine, the factor applied to every combine (1 = none) and the
    engine's sentence on each. grower_weight (None when none was given): the weight as given and in lb and bu, the
    bushel it was converted with, the monitor's total it was compared with, the level factor (the weight / that
    total; after the combine factor when one was applied). `column` is the adjusted yield of the full clean file and
    `what` the engine's description of it as a product (adj = the engine's own account of the adjustment,
    engine/difm.py adjustment_of); both None when nothing was applied."""
    S = R.get("settings") or {}
    Ms, M = S.get("machines") or {}, R.get("machines") or {}
    factor = None
    if Ms.get("harmonise"):
        applied = {str(k): v for k, v in (M.get("factor_applied") or {}).items()}
        factor = {"method_asked": M.get("method_requested") or Ms.get("method"), "method_used": M.get("method_used"),
                  "reference": M.get("reference"), "factors": applied,
                  "applied": any(abs(float(f) - 1.0) > 1e-9 for f in applied.values() if f is not None),
                  "decision": {str(k): v for k, v in (M.get("decision") or {}).items()}}
    L = R.get("level") or {}
    weight = None
    if (S.get("scale_ticket") or {}).get("value") is not None:
        T = S["scale_ticket"]
        weight = {"value": T.get("value"), "unit": T.get("unit"), "basis": T.get("basis"),
                  "moisture_pct": T.get("moisture_pct"), "area_ac": L.get("ticket_area_ac"),
                  "area_from": L.get("ticket_area_source"), "weight_lb": L.get("ticket_lb"),
                  "weight_bu": L.get("ticket_bu"), "bushel_lb": L.get("test_weight_used"),
                  "bushel_from": L.get("test_weight_source"), "compared_on": L.get("comparison_basis"),
                  "monitor_total_bu": L.get("export_total_bu"), "level_factor": L.get("factor"),
                  "level_factor_after_combine_factor": L.get("factor_harmonised"),
                  "column": R.get("level_column_for_difm")}
    return {"combine_factor": factor, "grower_weight": weight, "column": (adj or {}).get("column"),
            "what": (adj or {}).get("what")}


def _difm_file(W: dict | None, which: str | None, asked: str | None, R: dict) -> dict | None:
    """The shapefile for DIFM as the engine's writer recorded it, and which yield it carries."""
    if not W:
        return None
    d = {"zip": os.path.basename(str(W.get("zip") or "")), "zip_bytes": W.get("zip_bytes"),
         "yield": which, "yield_asked": asked, "label": W.get("label"), "line": W.get("line"),
         "yield_column_of_the_clean_file": (W.get("columns") or {}).get("Yld_Vol_Dr"),
         "readings_adjusted": W.get("n_adjusted") if which == "adjusted" else None,
         "factor": W.get("factor"), "layout": W.get("layout"), "readings": W.get("n"),
         "fields": [f[0] for f in W.get("fields") or []], "filled_from_the_import": W.get("derived") or {},
         "date_from": W.get("days"), "readings_without_date": W.get("n_no_day"),
         "readings_without_yield": W.get("n_blank_yield"),
         "notes": list(W.get("notes") or []), "warnings": list(W.get("warnings") or [])}
    if asked == "adjusted" and which != "adjusted":
        d["notes"].insert(0, "the adjusted yield was asked for, but the cleaning applied no adjustment (summary "
                             "'adjustment' says why): this file carries the monitor's yield")
    return d


def build(R: dict, files: dict, dataset: dict, units: list, unit_note: str, decode_report: dict | None,
          import_record: dict | None, boundary: str | None, notes: list | None = None, difm: dict | None = None,
          difm_yield: str | None = None, difm_yield_asked: str | None = None) -> dict:
    """The summary dict of one run (module docstring) from the engine's result R and what clean_yield chose (difm =
    the record of the engine's writer of the shapefile for DIFM, difm_yield = the yield it carries)."""
    M = R.get("sms3sigma") or {}
    Sm = M.get("settings") or {}
    G = M.get("grouping") or {}
    fm = R.get("final_map") or {}
    A = R.get("aoi") or {}
    fields = []
    try:
        import pyogrio
        fields = list(pyogrio.read_info(files["difm_ready_shp"])["fields"])
    except Exception:                                   # the mapping is then taken from the engine's record alone
        fields = []
    rep = decode_report or {}
    tot = rep.get("totals") or {}
    imp = import_record or {}
    left = dict(imp.get("left_out") or {})
    S = {
        "package": {"name": __about__.NAME, "version": __about__.__version__, "engine": engine_source()},
        "input": {
            "dataset": dataset.get("label"), "kind": dataset.get("kind"), "format": dataset.get("format"),
            "units": units, "units_chosen_by": unit_note or None,
            "readings_decoded": rep.get("n_readings") if rep else None,
            "decoded_totals": {k: tot.get(k) for k in ("area_ac", "wet_lb", "bu_wet", "bu_dry")} if tot else None,
            "time_basis": rep.get("time_basis") if rep else None,
            "crop": imp.get("crop") or rep.get("crop_name"),
        },
        "import": {
            "as_sms": bool(imp),
            "settings": imp.get("settings"),
            "values": {k: (imp.get("resolved") or {}).get(k) for k in
                       ("product", "test_weight_lb_bu", "dry_moisture_pct", "min_yield", "max_yield",
                        "expand_dry_moisture")} if imp else None,
            "values_from": imp.get("sources"), "sms_product": imp.get("sms_product"),
            "readings_in": imp.get("n_all"), "readings_imported": imp.get("n_in"),
            "left_out": left, "notes": list(imp.get("notes") or []),
        },
        "boundary": ({"file": os.path.basename(boundary), "area_ac": A.get("area_ac"),
                      "polygons": A.get("n_polygons"), "trial_design": bool(A.get("plan")),
                      "plots": A.get("n_plots") or 0, "readings_outside": A.get("n_outside_aoi")}
                     if boundary else None),
        "cleaning": {
            "method": "SMS 3-sigma (OCED)", "method_revision": M.get("method_revision"),
            "order": ["clip", "swath"] + list((M.get("step_attributes") or {}).values()),
            "k_sd": Sm.get("k_sd"), "second_yield_pass_when_sd_drop_pct_above": Sm.get("sd_drop_pct"),
            "limits_rounded_to_decimals": Sm.get("round_decimals"),
            "group_by": G.get("mode"), "group_by_requested": G.get("requested"), "groups": list(G.get("groups") or []),
            "grouping_rule": G.get("rule"), "yield_passes": M.get("yield_passes"),
            "readings_in": R.get("n_read"), "readings_in_boundary": R.get("n_in"),
            "readings_kept": R.get("n_kept"),
            "removed_pct_of_readings_in_boundary": R.get("removed_pct"),
            "steps": _steps(R),
            "combines": _combines(R),
        },
        "result": {
            "yield_column": R.get("yield_column_for_difm"), "yield_unit": "bu/ac",
            "readings": R.get("n_difm_ready"), "area_ac": fm.get("area_ac"),
            "mean": fm.get("mean"), "average_area_weighted": fm.get("average_area_weighted"),
            "median": fm.get("median"), "sd": fm.get("sd"), "min": fm.get("min"), "max": fm.get("max"),
            "coordinate_system": R.get("output_crs"), "added_columns": list(R.get("difm_ready_added_columns") or []),
            "swath_changes": {k: (M.get("difm_ready_changes") or {}).get(k) for k in
                              ("n_narrowed_points", "n_scaled_points", "n_shortened_points", "width_column",
                               "scaled_columns")},
        },
        "adjustment": _adjustment(R, (difm or {}).get("adjustment")),
        "difm_file": _difm_file(difm, difm_yield, difm_yield_asked, R),
        "columns": _columns(R, fields, ((difm or {}).get("adjustment") or {}).get("column")),
        "files": {"difm_zip": (difm or {}).get("zip"),
                  "clean_shp": files.get("difm_ready_shp"), "clean_zip": files.get("difm_ready_zip"),
                  "flagged_shp": files.get("flagged_shp"), "flagged_zip": files.get("flagged_zip"),
                  "engine_json": files.get("json"), "output_dir": files.get("dir")},
        # the engine's warnings of the cleaning, then those of its writer of the shapefile for DIFM (a reading
        # without moisture or swath width, which DIFM is to have mapped; without a date or a yield; a zip too large)
        "warnings": list(R.get("warnings") or []) + [DIFM_WARNING + w for w in (difm or {}).get("warnings") or []],
        "checks": list(R.get("advice") or []),
        "notes": list(R.get("notes") or []) + [f"input: {n}" for n in (notes or [])],
    }
    return _plain(S)


def _n(v, d=0) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):,.{d}f}"
    except (TypeError, ValueError):
        return str(v)


def as_text(S: dict) -> str:
    """The summary as a short plain-text report (what the command line prints)."""
    I, P, C, Rz = S["input"], S["import"], S["cleaning"], S["result"]
    eng = S["package"]["engine"]
    L = [f"{S['package']['name']} {S['package']['version']} (OCED engine {eng.get('oced_version') or '?'}, commit "
         f"{str(eng.get('commit') or '?')[:12]})",
         f"input        {I['dataset']}"]
    if I["units"]:
        L.append(f"units        {len(I['units'])} read" + (f" ({I['units_chosen_by']})" if I["units_chosen_by"] else ""))
    if P["as_sms"]:
        L.append(f"import       {_n(P['readings_imported'])} of {_n(P['readings_in'])} readings; {P['settings']}")
        if P["left_out"]:
            L.append("             left out: " + ", ".join(f"{k} {_n(v)}" for k, v in P["left_out"].items()))
    B = S.get("boundary")
    L.append(f"boundary     {B['file']} ({_n(B['area_ac'], 2)} ac"
             + (f", trial design with {B['plots']} plots" if B["trial_design"] else "") + ")"
             if B else "boundary     none given: no clip")
    L.append(f"groups       {len(C['groups'])} ({C['group_by']}): {', '.join(map(str, C['groups']))}")
    L.append("")
    L.append(f"{'step':<5}{'what':<26}{'group':<22}{'mean':>9}{'SD':>9}{'lower':>9}{'upper':>9}{'removed':>9}{'left':>9}")
    for r in C["steps"]:
        L.append(f"{r['step']:<5}{r['name'][:25]:<26}{str(r['group'] or 'all')[:21]:<22}"
                 f"{_n(r['mean'], 2) if r['mean'] is not None else '':>9}{_n(r['sd'], 2) if r['sd'] is not None else '':>9}"
                 f"{_n(r['lower'], 2) if r['lower'] is not None else '':>9}"
                 f"{_n(r['upper'], 2) if r['upper'] is not None else '':>9}{_n(r['removed']):>9}{_n(r['remaining']):>9}")
    L.append("")
    L.append(f"readings     {_n(C['readings_in'])} imported, {_n(C['readings_in_boundary'])} in the boundary, "
             f"{_n(C['readings_kept'])} kept, {_n(Rz['readings'])} in the clean file")
    L.append(f"clean yield  {Rz['yield_column']}: mean {_n(Rz['mean'], 2)} bu/ac, area-weighted average "
             f"{_n(Rz['average_area_weighted'], 2)} bu/ac over {_n(Rz['area_ac'], 2)} ac")
    for c in C["combines"]:
        L.append(f"  combine {c['combine']}: {_n(c['kept'])} of {_n(c['readings'])} readings kept, mean "
                 f"{_n(c['mean_dry_yield'], 2)} bu/ac")
    A = S.get("adjustment") or {}
    F = A.get("combine_factor")
    if F:
        L.append("combine factor  " + (", ".join(f"{m} x {_n(f, 4)}" for m, f in F["factors"].items())
                                       if F["applied"] else "asked for, none applied")
                 + f" (reference {F['reference']})")
    W = A.get("grower_weight")
    if W:
        L.append(f"grower's weight {_n(W['value'], 2)} {W['unit']} ({W['basis']} basis) = {_n(W['weight_bu'], 1)} bu "
                 f"against the monitor's {_n(W['monitor_total_bu'], 1)} bu: level factor {_n(W['level_factor'], 4)}")
    D = S.get("difm_file")
    if D:
        L.append(f"for DIFM     {S['files']['difm_zip']}")
        L.append(f"             {D['yield']} yield ({D['yield_column_of_the_clean_file']} of the clean file)")
        for note in D["notes"][:1] if D["yield_asked"] == "adjusted" and D["yield"] != "adjusted" else []:
            L.append(f"             {note}")
    L.append(f"clean file   {S['files']['clean_zip']}")
    for w in S["warnings"]:
        L.append(f"WARNING      {w}")
    for w in S["checks"]:
        L.append(f"CHECK        {w}")
    return "\n".join(L)
