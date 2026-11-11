# -*- coding: utf-8 -*-
"""oced-clean: OCED's raw-data yield cleaning as a library and a command line, without the app.

    import oced_clean
    result = oced_clean.clean_yield(["upload.zip"], "boundary.geojson", "work/job_123")
    result.difm_zip, result.difm_yield, result.summary

Raw yield-monitor data (a Case IH / New Holland .cn1 card, a John Deere GreenStar 3 card, John Deere Gen4 .jdl logs,
a John Deere Operations Center export) and the field boundary go in; the clean readings as a shapefile for DIFM and a
cleaning summary come out. The data is imported as Ag Leader SMS imports it and cleaned with the SMS 3-sigma
sequence: clip to the boundary, Optimize Swath Widths, moisture, speed, dry yield (mean +/- 3 SD recomputed before
every step, one group per combine), and a second dry-yield pass when the first one cut the SD by more than 25 %.
On request the combine factor and the grower's weight follow, and the shapefile for DIFM carries the adjusted yield.

The rules are OCED's (Olds College Extract Data): the engine in oced_clean/_engine is copied from the OCED repository
by tools/sync_engine.py and is not edited here.
"""
from .__about__ import NAME, __version__
from .api import (CleanResult, CleaningError, SeveralDatasetsError, clean_yield, inspect_upload)
from .summary import as_text as summary_text
from .summary import engine_source

__all__ = ["NAME", "__version__", "CleanResult", "CleaningError", "SeveralDatasetsError", "clean_yield",
           "inspect_upload", "summary_text", "engine_source"]
