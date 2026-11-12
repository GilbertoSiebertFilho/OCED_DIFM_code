# -*- coding: utf-8 -*-
"""Stand-in for OCED's engine/report/applied_report.py (the PDF report of a planter / sprayer cleaning), written by
tools/sync_engine.py.

This package cleans harvests only and builds no PDF report. The module is here because the engine's planter /
sprayer cleaning, which is copied with the route but never run, imports it.
"""


def make_report(R, path, *args, **kwargs):
    """No PDF report in this package (see the module docstring). Returns None and writes nothing."""
    return None
