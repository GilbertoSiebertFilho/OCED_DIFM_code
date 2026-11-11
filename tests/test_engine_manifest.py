# -*- coding: utf-8 -*-
"""The engine in this package is OCED's, as copied: every copied file still has the hash the sync recorded (nobody
edited it here), the manifest names the OCED commit and version, and no copied module imports the top-level package
`engine` (which could be another program's on the server)."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import re

import oced_clean

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(os.path.dirname(os.path.abspath(oced_clean.__file__)), "_engine")


def _sync_tool():
    spec = importlib.util.spec_from_file_location("sync_engine", os.path.join(ROOT, "tools", "sync_engine.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_copied_files_match_the_manifest(capsys):
    assert _sync_tool().check() == 0, capsys.readouterr().out


def test_manifest_names_the_oced_commit():
    with open(os.path.join(ENGINE, "ENGINE_SOURCE.json"), encoding="utf-8") as f:
        m = json.load(f)
    assert re.fullmatch(r"[0-9a-f]{40}", m["commit"])
    assert re.fullmatch(r"\d+\.\d+\.\d+", m["oced_version"])
    assert m["package"] == "oced_clean._engine"
    e = oced_clean.engine_source()
    assert (e["commit"], e["oced_version"]) == (m["commit"], m["oced_version"])
    # every file from OCED is byte for byte the committed one unless the manifest lists a change for it
    for rel, info in m["files"].items():
        if info["source"] and not info["changes"]:
            assert info["sha256"] == info["sha256_source"], rel


def test_no_copied_module_imports_a_top_level_engine_package():
    bad = []
    for dp, dn, fn in os.walk(ENGINE):
        dn[:] = [d for d in dn if d != "__pycache__"]
        for f in fn:
            if not f.endswith(".py"):
                continue
            with open(os.path.join(dp, f), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module or ""]
                bad += [f"{f}:{node.lineno} {n}" for n in names if n == "engine" or n.startswith("engine.")]
    assert not bad, bad
