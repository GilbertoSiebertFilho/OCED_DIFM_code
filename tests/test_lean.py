# -*- coding: utf-8 -*-
"""The package stays lean: a whole cleaning run loads no plotting, PDF, image or app library, and writes no figure and
no PDF. (A fresh Python process, so that what other tests imported does not count.)"""
from __future__ import annotations

import json
import subprocess
import sys

from tests.test_cli import _env

HEAVY = ("matplotlib", "reportlab", "fitz", "pymupdf", "skimage", "streamlit", "pydeck")

SCRIPT = r"""
import json, os, sys
import oced_clean
r = oced_clean.clean_yield([sys.argv[1]], sys.argv[2], sys.argv[3], log=lambda m: None)
heavy = sorted({m.split(".")[0] for m in sys.modules} & set(sys.argv[4].split(",")))
print(json.dumps({"heavy": heavy, "files": sorted(os.listdir(os.path.dirname(r.clean_shp)))}))
"""


def test_a_run_loads_no_plotting_or_pdf_library_and_writes_none_of_their_files(field, tmp_path):
    p = subprocess.run([sys.executable, "-c", SCRIPT, field["shp"], field["boundary"], str(tmp_path / "run"),
                        ",".join(HEAVY)], capture_output=True, text=True, encoding="utf-8", env=_env())
    assert p.returncode == 0, p.stderr
    d = json.loads(p.stdout.strip().splitlines()[-1])
    assert d["heavy"] == []
    assert not [f for f in d["files"] if f.lower().endswith((".pdf", ".png", ".jpg", ".svg"))]
    kinds = {f.rsplit("_", 1)[-1] for f in d["files"]}
    assert {"clean.shp", "clean.zip", "flagged.shp", "cleaning.json", "summary.json"} <= kinds
