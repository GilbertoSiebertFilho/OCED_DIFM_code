# -*- coding: utf-8 -*-
"""Stand-in for OCED's engine/report/clean_report.py (the PDF cleaning report), written by tools/sync_engine.py.

This package returns the clean shapefile and a JSON summary; it does not build the PDF report, so reportlab, PyMuPDF
and matplotlib are not needed. The cleaning module calls make_report() at the end of every run: here it does nothing.
"""


def make_report(R, path, *args, **kwargs):
    """No PDF report in this package (see the module docstring). Returns None and writes nothing."""
    return None
