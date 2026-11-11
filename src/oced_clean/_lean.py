# -*- coding: utf-8 -*-
"""What this package leaves out of an OCED cleaning run: the figures and the PDF report.

OCED's cleaning module draws its figures (matplotlib) and builds a PDF report (reportlab, PyMuPDF) at the end of
every run. A server that only needs the clean shapefile and a summary should not carry those libraries, so:

  * the figure functions of the copied cleaning module are replaced here by a function that draws nothing;
  * the report module is a stub written by tools/sync_engine.py (oced_clean/_engine/report/clean_report.py).

Nothing that decides which reading is kept, or what the clean file holds, is touched: the figures and the report are
made after the clean shapefile is written. tests/test_lean.py checks that a run imports none of those libraries and
that the clean file is the one the copied engine writes through its own command line.
"""
from __future__ import annotations

from ._engine.clean import yield_clean as _yc

_FIGURE_FUNCTIONS = ("figures", "figures_sms")
_installed = False


def _no_figures(*args, **kwargs) -> dict:
    """Stands in for yield_clean.figures / figures_sms: no figure is drawn, no file written."""
    return {}


def install() -> None:
    """Replace the figure functions of the copied cleaning module (once). Raises ImportError when the engine no longer
    has them under these names, so a change in OCED is noticed at import instead of bringing matplotlib back."""
    global _installed
    if _installed:
        return
    for name in _FIGURE_FUNCTIONS:
        if not callable(getattr(_yc, name, None)):
            raise ImportError(f"oced_clean: the copied OCED engine has no yield_clean.{name}(); the engine changed, "
                              "review oced_clean/_lean.py")
        setattr(_yc, name, _no_figures)
    _installed = True


# engine log lines about what this package does not make
SILENT_LOG_LINES = ("figures ...", "report ...")


def keep_log_line(message: str) -> bool:
    """False for the engine's progress lines of the figures and the report."""
    return str(message).strip() not in SILENT_LOG_LINES
