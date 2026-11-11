# -*- coding: utf-8 -*-
"""Equivalence check on your own data: this package against OCED's own engine, same input, same settings.

    python tools/check_equivalence.py --oced <path to an OCED clone> --cases <cases.json> --work <folder>
                                      [--only KEY[,KEY]] [--keep]

For every case the raw data is cleaned twice, each in its own Python process:

    OCED       python -m engine.run <inputs> --out <work>/<KEY>/oced --aoi <boundary> ...   (run inside the OCED clone,
               with OCED's unmodified engine, figures and PDF report included: it needs OCED's requirements.txt)
    this one   python -m oced_clean clean <inputs> --boundary <boundary> --out <work>/<KEY>/pkg ...

and the clean shapefile and the flagged shapefile of the two runs are compared byte for byte (.shp, .shx, .dbf, .prj,
.cpg). A case passes only when all of them are identical. The report (counts per case, pass / fail, the first
difference of a failing file) is written to <work>/equivalence_report.json and printed.

The OCED clone must be at the commit this package's engine was copied from (_engine/ENGINE_SOURCE.json); the check
refuses another one unless --any-commit is given.

cases.json holds YOUR data's paths, so it stays on your computer (keep it and --work outside this repository, or in
the git-ignored folder local/):

    {"A": {"inputs": ["D:/raw/a/card.zip"], "boundary": "D:/raw/a/boundary.shp"},
     "B": {"inputs": ["D:/raw/b/JD-Data"], "boundary": "D:/raw/b/boundary.geojson", "product": "barley",
           "group_by": "task"}}

Optional keys of a case, each passed to both programs: boundary_feature, candidate, units ("all" or a list of ids),
field, year, allow_mixed, product, weight_to_volume, dry_moisture, min_yield, max_yield, expand_dry_moisture,
group_by, tag. "boundary": null runs both without a boundary.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PARTS = (".shp", ".shx", ".dbf", ".prj", ".cpg")
# case key -> (OCED command line option, oced-clean command line option); True = a flag without a value
OPTIONS = {
    "boundary_feature": ("--aoi-feature", "--boundary-feature"),
    "candidate": ("--candidate", "--candidate"),
    "units": ("--units", "--units"),
    "field": ("--field", "--field"),
    "year": ("--year", "--year"),
    "product": ("--product", "--product"),
    "weight_to_volume": ("--weight-to-volume", "--weight-to-volume"),
    "dry_moisture": ("--dry-moisture", "--dry-moisture"),
    "min_yield": ("--min-yield", "--min-yield"),
    "max_yield": ("--max-yield", "--max-yield"),
    "group_by": ("--sms-group", "--group-by"),
    "tag": ("--tag", "--tag"),
}
FLAGS = {"allow_mixed": ("--allow-mixed", "--allow-mixed"),
         "expand_dry_moisture": ("--expand-dry-moisture", "--expand-dry-moisture")}


def sha(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def first_difference(a: str, b: str) -> str:
    with open(a, "rb") as fa, open(b, "rb") as fb:
        x, y = fa.read(), fb.read()
    if len(x) != len(y):
        return f"sizes {len(x):,} and {len(y):,} bytes"
    i = next(k for k in range(len(x)) if x[k] != y[k])
    return f"first different byte at offset {i:,} of {len(x):,}"


def values(case: dict, which: int) -> list:
    out = []
    for key, names in OPTIONS.items():
        v = case.get(key)
        if v is None:
            continue
        if isinstance(v, (list, tuple)):
            v = ",".join(str(x) for x in v)
        out += [names[which], str(v)]
    for key, names in FLAGS.items():
        if case.get(key):
            out.append(names[which])
    return out


def run(cmd, cwd, env, log_path) -> tuple[int, float]:
    t0 = time.time()
    with open(log_path, "w", encoding="utf-8") as log:
        p = subprocess.run(cmd, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
    return p.returncode, time.time() - t0


def one_output(folder: str, kind: str):
    hits = sorted(glob.glob(os.path.join(folder, "output*", f"*_{kind}.shp")))
    return hits[-1] if hits else None


def check_case(key: str, case: dict, oced: str, work: str, keep: bool) -> dict:
    d = os.path.join(work, key)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d)
    inputs = [os.path.abspath(p) for p in case["inputs"]]
    boundary = os.path.abspath(case["boundary"]) if case.get("boundary") else None
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env_oced = dict(env, PYTHONPATH=oced)
    env_pkg = dict(env, PYTHONPATH=os.path.join(ROOT, "src"))
    oced_out, pkg_out = os.path.join(d, "oced"), os.path.join(d, "pkg")
    rc1, t1 = run([sys.executable, "-m", "engine.run", *inputs, "--out", oced_out,
                   *(["--aoi", boundary] if boundary else []), *values(case, 0)],
                  oced, env_oced, os.path.join(d, "oced.log"))
    rc2, t2 = run([sys.executable, "-m", "oced_clean", "clean", *inputs, "--out", pkg_out,
                   *(["--boundary", boundary] if boundary else ["--no-boundary"]), *values(case, 1)],
                  ROOT, env_pkg, os.path.join(d, "pkg.log"))
    res = {"case": key, "oced_exit": rc1, "package_exit": rc2, "oced_seconds": round(t1, 1),
           "package_seconds": round(t2, 1), "files": {}, "identical": False}
    if rc1 != 0 or rc2 != 0:
        res["note"] = ("both refused the input (same exit code): see the two logs" if rc1 == rc2 else
                       "one of the two runs failed: see the two logs")
        res["identical"] = rc1 == rc2 == 2            # both refuse the same input: equivalent, nothing to compare
        return res
    same = True
    for kind in ("clean", "flagged"):
        a, b = one_output(oced_out, kind), one_output(pkg_out, kind)
        if not a or not b:
            res["files"][kind] = "missing"
            same = False
            continue
        if os.path.basename(a) != os.path.basename(b):
            res["files"][kind + "_name"] = f"{os.path.basename(a)} vs {os.path.basename(b)}"
            same = False
        for ext in PARTS:
            pa, pb = os.path.splitext(a)[0] + ext, os.path.splitext(b)[0] + ext
            ok = os.path.exists(pa) and os.path.exists(pb) and sha(pa) == sha(pb)
            res["files"][kind + ext] = "identical" if ok else (
                "missing" if not (os.path.exists(pa) and os.path.exists(pb)) else first_difference(pa, pb))
            same = same and ok
    try:
        sj = glob.glob(os.path.join(pkg_out, "output*", "*_summary.json"))[-1]
        with open(sj, encoding="utf-8") as f:
            S = json.load(f)
        res.update({"format": S["input"]["format"], "groups": len(S["cleaning"]["groups"]),
                    "readings_imported": S["import"]["readings_imported"],
                    "readings_kept": S["cleaning"]["readings_kept"], "clean_file_readings": S["result"]["readings"]})
    except (IndexError, OSError, KeyError, ValueError):
        pass
    res["identical"] = same
    if same and not keep:
        for sub in (oced_out, pkg_out):               # the two runs hold the unpacked upload twice
            for name in os.listdir(sub):
                if name.startswith("input") or name in ("decoded", "sections"):
                    shutil.rmtree(os.path.join(sub, name), ignore_errors=True)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="This package against OCED's own engine on your data, byte for byte.")
    ap.add_argument("--oced", required=True, help="a clone of the OCED repository (unmodified)")
    ap.add_argument("--cases", required=True, help="cases.json (see the module docstring); keep it out of the repository")
    ap.add_argument("--work", required=True, help="folder for the runs and the report; keep it out of the repository")
    ap.add_argument("--only", default=None, help="case keys to run, separated by commas (default: all)")
    ap.add_argument("--keep", action="store_true", help="keep the unpacked inputs and decoded files of passing cases")
    ap.add_argument("--any-commit", action="store_true", help="do not insist that the OCED clone is at the commit the "
                    "engine of this package was copied from")
    a = ap.parse_args(argv)
    oced = os.path.abspath(a.oced)
    with open(os.path.join(ROOT, "src", "oced_clean", "_engine", "ENGINE_SOURCE.json"), encoding="utf-8") as f:
        man = json.load(f)
    head = subprocess.run(["git", "-C", oced, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", oced, "status", "--porcelain", "--", "engine"], capture_output=True,
                           text=True).stdout.strip()
    if (head != man["commit"] or dirty) and not a.any_commit:
        print(f"the OCED clone is at {head[:12] or '?'}" + (" with local changes under engine/" if dirty else "")
              + f"; this package's engine was copied from {man['commit'][:12]} (OCED {man.get('oced_version')}). "
              "Check out that commit (or pass --any-commit).", file=sys.stderr)
        return 2
    with open(a.cases, encoding="utf-8") as f:
        cases = json.load(f)
    only = [k.strip() for k in a.only.split(",")] if a.only else list(cases)
    work = os.path.abspath(a.work)
    os.makedirs(work, exist_ok=True)
    results = []
    for key in only:
        print(f"{key} ...", flush=True)
        r = check_case(key, cases[key], oced, work, a.keep)
        results.append(r)
        print(f"  {'IDENTICAL' if r['identical'] else 'DIFFERENT'}"
              + (f"  {r.get('format')}, {r.get('groups')} group(s), {r.get('clean_file_readings'):,} readings in the "
                 "clean file" if r.get("clean_file_readings") is not None else "")
              + (f"  ({r['note']})" if r.get("note") else "")
              + f"  [OCED {r['oced_seconds']} s, package {r['package_seconds']} s]", flush=True)
        for name, v in r["files"].items():
            if v != "identical":
                print(f"    {name}: {v}")
    report = {"oced_commit": head, "oced_version": man.get("oced_version"), "python": sys.version.split()[0],
              "cases": results, "identical": sum(r["identical"] for r in results), "total": len(results)}
    with open(os.path.join(work, "equivalence_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    print(f"\n{report['identical']} of {report['total']} case(s) identical to OCED {man.get('oced_version')} "
          f"({head[:12]}); report: {os.path.join(work, 'equivalence_report.json')}")
    return 0 if report["identical"] == report["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
