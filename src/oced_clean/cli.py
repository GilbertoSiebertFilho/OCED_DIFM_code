# -*- coding: utf-8 -*-
"""The command line of oced-clean.

    oced-clean clean   <upload ...> --boundary FILE --out DIR [options]     raw data -> the clean shapefile
    oced-clean inspect <upload ...> --out DIR [--boundary FILE]             what an upload holds (nothing is cleaned)
    oced-clean version                                                      this package and the OCED engine it carries

With --json the result is ONE JSON document on standard output and every progress line goes to standard error, so
another program (R: system2 + jsonlite, a shell script, a job runner) can read the result without parsing text.

Exit codes: 0 done; 2 a problem with the input that the user can fix (the message says what; with --json it is
{"ok": false, "error": ...}); 3 an unexpected error (the traceback is written to error_<time>.log in --out).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import traceback

from . import __about__
from . import api
from . import summary as _summary


def _feature(text):
    """--boundary-feature: '2' -> 2, '1,3' -> [1, 3]."""
    if text is None:
        return None
    try:
        items = [int(x) for x in str(text).split(",") if x.strip()]
    except ValueError:
        raise api.CleaningError(f"--boundary-feature takes a polygon number or several separated by commas, not {text!r}")
    return items[0] if len(items) == 1 else (items or None)


def _units(text):
    """--units: 'all' -> 'all', 'a,b' -> ['a', 'b']."""
    if text is None:
        return None
    if text.strip().lower() == "all":
        return "all"
    return [x.strip() for x in text.split(",") if x.strip()]


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="oced-clean",
        description="Raw yield-monitor data + the field boundary -> the clean shapefile, with OCED's rules: import as "
                    "Ag Leader SMS, then the SMS 3-sigma sequence (clip, Optimize Swath Widths, moisture, speed, dry "
                    "yield; mean +/- 3 SD recomputed before every step; one group per combine).")
    sub = ap.add_subparsers(dest="command", required=True)

    c = sub.add_parser("clean", help="clean an upload and write the clean shapefile",
                       description="Clean an upload. The clean shapefile (zipped) and the summary land in "
                                   "<out>/output/.")
    c.add_argument("inputs", nargs="+", help="the uploaded file(s), folder(s) or zip(s): a .cn1 card, a GreenStar 3 "
                   "card, a JD-Data folder of .jdl logs, an Operations Center export, or a yield point shapefile")
    b = c.add_mutually_exclusive_group(required=True)
    b.add_argument("--boundary", help="the field boundary or trial design: a polygon file (.shp, .zip, .geojson, "
                   ".gpkg, .kml, .kmz)")
    b.add_argument("--no-boundary", action="store_true", help="clean without a boundary: no clip (say so on purpose)")
    c.add_argument("--out", required=True, help="the work folder of this run (input/, decoded/, output/ inside)")
    c.add_argument("--boundary-feature", default=None, metavar="N[,M]", help="the boundary file holds several field "
                   "boundaries: the number of the field's polygon (1-based), or several separated by commas")
    c.add_argument("--candidate", type=int, default=None, metavar="N", help="the upload holds several datasets: the "
                   "number of the one to clean (1-based; 'inspect' lists them)")
    c.add_argument("--units", default=None, help="raw data: 'all', or unit ids separated by commas (default: the units "
                   "inside the boundary)")
    c.add_argument("--field", default=None, help="raw data: keep the units of this monitor field name")
    c.add_argument("--year", type=int, default=None, help="raw data: keep the units of this season")
    c.add_argument("--allow-mixed", action="store_true", help="raw data: allow units of several fields, crops or "
                   "seasons in one file (refused by default)")
    c.add_argument("--product", default=None, help="import as SMS: the crop (wheat, durum, barley, canola, oats, peas, "
                   "lentils, flax, rye, corn, soybeans, or an SMS product name); default: the crop of the data")
    c.add_argument("--weight-to-volume", type=float, default=None, metavar="LB", help="import as SMS: lb/bu (default: "
                   "what SMS gives this source)")
    c.add_argument("--dry-moisture", type=float, default=None, metavar="PCT", help="import as SMS: dry moisture, %%")
    c.add_argument("--min-yield", type=float, default=None, metavar="BU", help="import as SMS: minimum yield volume, "
                   "bu/ac (0 = none)")
    c.add_argument("--max-yield", type=float, default=None, metavar="BU", help="import as SMS: maximum yield volume, bu/ac")
    c.add_argument("--expand-dry-moisture", action="store_true", help="import as SMS: Expand Dry Moisture on (off by "
                   "default, as in SMS)")
    c.add_argument("--group-by", choices=list(api.GROUP_MODES), default=None, help="where the mean and SD are "
                   "computed: auto (default) = one group per combine; task = one group per task; machine; field")
    c.add_argument("--tag", default=None, help="name prefix of the output files")
    c.add_argument("--cleanup", action="store_true", help="remove input/, decoded/ and sections/ from --out at the end")
    c.add_argument("--json", action="store_true", help="print the result as one JSON document on standard output")
    c.add_argument("--quiet", action="store_true", help="no progress lines")

    i = sub.add_parser("inspect", help="list what an upload holds (nothing is cleaned)")
    i.add_argument("inputs", nargs="+", help="the uploaded file(s), folder(s) or zip(s)")
    i.add_argument("--out", required=True, help="a folder for the unpacked files")
    i.add_argument("--boundary", default=None, help="with it, every unit of a raw card gets the share of its readings "
                   "inside the boundary (slower: every unit is decoded once)")
    i.add_argument("--boundary-feature", default=None, metavar="N[,M]")
    i.add_argument("--json", action="store_true", help="print the result as one JSON document on standard output")
    i.add_argument("--quiet", action="store_true", help="no progress lines")

    sub.add_parser("version", help="this package's version and the OCED engine it carries")
    return ap


def _log(a):
    if getattr(a, "quiet", False):
        return lambda m: None
    stream = sys.stderr if getattr(a, "json", False) else sys.stdout
    return lambda m: print(m, file=stream, flush=True)


def _run_clean(a) -> int:
    r = api.clean_yield(
        a.inputs, None if a.no_boundary else a.boundary, a.out, boundary_feature=_feature(a.boundary_feature),
        candidate=a.candidate, units=_units(a.units), field=a.field, year=a.year, allow_mixed=a.allow_mixed,
        product=a.product, weight_to_volume=a.weight_to_volume, dry_moisture=a.dry_moisture, min_yield=a.min_yield,
        max_yield=a.max_yield, expand_dry_moisture=a.expand_dry_moisture, group_by=a.group_by, tag=a.tag,
        cleanup=a.cleanup, log=_log(a))
    if a.json:
        print(json.dumps({"ok": True, **r.to_dict()}, indent=1))
    else:
        print("\n" + r.text())
        print(f"summary      {r.summary_json}")
    return 0


def _run_inspect(a) -> int:
    d = api.inspect_upload(a.inputs, a.out, a.boundary, boundary_feature=_feature(a.boundary_feature), log=_log(a))
    if a.json:
        print(json.dumps({"ok": True, **d}, indent=1))
        return 0
    if not d["datasets"]:
        print(d["message"])
    for ds in d["datasets"]:
        print(f"[{ds['index']}] {ds['label']}" + ("" if ds["harvest"] is not False else "  (not a harvest file)"))
        for u in ds["units"]:
            share = u["share_inside_boundary"]
            print(f"      {'*' if u['latest_season'] else ' '} {u['id']}"
                  + (f"  [{100 * share:.0f} % inside the boundary]" if share is not None else "")
                  + f"\n          {u['label']}")
    for n in d["notes"]:
        print("note: " + n)
    return 0


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    a = parser().parse_args(argv)
    if a.command == "version":
        e = _summary.engine_source()
        print(f"{__about__.NAME} {__about__.__version__}; OCED engine {e.get('oced_version') or '?'} "
              f"(commit {str(e.get('commit') or '?')[:12]})")
        return 0
    try:
        return _run_clean(a) if a.command == "clean" else _run_inspect(a)
    except ValueError as e:                              # the user can fix it: one line, no traceback
        if getattr(a, "json", False):
            body = {"ok": False, "error": str(e), "error_type": type(e).__name__}
            if isinstance(e, api.SeveralDatasetsError):
                body["datasets"] = e.datasets
            print(json.dumps(body, indent=1))
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:                               # unexpected: one line + the traceback in a log file
        log = None
        try:
            os.makedirs(os.path.abspath(a.out), exist_ok=True)
            log = os.path.join(os.path.abspath(a.out), f"error_{_dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.log")
            with open(log, "w", encoding="utf-8") as f:
                f.write(" ".join(sys.argv) + "\n\n" + traceback.format_exc())
        except OSError:
            log = None
        if getattr(a, "json", False):
            print(json.dumps({"ok": False, "error": f"unexpected {type(e).__name__}: {e}", "error_type": "unexpected",
                              "traceback_file": log}, indent=1))
        print(f"error: unexpected {type(e).__name__}: {e}" + (f" (traceback in {log})" if log else ""), file=sys.stderr)
        return 3


if __name__ == "__main__":
    sys.exit(main())
