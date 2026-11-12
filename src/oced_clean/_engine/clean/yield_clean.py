# -*- coding: utf-8 -*-
"""Settings-driven cleaning of a yield point shapefile, producing the clean file.

    python -m engine.clean.yield_clean <input.shp> --out <dir> [--settings settings.json]

    from oced_clean._engine.clean import yield_clean
    R = yield_clean.run("harvest.shp", "out", settings={"scale_ticket_bu": 9000})

This is the cleaning of the earlier DIFM-ready engine with every algorithm kept
identical, driven by a SETTINGS dict instead of a trial config, so that the app can clean any yield point export: an
Ag Leader SMS / John Deere Ops Center export, or the standard-schema shapefile written by a raw-monitor decoder
(engine/formats/common.py, ``write_standard``). With the v2.3 overlap model (settings LEGACY_V23) an SMS export gives
exactly the old engine's result (the same kept points, cascade, F8 threshold and flow-delay shift) and, with the scale
ticket, a clean .dbf byte-identical to the old engine's.

v2.4 (2026-10-29, from the pre-registered overlap tests and their reviews; docs/METHOD.md): the F8 footprint
is the strip the header swept since the previous reading (swept_strips; the v2.3 chord rectangle counted a wedge of the
reading's own predecessor as already cut in every curve), placed Y_Offset to the right of travel; the F8 dose-response
compares every record of the pass interiors with local no-overlap controls of the same combine at the same distance
from the harvested edge, and the automatic threshold never goes below 5 %; the maps draw the swept strips. Applying the
flow delay before F7 and F8 (lag_stage "before_f7") failed its pre-registered test and stays an option. LEGACY_V23 (or
the switches filters.F8.footprint / y_offset / reference / min_threshold) reproduces v2.3 record for record.

Principle (the OCCD method, settings["method"] = "occd", the default): the cleaning works on the export as it
is, never on a re-computed yield. It only (1) removes points, (2) moves the geometry when a residual flow delay
is proven, and (3) adds columns. The kept records of the original .shp/.dbf are copied byte for byte (same field
definitions), so the clean file differs from the original export only by the removed points and the added columns,
and the yield column to map is the same with and without cleaning.

settings["method"]: "occd" (above; outputs and bytes unchanged from v2.2) | "sms3sigma" (the Ag Leader SMS 3-sigma
sequence S0 import check, S1 clip, S2 Optimize Swath Widths, S3 moisture, S4 speed (or speed first:
sms3sigma.filter_order), S5 dry-yield mean +/- 3 SD per group, one group per combine;
engine/clean/sms3sigma.py and sms_swath.py; no cascade F0-F11, no flow-delay scan; with several combines the
combine factor of machines.method follows the SMS sequence, as in the OCCD run, an OCCD step after the cleaning) |
"both" (both methods and a comparison in out_dir/compare; engine/clean/compare.py, run_both). The sms3sigma
clean file is written as SMS exports it after the swath step and so departs from the principle above on
purpose (docs/METHOD.md section 10; write_points `patch`): the width column holds the new width of the narrowed
points, Distance / Speed / distance per fuel of the shortened points are rewritten, the per-area columns are
multiplied by Swath_Scl = (W x D) / (W' x D') (Prod_ac_h_ and Fuel_Eff__ divided by it), Y_Offset is written 0 and
every point sits where SMS moved it; every other cell is byte for byte the export's, and Wdth_Raw / Swath_Scl are
added. Its files are named <tag>_sms3s_*, its figures are the final map, map, cascade (the S0-S5 steps), limits
(fig_sms_limits.png) and sessions (with several combines the combine and seam maps); the flagged file adds Wdth_Opt,
Dist_Opt, Swath_Scl and Rem_By = S0 .. S5a, S5b.

Input: a POINT SHAPEFILE (.shp + .shx + .dbf; the app unpacks zips before calling run). Units are the SMS / Ops
Center ones: yield bu/ac, moisture %, speed mph, distance ft, swath width ft, heading deg (clockwise from north),
duration s, wet mass lb/ac. Columns are found by ``detect_columns`` (SMS / Ops Center names, case-insensitive, plus a
few common alternates); settings["cols"] overrides any key. Required: yield, speed_mph, heading, distance_ft or
duration_s, and width_ft or settings["harvester_width_ft"]. Without moisture F1 is switched off; without wet mass and
without (wet or dry yield + test_weight_lb_bu) F9 is switched off; both with a warning.

Steps
  0. field of interest (settings aoi.file, engine/clean/aoi.py; a pre-step, not a filter). A grower's export often holds
     more than the field of the trial. Records farther than aoi.buffer_m (50 m) from the AOI are set aside BEFORE the
     cascade and logged in R['pre_steps'] ({"step": "AOI", "criterion": "outside the field of interest + 50 m",
     "removed": n}); the buffer keeps the headland context that the pass structure, the overlap raster, the lag scan
     and F7 need. With aoi.clip_output the clean file keeps only the kept points inside the AOI itself (0 m); the
     flagged file keeps every processed point with In_AOI. A file with > 1 polygon is a trial plan when it looks like
     one (a rate or plot column, a typed headland, or >= 4 polygons of similar size; engine/clean/aoi.py): Plot_Id
     (C 16) and Tgt_Rate (N 12.3) are added by point-in-polygon, the F8 dose-response is measured on the in-plot
     records only (interior ground; R['f8']['dose_population']) and the machine pairs compare the same plot. Otherwise
     it holds several field boundaries and the run refuses to union them until aoi.feature names the field of interest
     (one polygon, or a list of polygons that together form the field: their union; never a Plot_Id from a boundary
     file). The old 'boundary' setting is an alias for the v1 behaviour: level
     diagnostics inside the polygon(s), nothing dropped, clipped or tagged, whatever the number of polygons.
     settings crop (a key of engine.formats.common.CROPS) sets the F1 window (F1_CROP_RANGES, house conventions,
     only when the user did not set F1 min/max) and the fallback standard moisture.
  1. chronological order and passes. Order: per-record timestamps when the 'time' column parses to timestamps WITH a
     time of day (rows sorted by machine, then time; the overlap raster is burnt in time order across machines);
     otherwise the dataset start time 'YY/MM/DD-HH:MM:SS' found in the dataset name, then the record id (the SMS
     'Time' is a date only, so this is the SMS path). When the record id repeats inside one dataset (an Ops Center
     export with several combines in one task: the id restarts for every combine's log), every restart in file order
     starts a new log, sorted after the previous one. Passes break at a dataset / log / machine change, a GNSS step
     > pass_break.max_step_m and a heading change > pass_break.max_heading_change_deg.
  2. the house cascade F0-F11 + F5b (FILTER_INFO: rule and literature source of each). Any filter can be switched
     off: it is still logged with removed = 0 and its criterion marked '(off)'. F0 also removes the records without
     a plausible position (no geometry, (0,0), outside the lon/lat ranges, > POS_MAX_KM = 50 km from the median
     position); F1 also removes a missing moisture (its window comes from the crop, F1_CROP_RANGES; a user bound
     overrides only the bound given). F8 measures the share of each reading's footprint over ground already cut:
     footprint "swept" (v2.4) = the strip between the header line at the previous reading of the pass and the one at
     this reading (swept_strips), centred Y_Offset to the right of travel (filters.F8.y_offset), burnt in time order
     on a 0.25 m raster across machines; "chord" = the v2.3 rectangle. The F8 overlap threshold is "auto" (the field's
     own dose-response) or a fixed fraction. Reference "local" (v2.4): every record of the pass interiors (>= 15
     records alive after F7 on both sides) against its local no-overlap controls (local_control_ratio: same combine
     by combine_key - the machine column, else the sources in the dataset names, the SMS serial or the Ops Center
     log -, another pass, within max(15 m,
     1.5 header widths), the same distance from the harvested edge within 4 m, >= 5 controls), per band the median
     ratio over the no-overlap records' own median ratio (the placebo); with fewer than 30 no-overlap records that
     have controls it falls back to the v2.3 reference with a warning. Reference "field" (v2.3): WITHIN each combine against that combine's own no-overlap band, pooled by band
     size; a combine's 0-band is its reference only with >= F8_MIN_REF_POINTS (500) points or >= F8_MIN_REF_SHARE
     (10 %) of its records, else the combine is left out of the pooled curve (a small, noisy 0-band can drag the
     pooled curve across the tolerance although no yield changed). Either way the threshold is the upper edge
     of the last band, from 0 up, with >= 30 (matched) points within -auto_tolerance_pct of the reference, never below
     min_threshold (5 %, the resolution of the overlap measure; 0 in v2.3); the band that set it, its margin to the
     tolerance and the band that stopped the scan are in R['f8']. When already the
     lowest band (0-2 %) fails, the dose-response cannot set the threshold and F8 uses fallback_threshold (10 %, a
     house value) with a warning: the rule would stop at 1 % and remove most of the field.
     Fewer than MIN_KEPT = 30 kept points: ValueError naming the filter that removed the most points (no output).
  3. residual flow-delay scan between opposite-direction neighbours, lags -8..8 records. lag_correction "auto" shifts
     by -best only when 2 <= |best| < 8 records (an optimum at the edge of the scan is not resolved) AND r improves
     by more than 0.01; an integer N is given in the scan's convention (the best lag the figure shows) and shifts by
     -N, as auto would for that optimum; "none" never shifts. lag_stage "output" (the default, as v2.3): the scan runs
     on the kept records after the cascade and moves the output geometry only. "before_f7" (an option): the scan runs
     on the pass interiors of the records alive after F6 and every yield is tied to the position its grain came from
     (record k of a pass -> position k + shift) before F7 and F8; a record whose source lies beyond its pass ends goes
     with F7. The option failed its pre-registered test: the end-of-pass deficit did not
     fall enough, the scan's optimum moved with its population and the shifted overlap could flatten the F8
     dose-response.
  3b. moisture basis and harvest sessions (engine/clean/sessions.py; diagnostics, never a correction). The standard
     moisture Ms comes from settings moisture.standard_pct > the decoder report > the export itself (dry / wet < 0.999
     on >= 200 records) > the crop table; the shrink of the mapped dry yield (one-sided as SMS does, or symmetric) is
     detected from the records below Ms. New column Yld_Dry_Sy (N 15.4) = wet yield x (100 - M) / (100 - Ms), the
     symmetric dry-matter basis. Sessions = machine x calendar day (the dataset when the records carry no time of
     day): columns Harv_Day (C 10) and Session (C 32); R['sessions'] holds the per-session table, the field mean of
     mass per area (the integral over every processed reading, not the cleaned-map mean), the paired-cell yield ratio
     and moisture difference between consecutive sessions of a machine, and the recommendation ("map Yld_Dry_Sy and
     keep Harv_Day as a block" when sessions differ by > 2 moisture points or > 20 % of the readings fall below Ms
     under one-sided shrink; house rules). Figure fig_clean_sessions.png.
  4. several combines (a machine column with > 1 value; without one, with machines.from_dataset, two or more
     sources in parentheses at the end of the dataset names, or dataset names that are VINs, each a combine:
     sms3sigma.dataset_machines; machines.harmonise true; engine/clean/machines.py, after the cascade or the SMS
     3-sigma sequence, on the kept points). First the harvest layout of every pair of combines (interleaved passes,
     mixed, blocks, no contact: the adjacency of their passes) and the four methods (paired cells, machine x machine,
     track x track, point x point) with a traffic light each; machines.method chooses the factor ("auto" = the
     paired cells where the combines met on interleaved or mixed passes, no factor in blocks or without contact).
     The paired cells:
     30 m and 50 m cells (>= 5 kept points of both machines with <= machines.pair_max_overlap = 2 % of the footprint
     already cut, same plot when a plan exists) against the reference machine (machines.reference "auto" = most kept
     points; the factor goes on the OTHER machines); f = exp(mean(log(ref / m))) with a block-bootstrap CI over pass
     pairs. The 30 m factor is applied -> new column Yld_Harm (the yield to map) only when both CIs exclude
     1, the 30 m and 50 m factors agree within 2 % and 1 % < |f - 1| <= 10 %; above 10 % nothing is applied and the
     run asks for per-machine weights - unless the grower's weight confirms the factor (engine/clean/machines.py
     ticket_closure: the monitor total with the factor within machines.ticket_close_tol = 1 % of the ticket while the
     total without it is more than 3 % away -> applied, "the grower's weight confirms it").
     Per-machine tickets (machines.totals_bu; applied whenever given, with or without
     the relative step, also for one machine) level every machine to its own ticket (Yld_Level = y x L_m, Yld_Harm =
     y x L_m / L_ref) and the paired estimate becomes a validation where it passes its own pair gate. Several combines
     working at the same time need per-record timestamps for the overlap order; without them a warning is recorded.
  5. level = the grower's weight (settings scale_ticket = {value, unit lb | kg | bu | t, basis wet | dry, moisture_pct,
     note}; the legacy scale_ticket_bu + scale_ticket_basis is an alias in bu with the v1 behaviour). The comparison
     is one of MASS: the ticket is converted to bushels with the bushel of the file's own volume columns (the file's
     median Yld_Mass_W / Yld_Vol_We > the decoder report > settings test_weight_lb_bu > the crop table;
     monitor_bushel_weight, ticket_summary), never with another bushel weight (the grower's own, scale_ticket
     .test_weight_lb_bu, says what the grower's bushels weigh: grower_bushel_weight), and compared with the
     monitor total of the processed set on the same basis (sum over every processed record of yield x distance x
     width / 43560): basis wet -> the wet yield; basis dry -> the file's own dry yield, the column that is imported,
     shown and leveled (one-sided shrink after an SMS-like import, as SMS reports its dry total), so Yld_Level
     integrates to the weight; basis wet with the ticket's moisture m -> the ticket is brought to the standard moisture
     by the same rule as the file's dry yield (one-sided: x min(1, (100 - m) / (100 - Ms))) and compared with it, so
     a wet weight at m = Ms and the same weight 'at the standard moisture' give one L (an OCCD run whose sessions
     recommend the symmetric basis compares with Yld_Dry_Sy, symmetric both ways, and maps Yld_Lvl_Sy). A
     yield per acre (bu/ac) without acres is over the boundary's area, else over the ground harvested (every strip
     once: the optimised area after Optimize Swath Widths), not over the swept area. scale_ticket.use 'validate':
     L and the closure are reported, no leveled column reaches the clean file and the weight confirms no combine
     factor. L = ticket / monitor total (after the harmonisation when a factor was applied) -> new columns
     Yld_Level = mapped dry yield x harmonisation x L and, in a v2 run, Yld_Lvl_Sy = Yld_Dry_Sy x harmonisation x L
     (a legacy scale_ticket_bu run writes Yld_Level only, byte-identical to v1). R['level'] carries the ticket as
     given (ticket_value / ticket_unit / ticket_lb / ticket_bu / test_weight_used / test_weight_source), the monitor
     totals (export_total_bu, export_total_lb) and the closure (monitor vs ticket before and after the harmonisation,
     with a verdict). With a v2 ticket the yield column to map (R['yield_column_for_difm'], R['yield_column_note'])
     is Yld_Lvl_Sy when the sessions recommend the symmetric basis in an OCCD run, else Yld_Level (always in an SMS
     3-sigma run); the note says the bu/ac sum to the weight only when the integral of that column does
     (R['level']['yld_level_vs_ticket_pct'] / 'yld_lvl_sy_vs_ticket_pct'). With several combines the ticket
     is also a closure test for a paired factor above max_factor (step 4; machines.ticket_confirms,
     R['machines']['ticket_test']). With an AOI the integral inside the AOI itself (0 m) is reported too
     (R['level']['inside_aoi_integral_bu']). advice(): |L - 1| > 10 % -> check the ticket coverage before trusting L.
  6. outputs in out_dir (the OCCD method; the SMS method's names and figures are in the method paragraph above):
     <tag>_clean.shp/.shx/.dbf/.prj/.cpg and <tag>_clean.zip (the clean file: only
     those five files), <tag>_flagged.shp(+zip) with every processed point and Keep / Rem_By / Pass_Id / Run_Id /
     Overlap_f / Lag_rec / Yld_Harm / Yld_Level / Yld_Lvl_Sy / X_log / Y_log / Yld_Dry_Sy / Harv_Day / Session (+ In_AOI /
     Plot_Id / Tgt_Rate with an AOI), <tag>_cleaning.json, the figures (fig_clean_final = the yield column to map
     over the clean points, read back from that file (R['final_map']); fig_clean_map, _f8, _cascade, _lag,
     _sessions; with several combines fig_clean_combines and fig_clean_seams, R['machines']['seams']) and
     <tag>_cleaning_report.pdf (engine/report/clean_report.py). The clean file gets Yld_Harm /
     Yld_Level as before and, in a v2 run only (crop, moisture.standard_pct, an AOI or the scale_ticket dict given;
     R['v2_columns']), Yld_Lvl_Sy / Yld_Dry_Sy / Harv_Day / Session / Plot_Id / Tgt_Rate (R['difm_ready_added_columns']),
     so a plain run writes it byte for byte as the v1 engine did. Both point files are in WGS 84:
     an input in another CRS is reprojected (a note says so). An input that already holds the columns the cleaning
     adds (an output of this app) is refused; a vendor field that clashes with an added name gets the added column
     renamed (<name>_2, with a warning), so the .dbf never holds a field name twice. R['advice'] lists the plain
     checks (advice()) for the JSON, the PDF, the app and the CLI.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import os
import re
import struct
import sys
import zipfile

import numpy as np
import pandas as pd
import geopandas as gpd
from pyproj import Transformer
from scipy.spatial import cKDTree
def sk_polygon(*args, **kwargs):          # oced_clean: lazy import (scikit-image is not needed by the SMS route)
    from skimage.draw import polygon
    return polygon(*args, **kwargs)

from oced_clean._engine.clean import aoi as aoi_mod
from oced_clean._engine.clean import sessions as sessions_mod
from oced_clean._engine.clean import machines as machines_mod
from oced_clean._engine.clean import sms3sigma as sms_mod
from oced_clean._engine.formats.common import CROPS
try:                                          # written by another builder: FILTER_INFO['source'] falls back below
    from oced_clean._engine.clean import references as references_mod
except Exception:                             # pragma: no cover - the module may not exist yet
    references_mod = None

HARM_AGREE = machines_mod.HARM_AGREE         # the 30 m and 50 m factors must agree within 2 %
HARM_MIN_EFFECT = 0.01                       # and differ from 1 by more than 1 % (machines.min_factor default)
LEVEL_TOL = 0.01                             # warn when the leveled export misses the ticket by more than 1 %
AOI_BUFFER_M = 50.0                          # processing clip: records farther than this from the AOI are dropped
F8_MIN_REF_POINTS = 500                      # a combine's own no-overlap band is its F8 reference only with >= 500
F8_MIN_REF_SHARE = 0.10                      # points or >= 10 % of its records in the dose population (house rule)
FT2_PER_AC = 43560.0
RES = 0.25                                   # overlap raster resolution (m)
RASTER_ORIGIN_SHIFT_M = 0.0                  # diagnostics only: moves the raster grid against the points (raster
                                             # sensitivity of F8 decisions); 0 in every cleaning
EDGE_CELL_M = 2.0                            # grid of the harvested-area edge distance (local F8 controls)
DOSE_BANDS = [-1e-9, .001, .02, .05, .10, .15, .25, .35, 1.01]
DOSE_LABELS = ["0", "0-2", "2-5", "5-10", "10-15", "15-25", "25-35", ">35"]
DOSE_UPPER = [0.0, .02, .05, .10, .15, .25, .35, 1.0]
LAG_RANGE = 8                                # flow-delay scan: -8..8 records
POS_MAX_KM = 50.0                            # F0: farther than this from the median position = garbage fix (the
                                             # rule of the jd_jdl decoder, OUTLIER_KM)
MAX_RASTER_CELLS = 1_000_000_000             # overlap raster cap: 1e9 booleans (1 GB) = 7.9 x 7.9 km at 0.25 m
MIN_KEPT = 30                                # fewer kept points: no clean file is written (ValueError)
REMOVED_CHECK_PCT = 60.0                     # advice: the cleaning removed more than this share of the points
# columns the cleaning adds (flagged file; the clean file gets Yld_Harm / Yld_Level and, in a v2 run - crop,
# standard moisture or AOI given - Yld_Dry_Sy / Harv_Day / Session / Plot_Id / Tgt_Rate). An input that already
# holds one of CLEANED_MARKERS is an output of this app and is refused (cleaning twice removes points twice and
# duplicates the added .dbf fields, which many GIS programs reject).
ADDED_COLUMNS = ["Pass_Id", "Run_Id", "Keep", "Rem_By", "Overlap_f", "Lag_rec", "Yld_Harm", "Yld_Level", "Yld_Lvl_Sy",
                 "X_log", "Y_log", "Yld_Dry_Sy", "Harv_Day", "Session", "In_AOI", "Plot_Id", "Tgt_Rate",
                 "Wdth_Opt", "Dist_Opt", "Swath_Scl", "Wdth_Raw"]
V2_READY_COLUMNS = ["Yld_Dry_Sy", "Harv_Day", "Session", "Plot_Id", "Tgt_Rate"]
CLEANED_MARKERS = ["Run_Id", "Rem_By", "Overlap_f", "Lag_rec", "Yld_Harm", "Yld_Level", "Yld_Lvl_Sy", "X_log", "Y_log",
                   "Yld_Dry_Sy", "In_AOI", "Wdth_Opt", "Dist_Opt", "Swath_Scl", "Wdth_Raw"]
# cleaning methods: the OCCD cascade (F0-F11, the house standard), the Ag Leader SMS 3-sigma sequence
# (engine/clean/sms3sigma.py), or both on the same readings followed by a comparison (engine/clean/compare.py)
METHODS = ("occd", "sms3sigma", "both")
METHOD_ALIASES = {"house": "occd", "cascade": "occd", "occd": "occd", "sms3sigma": "sms3sigma", "sms": "sms3sigma",
                  "3sigma": "sms3sigma", "sigma3": "sms3sigma", "sms_3sigma": "sms3sigma", "both": "both",
                  "compare": "both"}
SMS_TAG_SUFFIX = "_sms3s"
# grower's weight (scale ticket): units and the conversion to bushels (bu = lb / test weight lb/bu)
TICKET_UNITS = ("lb", "kg", "bu", "t", "bu/ac")   # t = metric tonne (1000 kg); bu/ac = the grower's yield per acre
TICKET_UNIT_ALIASES = {"tonne": "t", "tonnes": "t", "mt": "t", "bu_ac": "bu/ac", "buac": "bu/ac", "bu/acre": "bu/ac",
                       "bpa": "bu/ac", "bu per ac": "bu/ac", "bu per acre": "bu/ac"}
LB_PER_KG = 2.20462262185
TICKET_LB_PER_UNIT = {"lb": 1.0, "kg": LB_PER_KG, "t": 1000.0 * LB_PER_KG}
TICKET_MIN_RATIO_POINTS = 30                 # the file's own test weight needs >= 30 readings with both mass and volume
TICKET_USES = ("adjust", "validate")         # scale_ticket.use: level the yields, or only validate the monitor
BUSHEL_FROM = ("file", "declared")           # scale_ticket.bushel_from: the bushel of the monitor's total
KG_HA_PER_LB_AC = 1.12085                    # a mass per area in kg/ha read as lb/ac is this much too large
BUSHEL_DIFF_TOL = 0.005                      # the grower's and the file's bushel weights "differ" beyond 0.5 %
CARD_SUMMARY_TOL = 0.005                     # a Case IH card's readings and its own Summary "differ" beyond 0.5 %
# F1 moisture window per crop (house conventions; the only published fixed window is 10-33 %, Luck & Fulton 2014 /
# Luck et al. 2015, the range in which the capacitance sensor reads adequately): applied when the user did not set F1
# min_pct / max_pct. Unknown or no crop: the small-cereal window 8-20 %.
F1_CROP_RANGES = {"barley": (8.0, 20.0), "wheat": (8.0, 20.0), "durum": (8.0, 20.0), "oats": (8.0, 20.0),
                  "rye": (8.0, 20.0), "canola": (4.0, 16.0), "flax": (4.0, 16.0), "peas": (8.0, 24.0),
                  "lentils": (8.0, 22.0), "corn": (10.0, 33.0), "soybeans": (8.0, 25.0)}
F1_DEFAULT_RANGE = (8.0, 20.0)

# ===================================================================================================== settings
FILTER_ORDER = ["F0", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F5b"]
DEFAULT_SETTINGS = {
    "tag": None,                      # output prefix; None = input file stem
    "cols": None,                     # {key: column}; missing keys auto-detected by detect_columns
    "harvester_width_ft": None,       # only when there is no width column
    "test_weight_lb_bu": None,        # only for F9 when there is no wet-mass column
    "filters": {
        "F0": {"on": True},
        "F1": {"on": True, "min_pct": 8.0, "max_pct": 20.0},
        "F2": {"on": True, "min_mph": 1.5, "max_mph": 6.5},
        "F3": {"on": True, "max_change_per_s": 0.20},
        "F4": {"on": True, "max_rel_error": 0.50},
        "F5": {"on": True, "min_points": 10},
        "F6": {"on": True, "max_deg_per_s": 30.0},
        "F7": {"on": True, "drop_start": 4, "drop_end": 2},
        # footprint "swept" = the ground the header swept since the previous reading (v2.4), "chord" = the v2.3
        # rectangle along the chord (it counts a wedge of the predecessor as already cut); y_offset: the narrowed John
        # Deere strip sits Y_Offset to the right of travel; reference "local" = each record against no-overlap records
        # of the same combine nearby at the same distance from the harvested edge (v2.4), "field" = against the
        # combine's whole no-overlap band (v2.3); min_threshold: floor of the automatic threshold (the resolution of
        # the overlap measure, 0 in v2.3)
        "F8": {"on": True, "threshold": "auto", "auto_tolerance_pct": 3.0, "fallback_threshold": 0.10,
               "footprint": "swept", "y_offset": True, "reference": "local", "min_threshold": 0.05,
               "local_radius_m": 15.0, "edge_tol_m": 4.0, "min_controls": 5},
        "F9": {"on": True, "min_lbac": 500.0, "max_lbac": 10000.0},
        "F10": {"on": True, "k_sd": 3.0},
        "F11": {"on": True, "half_window": 10, "max_rel_dev": 0.30},
        "F5b": {"on": True, "min_points": 10},
    },
    "lag_correction": "auto",         # "auto" | "none" | integer N = the scan's best lag to correct (shift = -N)
    # when the flow delay is measured and applied: "output" (the default, as v2.3) = on the kept records after the
    # cascade, the output geometry only; "before_f7" (an option) = on the records alive after F6, every yield tied to
    # the position its grain came from before F7 and F8. The pre-registered test of "before_f7" failed (the
    # end-of-pass deficit did not fall enough, the scan moved with its population and the
    # shifted overlap could flatten the F8 dose-response), so it is not the default
    "lag_stage": "output",
    # the pass axis the scan compares: "dominant" = the direction the field was harvested in (v2.4), "ew_first" = E-W
    # whenever E and W both hold >= 200 records (v2.3; it scanned the headland passes of N-S fields)
    "lag_axis": "dominant",
    "harmonise_machines": True,     # alias of machines.harmonise (kept for old settings files)
    "scale_ticket_bu": None,          # legacy alias of scale_ticket (unit bu): the v1 behaviour, Yld_Level only
    "scale_ticket_basis": "wet",      # basis of the legacy scale_ticket_bu
    # grower's weight (scale ticket) = the real level of the field. value in `unit` (lb | kg | bu | t = metric tonne);
    # basis "wet" (the scale weighs wet grain: compared with the wet integral) or "dry" (the ticket is already at the
    # standard moisture: compared with the dry integral of the file, the mapped dry yield as imported); moisture_pct =
    # the ticket's moisture (optional; with basis wet the ticket and the monitor's wet yield are both brought to the
    # standard moisture symmetric-wise: a dry-matter comparison); note = free text (e.g. "elevator ticket, whole
    # field"). The comparison is always a comparison of MASS: a weight in lb / kg / t is turned into bushels with the
    # bushel of the file's own volume columns (monitor_bushel_weight: median Yld_Mass_W / Yld_Vol_We > the decoder
    # report > settings.test_weight_lb_bu > the crop table), never with another one; test_weight_lb_bu (inside the
    # ticket) = the grower's own bushel weight (lb per bushel): it says what the grower's bushels weigh (a weight in
    # bu or bu/ac x it = the mass compared) and, for a weight in lb / kg / t, only how many of the grower's bushels
    # that is (reported, not compared). null = the file's bushel.
    # unit bu/ac: the grower's yield per acre; the total = value x area_ac (the acres the grower's yield is over;
    # null = the field boundary's area, else, without a boundary, the area harvested: every strip once, after
    # Optimize Swath Widths in an SMS 3-sigma run)
    # use: "adjust" (the default: Yld_Level / Yld_Lvl_Sy are written and named the column to map; the weight may
    # confirm a combine factor above machines.max_factor) or "validate" (the weight only validates the monitor: L, the
    # closure and the ticket closure test are reported, no leveled column reaches the clean file and no combine
    # factor is applied because of the weight)
    # bushel_from: "file" (the default: the bushel of the file's own volume columns, median wet mass / wet volume) or
    # "declared" (the bushel weight declared for the file: settings.test_weight_lb_bu > the decoder report > the crop
    # table), for a file whose mass column is not lb/ac (the cleaning warns when the two differ: BUSHEL_DIFF_TOL)
    "scale_ticket": {"value": None, "unit": "lb", "basis": "wet", "moisture_pct": None, "note": None, "area_ac": None,
                     "test_weight_lb_bu": None, "use": "adjust", "bushel_from": "file"},
    "boundary": None,                # alias: polygon file for the level diagnostics only (-> aoi.file, no clip)
    "pass_break": {"max_step_m": 15.0, "max_heading_change_deg": 60.0},
    "crop": None,                     # key of engine.formats.common.CROPS; drives the F1 window and the standard moisture
    # field of interest / trial plan: polygon file (.shp/.zip/.geojson/.gpkg/.kml/.kmz). buffer_m: records farther than
    # this from the AOI are dropped before the cascade (null = no processing clip); clip_output: the clean file
    # keeps only the kept records inside the AOI; id_col / rate_col: plot id and target-rate columns of a plan (null =
    # auto); headland_types: 'type' values that mark a headland / border polygon
    # feature: 1-based index of the polygon to use when the file holds several field boundaries (not a plan), or a list
    # of indices when the field is drawn in several polygons (the AOI is then their union)
    "aoi": {"file": None, "buffer_m": AOI_BUFFER_M, "clip_output": True, "id_col": None, "rate_col": None,
            "headland_types": ["headland", "border"], "feature": None},
    # standard moisture (null = decoder report > inferred from the export > crop table); shrink = how the mapped dry
    # yield was computed: auto | one_sided | symmetric; add_symmetric_column: write Yld_Dry_Sy
    "moisture": {"standard_pct": None, "shrink": "auto", "add_symmetric_column": True},
    # several combines (engine/clean/machines.py): reference "auto" = most kept points or a machine id; totals_bu =
    # {machine: wet bu} per-machine scale tickets
    # pair_max_overlap: the paired cells use only kept records with <= this share of the footprint already cut
    # ticket_confirms: a paired factor above max_factor is still applied when the grower's weight confirms it (the
    # monitor total with the factor within ticket_close_tol of the ticket, without it more than 3 x that away)
    # from_dataset: without a machine column, the combines are told apart by the sources SMS writes in parentheses at
    # the end of the dataset names when they differ (sms3sigma.dataset_machines); false = one combine, as before
    # network: the factor of a combine from every neighbouring pair of combines (weighted least squares), not only
    # from its pair with the reference; spatial_check: one factor must fit both halves of the field (machines.py)
    # method: how the factor is computed - "auto" (paired cells where the combines met on interleaved or mixed passes,
    # no factor in blocks), "cells", "machine" (machine x machine, whole-field means), "track" (track x track) or
    # "point" (point x point); the harvest layout and all four methods are reported whatever the choice
    "machines": {"harmonise": True, "reference": "auto", "totals_bu": None, "min_pairs": 30, "min_pass_pairs": 5,
                 "ci": 0.95, "min_factor": 0.01, "max_factor": 0.10, "same_plot_only": True, "bootstrap_reps": 1000,
                 "seed": 2026, "pair_max_overlap": machines_mod.PAIR_MAX_OVERLAP, "ticket_confirms": True,
                 "ticket_close_tol": machines_mod.TICKET_CLOSE_TOL, "from_dataset": True, "network": True,
                 "spatial_check": True, "method": "auto"},
    # cleaning method: "occd" (the cascade above), "sms3sigma" (the SMS 3-sigma sequence, settings in "sms3sigma")
    # or "both" (both on the same readings, then engine/clean/compare.py writes the comparison)
    "method": "occd",
    "sms3sigma": dict(sms_mod.DEFAULTS),
    # the record of the file's Import as SMS (engine/sms_import.py resolve / apply; engine.run.import_as_sms): with it
    # the SMS 3-sigma S0 takes the minimum yield of the import itself (None or 0 = no minimum), and the record goes
    # into R['sms_import'], the JSON and the PDF. None = no import record (S0 uses sms3sigma.min_yield)
    "sms_import": None,
}
NESTED_SETTINGS = ("pass_break", "aoi", "moisture", "machines", "scale_ticket", "sms3sigma")
# the v2.3 overlap and flow-delay model, for reproducing a cleaning delivered before v2.4 (merge into the settings)
LEGACY_V23 = {"filters": {"F8": {"footprint": "chord", "y_offset": False, "reference": "field", "min_threshold": 0.0}},
              "lag_stage": "output", "lag_axis": "ew_first"}
F8_FOOTPRINTS = ("swept", "chord")
F8_REFERENCES = ("local", "field")
LAG_STAGES = ("output", "before_f7")
LAG_AXES = ("dominant", "ew_first")

G_SENSOR, G_PASS, G_OVL, G_OUT, G_RUNS = ("Sensor & record (F0-F4)", "Pass structure (F5-F7)", "Overlap (F8)",
                                          "Yield outliers (F9-F11)", "Short runs (F5b)")
# rule: a str.format template over the filter's settings (F8 and F10 get extra run-time fields, see describe_filter)
FILTER_INFO = {
    "F0": {"name": "Record integrity", "rule": "missing/zero yield, zero distance, or no / implausible position "
                                               "(none, (0,0), outside lon/lat ranges, > {pos_km:g} km from the median)",
           "source": "record integrity", "group": G_SENSOR},
    "F1": {"name": "Moisture", "rule": "moisture outside {min_pct:g}-{max_pct:g} % or missing",
           "source": "Luck & Fulton 2014", "group": G_SENSOR},
    "F2": {"name": "Speed", "rule": "speed outside {min_mph:g}-{max_mph:g} mph",
           "source": "Sudduth & Drummond 2007", "group": G_SENSOR},
    "F3": {"name": "Speed change", "rule": "speed change > {pct_change:g} % per second",
           "source": "Sudduth & Drummond 2007", "group": G_SENSOR},
    "F4": {"name": "GNSS step", "rule": "|GNSS step - logged distance| > {pct_error:g} %",
           "source": "Lyle et al. 2014; 50 %: house rule", "group": G_SENSOR},
    "F5": {"name": "Short pass", "rule": "pass shorter than {min_points} surviving points",
           "source": "Ping & Dobermann 2005", "group": G_PASS},
    "F6": {"name": "Heading change", "rule": "heading change > {max_deg_per_s:g} deg/s",
           "source": "Clarke et al. 2024", "group": G_PASS},
    "F7": {"name": "Pass ends", "rule": "first {drop_start} and last {drop_end} points of every pass",
           "source": "Sudduth & Drummond 2007; 4 / 2: house rule", "group": G_PASS},
    "F8": {"name": "Overlap", "rule": "> {threshold_txt} of the footprint over ground already cut ({how})",
           "source": "Drummond et al. 1999; dose-response: house method", "group": G_OVL},
    "F9": {"name": "Wet mass", "rule": "wet mass outside {min_lbac:g}-{max_lbac:g} lb/ac",
           "source": "Arizo-García et al. 2026; window: house rule", "group": G_OUT},
    "F10": {"name": "Mean +/- k SD", "rule": "outside mean +/- {k_sd:g} SD{range_txt}",
            "source": "dos Santos & Miguez 2025", "group": G_OUT},
    "F11": {"name": "Local median", "rule": "> {pct_dev:g} % from the median of its +/-{half_window} in-pass neighbours",
            "source": "Maldaner & Molin 2020", "group": G_OUT},
    "F5b": {"name": "Short runs", "rule": "surviving run of fewer than {min_points} points",
            "source": "Ping & Dobermann 2005; house extension", "group": G_RUNS},
}
GROUPS = [(G_SENSOR, ["F0", "F1", "F2", "F3", "F4"]), (G_PASS, ["F5", "F6", "F7"]), (G_OVL, ["F8"]),
          (G_OUT, ["F9", "F10", "F11"]), (G_RUNS, ["F5b"])]


def _attach_references():
    """FILTER_INFO['source'] = references.cite(step) (one line: works + what each supports) and FILTER_INFO['sources']
    = the list of sources, when engine/clean/references.py is present; otherwise the strings above stay."""
    if references_mod is None:
        return
    for fid in FILTER_INFO:
        try:
            s = references_mod.cite(fid)
            if s:
                FILTER_INFO[fid]["source"] = s
            if fid in getattr(references_mod, "HOUSE_RULES_V23", {}):      # cited by runs with the v2.3 model
                FILTER_INFO[fid]["source_v23"] = references_mod.cite(fid, legacy=True)
            FILTER_INFO[fid]["sources"] = list((getattr(references_mod, "SOURCES", {}) or {}).get(fid, []))
        except Exception:                      # the references module is another builder's: never break the cleaning
            continue


_attach_references()


def step_source(step_id: str, fallback: str = "") -> str:
    """Literature line of a non-filter step ('aoi', 'sessions', 'machines', 'level', 'lag', 'order') from
    engine/clean/references.py, or `fallback` when that module is absent."""
    if references_mod is not None:
        try:
            s = references_mod.cite(step_id)
            if s:
                return s
        except Exception:
            pass
    return fallback

# (kind, lower bound, upper bound, bound inclusive) per filter parameter; kind "f" float, "i" int
_PARAM_SPEC = {
    "F1": {"min_pct": ("f", 0, 100), "max_pct": ("f", 0, 100)},
    "F2": {"min_mph": ("f", 0, 100), "max_mph": ("f", 0, 100)},
    "F3": {"max_change_per_s": ("f", 1e-9, 100)},
    "F4": {"max_rel_error": ("f", 1e-9, 100)},
    "F5": {"min_points": ("i", 0, 100000)},
    "F6": {"max_deg_per_s": ("f", 1e-9, 360)},
    "F7": {"drop_start": ("i", 0, 1000), "drop_end": ("i", 0, 1000)},
    "F8": {"auto_tolerance_pct": ("f", 0, 100), "fallback_threshold": ("f", 0, 1), "min_threshold": ("f", 0, 1),
           "local_radius_m": ("f", 1, 100), "edge_tol_m": ("f", 0, 1000), "min_controls": ("i", 1, 1000)},
    "F9": {"min_lbac": ("f", 0, 1e6), "max_lbac": ("f", 0, 1e6)},
    "F10": {"k_sd": ("f", 1e-9, 100)},
    "F11": {"half_window": ("i", 1, 1000), "max_rel_dev": ("f", 1e-9, 100)},
    "F5b": {"min_points": ("i", 0, 100000)},
}


def _num(v, kind, name, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)):
        raise ValueError(f"{name} must be a number, not {v!r}")
    if kind == "i":
        if float(v) != int(v):
            raise ValueError(f"{name} must be a whole number, not {v!r}")
        v = int(v)
    else:
        v = float(v)
    if not np.isfinite(v) or v < lo or v > hi:
        raise ValueError(f"{name} = {v!r} is outside the allowed range {lo:g}..{hi:g}")
    return v


def sanitise_tag(s) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(s)).strip("_")


def merge_settings(user=None) -> dict:
    """DEFAULT_SETTINGS updated with `user` (any subset), validated. Raises ValueError with a clear message."""
    S = copy.deepcopy(DEFAULT_SETTINGS)
    user = {} if user is None else user
    if not isinstance(user, dict):
        raise ValueError(f"settings must be a dict, not {type(user).__name__}")
    unknown = set(user) - set(S)
    if unknown:
        raise ValueError(f"unknown setting(s): {sorted(unknown)}; allowed: {sorted(S)}")
    for k, v in user.items():
        if k == "filters":
            if v is None:
                continue
            if not isinstance(v, dict):
                raise ValueError("settings['filters'] must be a dict {filter id: {...}}")
            bad = set(v) - set(FILTER_ORDER)
            if bad:
                raise ValueError(f"unknown filter(s): {sorted(bad)}; filters are {FILTER_ORDER}")
            for fid, p in v.items():
                if p is None:
                    continue
                if not isinstance(p, dict):
                    raise ValueError(f"settings['filters']['{fid}'] must be a dict")
                badp = set(p) - set(S["filters"][fid])
                if badp:
                    raise ValueError(f"unknown parameter(s) for {fid}: {sorted(badp)}; allowed: {sorted(S['filters'][fid])}")
                S["filters"][fid].update(p)
        elif k in NESTED_SETTINGS:
            if v is None:
                continue
            if not isinstance(v, dict) or set(v) - set(S[k]):
                raise ValueError(f"settings['{k}'] must be a dict with keys {sorted(S[k])}")
            S[k].update(v)
        else:
            S[k] = v
    # ---- aliases of the v1 keys: harmonise_machines <-> machines.harmonise, boundary -> aoi.file (diagnostic only)
    um = user.get("machines") if isinstance(user.get("machines"), dict) else {}
    if "harmonise_machines" in user and "harmonise" not in um:
        S["machines"]["harmonise"] = S["harmonise_machines"]
    else:
        S["harmonise_machines"] = S["machines"]["harmonise"]
    ua = user.get("aoi") if isinstance(user.get("aoi"), dict) else {}
    if S["boundary"] is not None and not ua.get("file"):
        if not isinstance(S["boundary"], str):
            raise ValueError("boundary must be a file path (str) or null")
        # the v1 behaviour: the integral inside the boundary is a diagnostic, nothing is clipped or dropped
        S["aoi"].update(file=S["boundary"], buffer_m=None, clip_output=False)
    # ---- crop and the F1 window
    if S["crop"] is not None:
        if not isinstance(S["crop"], str) or not S["crop"].strip():
            raise ValueError(f"crop must be a crop name (one of {sorted(CROPS)}) or null, not {S['crop']!r}")
        S["crop"] = S["crop"].strip().lower()
    uf1 = (user.get("filters") or {}).get("F1") if isinstance(user.get("filters"), dict) else None
    uf1 = uf1 if isinstance(uf1, dict) else {}
    # both bounds come from the crop (or the small-cereal default); the user overrides only the bound(s) given
    lo, hi = F1_CROP_RANGES.get(S["crop"], F1_DEFAULT_RANGE)
    if "min_pct" not in uf1:
        S["filters"]["F1"]["min_pct"] = lo
    if "max_pct" not in uf1:
        S["filters"]["F1"]["max_pct"] = hi
    # ---- validate
    for fid in FILTER_ORDER:
        p = S["filters"][fid]
        if not isinstance(p["on"], bool):
            raise ValueError(f"filters.{fid}.on must be true or false, not {p['on']!r}")
        for name, (kind, lo, hi) in _PARAM_SPEC.get(fid, {}).items():
            p[name] = _num(p[name], kind, f"filters.{fid}.{name}", lo, hi)
    for fid, a, b in (("F1", "min_pct", "max_pct"), ("F2", "min_mph", "max_mph"), ("F9", "min_lbac", "max_lbac")):
        if S["filters"][fid][a] >= S["filters"][fid][b]:
            raise ValueError(f"filters.{fid}: {a} ({S['filters'][fid][a]:g}) must be below {b} ({S['filters'][fid][b]:g})")
    th = S["filters"]["F8"]["threshold"]
    if isinstance(th, str):
        if th.strip().lower() != "auto":
            raise ValueError(f"filters.F8.threshold must be 'auto' or a fraction 0..1 (0.05 = 5 %), not {th!r}")
        S["filters"]["F8"]["threshold"] = "auto"
    else:
        t = _num(th, "f", "filters.F8.threshold", 0, 1)
        S["filters"]["F8"]["threshold"] = t
    P8 = S["filters"]["F8"]
    for k, allowed in (("footprint", F8_FOOTPRINTS), ("reference", F8_REFERENCES)):
        v = str(P8[k]).strip().lower() if isinstance(P8[k], str) else P8[k]
        if v not in allowed:
            raise ValueError(f"filters.F8.{k} must be one of {list(allowed)}, not {P8[k]!r}")
        P8[k] = v
    if not isinstance(P8["y_offset"], bool):
        raise ValueError(f"filters.F8.y_offset must be true or false, not {P8['y_offset']!r}")
    ls = str(S["lag_stage"]).strip().lower() if isinstance(S["lag_stage"], str) else S["lag_stage"]
    if ls not in LAG_STAGES:
        raise ValueError(f"lag_stage must be one of {list(LAG_STAGES)}, not {S['lag_stage']!r}")
    S["lag_stage"] = ls
    la = str(S["lag_axis"]).strip().lower() if isinstance(S["lag_axis"], str) else S["lag_axis"]
    if la not in LAG_AXES:
        raise ValueError(f"lag_axis must be one of {list(LAG_AXES)}, not {S['lag_axis']!r}")
    S["lag_axis"] = la
    S["pass_break"]["max_step_m"] = _num(S["pass_break"]["max_step_m"], "f", "pass_break.max_step_m", 1e-9, 1e6)
    S["pass_break"]["max_heading_change_deg"] = _num(S["pass_break"]["max_heading_change_deg"], "f",
                                                     "pass_break.max_heading_change_deg", 1e-9, 180)
    lc = S["lag_correction"]
    if isinstance(lc, str):
        if lc.strip().lower() not in ("auto", "none"):
            raise ValueError(f"lag_correction must be 'auto', 'none' or a whole number of records, not {lc!r}")
        S["lag_correction"] = lc.strip().lower()
    else:
        S["lag_correction"] = _num(lc, "i", "lag_correction", -50, 50)
    if not isinstance(S["harmonise_machines"], bool):
        raise ValueError(f"harmonise_machines must be true or false, not {S['harmonise_machines']!r}")
    for k, lo, hi in (("harvester_width_ft", 1e-9, 1000), ("test_weight_lb_bu", 1e-9, 1000),
                      ("scale_ticket_bu", 1e-9, 1e9)):
        if S[k] is not None:
            S[k] = _num(S[k], "f", k, lo, hi)
    basis = str(S["scale_ticket_basis"]).strip().lower()
    if basis not in ("wet", "dry"):
        raise ValueError(f"scale_ticket_basis must be 'wet' or 'dry', not {S['scale_ticket_basis']!r}")
    S["scale_ticket_basis"] = basis
    # ---- grower's weight (scale ticket)
    T = S["scale_ticket"]
    if T["value"] is not None:
        T["value"] = _num(T["value"], "f", "scale_ticket.value", 1e-9, 1e12)
    unit = str(T["unit"] if T["unit"] is not None else "lb").strip().lower()
    unit = TICKET_UNIT_ALIASES.get(unit, unit)
    if unit not in TICKET_UNITS:
        raise ValueError(f"scale_ticket.unit must be one of {list(TICKET_UNITS)} (t = metric tonne, bu/ac = the "
                         f"grower's yield per acre), not {T['unit']!r}")
    T["unit"] = unit
    if T.get("area_ac") is not None:
        T["area_ac"] = _num(T["area_ac"], "f", "scale_ticket.area_ac", 1e-6, 1e7)
        if unit != "bu/ac":
            raise ValueError("scale_ticket.area_ac only applies to a yield per acre (unit 'bu/ac'): drop it or give the "
                             "grower's figure in bu/ac")
    tb = str(T["basis"] if T["basis"] is not None else "wet").strip().lower()
    if tb not in ("wet", "dry"):
        raise ValueError(f"scale_ticket.basis must be 'wet' or 'dry', not {T['basis']!r}")
    T["basis"] = tb
    if T["moisture_pct"] is not None:
        T["moisture_pct"] = _num(T["moisture_pct"], "f", "scale_ticket.moisture_pct", 0, 60)
        if tb == "dry":
            raise ValueError("scale_ticket.moisture_pct only applies to the wet basis (a dry-basis weight is already at "
                             "the standard moisture, so the ticket's moisture would be ignored): drop it or give the "
                             "weight as the scale wrote it with basis 'wet'")
    if T["note"] is not None:
        if not isinstance(T["note"], str):
            raise ValueError(f"scale_ticket.note must be text or null, not {T['note']!r}")
        T["note"] = T["note"].strip() or None
    if T.get("test_weight_lb_bu") is not None:
        T["test_weight_lb_bu"] = _num(T["test_weight_lb_bu"], "f", "scale_ticket.test_weight_lb_bu", 1e-9, 1000)
    use = str(T.get("use") if T.get("use") is not None else "adjust").strip().lower()
    if use not in TICKET_USES:
        raise ValueError(f"scale_ticket.use must be one of {list(TICKET_USES)} (adjust = the leveled yield is written "
                         f"and mapped, validate = the weight only validates the monitor), not {T.get('use')!r}")
    T["use"] = use
    bf = str(T.get("bushel_from") if T.get("bushel_from") is not None else "file").strip().lower()
    if bf not in BUSHEL_FROM:
        raise ValueError(f"scale_ticket.bushel_from must be one of {list(BUSHEL_FROM)} (file = the file's own mass / "
                         "volume ratio, declared = settings.test_weight_lb_bu, else the decoder report, else the crop "
                         f"table), not {T.get('bushel_from')!r}")
    T["bushel_from"] = bf
    if T["value"] is not None and S["scale_ticket_bu"] is not None:
        raise ValueError("give the grower's weight once: scale_ticket (value + unit) or the legacy scale_ticket_bu, "
                         "not both")
    if S["boundary"] is not None and not isinstance(S["boundary"], str):
        raise ValueError("boundary must be a file path (str) or null")
    # ---- aoi
    A = S["aoi"]
    if A["file"] is not None and not isinstance(A["file"], str):
        raise ValueError("aoi.file must be a file path (str) or null")
    if A["buffer_m"] is not None:
        A["buffer_m"] = _num(A["buffer_m"], "f", "aoi.buffer_m", 0, 5000)
    if not isinstance(A["clip_output"], bool):
        raise ValueError(f"aoi.clip_output must be true or false, not {A['clip_output']!r}")
    for k in ("id_col", "rate_col"):
        if A[k] is not None and not isinstance(A[k], str):
            raise ValueError(f"aoi.{k} must be a column name or null, not {A[k]!r}")
    if A["feature"] is not None:
        fl = aoi_mod.feature_list(A["feature"])          # one polygon (int) or several (list), 1-based
        A["feature"] = aoi_mod.feature_setting([_num(v, "i", "aoi.feature", 1, 100000) for v in fl]) if fl else None
    if isinstance(A["headland_types"], str):
        A["headland_types"] = [A["headland_types"]]
    if not isinstance(A["headland_types"], (list, tuple)) or not all(isinstance(t, str) for t in A["headland_types"]):
        raise ValueError("aoi.headland_types must be a list of 'type' values (strings)")
    A["headland_types"] = [t.strip().lower() for t in A["headland_types"]]
    # ---- moisture
    Mo = S["moisture"]
    if Mo["standard_pct"] is not None:
        Mo["standard_pct"] = _num(Mo["standard_pct"], "f", "moisture.standard_pct", 0, 60)
    sh = str(Mo["shrink"]).strip().lower()
    if sh not in ("auto", "one_sided", "symmetric"):
        raise ValueError(f"moisture.shrink must be 'auto', 'one_sided' or 'symmetric', not {Mo['shrink']!r}")
    Mo["shrink"] = sh
    if not isinstance(Mo["add_symmetric_column"], bool):
        raise ValueError(f"moisture.add_symmetric_column must be true or false, not {Mo['add_symmetric_column']!r}")
    # ---- machines
    Ma = S["machines"]
    if not isinstance(Ma["harmonise"], bool):
        raise ValueError(f"machines.harmonise must be true or false, not {Ma['harmonise']!r}")
    S["harmonise_machines"] = Ma["harmonise"]
    if Ma["reference"] is None:
        Ma["reference"] = "auto"
    if not isinstance(Ma["reference"], (str, int, np.integer)) or isinstance(Ma["reference"], bool):
        raise ValueError(f"machines.reference must be 'auto' or a machine id, not {Ma['reference']!r}")
    Ma["reference"] = str(Ma["reference"]).strip() or "auto"
    if Ma["totals_bu"] is not None:
        if not isinstance(Ma["totals_bu"], dict) or not Ma["totals_bu"]:
            raise ValueError("machines.totals_bu must be a dict {machine id: wet bushels} or null")
        Ma["totals_bu"] = {str(k): _num(v, "f", f"machines.totals_bu[{k!r}]", 1e-9, 1e9)
                           for k, v in Ma["totals_bu"].items()}
    for name, (kind, lo, hi) in (("min_pairs", ("i", 2, 100000)), ("min_pass_pairs", ("i", 2, 100000)),
                                 ("ci", ("f", 0.5, 0.9999)), ("min_factor", ("f", 0, 1)), ("max_factor", ("f", 1e-9, 1)),
                                 ("bootstrap_reps", ("i", 0, 100000)), ("seed", ("i", 0, 2**31 - 1))):
        Ma[name] = _num(Ma[name], kind, f"machines.{name}", lo, hi)
    if Ma["pair_max_overlap"] is not None:
        Ma["pair_max_overlap"] = _num(Ma["pair_max_overlap"], "f", "machines.pair_max_overlap", 0, 1)
    if Ma["min_factor"] >= Ma["max_factor"]:
        # a max_factor at or below the default min_factor (the app's 'Max factor' at 1 %) with no min_factor given:
        # the smallest factor worth applying follows it (half of the maximum), so the cleaning runs; the two numbers
        # given together and contradictory are still refused
        if "min_factor" in um and "max_factor" in um:
            raise ValueError(f"machines.min_factor ({Ma['min_factor']:g}) must be below max_factor "
                             f"({Ma['max_factor']:g})")
        if "min_factor" in um:
            raise ValueError(f"machines.min_factor ({Ma['min_factor']:g}) must be below max_factor "
                             f"({Ma['max_factor']:g}, the default): give a larger machines.max_factor too")
        Ma["min_factor"] = Ma["max_factor"] / 2.0
    if not isinstance(Ma["same_plot_only"], bool):
        raise ValueError(f"machines.same_plot_only must be true or false, not {Ma['same_plot_only']!r}")
    if not isinstance(Ma["ticket_confirms"], bool):
        raise ValueError(f"machines.ticket_confirms must be true or false, not {Ma['ticket_confirms']!r}")
    Ma["ticket_close_tol"] = _num(Ma["ticket_close_tol"], "f", "machines.ticket_close_tol", 1e-9, 0.5)
    for name in ("from_dataset", "network", "spatial_check"):
        if not isinstance(Ma[name], bool):
            raise ValueError(f"machines.{name} must be true or false, not {Ma[name]!r}")
    mm_ = str(Ma["method"] if Ma["method"] is not None else "auto").strip().lower()
    mm_ = {"machine_x_machine": "machine", "combine": "machine", "whole": "machine", "track_x_track": "track",
           "point_x_point": "point", "paired_cells": "cells"}.get(mm_, mm_)
    if mm_ not in machines_mod.FACTOR_METHODS:
        raise ValueError(f"machines.method must be one of {list(machines_mod.FACTOR_METHODS)} (auto = paired cells "
                         "where the combines met on interleaved or mixed passes, no factor in blocks), not "
                         f"{Ma['method']!r}")
    Ma["method"] = mm_
    # ---- cleaning method
    m = str(S["method"] if S["method"] is not None else "occd").strip().lower()
    if m not in METHOD_ALIASES:
        raise ValueError(f"method must be one of {list(METHODS)} (occd = the OCCD cascade, sms3sigma = the SMS 3-sigma "
                         f"sequence, both = both and a comparison), not {S['method']!r}")
    S["method"] = METHOD_ALIASES[m]
    sms_mod.validate(S["sms3sigma"])
    if S["sms_import"] is not None:
        if not isinstance(S["sms_import"], dict):
            raise ValueError("sms_import must be the import record (a dict, engine.run.import_as_sms) or null")
        mi = sms_min_yield(S)[0]
        if mi is not None:
            _num(mi, "f", "sms_import.min_yield", 0, 1e6)
    if S["tag"] is not None:
        t = sanitise_tag(S["tag"])
        if not t:
            raise ValueError(f"tag {S['tag']!r} has no usable character (A-Z, a-z, 0-9, _ and - are allowed)")
        S["tag"] = t
    if S["cols"] is not None:
        if not isinstance(S["cols"], dict):
            raise ValueError("cols must be a dict {key: column name}")
        bad = set(S["cols"]) - set(COLUMN_KEYS)
        if bad:
            raise ValueError(f"unknown column key(s) {sorted(bad)}; keys are {COLUMN_KEYS}")
        for k, v in S["cols"].items():
            if v is not None and not isinstance(v, str):
                raise ValueError(f"cols.{k} must be a column name or null, not {v!r}")
    return S


def sms_min_yield(S) -> tuple:
    """(minimum dry yield for the SMS 3-sigma S0, where it comes from). The minimum of the file's own Import as SMS
    when the settings carry its record (settings['sms_import']: the resolved import settings, or a record holding them
    under 'resolved' / 'settings'): the import already left out every reading below it, so S0 must not apply another
    one (None or 0 = no minimum). Without a record: sms3sigma.min_yield, the caller's."""
    rec = S.get("sms_import") if isinstance(S, dict) else None
    if isinstance(rec, dict):
        for d in (rec.get("resolved"), rec.get("settings"), rec):
            if isinstance(d, dict) and "min_yield" in d:
                v = d.get("min_yield")
                v = None if v is None or (isinstance(v, (int, float)) and float(v) <= 0) else v
                return v, "the file's Import as SMS"
    return S["sms3sigma"].get("min_yield"), None


def describe_filter(fid: str, params: dict, threshold=None, auto=None, range_txt: str = "",
                    fallback: bool = False, floor: bool = False) -> str:
    """The rule of a filter with its thresholds, e.g. 'speed outside 1.5-6.5 mph'. For F8, `threshold` is the fraction
    used (None = the settings' value), `auto` the dose-response choice, `fallback` True when the dose-response could
    not set it (the fallback threshold was used) and `floor` True when it stopped below min_threshold (the floor was
    used); for F10, `range_txt` the computed range."""
    p = dict(params)
    if fid == "F0":
        p["pos_km"] = POS_MAX_KM
    elif fid == "F3":
        p["pct_change"] = 100 * p["max_change_per_s"]
    elif fid == "F4":
        p["pct_error"] = 100 * p["max_rel_error"]
    elif fid == "F11":
        p["pct_dev"] = 100 * p["max_rel_dev"]
    elif fid == "F10":
        p["range_txt"] = range_txt
    elif fid == "F8":
        th = p["threshold"] if threshold is None else threshold
        tol = p["auto_tolerance_pct"]
        local = p.get("reference") == "local"
        if th == "auto":
            p["threshold_txt"] = "the automatic threshold"
            p["how"] = (f"upper edge of the last overlap band within -{tol:g} % of "
                        + ("its local no-overlap controls" if local else "the no-overlap band")
                        + ", from this field's dose-response"
                        + (f", never below {100 * p['min_threshold']:.3g} %" if p.get("min_threshold") else ""))
        else:
            p["threshold_txt"] = f"{100 * float(th):.3g} %"
            if p["threshold"] == "auto" and fallback:
                p["how"] = ("fallback threshold: the lowest overlap band already fails the tolerance, so this field's "
                            "dose-response cannot set it")
            elif p["threshold"] == "auto" and floor:
                p["how"] = ("the floor: this field's dose-response stops at "
                            + (f"{100 * auto:.3g} %" if auto is not None else "a lower band")
                            + ", below the resolution of the overlap measure")
            elif p["threshold"] == "auto":
                p["how"] = "threshold from this field's dose-response"
            else:
                p["how"] = "threshold fixed in the settings" + (
                    f"; the dose-response suggests {100 * auto:.3g} %" if auto is not None else "")
    return FILTER_INFO[fid]["rule"].format(**p)


# ===================================================================================================== columns
COLUMN_KEYS = ["yield", "yield_wet", "moisture", "speed_mph", "distance_ft", "width_ft", "heading", "duration_s",
               "wet_mass_lbac", "dataset", "order", "time", "machine"]
# first entry = the SMS / Ops Center name; matching ignores case and leading/trailing underscores
COLUMN_ALIASES = {
    "yield": ["Yld_Vol_Dr", "VRYIELDVOL", "Yld_Vol_Dry", "Yield_Dry", "DryYield", "Yield_bu_ac", "Yield"],
    "yield_wet": ["Yld_Vol_We", "Yld_Vol_Wet", "Yield_Wet", "WetYield", "WETVOLUME"],
    "moisture": ["Moisture__", "Moisture", "Moist", "Moisture_pct", "GrainMoist"],
    "speed_mph": ["Speed_mph_", "Speed_mph", "VEHICLSPEED", "VEHICLSPEE", "VEHICSPEED", "Speed"],
    "distance_ft": ["Distance_f", "Distance_ft", "DISTANCE", "Dist_ft"],
    "width_ft": ["Swth_Wdth_", "Swath_Width", "SwathWidth", "SWATHWIDTH", "Swth_Wdth", "Width_ft", "Width"],
    "heading": ["Track_deg_", "Track_deg", "Hding_Veh_", "Heading", "Track", "Direction", "Course"],
    "duration_s": ["Duration_s", "Duration", "Elapsed_s"],
    "wet_mass_lbac": ["Yld_Mass_W", "Yld_Mass_Wet", "WetMass", "Wet_Mass"],
    "dataset": ["Dataset", "Task", "TaskName", "Task_Name", "Load", "LoadName"],
    "order": ["Obj__Id", "ObjectId", "Obj_Id", "Object_Id", "Record", "Rec_Id"],
    "time": ["Time", "Timestamp", "DateTime", "Date_Time", "IsoTime"],
    "machine": ["Machine", "VIN", "Combine"],
}
COLUMN_UNITS = {"yield": "bu/ac", "yield_wet": "bu/ac", "moisture": "%", "speed_mph": "mph", "distance_ft": "ft",
                "width_ft": "ft", "heading": "deg", "duration_s": "s", "wet_mass_lbac": "lb/ac", "dataset": "",
                "order": "", "time": "", "machine": ""}


def _norm(name) -> str:
    return str(name).strip().strip("_").lower()


def detect_columns(columns) -> dict:
    """{key: column or None} for COLUMN_KEYS from a list of column names (SMS / Ops Center names first, then common
    alternates; case-insensitive; a column is used for one key only). 'machine' only matches a column literally named
    Machine, VIN or Combine."""
    cols = [c for c in columns if c != "geometry"]
    by_norm = {}
    for c in cols:
        by_norm.setdefault(_norm(c), c)
    used, out = set(), {}
    for key in COLUMN_KEYS:
        out[key] = None
        for alias in COLUMN_ALIASES[key]:
            c = by_norm.get(_norm(alias))
            if c is not None and c not in used:
                out[key] = c
                used.add(c)
                break
    return out


def _parse_time(v: pd.Series):
    """(timestamps, ok): ok only when the values parse to timestamps WITH a time of day (an SMS 'Time' is a date)."""
    if pd.api.types.is_datetime64_any_dtype(v):
        t = v
    else:
        s = v.astype(str).str.strip().replace({"": None, "None": None, "nan": None, "NaT": None})
        try:
            t = pd.to_datetime(s, errors="coerce", format="ISO8601")
        except (ValueError, TypeError):
            t = pd.to_datetime(s, errors="coerce", format="mixed")
        if t.notna().sum() < 0.99 * s.notna().sum():
            t = pd.to_datetime(s, errors="coerce", format="mixed")
    if getattr(t.dt, "tz", None) is not None:
        t = t.dt.tz_convert("UTC").dt.tz_localize(None)
    good = t.notna()
    if good.sum() == 0 or good.mean() < 0.99:
        return t, False
    tod = (t[good] - t[good].dt.normalize()).dt.total_seconds()
    return t, bool((tod != 0).mean() >= 0.5)


# ===================================================================================================== shapefile I/O
# The clean file must carry the original columns unchanged. GDAL would rewrite the field definitions (N(15,4) ->
# N(24,15), Date -> String), so the kept records of the original .shp/.dbf are copied byte for byte and the new
# columns are appended to each .dbf record.
def _read_dbf(path):
    b = open(path, "rb").read()
    n, hlen, rlen = struct.unpack("<IHH", b[4:12])
    fdesc, i = [], 32
    while b[i] != 0x0D:
        fdesc.append(b[i:i + 32])
        i += 32
    recs = np.frombuffer(b, np.uint8, count=n * rlen, offset=hlen).reshape(n, rlen)
    return {"head": b[:32], "fdesc": fdesc, "n": n, "rlen": rlen, "recs": recs,
            "names": [d[:11].split(b"\0")[0].decode("latin-1") for d in fdesc]}


def _read_shp(path):
    shp = open(path, "rb").read()
    shx = open(os.path.splitext(path)[0] + ".shx", "rb").read()
    n = (len(shx) - 100) // 8
    off = np.frombuffer(shx, ">i4", count=2 * n, offset=100).reshape(n, 2).astype(np.int64) * 2
    contents = [shp[o + 8:o + 8 + ln] for o, ln in off]
    return {"stype": struct.unpack("<i", shp[32:36])[0], "contents": contents}


def _field_bytes(name, typ, values, width, dec):
    """Fixed-width dbf cells for one new column (None / NaN -> blank)."""
    out = []
    for v in values:
        if v is None or (isinstance(v, float) and not np.isfinite(v)):
            s = ""
        elif typ == "N":
            s = f"{float(v):.{dec}f}" if dec else f"{int(round(float(v)))}"
        else:
            s = str(v)
        enc = s.encode("utf-8")[:width]
        out.append(enc.rjust(width) if typ == "N" else enc.ljust(width))
    desc = name.encode("ascii")[:10].ljust(11, b"\0") + typ.encode() + b"\0" * 4 + bytes([width, dec]) + b"\0" * 14
    return desc, np.frombuffer(b"".join(out), np.uint8).reshape(len(values), width)


def _patch_cells(recs, dbf, patch):
    """Rewrite numeric cells in place (the SMS 3-sigma clean file: an SMS export after Optimize Swath Widths).
    recs: the (n x rlen) record bytes of the rows written; patch: {field name: (values n, mask n)}, only the rows of the
    mask are rewritten, in the field's own width and decimals (fewer decimals when a value would not fit). Returns
    the number of cells that still did not fit (written as '*', the dBASE overflow mark)."""
    off, over = 1, 0
    for d in dbf["fdesc"]:
        name = d[:11].split(b"\0")[0].decode("latin-1")
        typ, w, dec = chr(d[11]), d[16], d[17]
        if name in patch and typ in ("N", "F"):
            vals, mask = patch[name]
            for k in np.nonzero(np.asarray(mask, bool))[0]:
                v = vals[k]
                if v is None or not np.isfinite(v):
                    s = ""
                else:
                    q = dec
                    s = f"{float(v):.{q}f}" if q else f"{int(round(float(v)))}"
                    while len(s) > w and q > 0:
                        q -= 1
                        s = f"{float(v):.{q}f}" if q else f"{int(round(float(v)))}"
                    if len(s) > w:
                        s, over = "*" * w, over + 1
                recs[k, off:off + w] = np.frombuffer(s.encode("ascii").rjust(w), np.uint8)
        off += w
    return over


def _write_dbf(path, dbf, rows, extra, patch=None):
    """Original records `rows` verbatim + `extra` columns: list of (name, type, values, width, decimals). `patch`
    (SMS 3-sigma method only) rewrites some numeric cells, see _patch_cells."""
    recs = dbf["recs"][rows]
    over = 0
    if patch:
        recs = recs.copy()
        over = _patch_cells(recs, dbf, patch)
    blocks, descs = [recs], list(dbf["fdesc"])
    for name, typ, vals, w, d in extra:
        desc, arr = _field_bytes(name, typ, vals, w, d)
        descs.append(desc)
        blocks.append(arr)
    body = np.hstack(blocks) if len(rows) else np.zeros((0, sum(b.shape[1] for b in blocks)), np.uint8)
    rlen, hlen = body.shape[1], 32 + 32 * len(descs) + 1
    head = bytearray(dbf["head"])
    head[0] = 0x03
    struct.pack_into("<IHH", head, 4, len(rows), hlen, rlen)
    head[12:32] = bytes(20) if dbf["head"][29] == 0 else bytes(17) + dbf["head"][29:30] + bytes(2)
    with open(path, "wb") as f:
        f.write(bytes(head) + b"".join(descs) + b"\x0D" + body.tobytes() + b"\x1A")
    return over


def _write_shp(path_noext, shp, rows, xy=None):
    """Original point records `rows`; when xy (n x 2, original CRS) is given, X/Y are replaced (flow-delay shift)."""
    recs = []
    for k, i in enumerate(rows):
        c = shp["contents"][i]
        if xy is not None and len(c) >= 20 and struct.unpack("<i", c[:4])[0] in (1, 11, 21):
            c = c[:4] + struct.pack("<2d", float(xy[k, 0]), float(xy[k, 1])) + c[20:]
        recs.append(c)
    pts = [struct.unpack("<2d", c[4:20]) for c in recs if len(c) >= 20]
    zs = [struct.unpack("<d", c[20:28])[0] for c in recs if len(c) >= 36 and struct.unpack("<i", c[:4])[0] == 11]
    ms = [struct.unpack("<d", c[28:36])[0] for c in recs if len(c) >= 36 and struct.unpack("<i", c[:4])[0] == 11]
    ms += [struct.unpack("<d", c[20:28])[0] for c in recs if len(c) >= 28 and struct.unpack("<i", c[:4])[0] == 21]
    p = np.array(pts) if pts else np.zeros((1, 2))
    bbox = (p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max(),
            min(zs) if zs else 0.0, max(zs) if zs else 0.0, min(ms) if ms else 0.0, max(ms) if ms else 0.0)
    shp_len = 100 + sum(8 + len(c) for c in recs)

    def header(total_bytes):
        return struct.pack(">7i", 9994, 0, 0, 0, 0, 0, total_bytes // 2) + struct.pack("<2i", 1000, shp["stype"]) \
            + struct.pack("<8d", *bbox)
    main, index, off = io.BytesIO(), io.BytesIO(), 100
    main.write(header(shp_len))
    index.write(header(100 + 8 * len(recs)))
    for k, c in enumerate(recs):
        main.write(struct.pack(">2i", k + 1, len(c) // 2) + c)
        index.write(struct.pack(">2i", off // 2, len(c) // 2))
        off += 8 + len(c)
    open(path_noext + ".shp", "wb").write(main.getvalue())
    open(path_noext + ".shx", "wb").write(index.getvalue())


def _cpg_for(src_noext, dbf):
    """Copy the original .cpg; without one, declare UTF-8 if every text cell decodes as UTF-8, else ISO-8859-1."""
    if os.path.exists(src_noext + ".cpg"):
        return open(src_noext + ".cpg", "rb").read()
    try:
        bytes(dbf["recs"]).decode("utf-8")
        return b"UTF-8"
    except UnicodeDecodeError:
        return b"ISO-8859-1"


def write_points(path, src_path, g0, rows, extra, xy=None, byte_copy=None, crs_out=None, patch=None):
    """Write the records `rows` (original order) of the input with `extra` columns, as <path>.shp/.shx/.dbf/.prj/.cpg.
    xy (n x 2) replaces the coordinates; crs_out (pyproj CRS) is the CRS of xy when it is not the input's (the .prj
    is then written from it instead of copied). patch {field: (values, mask)}: numeric cells rewritten (the SMS
    3-sigma method only; every other cell stays byte for byte)."""
    noext, src_noext = os.path.splitext(path)[0], os.path.splitext(src_path)[0]
    for ext in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
        if os.path.exists(noext + ext):
            os.remove(noext + ext)
    if byte_copy is not None:
        shp, dbf = byte_copy
        _write_shp(noext, shp, rows, xy)
        over = _write_dbf(noext + ".dbf", dbf, rows, extra, patch)
        if crs_out is not None:
            open(noext + ".prj", "w").write(crs_out.to_wkt("WKT1_ESRI"))
        elif os.path.exists(src_noext + ".prj"):
            open(noext + ".prj", "wb").write(open(src_noext + ".prj", "rb").read())
        elif g0.crs is not None:
            open(noext + ".prj", "w").write(g0.crs.to_wkt("WKT1_ESRI"))
        open(noext + ".cpg", "wb").write(_cpg_for(src_noext, dbf))
        return "byte-copy" + (f" ({over} rescaled cells did not fit their field and hold '*')" if over else "")
    out = g0.iloc[rows].copy()
    for name, (vals, mask) in (patch or {}).items():
        # a writable copy: with pandas 3 (copy on write) to_numpy() of a float column is a read-only view
        col = np.array(pd.to_numeric(out[name], errors="coerce"), dtype=float)
        col[np.asarray(mask, bool)] = np.asarray(vals, float)[np.asarray(mask, bool)]
        out[name] = col
    for name, _, vals, _, _ in extra:
        out[name] = list(vals)
    if xy is not None:
        out = out.set_geometry(gpd.points_from_xy(xy[:, 0], xy[:, 1]), crs=crs_out or g0.crs)
    out.to_file(noext + ".shp", encoding="UTF-8")
    return "geopandas"


def _rename_clashes(extra, existing, warnings, what):
    """`extra` with every added column whose name (first 10 characters, case-insensitive) is already a field of the
    input renamed to <name[:8]>_<k>; returns (extra, {old: new}). Each rename is recorded in `warnings`."""
    taken = {str(n)[:10].upper() for n in existing}
    out, ren = [], {}
    for name, typ, vals, w, d in extra:
        new = name
        k = 2
        while new[:10].upper() in taken:
            new = f"{name[:8]}_{k}"
            k += 1
        if new != name:
            ren[name] = new
            warnings.append(f"the input already has a field named {name!r}: the column the cleaning adds is written "
                            f"as {new!r} in the {what} file")
        taken.add(new[:10].upper())
        out.append((new, typ, vals, w, d))
    return out, ren


def byte_copy_source(src_path, g0):
    """(shp, dbf) for a byte-level copy when the input is a clean point shapefile, else (None, reason)."""
    try:
        dbf, shp = _read_dbf(os.path.splitext(src_path)[0] + ".dbf"), _read_shp(src_path)
    except (OSError, struct.error, ValueError, IndexError) as e:
        return None, f"could not read the .shp/.shx/.dbf bytes ({e})"
    if shp["stype"] not in (1, 11, 21):
        return None, f"shape type {shp['stype']} is not a point type"
    if dbf["n"] != len(g0) or len(shp["contents"]) != len(g0):
        return None, "record counts of .shp/.dbf differ from the features read"
    if (dbf["recs"][:, 0] == 0x2A).any():
        return None, "the .dbf holds deleted records"
    xs = np.array([struct.unpack("<d", c[4:12])[0] if len(c) >= 20 else np.nan for c in shp["contents"]])
    if not np.allclose(xs, g0.geometry.x.values, equal_nan=True):
        return None, "the .shp coordinates differ from the features read"
    if [c for c in g0.columns if c != "geometry"] != dbf["names"]:
        return None, "the .dbf field names differ from the columns read"
    return (shp, dbf), None


def _zip(paths, zpath):
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            if os.path.exists(p):
                z.write(p, os.path.basename(p))
    return zpath


# ===================================================================================================== cleaning
def ds_key(s):
    """SMS task names carry their start time 'YY/MM/DD-HH:MM:SS'; sort datasets chronologically by it."""
    m = re.search(r"(\d\d/\d\d/\d\d-\d\d:\d\d:\d\d)", str(s))
    return m.group(1) if m else str(s)


def ds_sort_key(s):
    """Harvest order of a dataset name: its start (sms3sigma.dataset_start: the SMS time or the GreenStar 4 / Ops
    Center date; names without one last), then ds_key."""
    t = sms_mod.dataset_start(s)
    return ((t if np.isfinite(t) else np.inf), ds_key(s))


def resolve_columns(available, S):
    """Column mapping used for the run: detect_columns, overridden by settings['cols']. Raises ValueError when a
    required column is missing or a mapped column does not exist."""
    det = detect_columns(available)
    user = S.get("cols") or {}
    missing_user = [f"{k} -> {v!r}" for k, v in user.items() if v is not None and v not in available]
    if missing_user:
        raise ValueError(f"mapped column(s) not in the file: {', '.join(missing_user)}; columns are {list(available)}")
    C = {k: (user[k] if k in user else det[k]) for k in COLUMN_KEYS}
    need = [k for k in ("yield", "speed_mph", "heading") if not C[k]]
    if not (C["distance_ft"] or C["duration_s"]):
        need.append("distance_ft or duration_s")
    if not (C["width_ft"] or S.get("harvester_width_ft")):
        need.append("width_ft (or settings harvester_width_ft)")
    if need:
        raise ValueError(f"required column(s) not found: {', '.join(need)}. Map them in settings['cols'] "
                         f"(columns in the file: {list(available)})")
    return C, det


def _is_wgs84(crs) -> bool:
    """True when crs is WGS 84 geographic (EPSG:4326, also from an ESRI 'GCS_WGS_1984' .prj)."""
    if crs is None:
        return False
    try:
        return crs.to_epsg(min_confidence=20) == 4326
    except Exception:                                  # an exotic CRS pyproj cannot match: not WGS 84
        return False


def position_check(g0, nogeom):
    """F0 position rule. Returns (lon, lat, bad, counts): WGS 84 degrees of every record and the records whose position
    is not plausible: no geometry / NaN, exactly (0,0) (the no-fix record of SMS and Ops Center exports), outside the
    lon/lat ranges, or more than POS_MAX_KM from the median of the other positions (the jd_jdl decoder's rule). One such
    point used to stretch the overlap raster to the size of a continent (MemoryError)."""
    gg = g0.geometry if _is_wgs84(g0.crs) else g0.geometry.to_crs("EPSG:4326")
    lon, lat = gg.x.to_numpy(float), gg.y.to_numpy(float)
    fin = np.isfinite(lon) & np.isfinite(lat) & ~nogeom
    zero = fin & (np.abs(lon) < 1e-6) & (np.abs(lat) < 1e-6)
    rng = fin & ~zero & ((np.abs(lat) > 90) | (np.abs(lon) > 180))
    ok = fin & ~zero & ~rng
    far = np.zeros(len(g0), bool)
    if ok.any():
        lat0, lon0 = float(np.median(lat[ok])), float(np.median(lon[ok]))
        dx = np.radians(lon - lon0) * 6371.0088 * np.cos(np.radians(lat0))
        dy = np.radians(lat - lat0) * 6371.0088
        far = ok & ~(np.hypot(dx, dy) <= POS_MAX_KM)
    counts = {"no position": int((~fin).sum()), "(0,0)": int(zero.sum()), "outside lon/lat ranges": int(rng.sum()),
              f"> {POS_MAX_KM:g} km from the median position": int(far.sum())}
    return lon, lat, ~(ok & ~far), counts


def too_few_message(cascade, n_in, n_kept) -> str:
    """Why no clean file can be written: the filter that removed the most points (ties: the largest share of the points
    reaching it), with a hint for the setting that usually causes it."""
    hints = {
        "F0": "the yield column is empty or zero, or the positions are missing: check the column mapping and the file.",
        "F1": "is the moisture range right for this crop (dry canola or flax is often below 8 %)? Change the F1 "
              "range or switch F1 off.",
        "F2": "is the speed range right for this machine (the speed column must be in mph)?",
        "F8": "the overlap threshold removes most of the field: check the swath width, or raise / switch off F8.",
        "F9": "check the wet-mass column and its unit (lb/ac), or the F9 range.",
    }
    on = [c for c in cascade if c["on"] and c["removed"] + c["remaining"] > 0]
    if not on:
        return f"only {n_kept} of the {n_in:,} points survive the cleaning; a clean file needs at least {MIN_KEPT}."
    top = max(on, key=lambda c: (c["removed"], c["removed"] / (c["removed"] + c["remaining"])))
    reach = top["removed"] + top["remaining"]
    return (f"only {n_kept} of the {n_in:,} points survive the cleaning; a clean file needs at least {MIN_KEPT}. "
            f"{top['filter']} ({top['name']}) removed {top['removed']:,} of the {reach:,} points that reached it "
            f"({100 * top['removed'] / reach:.0f} %; rule: {top['criterion']}): "
            + hints.get(top["filter"], "check that filter's thresholds in the settings.")
            + " No clean file was written.")


def aoi_zone(buffer_m) -> str:
    """The area the cleaning processes: 'the field of interest + 50 m', or 'the field of interest' when the records
    are cut at the boundary itself (buffer 0 m, the app's default)."""
    return "the field of interest" + (f" + {float(buffer_m):g} m" if buffer_m else "")


def _legacy_boundary(S) -> bool:
    """True when the AOI came from the v1 'boundary' alias: level diagnostics inside the polygon(s) only, nothing
    dropped, clipped or tagged (also for a file with several polygons), so the clean file stays the v1 one."""
    return bool(S.get("boundary")) and S["aoi"].get("file") == S.get("boundary") and S["aoi"].get("buffer_m") is None \
        and not S["aoi"].get("clip_output")


def _v2_run(S, plan) -> bool:
    """True when the run engages a v2 feature (crop, standard moisture or an AOI given directly): the clean
    file then also carries the v2 columns (Yld_Dry_Sy, Harv_Day, Session, Plot_Id, Tgt_Rate). A plain v1 run (none of
    them; the 'boundary' alias, whatever the number of polygons) writes the clean file exactly as before, byte
    for byte."""
    if S.get("crop") or S["moisture"]["standard_pct"] is not None:
        return True
    if (S.get("scale_ticket") or {}).get("value") is not None:        # the grower's weight given the v2 way
        return True
    if _legacy_boundary(S):
        return False
    A = S["aoi"]
    if A["file"] and (A["clip_output"] or A["buffer_m"] is not None):
        return True
    return bool(plan is not None and plan.is_plan)


# ===================================================================================================== scale ticket
def ticket_spec(S) -> dict | None:
    """The grower's weight the settings carry, or None: {value, unit, basis, moisture_pct, note, legacy}. The v2 dict
    settings['scale_ticket'] (legacy False) or the v1 alias scale_ticket_bu + scale_ticket_basis (unit bu, legacy True:
    the run then writes Yld_Level only, as the v1 engine did)."""
    T = S.get("scale_ticket") or {}
    if T.get("value") is not None:
        return {"value": float(T["value"]), "unit": T.get("unit") or "lb", "basis": T.get("basis") or "wet",
                "moisture_pct": T.get("moisture_pct"), "note": T.get("note"), "area_ac": T.get("area_ac"),
                "test_weight_lb_bu": T.get("test_weight_lb_bu"), "use": T.get("use") or "adjust",
                "bushel_from": T.get("bushel_from") or "file", "legacy": False}
    if S.get("scale_ticket_bu"):
        return {"value": float(S["scale_ticket_bu"]), "unit": "bu", "basis": S.get("scale_ticket_basis") or "wet",
                "moisture_pct": None, "note": None, "area_ac": None, "test_weight_lb_bu": None, "use": "adjust",
                "bushel_from": "file", "legacy": True}
    return None


def ticket_to_lb(value, unit: str, test_weight_lb_bu=None, area_ac=None):
    """A ticket in lb: lb / kg / t need no test weight; bu (and bu/ac x area_ac) x test weight (None when the test
    weight is unknown)."""
    unit = TICKET_UNIT_ALIASES.get(str(unit).strip().lower(), str(unit).strip().lower())
    if unit not in TICKET_UNITS:
        raise ValueError(f"unknown ticket unit {unit!r}: use one of {list(TICKET_UNITS)}")
    if unit in ("bu", "bu/ac"):
        bu = ticket_to_bu(value, unit, area_ac=area_ac)
        return bu * float(test_weight_lb_bu) if test_weight_lb_bu else None
    return float(value) * TICKET_LB_PER_UNIT[unit]


def ticket_to_bu(value, unit: str, test_weight_lb_bu=None, area_ac=None):
    """A ticket in bu: bu as given; bu/ac x area_ac (the acres the grower's yield is over); lb / kg / t divided by
    the test weight (lb/bu). Raises when the test weight or the acres are needed and missing."""
    unit = TICKET_UNIT_ALIASES.get(str(unit).strip().lower(), str(unit).strip().lower())
    if unit == "bu":
        return float(value)
    if unit == "bu/ac":
        if not area_ac or not np.isfinite(float(area_ac)) or float(area_ac) <= 0:
            raise ValueError("the grower's yield is per acre (bu/ac) but no area is known to make it a total: give "
                             "scale_ticket.area_ac (the grower's acres) or a field boundary")
        return float(value) * float(area_ac)
    lb = ticket_to_lb(value, unit)
    if not test_weight_lb_bu:
        raise ValueError(f"the scale ticket is in {unit} but no test weight (lb/bu) is available to convert it to "
                         "bushels: give test_weight_lb_bu (or the crop), or enter the ticket in bu")
    return lb / float(test_weight_lb_bu)


def _source_test_weight(source):
    """The decoder's test weight from a clean(source=...) dict when it is a single number (calibration dict or the
    'test weight 48.00 lb/bu' part of calibration_summary), else None."""
    if not isinstance(source, dict):
        return None
    cands = []
    cal = source.get("calibration")
    if isinstance(cal, dict):
        cands.append(cal.get("test_weight_lb_bu"))
    cands.append(source.get("test_weight_lb_bu"))
    summ = source.get("calibration_summary")
    if isinstance(summ, str):
        m = re.search(r"test weight ([0-9.]+) lb/bu", summ)
        if m:
            cands.append(m.group(1))
    for v in cands:
        if isinstance(v, dict):
            v = list(v.values())
        if isinstance(v, (list, tuple, set)):
            vals = set()
            for x in v:
                try:
                    vals.add(float(x))
                except (TypeError, ValueError):
                    continue
            if len(vals) == 1:
                return vals.pop()
            continue
        if v is None or isinstance(v, bool):
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def resolve_test_weight(S, frame=None, source=None, cols=None):
    """(test weight lb/bu, where it came from) for the ticket conversion, or (None, None). Priority: settings
    test_weight_lb_bu > the file's own ratio median(wet mass lb/ac / wet volume bu/ac) over >= TICKET_MIN_RATIO_POINTS
    readings when both columns exist (frame: a DataFrame of the export; cols: its column mapping, else detect_columns)
    > the decoder report (source) > the crop table (settings crop, engine.formats.common.CROPS)."""
    S = S or {}
    if S.get("test_weight_lb_bu"):
        return float(S["test_weight_lb_bu"]), "settings (test_weight_lb_bu)"
    tw, src = _file_test_weight(frame, cols)
    if tw:
        return tw, src
    tw = _source_test_weight(source)
    if tw:
        return float(tw), "decoder report"
    crop = (S.get("crop") or "").strip().lower() if isinstance(S.get("crop"), str) else None
    if crop in CROPS:
        return float(CROPS[crop][0]), f"crop table ({crop})"
    return None, None


def _file_test_weight(frame, cols=None):
    """(median wet mass lb/ac / wet volume bu/ac of the file, its description) over >= TICKET_MIN_RATIO_POINTS
    readings with both, or (None, None): the weight of the bushel the file's volume columns are counted in."""
    if frame is None:
        return None, None
    C = cols or detect_columns(list(frame.columns))
    wm, wv = C.get("wet_mass_lbac"), C.get("yield_wet")
    if wm and wv and wm in frame and wv in frame:
        mass = pd.to_numeric(frame[wm], errors="coerce").to_numpy(float)
        vol = pd.to_numeric(frame[wv], errors="coerce").to_numpy(float)
        ok = np.isfinite(mass) & np.isfinite(vol) & (mass > 0) & (vol > 0)
        if ok.sum() >= TICKET_MIN_RATIO_POINTS:
            return float(np.median(mass[ok] / vol[ok])), f"the file (median {wm} / {wv} over {int(ok.sum()):,} readings)"
    return None, None


def monitor_bushel_weight(S, frame=None, source=None, cols=None):
    """(lb per bushel of the file's own volume columns, where it came from), or (None, None). This is the bushel the
    monitor total is counted in, so the only one a weight in lb / kg / t may be turned into bushels with: a weight
    divided by another bushel weight and compared with the monitor's bushels is wrong by file / other (e.g. 60 / 63 =
    -4.8 %). Priority: the file's own ratio (median wet mass / wet volume) > the decoder report (source) > settings
    test_weight_lb_bu (a file without both columns: the weight to volume the yields were made with) > the crop table.
    resolve_test_weight() keeps the older order (settings first) for callers that want the user's number."""
    S = S or {}
    tw, src = _file_test_weight(frame, cols)
    if tw:
        return tw, src
    tw = _source_test_weight(source)
    if tw:
        return float(tw), "decoder report"
    if S.get("test_weight_lb_bu"):
        return float(S["test_weight_lb_bu"]), "the settings (test_weight_lb_bu, the weight to volume of the file)"
    crop = (S.get("crop") or "").strip().lower() if isinstance(S.get("crop"), str) else None
    if crop in CROPS:
        return float(CROPS[crop][0]), f"crop table ({crop})"
    return None, None


def declared_bushel_weight(S, source=None):
    """(the bushel weight declared for the file, where it came from) or (None, None): settings test_weight_lb_bu (the
    weight to volume the file was imported / made with) > the decoder report > the crop table. What the file's own
    mass / volume ratio is checked against (bushel_check) and what scale_ticket.bushel_from 'declared' uses."""
    S = S or {}
    if S.get("test_weight_lb_bu"):
        return float(S["test_weight_lb_bu"]), "the settings (test_weight_lb_bu)"
    tw = _source_test_weight(source)
    if tw:
        return float(tw), "the decoder report"
    crop = (S.get("crop") or "").strip().lower() if isinstance(S.get("crop"), str) else None
    if crop in CROPS:
        return float(CROPS[crop][0]), f"the crop table ({crop})"
    return None, None


def bushel_check(file_tw, file_src, declared_tw, declared_src, used: str = "file") -> str | None:
    """A warning when the file's own bushel (its mass / volume ratio) and the declared one differ by more than
    BUSHEL_DIFF_TOL, naming both values and their ratio (a ratio of 1.1209 is a mass column in kg/ha read as lb/ac),
    and how to make the other one win; None when they agree or one is unknown."""
    if not file_tw or not declared_tw:
        return None
    r = float(file_tw) / float(declared_tw)
    if abs(r - 1) <= BUSHEL_DIFF_TOL:
        return None
    hint = ""
    if abs(r / KG_HA_PER_LB_AC - 1) < 0.005:
        hint = f"; {KG_HA_PER_LB_AC:.4f} = kg/ha over lb/ac: the mass column looks like kg/ha, not lb/ac"
    elif abs(r * KG_HA_PER_LB_AC - 1) < 0.005:
        hint = f"; 1 / {KG_HA_PER_LB_AC:.4f}: the volume or the mass column is in another unit"
    which = ("the file's bushel is used (scale_ticket.bushel_from 'file'); set scale_ticket.bushel_from to 'declared' "
             "when the declared one is right" if used == "file" else
             "the declared bushel is used (scale_ticket.bushel_from 'declared'); set it to 'file' to use the file's")
    return (f"scale ticket: the file's own bushel is {float(file_tw):.2f} lb/bu ({file_src}) but "
            f"{declared_src} says {float(declared_tw):.2f} lb/bu (ratio {r:.4f}{hint}). The grower's weight is turned "
            f"into the monitor's bushels with one of them, so L differs by that ratio: {which}")


def grower_bushel_weight(S, monitor_tw=None):
    """(another bushel weight than the file's in lb/bu, where it came from, explicit), or (None, None, False) when
    there is none (every bushel is then the file's). explicit True: scale_ticket.test_weight_lb_bu, the grower's own
    bushel weight: a weight the grower gave in bu or bu/ac is in bushels of that weight. explicit False: settings
    test_weight_lb_bu when it differs from the file's bushel (`monitor_tw`) by more than BUSHEL_DIFF_TOL; that
    setting was the only place for a bushel weight before scale_ticket carried one, so it only says how many such
    bushels a weight in lb / kg / t makes (reported, never compared), and bushels given are compared as given."""
    S = S or {}
    T = S.get("scale_ticket") or {}
    if T.get("value") is not None and T.get("test_weight_lb_bu"):
        return float(T["test_weight_lb_bu"]), "the grower's bushel weight", True
    tw = S.get("test_weight_lb_bu")
    if tw and monitor_tw and abs(float(tw) / float(monitor_tw) - 1) > BUSHEL_DIFF_TOL:
        return float(tw), "the bushel weight given", False
    return None, None, False


def ticket_value_text(value, unit: str = "lb", bu_decimals: int = 1) -> str:
    """The ticket's value as the ticket wrote it: '1,500,000 lb', '9,000 bu', '7,500.5 bu', '200.1234 t', '0.5 t'. lb
    and whole numbers carry no decimals; a fractional bu `bu_decimals`; a fractional kg / t up to four (a tonne
    ticket is written with its decimals: 200.1234 t is 441,197 lb, '200 t' would be 272 lb short)."""
    v = float(value)
    u = str(unit or "lb")
    if u == "bu/ac":                              # a yield: '50 bu/ac', '50.25 bu/ac'
        return f"{v:,.0f} {u}" if v == int(v) else f"{v:,.2f} {u}"
    if u in ("kg", "t") and v != int(v):
        return f"{v:,.4f}".rstrip("0").rstrip(".") + f" {u}"
    if u == "bu" and v != int(v):
        return f"{v:,.{bu_decimals}f} {u}"
    return f"{v:,.0f} {u}"


def ticket_summary(S, test_weight_lb_bu=None, test_weight_source=None, area_ac=None, area_source=None,
                   grower_test_weight=None, grower_test_weight_source=None, grower_explicit=False) -> dict | None:
    """The ticket of the settings converted: {value, unit, basis, moisture_pct, note, legacy, ticket_lb, ticket_bu,
    ticket_area_ac, ticket_area_source, test_weight_used, test_weight_source, caption, ticket_bu_grower,
    test_weight_grower, test_weight_grower_source, bushels_differ}, or None without a ticket. The caption is the
    one-line conversion the app shows ("1,500,000 lb = 31,250.00 bu at 48.00 lb/bu (test weight from the file)";
    "50 bu/ac x 100.00 ac (the field boundary) = 5,000.00 bu"). A bu/ac ticket uses its own area_ac when given, else
    `area_ac` (the caller's default: the boundary's area or the area harvested). Raises ValueError when a lb / kg / t
    ticket has no test weight or a bu/ac ticket no area.

    test_weight_lb_bu is the bushel of the monitor's total (monitor_bushel_weight): ticket_bu is ALWAYS in that
    bushel, the one L is computed in. grower_test_weight (grower_bushel_weight) is another bushel weight, when one was
    given and differs: a weight in lb / kg / t is still divided by the monitor's bushel (a comparison of mass) and
    ticket_bu_grower says how many bushels of the other weight it makes (reported only); a weight in bu or bu/ac with
    grower_explicit (the grower's own bushel weight) is bushels of that weight, so ticket_lb = bu x the grower's
    weight and ticket_bu = ticket_lb / the monitor's bushel; without grower_explicit the bushels are compared as
    given."""
    tk = ticket_spec(S)
    if tk is None:
        return None
    tw_m = float(test_weight_lb_bu) if test_weight_lb_bu else None
    tw_g = float(grower_test_weight) if grower_test_weight else None
    if tw_m is None and tw_g is not None:            # no bushel known for the file: the one given is all there is
        tw_m, test_weight_source, tw_g = tw_g, grower_test_weight_source, None
    differ = bool(tw_m and tw_g and abs(tw_g / tw_m - 1) > BUSHEL_DIFF_TOL)
    if not differ:
        tw_g = None
    tw = tw_m
    t_area = t_area_src = None
    if tk["unit"] == "bu/ac":
        if tk.get("area_ac"):
            t_area, t_area_src = float(tk["area_ac"]), "the grower's acres"
        elif area_ac:
            t_area, t_area_src = float(area_ac), area_source or "the field's area"
    bu = ticket_to_bu(tk["value"], tk["unit"], tw, area_ac=t_area)
    lb = ticket_to_lb(tk["value"], tk["unit"], tw, area_ac=t_area)
    bu_g = None
    mass_unit = tk["unit"] not in ("bu", "bu/ac")
    g_src = grower_test_weight_source or "the grower's bushel weight"
    if differ and mass_unit:
        bu_g = lb / tw_g                                 # reported: the same weight in the other bushel
    elif differ and grower_explicit:
        bu_g = bu                                        # the bushels the grower gave are bushels of tw_g
        lb = bu_g * tw_g
        bu = lb / tw                                     # the same grain in the monitor's bushel
    v = tk["value"]
    vtxt = (ticket_value_text(v, tk["unit"]).rsplit(" ", 1)[0] if tk["unit"] in ("kg", "t", "bu/ac") else
            (f"{v:,.0f}" if v >= 100 else f"{v:,.3g}"))
    own = differ and not mass_unit and grower_explicit   # the grower's bushels made a mass, then the file's bushels
    if tk["unit"] == "bu/ac":
        cap = f"{vtxt} bu/ac x {t_area:,.2f} ac ({t_area_src}) = {(bu_g if own else bu):,.2f} bu"
    elif tk["unit"] == "bu":
        cap = f"{vtxt} bu"
    else:
        cap = f"{vtxt} {tk['unit']} = {bu:,.2f} bu at {tw:.2f} lb/bu (test weight from {test_weight_source})"
    if own:
        cap += (f" of {tw_g:.2f} lb ({g_src}) = {lb:,.0f} lb = {bu:,.2f} bu at {tw:.2f} lb/bu (test weight from "
                f"{test_weight_source}: the bushel of the monitor's total)")
    elif not mass_unit and lb and tw:
        cap += f" = {lb:,.0f} lb at {tw:.2f} lb/bu (test weight from {test_weight_source})"
    if differ and mass_unit:
        cap += (f"; that weight is {bu_g:,.2f} bu at {tw_g:.2f} lb/bu ({g_src}), not compared: the monitor's bushels "
                f"weigh {tw:.2f} lb")
    elif differ and not own:
        cap += (f"; the bushels are compared as given (a bushel weight of {tw_g:.2f} lb/bu from {g_src} is not "
                "applied to a weight given in bushels)")
    cap += f", {tk['basis']} basis" + (f", ticket moisture {tk['moisture_pct']:g} %" if tk["moisture_pct"] is not None
                                        else "")
    return {**tk, "ticket_lb": lb, "ticket_bu": bu, "ticket_area_ac": t_area, "ticket_area_source": t_area_src,
            "test_weight_used": tw, "test_weight_source": test_weight_source, "caption": cap,
            "ticket_bu_grower": bu_g, "test_weight_grower": tw_g,
            "test_weight_grower_source": (g_src if tw_g else None), "bushels_differ": differ,
            "grower_bushels_applied": bool(own)}


# ===================================================================================================== geometry
def travel_direction(x, y, heading_deg, link):
    """Unit direction of travel (tx, ty), x east / y north, at every reading: the centred chord from the previous to
    the next reading of the same sequence (link[i]: the step i-1 -> i is usable), the one-sided chord at a sequence
    end, the logged heading where neither step is usable. Taken from the positions, so it carries neither the grid
    convergence of a projected plane nor the smoothing of the John Deere Track (which lags behind the travel by a
    few records)."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    link = np.asarray(link, bool)
    n = len(x)
    bx, by = np.zeros(n), np.zeros(n)
    if n > 1:
        with np.errstate(invalid="ignore"):
            bx[1:] = np.where(link[1:], x[1:] - x[:-1], 0.0)
            by[1:] = np.where(link[1:], y[1:] - y[:-1], 0.0)
    tx, ty = bx + np.r_[bx[1:], 0.0], by + np.r_[by[1:], 0.0]     # backward + forward step = the centred chord
    L = np.hypot(tx, ty)
    h = np.radians(np.asarray(heading_deg, float))
    ok = L > 1e-9
    with np.errstate(invalid="ignore", divide="ignore"):
        tx = np.where(ok, tx / np.where(ok, L, 1.0), np.sin(h))
        ty = np.where(ok, ty / np.where(ok, L, 1.0), np.cos(h))
    return tx, ty


def swept_strips(x, y, dist_m, width_m, heading_deg, new_seq=None, offset_m=None, max_step_factor=3.0):
    """(n, 4, 2) corners (left-back, right-back, right-front, left-front) of the ground each reading's header swept:
    the quadrilateral between the header line at the previous reading of the same sequence and the header line at
    this one. Each line lies across the direction of travel at its reading (travel_direction), with the reading's own
    width, centred offset_m to the right of travel (Y_Offset: where the active sections of a narrowed John Deere
    header are). Both lines of a strip carry the reading's own width and offset (an earlier form drew the back line with
    the previous reading's width; the two agree on a full-width header). Consecutive strips share an edge, so they tile
    the swept ground without overlap or gaps. Where the two header lines cross (a turn radius below half the header: a
    small share of the readings) the strip is the two triangles of the bow tie, which share some ground with the
    predecessor's strip. A
    reading without a usable step from its predecessor (first of a sequence, a step of 0, or a step > max_step_factor
    x Distance + 1 m: a GNSS jump) gets the v2.3 rectangle, Distance back along the direction of travel. A reading
    without a finite position or direction, a positive width or (for the rectangle) a positive distance is NaN."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    d = np.asarray(dist_m, float)
    hw = np.asarray(width_m, float) / 2
    n = len(x)
    off = np.zeros(n) if offset_m is None else np.nan_to_num(np.asarray(offset_m, float))
    fin = np.isfinite(x) & np.isfinite(y)
    ns = np.zeros(n, bool) if new_seq is None else np.asarray(new_seq, bool).copy()
    step = np.full(n, np.nan)
    if n:
        ns[0] = True
    if n > 1:
        step[1:] = np.hypot(np.diff(x), np.diff(y))
    prev_fin = np.r_[False, fin[:-1]] if n else fin
    with np.errstate(invalid="ignore"):
        link = ~ns & fin & prev_fin & (step > 0) & (step <= max_step_factor * np.nan_to_num(d) + 1.0)
    tx, ty = travel_direction(x, y, heading_deg, link)
    nx, ny = ty, -tx                                             # right of travel
    lo, hi = off - hw, off + hw                                  # left and right ends of the header line
    px, py, pnx, pny = ((np.r_[np.nan, v[:-1]] if n else v) for v in (x, y, nx, ny))
    bx, by = x - tx * d, y - ty * d                              # back edge of the rectangle (unlinked readings)
    lk = link[:, None]
    q = np.stack([np.where(lk, np.c_[px + pnx * lo, py + pny * lo], np.c_[bx + nx * lo, by + ny * lo]),
                  np.where(lk, np.c_[px + pnx * hi, py + pny * hi], np.c_[bx + nx * hi, by + ny * hi]),
                  np.c_[x + nx * hi, y + ny * hi], np.c_[x + nx * lo, y + ny * lo]], axis=1)
    with np.errstate(invalid="ignore"):
        ok = fin & (hw > 0) & (link | (d > 0)) & np.isfinite(q).all(axis=(1, 2))
    q[~ok] = np.nan
    return q


def strips_of(g):
    """The swept strips clean() stored on the processed table (internal columns _qx0.._qy3), (n, 4, 2), or None."""
    if "_qx0" not in g:
        return None
    return np.stack([np.c_[g[f"_qx{k}"].to_numpy(float), g[f"_qy{k}"].to_numpy(float)] for k in range(4)], axis=1)


def edge_distance(x, y, half_width_m, cell=EDGE_CELL_M):
    """Distance (m) from every reading to the edge of the harvested area: the positions on a `cell` grid, grown by
    half a header + 2 m (the strip each reading swept plus GNSS scatter), holes filled (an unharvested island inside
    the field is not an edge), then the Euclidean distance transform; NaN without a position. The edge measure of the
    edge-matched controls (local_control_ratio)."""
    from scipy import ndimage
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    e = np.full(len(x), np.nan)
    if not ok.any():
        return e
    pad = 50.0
    x0, y0 = x[ok].min() - pad, y[ok].min() - pad
    nxc, nyc = int((x[ok].max() + pad - x0) / cell) + 1, int((y[ok].max() + pad - y0) / cell) + 1
    ix, iy = ((x[ok] - x0) / cell).astype(int), ((y[ok] - y0) / cell).astype(int)
    occ = np.zeros((nyc, nxc), bool)
    occ[iy, ix] = True
    grow = (float(half_width_m) if np.isfinite(half_width_m) else 0.0) + 2.0
    harv = ndimage.distance_transform_edt(~occ) * cell <= grow
    harv = ndimage.binary_fill_holes(harv)
    e[ok] = ndimage.distance_transform_edt(harv)[iy, ix] * cell
    return e


def local_control_ratio(yv, f, machine, pass_id, x, y, edge, pop, radius=15.0, edge_tol=4.0, nmin=5):
    """Each record of `pop` against its local no-overlap controls: the records of `pop` with f == 0 of the same
    combine, of another pass, within `radius` m and within `edge_tol` m of the same distance from the harvested edge,
    at least `nmin` of them (edge-matched controls). Returns (ratio, n_controls):
    y / median(y of the controls), NaN and 0 without enough controls. A record with f == 0 is compared with the OTHER
    no-overlap records: the placebo of the dose-response."""
    yv = np.asarray(yv, float)
    f = np.asarray(f, float)
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    e = np.asarray(edge, float)
    pop = np.asarray(pop, bool) & np.isfinite(yv) & np.isfinite(x) & np.isfinite(y)
    mk = np.asarray(machine).astype(str)
    ps = np.asarray(pass_id)
    xy = np.c_[x, y]
    ratio, nctl = np.full(len(yv), np.nan), np.zeros(len(yv), int)
    for k in np.unique(mk[pop]):
        tgt = np.flatnonzero(pop & (mk == k))
        cand = np.flatnonzero(pop & (mk == k) & (f == 0))
        if len(cand) < nmin:
            continue
        tree = cKDTree(xy[cand])
        for s in range(0, len(tgt), 5000):              # in chunks: the neighbour lists grow with the radius squared
            chunk = tgt[s:s + 5000]
            for j, lst in zip(chunk, tree.query_ball_point(xy[chunk], r=radius)):
                if len(lst) < nmin:
                    continue
                c = cand[np.asarray(lst, int)]
                with np.errstate(invalid="ignore"):
                    c = c[(ps[c] != ps[j]) & (np.abs(e[c] - e[j]) <= edge_tol)]
                if len(c) >= nmin:
                    m = float(np.median(yv[c]))
                    if m > 0:
                        ratio[j], nctl[j] = yv[j] / m, len(c)
    return ratio, nctl


def combine_key(g, C) -> tuple:
    """(key per record, basis) of the combine that logged each record: the machine column; else the sources SMS writes
    in parentheses at the end of the dataset names when they differ (the _machine of clean(), machines.from_dataset);
    else the display serial SMS writes at the end of Case IH / Ag Leader dataset names; else the Ops Center log (a
    record-id restart inside a dataset, ' #log' of the processing key: one log per combine and task); else one combine
    for the whole file."""
    if C.get("machine"):
        return g["_machine"].astype(str).to_numpy(), f"machine column {C['machine']!r}"
    if g["_machine"].nunique() > 1:
        return g["_machine"].astype(str).to_numpy(), sms_mod.DATASET_MACHINES_BASIS
    if C.get("dataset"):
        ds = g[C["dataset"]].astype(str)
        if sms_mod.dataset_serials(ds):
            return (ds.map(lambda s: sms_mod.SERIAL_RE.search(s).group(1)).to_numpy(),
                    "display serial in the dataset names")
        if "_ds_key" in g:
            logs = g["_ds_key"].astype(str).str.extract(r"( #log\d+)$")[0]
            if logs.notna().any():
                return (ds + logs.fillna("")).to_numpy(), "Ops Center log (record-id restart) of each dataset"
    return np.array(["M1"] * len(g), dtype=object), "one combine (no machine column, source, serial or log id)"


def lag_source_index(pass_id, lag_s):
    """For every record (processing order, passes contiguous): the row of the position its yield belongs to after a
    flow-delay shift of lag_s records within its pass (the k-th record of a pass -> the (k + lag_s)-th position),
    clamped to the pass, and whether it fell outside the pass (an orphan: its grain came from beyond the pass ends)."""
    pid = np.asarray(pass_id)
    n = len(pid)
    idx = np.arange(n)
    if lag_s == 0 or n == 0:
        return idx, np.zeros(n, bool)
    start = np.r_[True, pid[1:] != pid[:-1]]
    end = np.r_[pid[1:] != pid[:-1], True]
    first = np.maximum.accumulate(np.where(start, idx, 0))
    last = np.minimum.accumulate(np.where(end, idx, n)[::-1])[::-1]
    t = idx + int(lag_s)
    return np.clip(t, first, last), (t < first) | (t > last)


def _lag_scan(g, alive, C, width_m, axis="dominant"):
    """The residual flow-delay scan: correlation of the smoothed yield (5-record rolling median) between
    opposite-direction neighbours for shifts -LAG_RANGE..LAG_RANGE records, on the records of `alive` at least 15
    alive records from both ends of their pass. axis "dominant" (v2.4): the passes of the axis (E-W or N-S) with the
    larger smaller-direction count, i.e. the direction the field was harvested in; "ew_first" (v2.3): E-W whenever
    both E and W hold >= 200 records, which on a N-S field scanned the headland passes (a few passes per direction).
    Returns (scan rows, scan frame, best, r at 0)."""
    k = pd.Series(alive)
    pos = k.groupby(g.Pass_Id).cumsum()
    npass = k.groupby(g.Pass_Id).transform("sum")
    core = g[alive & (pos >= 15).values & ((npass - pos) > 15).values].copy()
    core["ysm"] = core.groupby("Pass_Id")[C["yield"]].transform(
        lambda v: v.astype(float).rolling(5, center=True, min_periods=3).median())
    hd = core[C["heading"]] % 360
    E, Wst = core[hd.between(45, 135)], core[hd.between(225, 315)]
    Nn, Ss = core[(hd < 45) | (hd > 315)], core[hd.between(135, 225)]
    if axis == "dominant":
        if min(len(Nn), len(Ss)) > min(len(E), len(Wst)):
            E, Wst = Nn, Ss
    elif len(E) < 200 or len(Wst) < 200:        # passes run N-S
        E, Wst = Nn, Ss
    scan = []
    for lag in range(-LAG_RANGE, LAG_RANGE + 1):
        aa = E.assign(v=E.groupby("Pass_Id").ysm.shift(-lag)).dropna(subset=["v"])
        bb = Wst.assign(v=Wst.groupby("Pass_Id").ysm.shift(-lag)).dropna(subset=["v"])
        if len(aa) < 100 or len(bb) < 100:
            continue
        dd, jj = cKDTree(np.c_[bb._x, bb._y]).query(np.c_[aa._x, aa._y],
                                                     distance_upper_bound=float(np.median(width_m)) * 1.2)
        ok = np.isfinite(dd)
        if ok.sum() < 3:
            continue
        scan.append(dict(lag=lag, n=int(ok.sum()), r=float(np.corrcoef(aa.v.values[ok], bb.v.values[jj[ok]])[0, 1])))
    Sc = pd.DataFrame(scan)
    best = int(Sc.loc[Sc.r.idxmax(), "lag"]) if len(Sc) else 0
    r0 = float(Sc.set_index("lag").r.get(0, np.nan)) if len(Sc) else float("nan")
    return scan, Sc, best, r0


def _lag_decision(setting, Sc, best, r0, warnings):
    """(shift in records, rule text) from the scan and the lag_correction setting."""
    lag_s = 0
    if setting == "auto":
        if not len(Sc):
            warnings.append("flow-delay scan impossible (fewer than 100 opposite-direction neighbours): no shift")
        elif 2 <= abs(best) < LAG_RANGE and Sc.r.max() - r0 > 0.01:
            lag_s = -best                        # the yield logged at i belongs to the position |best| records away
    elif setting != "none":
        lag_s = -int(setting)                    # the setting is in the scan's convention: the shift auto applies
    if len(Sc) and abs(best) == LAG_RANGE:
        warnings.append(f"flow-delay scan: the best shift ({best:+d} records) is at the edge of the scanned range "
                        f"(+/-{LAG_RANGE}), so the optimum is not resolved"
                        + ("; the auto rule applies no shift in that case - check the lag figure" if setting == "auto"
                           else ""))
    if setting == "auto":
        rule = f"auto: shift only when 2 <= |best| < {LAG_RANGE} records and the r gain > 0.01"
    elif setting == "none":
        rule = "none: never shift"
    else:
        rule = (f"fixed: correction for a scan optimum of {int(setting):+d} records (geometry shifted by "
                f"{-int(setting):+d} records)")
    return lag_s, rule


def _shifted_positions(g, lag_s, D, ux, uy):
    """Output positions after a flow-delay shift of lag_s records within each pass: the position of the record
    lag_s rows away; beyond the pass ends, extended along the heading at the logged distance."""
    gx, gy = g._x.values.copy(), g._y.values.copy()
    if lag_s:
        for _, idx in g.groupby("Pass_Id").indices.items():
            n = len(idx)
            tgt = np.arange(n) + lag_s
            inside = (tgt < n) & (tgt >= 0)
            gx[idx[inside]] = g._x.values[idx[tgt[inside]]]
            gy[idx[inside]] = g._y.values[idx[tgt[inside]]]
            ext = ~inside                        # beyond the pass ends: extend along the heading at the logged distance
            if ext.any():
                end = np.where(tgt[ext] >= n, idx[-1], idx[0])
                steps = np.where(tgt[ext] >= n, tgt[ext] - (n - 1), tgt[ext])
                gx[idx[ext]] = g._x.values[end] + steps * D[end] * ux[end]
                gy[idx[ext]] = g._y.values[end] + steps * D[end] * uy[end]
    return gx, gy


IMPORT_MAX_STEP = "above the max. yield volume"          # engine/sms_import.apply's left-out step of the maximum


def import_max_check(imp, steps, passes, mode) -> tuple[dict | None, list]:
    """The import maximum next to S5a: (record, warnings). imp = the file's Import as SMS record (settings
    ['sms_import']: resolved max_yield, left_out, left_out_by_group, sources), steps = the SMS 3-sigma steps rows
    (group, step, hi), passes = {group: yield passes}, mode = the grouping mode (machine | task | field). Per S5a group:
    its upper limit, the readings of that group the import maximum left out (per combine when the grouping is per
    combine, the whole file when it is the field, None per task: the import counts per combine), the yield passes, and
    two flags: max_below_s5a_hi (the maximum cut readings the 3-sigma step would have kept) and
    one_pass_after_left_out (readings were left out and the dry yield ran one pass: holding them, the SD of S5a would
    be larger). A warning is added only when the maximum is not the source's own (the user typed it: sources
    max_yield 'you'); with SMS's value for the source SMS's import leaves out the same readings, so the flags are
    reported, not warned. (None, []) without an import record."""
    if not isinstance(imp, dict) or not imp:
        return None, []
    mx = (imp.get("resolved") or {}).get("max_yield")
    mx = None if mx is None else float(mx)
    src = (imp.get("sources") or {}).get("max_yield")
    n_lo = int((imp.get("left_out") or {}).get(IMPORT_MAX_STEP, 0) or 0)
    by = {str(k): int(v) for k, v in ((imp.get("left_out_by_group") or {}).get(IMPORT_MAX_STEP) or {}).items()}
    rows, warns = [], []
    for s in steps or []:
        if s.get("step") != "S5a" or s.get("hi") is None:
            continue
        gname = str(s.get("group"))
        if mode == "field":
            lo_g = n_lo
        elif mode == "machine" and (by or not n_lo):
            lo_g = by.get(gname, 0)
        else:
            lo_g = None
        npass = int((passes or {}).get(s.get("group"), (passes or {}).get(gname, 0)) or 0)
        above = mx is not None and mx < float(s["hi"])
        one = (n_lo if lo_g is None else lo_g) > 0 and npass == 1
        rows.append({"group": gname, "s5a_hi": float(s["hi"]), "left_out": lo_g, "passes": npass,
                     "max_below_s5a_hi": bool(above), "one_pass_after_left_out": bool(one)})
        if src == "you" and (above or one):
            what = []
            if above:
                what.append(f"the import's max. yield volume {mx:,.2f} bu/ac is below the S5a upper limit "
                            f"{float(s['hi']):,.2f}")
            if one:
                what.append(f"{(n_lo if lo_g is None else lo_g):,} readings above it were left out at import and the dry "
                            "yield ran one pass")
            warns.append(f"S5a {gname}: " + "; ".join(what) + " - the maximum typed for this file, not SMS's for the "
                         "source, decides the high readings and may change the yield passes against SMS")
    return {"max_yield": mx, "max_yield_source": src, "left_out": n_lo, "left_out_by_group": by, "groups": rows}, warns


def card_summary_gap(source) -> dict | None:
    """A Case IH / New Holland card decoded with the points' time basis: the readings' wet mass against the card's
    own Summary (the display's totals, which SMS shows as the dataset's Estimated Weight), from the decode source
    (engine.run.decode_source: 'totals' of every decoded reading, 'sms_summary' the Summary total). None for any other
    source or when either total is missing. {readings_wet_lb, summary_wet_lb, ratio (readings / Summary),
    summary_area_ac, readings_area_ac, time_basis}: the two differ by design (the points take each record's own
    interval, the display one logging period per record), by up to a few % on a card."""
    if not isinstance(source, dict) or str(source.get("format") or "") != "cnh_cn1":
        return None
    tot, summ = source.get("totals") or {}, source.get("sms_summary") or {}
    try:
        w_r, w_s = float(tot.get("wet_lb")), float(summ.get("wet_lb"))
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(w_r) and np.isfinite(w_s)) or w_r <= 0 or w_s <= 0:
        return None
    kw = source.get("decoder_kwargs") or {}
    return {"readings_wet_lb": w_r, "summary_wet_lb": w_s, "ratio": w_r / w_s,
            "readings_area_ac": tot.get("area_ac"), "summary_area_ac": summ.get("area_ac"),
            "time_basis": kw.get("time_basis") or "sms"}


def clean(path, S, C, log=print, source=None):
    """The cleaning proper. Returns (g0, g, gx, gy, R, diag); g is the processed set (inside the AOI + buffer when an
    AOI is given) in processing order, in UTM; g0 every record of the file. `source` is the decoder report of a
    decoded raw dataset (run(source=...)): its standard moisture and shrink option feed the moisture basis."""
    F = S["filters"]
    warnings, notes = [], []
    g0 = gpd.read_file(path)
    n_in = len(g0)
    if n_in == 0:
        raise ValueError("the input shapefile has no points")
    nogeom = (g0.geometry.isna() | g0.geometry.is_empty).to_numpy()
    other = sorted(set(g0.geom_type[~nogeom].dropna()) - {"Point"})
    if other:
        raise ValueError("the input must be a POINT shapefile (found " + ", ".join(other) + ")")
    if nogeom.all():
        raise ValueError(f"none of the {n_in:,} records has a geometry (position)")
    if g0.crs is None:
        raise ValueError("the input shapefile has no .prj (coordinate system): cannot measure distances")
    C = dict(C)
    tk0 = ticket_spec(S)
    if tk0 is not None and not tk0["legacy"]:
        # a lb / kg / t ticket needs a test weight: resolve it on the file now so that a missing one is refused before
        # the cascade runs (the same resolution is repeated on the processed set below, where the level is computed)
        # (a bu/ac yield needs no test weight; its acres are only known below, so a placeholder area here)
        tw0 = monitor_bushel_weight(S, g0, source, C)
        gw0 = grower_bushel_weight(S, tw0[0])
        ticket_summary(S, *tw0, area_ac=1.0, grower_test_weight=gw0[0], grower_test_weight_source=gw0[1],
                       grower_explicit=gw0[2])
    lon_deg, _, pos_bad, pos_counts = position_check(g0, nogeom)
    if pos_bad.all():
        raise ValueError("no record has a plausible position ("
                         + ", ".join(f"{k}: {v:,}" for k, v in pos_counts.items() if v) + ")")
    if pos_bad.any():
        warnings.append(f"F0: {int(pos_bad.sum()):,} record(s) without a plausible position removed ("
                        + ", ".join(f"{k}: {v:,}" for k, v in pos_counts.items() if v) + ")")
    utm = g0[~pos_bad].estimate_utm_crs()
    g = g0.to_crs(utm).copy()
    g["_x"] = np.where(pos_bad, np.nan, g.geometry.x.to_numpy(float))
    g["_y"] = np.where(pos_bad, np.nan, g.geometry.y.to_numpy(float))
    g["_posbad"] = pos_bad
    g["_row"] = np.arange(len(g))

    # ---- field of interest (AOI) / plan: a pre-step, not a filter. Records farther than aoi.buffer_m from the AOI
    # are dropped before the cascade (they belong to another field: see engine/clean/aoi.py); the records without a
    # plausible position stay for F0 to remove.
    plan, pre_steps, aoi_R, g_aside = None, [], None, None
    A = S["aoi"]
    if A.get("file"):
        plan = aoi_mod.read_plan(A["file"], id_col=A.get("id_col"), rate_col=A.get("rate_col"),
                                 headland_types=A.get("headland_types"), feature=A.get("feature"))
        if plan.needs_choice:
            # several field boundaries in one file: the user picks the field of interest; a silent union would
            # clean several fields as one (the opposite of what the AOI is for)
            raise ValueError(f"{os.path.basename(A['file'])} holds {plan.n_fields} field boundaries, not a trial plan: "
                             "choose the field of interest with aoi.feature (a 1-based polygon index, or a list of them "
                             "for a field drawn in several polygons; CLI --aoi-feature 2 or 1,2,3): "
                             + "; ".join(f"{f['index']} = {f['name'] or 'polygon ' + str(f['index'])} "
                                         f"({f['area_ac']:,.2f} ac)" for f in plan.fields)
                             + ". A field drawn in several parts should be one MultiPolygon feature.")
        if _legacy_boundary(S) and plan.is_plan:
            plan.is_plan = False                         # the v1 alias: a boundary union, never plots
            plan.headland[:] = False
            notes.append(f"boundary: {os.path.basename(A['file'])} holds {plan.n_polygons} polygons; with the 'boundary' "
                         "setting they are the field boundary (union) for the level diagnostics only, no Plot_Id "
                         "(give the file as aoi.file for the plan behaviour)")
        warnings.extend(f"AOI: {w}" for w in plan.warnings)
        aoi_R = plan.summary(utm)
        aoi_R["buffer_m"] = A.get("buffer_m")
        in0 = aoi_mod.inside(plan, g._x.values, g._y.values, utm, 0.0)
        aoi_R["n_outside_aoi"] = int((~in0 & ~pos_bad).sum())
        if A.get("buffer_m") is not None:
            inb = aoi_mod.inside(plan, g._x.values, g._y.values, utm, float(A["buffer_m"]))
            drop = ~inb & ~pos_bad
            crit = f"outside {aoi_zone(A['buffer_m'])}"
            pre_steps.append({"step": "AOI", "name": "Field of interest", "criterion": crit,
                              "source": step_source("aoi", "Menegatti & Molin 2004 (points outside the field "
                                                           "boundary deleted first); buffer: house rule"),
                              "removed": int(drop.sum()), "remaining": int((~drop).sum())})
            log(f"  AOI  removed {int(drop.sum()):>7,}  remaining {int((~drop).sum()):>7,}  ({crit})")
            if drop.any():
                notes.append(f"AOI: {int(drop.sum()):,} of the {n_in:,} records lie "
                             + (f"more than {A['buffer_m']:g} m outside" if A["buffer_m"] else "outside")
                             + f" the field of interest ({os.path.basename(A['file'])}) and were set aside before "
                             "the cleaning (another field, road or yard in the same export)")
            g_aside = g[drop].copy()                   # kept for the whole-file integral (level diagnostics)
            g = g[~drop].copy()
            in0 = in0[~drop]
            if len(g) < MIN_KEPT:
                raise ValueError(f"only {len(g)} of the {n_in:,} records lie inside {aoi_zone(A['buffer_m'])} "
                                 f"({os.path.basename(A['file'])}): wrong file or wrong field?")
        aoi_R["n_outside_buffer"] = pre_steps[0]["removed"] if pre_steps else 0
        aoi_R["n_in"] = int(len(g))
        g["In_AOI"] = in0.astype(int)
        pid, rate = aoi_mod.tag_plots(plan, g._x.values, g._y.values, utm)
        g["Plot_Id"] = pid
        g["Tgt_Rate"] = rate
        aoi_R["n_in_plots"] = int((pid != "").sum())
        if plan.is_plan and aoi_R["n_in_plots"] == 0:
            warnings.append(f"AOI: the plan {os.path.basename(A['file'])} has {plan.n_plots} plots but no record falls "
                            "inside any of them: check the plan's coordinate system and field")
        notes.append(f"AOI: {os.path.basename(A['file'])} ({plan.n_polygons} polygon(s), {aoi_R['area_ac']:.2f} ac"
                     + (f", {plan.n_plots} plots" if plan.is_plan else "") + f"); {aoi_R['n_outside_aoi']:,} records "
                     "outside it" + (f", {aoi_R['n_in_plots']:,} inside the plots" if plan.is_plan else ""))
    n_read, n_in = n_in, int(len(g))            # n_in: the processed set (the cascade's 100 %)
    # the combine of every record: the machine column; else (machines.from_dataset) the sources SMS writes in
    # parentheses at the end of the dataset names when they differ (an export of several monitors); else one combine
    machine_basis = f"machine column {C['machine']!r}" if C.get("machine") else None
    dm = (sms_mod.dataset_machines(g[C["dataset"]]) if not C.get("machine") and C.get("dataset")
          and S["machines"]["from_dataset"] else None)
    if C.get("machine"):
        g["_machine"] = g[C["machine"]].astype(str)
    elif dm is not None:
        g["_machine"], machine_basis = dm[0], f"{dm[1]} ({C['dataset']})"
        per = pd.Series(dm[0]).value_counts()
        notes.append(f"combines: no machine column, told apart by the {dm[1]} - "
                     f"{len(per)} combines: " + ", ".join(f"{m} ({n:,} records)" for m, n in per.items())
                     + "; the F8 controls, the sessions and the harmonisation keep to each (settings "
                     "machines.from_dataset false: one combine)")
    else:
        g["_machine"] = "M1"

    # ---- chronological order
    time_ok = False
    date_col = C.get("time")                  # kept for the sessions' day even when it carries no time of day
    if C.get("time"):
        t, time_ok = _parse_time(g[C["time"]])
        if not time_ok:
            msg = (f"column {C['time']!r} holds no time of day (dates only): not used for ordering; order = dataset "
                   "start time, then record id")
            (warnings if (S.get("cols") or {}).get("time") else notes).append(msg)
            C["time"] = None
    order_info = {}
    if time_ok:
        g["_t"] = t.values
        if t.isna().any():
            warnings.append(f"{int(t.isna().sum())} records without a valid timestamp in {C['time']!r}: sorted last")
        if C.get("dataset"):
            g["_ds_key"] = g[C["dataset"]].map(ds_key)
        keys = ["_machine", "_t"] + ([C["order"]] if C.get("order") else [])
        g = g.sort_values(keys, kind="stable", na_position="last")
        order_info = {"basis": "time", "column": C["time"],
                      "rule": "rows sorted by machine then time; overlap burnt in time order across machines"}
    elif C.get("dataset") and C.get("order"):
        g["_ds_key"] = g[C["dataset"]].map(ds_key)
        dup = g.duplicated([C["dataset"], C["order"]])
        n_logs = None
        if dup.any():
            # the record id restarts inside a dataset (Ops Center: one log per combine in the same task): every restart
            # in file order starts a new log, sorted after the previous one
            o = pd.to_numeric(g[C["order"]], errors="coerce")
            restart = o.groupby(g[C["dataset"]], sort=False).diff() < 0
            seg = restart.astype(int).groupby(g[C["dataset"]], sort=False).cumsum()
            g["_ds_key"] = g["_ds_key"] + " #log" + seg.map(lambda v: f"{int(v):04d}")
            n_logs = int(g["_ds_key"].nunique())
            warnings.append(f"the record id {C['order']!r} repeats inside a dataset ({int(dup.sum()):,} duplicates): the "
                            f"file holds {n_logs} separate logs (one per combine in an Ops Center task?); each restart "
                            "of the id starts a new log, logs are processed in file order. Without per-record time the "
                            "overlap order between logs is file order, and without a machine column the logs cannot "
                            "be harmonised")
        # the datasets in harvest order: by the start time 'YY/MM/DD-HH:MM:SS' or the date 'M/D/YYYY' in their names
        # (as text the GreenStar 4 names sort 'TaskName 1/10/2000' before '1/9/2000'); names without either last
        ts = g[C["dataset"]].map(sms_mod.dataset_start).astype(float)
        g["_ds_t"] = np.where(np.isfinite(ts), ts, np.inf)
        g = g.sort_values(["_ds_t", "_ds_key", C["order"]], kind="stable").drop(columns="_ds_t")
        order_info = {"basis": "dataset+order", "column": f"{C['dataset']} + {C['order']}",
                      "rule": "dataset start (the time 'YY/MM/DD-HH:MM:SS' or the date 'M/D/YYYY' in the dataset "
                              "name), then record id"}
        if n_logs:
            order_info["n_logs"] = n_logs
    else:
        order_info = {"basis": "file", "column": None, "rule": "file order (no time, or no dataset + record id)"}
        warnings.append("no per-record time and no dataset + record id columns: the file order is taken as the "
                        "harvest order")
    g = g.reset_index(drop=True)
    burn = np.argsort(g["_t"].values, kind="stable") if time_ok else np.arange(len(g))
    if g["_machine"].nunique() > 1 and not time_ok:
        warnings.append("several combines but no per-record time column: the overlap order is dataset start then "
                        "record id, so for combines working at the same time every pass of the later-started dataset "
                        "is treated as harvested after all passes of the other; export the file with a time of day per "
                        "record (on the command line, settings cols['time'] names such a column)")

    y = g[C["yield"]].astype(float)
    moi = g[C["moisture"]].astype(float) if C.get("moisture") else pd.Series(np.nan, index=g.index)
    spd = g[C["speed_mph"]].astype(float)
    if C.get("width_ft"):
        width_m = g[C["width_ft"]].astype(float) * 0.3048
    else:
        width_m = pd.Series(float(S["harvester_width_ft"]) * 0.3048, index=g.index)
        notes.append(f"no width column: constant harvester width {S['harvester_width_ft']:g} ft")
    if C.get("distance_ft"):
        dist_m = g[C["distance_ft"]].astype(float) * 0.3048
        dt = g[C["duration_s"]].astype(float) if C.get("duration_s") else dist_m / (spd * 0.44704)
    else:
        dt = g[C["duration_s"]].astype(float)
        dist_m = spd * 0.44704 * dt
        notes.append("no distance column: distance = speed x duration")
    hdg = g[C["heading"]].astype(float)
    # filters that cannot run on this file
    F = copy.deepcopy(F)
    sms_method = S.get("method") == "sms3sigma"        # the SMS method has no F1 / F9: no message about them
    if not C.get("moisture") and F["F1"]["on"]:
        F["F1"]["on"] = False
        if not sms_method:
            warnings.append("no moisture column: F1 (moisture) switched off")
    wet_lbac = None
    if C.get("wet_mass_lbac"):
        wet_lbac = g[C["wet_mass_lbac"]].astype(float)
    elif S.get("test_weight_lb_bu"):
        wet_lbac = g[C.get("yield_wet") or C["yield"]].astype(float) * float(S["test_weight_lb_bu"])
        if not sms_method:
            notes.append(f"F9 wet mass = {C.get('yield_wet') or C['yield']} x test weight {S['test_weight_lb_bu']:g} "
                         "lb/bu")
    if wet_lbac is None and F["F9"]["on"]:
        F["F9"]["on"] = False
        if not sms_method:
            warnings.append("no wet-mass column and no test_weight_lb_bu: F9 (wet mass) switched off")

    # ---- passes
    pb = S["pass_break"]
    same_ds = (g["_ds_key"] == g["_ds_key"].shift()) if "_ds_key" in g else pd.Series(True, index=g.index)
    step = np.hypot(g._x.diff(), g._y.diff())
    dh = ((hdg.diff() + 180) % 360) - 180
    brk = (~same_ds) | (g._machine != g._machine.shift()) | (step > pb["max_step_m"]) | \
        (dh.abs() > pb["max_heading_change_deg"])
    brk.iloc[0] = True
    g["Pass_Id"] = brk.cumsum().astype(int)
    same_pass = g.Pass_Id == g.Pass_Id.shift()

    # ---- the ground each reading's header swept since the previous reading of its pass (swept_strips): the F8
    # footprint and the strips of the maps. With filters.F8.y_offset the strip sits Y_Offset to the right of travel
    # (John Deere: the active sections of a narrowed header); Case IH logs 0.
    yoff_col = sms_mod.y_offset_column(g.columns)
    off_m = None
    if yoff_col and S["filters"]["F8"]["y_offset"]:
        off_m = pd.to_numeric(g[yoff_col], errors="coerce").to_numpy(float) * 0.3048
    Q = swept_strips(g._x.values, g._y.values, dist_m.to_numpy(float), width_m.to_numpy(float), hdg.to_numpy(float),
                     ~same_pass.to_numpy(bool), off_m)
    for k in range(4):
        g[f"_qx{k}"], g[f"_qy{k}"] = Q[:, k, 0], Q[:, k, 1]
    y_offset_R = {"column": yoff_col, "applied": off_m is not None,
                  "n_nonzero": int((np.abs(np.nan_to_num(off_m)) > 0.01 * 0.3048).sum()) if off_m is not None else 0}

    # ---- the cleaning proper: the SMS 3-sigma sequence (engine/clean/sms3sigma.py) or the OCCD cascade F0-F11
    sms_run = S["method"] == "sms3sigma"
    several = g._machine.nunique() > 1
    if sms_run:
        src_fmt = source.get("format") if isinstance(source, dict) else None
        if not src_fmt and "Src_Fmt" in g:
            fm = g["Src_Fmt"].dropna().astype(str).unique()
            src_fmt = str(fm[0]) if len(fm) == 1 else None
        sess0 = None
        if (src_fmt or "") in ("jd_jdl", "jd_gs3_rcd") and S["sms3sigma"]["group_by"] == "task":
            # group_by 'task' only: SMS splits John Deere logs into one task per combine per day, so the task key is
            # the session (machine x local day). The decoders of these formats write UTC; a decoded file opened
            # without its decode report (no `source`) is read the same way, so the tasks do not depend on the
            # report travelling with the file
            src_day = source if isinstance(source, dict) and source.get("time_basis") else {"time_basis": "UTC"}
            lon0 = np.asarray(lon_deg, float)[~pos_bad]
            _d0, sess0, _i0 = sessions_mod.session_keys(
                g, C, time_ok, g["_t"] if time_ok else None, g._machine, source=src_day,
                lon_deg=(float(np.nanmedian(lon0)) if np.isfinite(lon0).any() else None), date_col=date_col)
        # S0's minimum: the one of the file's own import when its record is given (sms_min_yield)
        my_eff, my_src = sms_min_yield(S)
        if my_src is not None:
            my0 = S["sms3sigma"].get("min_yield")
            if (my0 or 0) != (my_eff or 0):
                notes.append(f"SMS 3-sigma S0: the minimum yield is the one of the file's Import as SMS "
                             f"({'none' if my_eff is None else f'{float(my_eff):g} bu/ac'}), not sms3sigma.min_yield "
                             f"({'none' if not my0 else f'{float(my0):g} bu/ac'})")
            S["sms3sigma"]["min_yield"] = None if my_eff is None else float(my_eff)
        S_run = dict(S, sms3sigma=dict(S["sms3sigma"], min_yield_source=my_src))
        CS = sms_mod.run_cascade(g, S_run, C, {"y": y, "moi": moi if C.get("moisture") else None, "spd": spd,
                                           "width_m": width_m, "dist_m": dist_m, "hdg": hdg},
                                 plan, utm, burn, aoi_mod, log=log, warnings=warnings, notes=notes,
                                 src_format=src_fmt, session_keys=sess0)
        alive = CS["alive"]
        st = {"alive": alive, "why": CS["why"], "log": CS["log"]}
        g["Overlap_f"] = np.nan_to_num(CS["covered_frac"])
        g["Wdth_Opt"] = CS["width_ft_new"]
        g["Swath_Scl"] = CS["scale"]
        g["Dist_Opt"] = CS["dist_ft_new"]            # SMS may shorten a reading at the back (Speed follows)
        g["_spd_s"] = CS["speed_new"]
        g["_sms_grp"] = CS["group_key"]              # the 3-sigma group of every reading (combine, task or field)
        if CS["in_aoi_sms"] is not None and "In_AOI" in g:
            # the clip's own edge rule (a point on the boundary is inside), so the output clip keeps what S1 kept
            g["In_AOI"] = np.asarray(CS["in_aoi_sms"], bool).astype(int)
        if alive.sum() < MIN_KEPT:
            raise ValueError(too_few_message(st["log"], n_in, int(alive.sum())))
        run = (pd.Series(alive).ne(pd.Series(alive).shift()) | ~same_pass).cumsum()
        g["Keep"] = alive.astype(int)
        g["Rem_By"] = st["why"]
        g["Run_Id"] = np.where(alive, run, 0)
        # Optimize Swath Widths moves a narrowed reading to the centre of its kept strip (no flow-delay shift)
        gx, gy = g._x.values + CS["dx"], g._y.values + CS["dy"]
        g["Lag_rec"] = 0
        g["_src"] = np.arange(len(g))
        sms_scale = np.asarray(CS["scale"], float)
        y_map = pd.Series(CS["y_map"], index=g.index)
        th = th_auto = None
        sens, scan = [], []
        dose, Sc = pd.DataFrame(), pd.DataFrame()
        coverage_ac = None
        f8_R = {"threshold": None, "threshold_auto": None, "mode": "off", "fallback": False, "fallback_threshold": None,
                "tolerance_pct": None, "dose_population": None, "dose_n": 0, "dose": [], "reference": None,
                "reference_machines": [], "excluded_machines": [], "sensitivity": [],
                "not_used": "the SMS 3-sigma method has no overlap threshold: Optimize Swath Widths (S2) removes the "
                            "fully overlapped readings and narrows the partly overlapped ones"}
        lag_R = {"scan": [], "best_records": 0, "r_at_0": None, "applied_records": 0, "correction_for_records": 0,
                 "setting": "none", "rule": "SMS 3-sigma method: no residual flow-delay correction (SMS uses the "
                                            "monitor's flow delay at import)"}
        pre_rm = int(sum(p["removed"] for p in pre_steps))
        s1 = next((r for r in CS["log"] if r["filter"] == "S1"), {"removed": 0})
        sms_R = {"settings": dict(S["sms3sigma"]), "grouping": CS["grouping"],
                 "method_revision": sms_mod.METHOD_REVISION, "filter_order": S["sms3sigma"]["filter_order"],
                 "step_attributes": sms_mod.step_attributes(S["sms3sigma"]["filter_order"]),
                 "steps": sms_mod.criterion_table(CS["steps"]), "yield_passes": CS["yield_passes"],
                 "swath": CS["swath"], "source_format": src_fmt,
                 "min_yield": {"value": S["sms3sigma"]["min_yield"], "source": my_src or "sms3sigma.min_yield"},
                 "clip": {"pre_step_removed": pre_rm, "s1_removed": int(s1["removed"]),
                          "sms_clip_equivalent": pre_rm + int(s1["removed"]),
                          "note": ("the SMS clip count = the field-of-interest pre-step (records more than "
                                   f"{A.get('buffer_m'):g} m away, set aside first) + S1"
                                   if pre_steps else "the SMS clip count = S1")
                          + (" (the 'boundary' setting is the field boundary of S1)" if _legacy_boundary(S) else "")},
                 "rule": "mean and SD recomputed on the points still alive before every step (the plain point mean and "
                         "SD of SMS Edit Legend, ddof 0); limits mean +/- k SD rounded half up to "
                         f"{S['sms3sigma']['round_decimals']} decimals, inclusive; the dry-yield limits on the yield "
                         "scaled by Optimize Swath Widths, the lower one floored at "
                         f"{S['sms3sigma']['yield_floor']}; another yield pass only while the previous one cut the SD by "
                         f"more than {S['sms3sigma']['sd_drop_pct']:g} %"}
        # the readings the import's maximum left out, next to each group's S5a (the report prints it)
        imx, imx_w = import_max_check(S.get("sms_import"), sms_R["steps"], CS["yield_passes"],
                                      (CS.get("grouping") or {}).get("mode"))
        if imx is not None:
            sms_R["import_max"] = imx
            warnings.extend(imx_w)
        if several and S["machines"]["harmonise"]:
            notes.append("SMS 3-sigma with several combines: after the SMS sequence the combine factor "
                         f"(machines.method {S['machines']['method']}) is computed on the kept points - an OCCD step "
                         "after the cleaning, not part of SMS; a factor that passes its gates goes into Yld_Harm")
    else:
        # ---- cascade
        st = {"alive": np.ones(len(g), bool), "why": np.array([""] * len(g), dtype=object), "log": []}
        P8s = F["F8"]
        # a run with the v2.3 overlap model cites the v2.3 house texts of F7 and F8
        legacy8 = ((P8s["footprint"], P8s["reference"], P8s["min_threshold"], P8s["y_offset"])
                   == ("chord", "field", 0.0, False))
        v23_text = {"F7": legacy8 and S["lag_stage"] == "output", "F8": legacy8}

        def apply(fid, crit, mask_fn):
            on = F[fid]["on"]
            if on:
                m = np.asarray(mask_fn(), bool)
                kill = st["alive"] & m
                st["why"][kill] = fid
                st["alive"] = st["alive"] & ~kill
            else:
                kill = np.zeros(len(g), bool)
                crit = crit + " (off)"
            src_txt = FILTER_INFO[fid].get("source_v23") if v23_text.get(fid) else None
            st["log"].append(dict(filter=fid, name=FILTER_INFO[fid]["name"], criterion=crit,
                                  source=src_txt or FILTER_INFO[fid]["source"], group=FILTER_INFO[fid]["group"],
                                  on=bool(on),
                                  removed=int(kill.sum()), remaining=int(st["alive"].sum())))
            log(f"  {fid:<4} {'' if on else '(off) '}removed {int(kill.sum()):>7,}  remaining {int(st['alive'].sum()):>7,}")

        d = describe_filter
        apply("F0", d("F0", F["F0"]), lambda: y.isna() | (y <= 0) | ~(dist_m > 0) | g._x.isna() | g._y.isna()
              | g._posbad.values)
        p1 = F["F1"]
        apply("F1", d("F1", p1), lambda: (moi < p1["min_pct"]) | (moi > p1["max_pct"]) | moi.isna())
        p2 = F["F2"]
        apply("F2", d("F2", p2), lambda: (spd < p2["min_mph"]) | (spd > p2["max_mph"]))
        acc = (spd.diff().abs() / spd.replace(0, np.nan) / dt.replace(0, np.nan)).where(same_pass)
        apply("F3", d("F3", F["F3"]), lambda: (acc > F["F3"]["max_change_per_s"]).fillna(False))
        gerr = ((step - dist_m).abs() / dist_m.replace(0, np.nan)).where(same_pass)
        apply("F4", d("F4", F["F4"]), lambda: (gerr > F["F4"]["max_rel_error"]).fillna(False))
        apply("F5", d("F5", F["F5"]),
              lambda: (pd.Series(st["alive"]).groupby(g.Pass_Id).transform("sum") < F["F5"]["min_points"]).values)
        apply("F6", d("F6", F["F6"]),
              lambda: ((dh.abs() / dt.replace(0, np.nan)).where(same_pass) > F["F6"]["max_deg_per_s"]).fillna(False))

        # ---- residual flow delay, measured before F7 and F8 (lag_stage "before_f7"): on the pass interiors of the
        # records alive after F6, and every yield is tied to the position its grain came from (src) before F7 trims
        # the pass ends and F8 measures the overlap there. A record whose source lies beyond its pass (an orphan: its
        # grain came from before the pass started or after it ended) goes with F7 (with the
        # shift before F8 the end-of-pass deficit fell).
        lag_early = S["lag_stage"] == "before_f7"
        src, orphan = np.arange(len(g)), np.zeros(len(g), bool)
        lag_s, lag_rule, scan, Sc, best, r0 = 0, "", [], pd.DataFrame(), 0, float("nan")
        if lag_early:
            log("  flow-delay scan ...")
            scan, Sc, best, r0 = _lag_scan(g, st["alive"], C, width_m, S["lag_axis"])
            lag_s, lag_rule = _lag_decision(S["lag_correction"], Sc, best, r0, warnings)
            src, orphan = lag_source_index(g.Pass_Id.values, lag_s)
            if lag_s and plan is not None:
                # the field of interest and the plots at the position each record's grain came from (its output
                # position): the in-plot dose population and the output clip follow the shift
                xs_, ys_ = g._x.values[src], g._y.values[src]
                g["In_AOI"] = aoi_mod.inside(plan, xs_, ys_, utm, 0.0).astype(int)
                g["Plot_Id"], g["Tgt_Rate"] = aoi_mod.tag_plots(plan, xs_, ys_, utm)
            if orphan.any() and not F["F7"]["on"]:
                notes.append(f"flow delay: F7 is off, so the {int(orphan.sum()):,} records whose grain came from beyond "
                             "their pass ends keep the overlap of the nearest pass end")

        def f7_mask():
            a = pd.Series(st["alive"], index=g.index)
            rank = a.groupby(g.Pass_Id).cumsum().where(a)
            rrank = a[::-1].groupby(g.Pass_Id[::-1]).cumsum()[::-1].where(a)
            return ((rank <= F["F7"]["drop_start"]) | (rrank <= F["F7"]["drop_end"])).fillna(False).to_numpy() | orphan
        n_orph7 = int((orphan & st["alive"]).sum()) if F["F7"]["on"] else 0     # orphans F7 actually removes
        crit7 = d("F7", F["F7"]) + (f", and the {n_orph7:,} records whose flow-delay source ({lag_s:+d} records) "
                                    "lies beyond their pass" if n_orph7 else "")
        apply("F7", crit7, f7_mask)

        # ---- overlap: the ground swept by every point, burnt in chronological order on a 0.25 m raster (all
        # machines). Footprint "swept" (v2.4): the strip between the header line at the previous point of the pass and
        # the one at this point (swept_strips; it shares an edge with its predecessor, so a point never counts its
        # own predecessor as overlap); "chord" (v2.3): the rectangle along the chord from the previous point, which
        # counts a wedge of the predecessor as already cut in every curve. Points removed by
        # F1-F7 still cut the ground they were logged on, so every point with a plausible position is burnt (and sets
        # the extent); the points without one (F0) are not. The overlap of a point is measured at its position; a
        # record's Overlap_f is that of its flow-delay source.
        log("  overlap raster ...")
        footprint = F["F8"]["footprint"]
        hr = np.radians(hdg.values)
        ux, uy = np.sin(hr), np.cos(hr)
        vx, vy = np.cos(hr), -np.sin(hr)
        x0 = np.nanmin(g._x) - 30 + RASTER_ORIGIN_SHIFT_M
        y0 = np.nanmin(g._y) - 30 + RASTER_ORIGIN_SHIFT_M
        nr, nc = int((np.nanmax(g._y) + 30 - y0) / RES) + 1, int((np.nanmax(g._x) + 30 - x0) / RES) + 1
        if nr * nc > MAX_RASTER_CELLS:
            raise ValueError(f"the points span {nc * RES / 1000:,.2f} x {nr * RES / 1000:,.2f} km (after removing the "
                             f"implausible positions): more than one field's overlap raster can hold ({MAX_RASTER_CELLS:,} "
                             f"cells of {RES} m). The file probably holds several fields or farms: export or decode one "
                             "field per file")
        cut = np.zeros((nr, nc), bool)
        ovl = np.full(len(g), np.nan)
        sp, X, Y, D, W, Sx = same_pass.values, g._x.values, g._y.values, dist_m.values, width_m.values, step.values
        for i in burn:
            if footprint == "swept":
                xs, ys = Q[i, :, 0], Q[i, :, 1]
                if not (np.isfinite(xs).all() and np.isfinite(ys).all()):
                    continue
            else:
                L, Wd = D[i], W[i]
                if not (L > 0 and Wd > 0) or not np.isfinite(X[i] + Y[i]):
                    continue
                cx, cy = X[i], Y[i]
                # v2.3 footprint: the rectangle along the chord from the previous point of the same pass to this one;
                # first point of a pass: back along the heading over the logged distance
                if i > 0 and sp[i] and 0 < Sx[i] < 3 * L + 1:
                    px, py = X[i - 1], Y[i - 1]
                    sx, sy = (cx - px) / Sx[i], (cy - py) / Sx[i]
                    nx, ny = sy, -sx
                else:
                    px, py = cx - ux[i] * L, cy - uy[i] * L
                    nx, ny = vx[i], vy[i]
                xs = np.array([px + nx * Wd / 2, px - nx * Wd / 2, cx - nx * Wd / 2, cx + nx * Wd / 2])
                ys = np.array([py + ny * Wd / 2, py - ny * Wd / 2, cy - ny * Wd / 2, cy + ny * Wd / 2])
            rr, cc = sk_polygon((ys - y0) / RES, (xs - x0) / RES, shape=cut.shape)
            if len(rr):
                ovl[i] = cut[rr, cc].mean()
                cut[rr, cc] = True
        ovl_pos = np.nan_to_num(ovl)                 # the overlap at each position, in its burn order
        g["Overlap_f"] = ovl_pos[src]                # each record: the overlap where its grain came from
        coverage_ac = float(cut.sum()) * RES * RES / 4046.8564224
        del cut

        # ---- F8 threshold from the field's dose-response (points alive after F7). Every band is compared with the
        # no-overlap band OF THE SAME COMBINE, then pooled over combines with the band sizes as weights.
        alive = st["alive"]
        # dose population: the in-plot records when a plan with plots exists (interior ground: the headland records are
        # also turn and width-transient records and bias the curve, review section 4.9); the threshold applies to all.
        dose_pop = "all"
        dose_sel = alive.copy()
        if plan is not None and plan.is_plan:
            inplot = (g["Plot_Id"].astype(str) != "").to_numpy()
            if (alive & inplot).sum() >= 200:
                dose_sel, dose_pop = alive & inplot, "in-plot"
            else:
                warnings.append(f"F8: only {int((alive & inplot).sum())} points alive after F7 fall inside the plots: the "
                                "dose-response uses all records")
        P8 = F["F8"]
        ref_used, local_R, local_by_m = P8["reference"], None, None
        auto8 = bool(P8["on"]) and P8["threshold"] == "auto"     # the dose-response decides the threshold
        # the combine of every record: the v2.4 model separates the combines of an export without a machine column by
        # the SMS serial or the Ops Center log (combine_key); the v2.3 model groups by the machine column only
        if ref_used == "local":
            mkey, mkey_basis = combine_key(g, C)
        else:
            mkey, mkey_basis = g._machine.astype(str).to_numpy(), machine_basis or "one combine"
        sdf = pd.DataFrame({"y": y[dose_sel].values, "o": g.Overlap_f[dose_sel].values, "m": mkey[dose_sel]})
        sdf["band"] = pd.cut(sdf.o, DOSE_BANDS, labels=DOSE_LABELS)
        if ref_used == "local":
            # v2.4 reference: every record of the pass interiors against its local no-overlap controls
            # (local_control_ratio: same combine, another pass, within the radius, the same distance from the harvested
            # edge within edge_tol_m, >= min_controls), per band the median ratio over the no-overlap records' own median
            # ratio (the placebo). The overlapped records lie near the edges and the no-overlap ones far inside, where
            # the yield differs: the v2.3 comparison with the whole no-overlap band mixed the edge gradient into the
            # dose-response. Pass interiors (>= 15 records alive after F7 on both
            # sides): the ramps at the pass ends are a machine transient longer than F7, not
            # overlap. The radius reaches the next pass of the same combine: max(local_radius_m, 1.5 header widths),
            # also for wide headers. The threshold still applies to every record.
            log("  F8 local controls ...")
            a_ = pd.Series(alive)
            rank = a_.groupby(g.Pass_Id).cumsum().to_numpy()
            rrank = a_[::-1].groupby(g.Pass_Id[::-1]).cumsum()[::-1].to_numpy()
            pop_l = dose_sel & (rank >= 16) & (rrank >= 16)
            wsel = width_m.to_numpy(float)[dose_sel]
            wsel = wsel[np.isfinite(wsel) & (wsel > 0)]
            full_w = float(np.percentile(wsel, 90)) if len(wsel) else float(np.nanmedian(width_m))
            radius = max(float(P8["local_radius_m"]), 1.5 * full_w)
            edge = edge_distance(g._x.values, g._y.values, full_w / 2)
            ratio, nctl = local_control_ratio(y.values, g.Overlap_f.values, mkey, g.Pass_Id.values,
                                              g._x.values[src], g._y.values[src], edge[src], pop_l, radius,
                                              P8["edge_tol_m"], P8["min_controls"])
            g["_edge_m"], g["_f8_ratio"] = edge[src], ratio
            ldf = pd.DataFrame({"y": y.values, "r": ratio, "m": mkey, "p": g.Pass_Id.values,
                                "band": pd.cut(g.Overlap_f, DOSE_BANDS, labels=DOSE_LABELS).values})[pop_l]
            matched = ldf[np.isfinite(ldf.r)]
            m0 = matched[matched.band == "0"]
            n_keys = int(pd.Series(mkey[pop_l]).nunique())
            local_R = {"radius_m": radius, "radius_setting_m": P8["local_radius_m"], "full_width_m": full_w,
                       "edge_tol_m": P8["edge_tol_m"], "min_controls": P8["min_controls"], "edge_cell_m": EDGE_CELL_M,
                       "population": ("pass interiors: records alive after F7 with >= 15 alive records before and after "
                                      "them in their pass" + (", inside the plots" if dose_pop == "in-plot" else "")),
                       "combine_key": mkey_basis, "n_combines": n_keys, "dose_n": int(len(ldf)),
                       "matched_n": int(len(matched)), "placebo_n": int(len(m0)), "placebo_ratio": None}
            if len(m0) >= 30:
                placebo = float(np.median(m0.r))
                local_R["placebo_ratio"] = placebo
                dose = ldf.groupby("band", observed=True).y.agg(["size", "mean"])
                mg = matched.groupby("band", observed=True)
                dose["size_used"] = mg.r.size().reindex(dose.index).fillna(0).astype(int)
                dose["median_ratio"] = mg.r.median().reindex(dose.index).astype(float)
                dose["n_pass"] = mg.p.nunique().reindex(dose.index).fillna(0).astype(int)
                dose["vs_zero_pct"] = 100 * (dose["median_ratio"] / placebo - 1)
                bm = matched.groupby(["m", "band"], observed=True).r.agg(["size", "median"]).reset_index()
                pm = matched[matched.band == "0"].groupby("m").r.median()
                bm["vs_zero_pct"] = 100 * (bm["median"] / bm.m.map(pm) - 1)
                ymean = ldf.groupby(["m", "band"], observed=True).y.mean()
                bm["mean"] = [float(ymean.get((r.m, r.band), np.nan)) for r in bm.itertuples()]
                local_by_m = bm
                ref_ok = sorted(matched.m.astype(str).unique())
                f8_excluded = sorted(set(map(str, ldf.m.unique())) - set(ref_ok))
                f8_reference = (f"local no-overlap controls of the pass interiors (same combine by {mkey_basis}, another "
                                f"pass, <= {radius:.3g} m, edge distance within {P8['edge_tol_m']:g} m, >= "
                                f"{P8['min_controls']} controls): median ratio per band over the no-overlap records' own "
                                f"(placebo {placebo:.3f}, {len(m0):,} records)")
                if f8_excluded:
                    notes.append(f"F8: {len(f8_excluded)} combine(s) found no local controls and are not in the "
                                 "dose-response: " + ", ".join(f8_excluded))
                if n_keys == 1 and C.get("dataset") and mkey_basis.startswith("one combine"):
                    notes.append("F8: the export names no combine (no machine column, no differing sources or serial "
                                 "in the dataset names, no Ops Center log): the local controls cannot keep to the same "
                                 "combine")
            else:
                ref_used = "field"
                mkey = g._machine.astype(str).to_numpy()
                sdf["m"] = mkey[dose_sel]
                why_l = (f"F8: only {len(m0):,} no-overlap records of the pass interiors have >= {P8['min_controls']} "
                         f"local controls of the same combine (<= {radius:.3g} m, same edge distance within "
                         f"{P8['edge_tol_m']:g} m"
                         + (f"; {n_keys} combines: interleaved combines leave their own next pass out of reach"
                            if n_keys > 1 else "") + "): the dose-response compares each combine's bands with its whole "
                         "no-overlap band (the v2.3 reference), which mixes the edge gradient into the curve")
                (warnings if auto8 else notes).append(why_l)
        if ref_used == "field":
            # v2.3 reference: each band against the whole no-overlap band of the same combine
            dose = sdf.groupby("band", observed=True).y.agg(["size", "mean"])
            by_m = sdf.groupby(["m", "band"], observed=True).y.agg(["size", "mean"]).reset_index()
            z = by_m[by_m.band == "0"].set_index("m")
            n_by_m = sdf.groupby("m").size()
            # A combine's own no-overlap band is its reference only when that band can carry a reference: >= 30 points and
            # (>= F8_MIN_REF_POINTS points or >= F8_MIN_REF_SHARE of the combine's records in the dose population; house rule).
            # A small, noisy 0-band (a combine that always follows another and overlaps on nearly every pass) would put the
            # combine's whole curve off and drag the pooled curve across the tolerance although no yield changed. With
            # one combine its 0-band is the field's: always the reference.
            ref_ok = [m for m in z.index if z.loc[m, "size"] >= 30 and (len(n_by_m) == 1 or z.loc[m, "size"] >= F8_MIN_REF_POINTS
                                                                         or z.loc[m, "size"] / n_by_m[m] >= F8_MIN_REF_SHARE)]
            f8_excluded = sorted(set(n_by_m.index) - set(ref_ok))
            by_m["vs_zero_pct"] = 100 * (by_m["mean"] / by_m.m.map(z["mean"]) - 1)      # NaN without a 0-band of its own
            by_m["in_pooled"] = by_m.m.isin(ref_ok)
            used = by_m[by_m.in_pooled]
            if len(used):
                pooled = used.groupby("band", observed=True).apply(
                    lambda dd: float(np.average(dd.vs_zero_pct, weights=dd["size"])), include_groups=False)
                dose["vs_zero_pct"] = pooled.reindex(dose.index).astype(float)
                dose["size_used"] = used.groupby("band", observed=True)["size"].sum().reindex(dose.index).fillna(0).astype(int)
            else:
                dose["vs_zero_pct"], dose["size_used"] = np.nan, 0
            f8_reference = "the no-overlap band of the same combine" + (f" ({len(ref_ok)} of {len(n_by_m)} combines)"
                                                                         if len(n_by_m) > 1 else "")
            if len(dose) and dose["vs_zero_pct"].isna().all():             # no combine with a reference band: pooled means
                dose["vs_zero_pct"] = 100 * (dose["mean"] / dose["mean"].iloc[0] - 1)
                dose["size_used"] = dose["size"]
                f8_reference = "the field's no-overlap band (no combine has a usable one)"
                warnings.append(f"F8: no combine has a usable no-overlap band (>= 30 points and >= {F8_MIN_REF_POINTS} points or "
                                f">= {100 * F8_MIN_REF_SHARE:.0f} % of its records): the dose-response compares the pooled band "
                                "means with the field's no-overlap band")
            elif f8_excluded:
                small = ", ".join(f"{m} ({int(z['size'].get(m, 0)):,} of {int(n_by_m[m]):,})" for m in f8_excluded)
                notes.append(f"F8: combine(s) with too small a no-overlap band to be their own reference (need >= "
                             f"{F8_MIN_REF_POINTS} points or >= {100 * F8_MIN_REF_SHARE:.0f} % of the combine's records): {small}; "
                             "left out of the pooled dose-response (their bands are listed in f8.by_machine)")
        tol = F["F8"]["auto_tolerance_pct"]
        edges = dict(zip(DOSE_LABELS, DOSE_UPPER))
        th = 0.01
        resolved = False
        f8_stop = {"last_pass_band": None, "stop_band": None, "stop_reason": None, "margin_pct": None,
                   "last_pass_band_size": None}
        for b in DOSE_LABELS[1:]:
            if b in dose.index and dose.loc[b, "size_used"] >= 30 and np.isfinite(dose.loc[b, "vs_zero_pct"]) \
                    and dose.loc[b, "vs_zero_pct"] > -tol:
                th = edges[b]
                resolved = True
                f8_stop.update(last_pass_band=b, margin_pct=float(dose.loc[b, "vs_zero_pct"] + tol),
                               last_pass_band_size=int(dose.loc[b, "size_used"]))
            else:
                loc_ = ref_used == "local"
                if b not in dose.index or dose.loc[b, "size_used"] < 30:
                    nu = int(dose.loc[b, "size_used"]) if b in dose.index else 0
                    why = (f"fewer than 30 records with local controls ({nu} of "
                           f"{int(dose.loc[b, 'size']) if b in dose.index else 0})" if loc_ else
                           f"fewer than 30 points ({nu})")
                    kind = "too_few"
                elif not np.isfinite(dose.loc[b, "vs_zero_pct"]):
                    why, kind = "no reference", "no_reference"
                else:
                    why = (f"{dose.loc[b, 'vs_zero_pct']:.2f} % vs "
                           + ("its local no-overlap controls" if loc_ else "the no-overlap band")
                           + f" (tolerance -{tol:g} %)")
                    kind = "tolerance"
                f8_stop.update(stop_band=b, stop_reason=why)
                if loc_:                     # whether a yield drop or a shortage of matched records set the threshold
                    f8_stop["stop_kind"] = kind
                break
        th_auto = th
        f8_mode = "auto"
        f8_fallback = f8_floor = False
        if F["F8"]["threshold"] != "auto":
            th, f8_mode = float(F["F8"]["threshold"]), "fixed"
        elif resolved and th < F["F8"]["min_threshold"]:
            # the floor: below about 5 % the overlap of a record is not resolved: a GNSS position error of 0.3-1 m is
            # several % of a header width, one raster column 1-2 % of it, and moving the raster by half a cell flips
            # many decisions at 5 %
            th, f8_floor = float(F["F8"]["min_threshold"]), True
            notes.append(f"F8: the dose-response stops at {100 * th_auto:.3g} %, below the floor of {100 * th:.3g} % (the "
                         "resolution of the overlap measure): F8 removes above the floor")
        elif not resolved:
            # the lowest overlap band (0-2 %) already fails the tolerance (or holds < 30 points): the yield does not fall
            # with the overlap from a flat start, so the no-overlap band is no reference and the rule cannot choose (it
            # would stop at 1 % and remove most of the field). Use the settings' fallback threshold and say so.
            th, f8_fallback = float(F["F8"]["fallback_threshold"]), True
            b1 = dose.loc["0-2"] if "0-2" in dose.index else None
            if ref_used == "local":
                why = ("fewer than 30 records with local controls in the lowest overlap band (0-2 %"
                       + (f", {int(b1['size_used'])} of {int(b1['size']):,})" if b1 is not None else ")")
                       if b1 is None or b1["size_used"] < 30 else
                       f"the yield already drops {-b1['vs_zero_pct']:.2f} % in the lowest overlap band (0-2 %, "
                       f"{int(b1['size_used']):,} records with local controls) against its local no-overlap controls")
            else:
                why = ("fewer than 30 points in the lowest overlap band (0-2 %)" if b1 is None or b1["size"] < 30 else
                       f"the yield already drops {-b1['vs_zero_pct']:.2f} % in the lowest overlap band (0-2 %, "
                       f"{int(b1['size']):,} points) against the no-overlap band")
            warnings.append(f"F8: {why}, so this field's dose-response cannot set the threshold (the automatic rule would "
                            f"stop at {100 * th_auto:.3g} %, removing {int((g.Overlap_f[alive] > th_auto).sum()):,} of the "
                            f"{int(alive.sum()):,} points that reach F8); F8 used the fallback threshold of {100 * th:.3g} %. "
                            "Check the overlap figure, the swath width and the header position of each combine, or set "
                            "a manual threshold")
        if not F["F8"]["on"]:
            f8_mode = "off"
        sens = []                                 # sensitivity: what F8 would do at other thresholds (all points alive)
        o_all, y_all = g.Overlap_f[alive], y[alive]
        for t_ in sorted(set([0.01, 0.02, 0.05, 0.10, 0.15, 0.25, 0.35, 0.50, th])):
            keep = o_all <= t_
            sens.append(dict(threshold=t_, removed=int((~keep).sum()),
                             mean_kept=float(y_all[keep].mean()) if keep.any() else float("nan")))
        apply("F8", d("F8", F["F8"], threshold=th, auto=th_auto, fallback=f8_fallback, floor=f8_floor),
              lambda: g.Overlap_f > th)
        p9 = F["F9"]
        apply("F9", d("F9", p9), lambda: (wet_lbac < p9["min_lbac"]) | (wet_lbac > p9["max_lbac"]))
        alive = st["alive"]
        k_sd = F["F10"]["k_sd"]
        mu, sd = y[alive].mean(), y[alive].std()
        apply("F10", d("F10", F["F10"], range_txt=f" ({mu - k_sd * sd:.2f}-{mu + k_sd * sd:.2f})"),
              lambda: (y - mu).abs() > k_sd * sd)
        p11 = F["F11"]
        win = 2 * p11["half_window"] + 1

        def f11_mask():
            yy = y.where(st["alive"])
            med = yy.groupby(g.Pass_Id).transform(lambda v: v.rolling(win, center=True, min_periods=min(6, win)).median())
            return ((yy - med).abs() / med > p11["max_rel_dev"]).fillna(False)
        apply("F11", d("F11", p11), f11_mask)
        alive = st["alive"]
        run = (pd.Series(alive).ne(pd.Series(alive).shift()) | ~same_pass).cumsum()
        rsz = pd.Series(alive).groupby(run).transform("sum")
        apply("F5b", d("F5b", F["F5b"]), lambda: (rsz < F["F5b"]["min_points"]).values & alive)
        alive = st["alive"]
        if alive.sum() < MIN_KEPT:                 # stop before any output is written
            raise ValueError(too_few_message(st["log"], n_in, int(alive.sum())))
        g["Keep"] = alive.astype(int)
        g["Rem_By"] = st["why"]
        g["Run_Id"] = np.where(alive, run, 0)

        # ---- residual flow delay (opposite-direction neighbours). lag_stage "output" (v2.3): measured on the kept
        # records after the whole cascade and applied to the output geometry only; "before_f7": already measured and
        # applied above, here the output positions follow the sources.
        beyond = orphan
        if not lag_early:
            log("  flow-delay scan ...")
            scan, Sc, best, r0 = _lag_scan(g, alive, C, width_m, S["lag_axis"])
            lag_s, lag_rule = _lag_decision(S["lag_correction"], Sc, best, r0, warnings)
            src, beyond = lag_source_index(g.Pass_Id.values, lag_s)
        gx, gy = _shifted_positions(g, lag_s, D, ux, uy)
        g["Lag_rec"] = lag_s
        g["_src"], g["_beyond"] = src, beyond         # for the maps: the strip of each record's source position

        f8_R = {"threshold": th if F["F8"]["on"] else None, "threshold_auto": th_auto, "mode": f8_mode,
                "fallback": f8_fallback, "fallback_threshold": F["F8"]["fallback_threshold"],
                "floor_applied": f8_floor, "min_threshold": F["F8"]["min_threshold"],
                "footprint": footprint, "y_offset": y_offset_R, "reference_mode": ref_used, "local": local_R,
                "tolerance_pct": tol, "dose_population": dose_pop + (", pass interiors" if ref_used == "local" else ""),
                "dose_n": int(local_R["dose_n"]) if ref_used == "local" else int(len(sdf)), "dose": [
                    {"band": str(b), "size": int(r["size"]), "size_used": int(r["size_used"]), "mean": float(r["mean"]),
                     "vs_zero_pct": float(r["vs_zero_pct"]),
                     **({"median_ratio": float(r["median_ratio"]), "n_pass": int(r["n_pass"])}
                        if ref_used == "local" else {})} for b, r in dose.iterrows()],
                "reference": f8_reference, "reference_machines": list(ref_ok), "excluded_machines": list(f8_excluded),
                "min_reference_points": F8_MIN_REF_POINTS, "min_reference_share": F8_MIN_REF_SHARE,
                **f8_stop, "sensitivity": sens}
        lag_R = {"scan": scan, "best_records": best, "r_at_0": r0, "applied_records": lag_s,
                 "correction_for_records": -lag_s, "setting": S["lag_correction"], "rule": lag_rule,
                 "stage": S["lag_stage"], "axis": S["lag_axis"], "n_orphans": n_orph7 if lag_early else 0,
                 "measured_on": ("the records alive after F6 (pass interiors), before F7 and F8: every yield is tied "
                                 "to the position its grain came from before the pass ends are trimmed and the overlap "
                                 "is measured" if lag_early else
                                 "the kept records after the cascade; the shift moves the output geometry only")}
        if several:
            if ref_used == "local":
                f8_R["by_machine"] = [{"machine": str(r.m), "band": str(r.band), "size": int(r["size"]),
                                       "mean": float(r["mean"]), "median_ratio": float(r["median"]),
                                       "vs_zero_pct": float(r.vs_zero_pct), "in_pooled": True}
                                      for _, r in local_by_m.iterrows()]
                f8_R["rule"] = ("every record against its local no-overlap controls of the same combine (so a "
                                "calibration offset between combines cannot move it); per band the median ratio over "
                                "the no-overlap records' own median ratio; threshold = upper edge of the last band from "
                                f"0 up with >= 30 matched records and > -{tol:g} %, never below "
                                f"{100 * F['F8']['min_threshold']:.3g} %")
            else:
                f8_R["by_machine"] = [{"machine": str(r.m), "band": str(r.band), "size": int(r["size"]),
                                       "mean": float(r["mean"]), "vs_zero_pct": float(r.vs_zero_pct),
                                       "in_pooled": bool(r.in_pooled)} for _, r in by_m.iterrows()]
                f8_R["rule"] = ("each band vs the no-overlap band of the same combine, for the combines whose no-overlap "
                                f"band holds >= {F8_MIN_REF_POINTS} points or >= {100 * F8_MIN_REF_SHARE:.0f} % of their "
                                "records (the others are left out), pooled with the band sizes as weights; threshold = "
                                f"upper edge of the last band from 0 up with >= 30 points and > -{tol:g} %")
        y_map, sms_scale, sms_R = y, None, None

    # ---- result skeleton
    R = {"n_in": n_in, "n_read": n_read, "n_kept": int(alive.sum()), "removed_pct": float(100 * (1 - alive.mean())),
         "pre_steps": pre_steps, "cascade": st["log"], "f8": f8_R, "lag": lag_R, "order": order_info,
         "coverage_ac": coverage_ac, "method": S["method"]}
    if sms_R is not None:
        R["sms3sigma"] = sms_R
    if isinstance(S.get("sms_import"), dict):
        R["sms_import"] = copy.deepcopy(S["sms_import"])
    if aoi_R is not None:
        R["aoi"] = aoi_R
    harmonise = several and S["machines"]["harmonise"]
    g["_ymap"] = y_map.to_numpy(float)

    # ---- swept area and wet yield per record (level, sessions, machine means)
    area_ac = (dist_m / 0.3048) * (width_m / 0.3048) / FT2_PER_AC
    area_ac = area_ac.where(np.isfinite(area_ac), 0)
    crop = S.get("crop")
    tw = S.get("test_weight_lb_bu") or (CROPS[crop][0] if crop in CROPS else None)
    ywet, ywet_src = None, None
    if C.get("yield_wet"):
        ywet, ywet_src = g[C["yield_wet"]].astype(float), C["yield_wet"]
    elif C.get("wet_mass_lbac") and tw:
        ywet, ywet_src = g[C["wet_mass_lbac"]].astype(float) / float(tw), f"{C['wet_mass_lbac']} / {tw:g} lb/bu"
        notes.append(f"wet yield = {ywet_src} (no wet volume column)")
    moi_arr = moi.to_numpy(float) if C.get("moisture") else None

    # ---- moisture basis: standard moisture, shrink of the mapped dry yield, Yld_Dry_Sy (engine/clean/sessions.py)
    moist = sessions_mod.standard_moisture(S["moisture"], source, crop, moi_arr,
                                           ywet.to_numpy(float) if ywet is not None else None, y.to_numpy(float), CROPS)
    warnings.extend(f"moisture: {w}" for w in moist["warnings"])
    Ms = moist["standard_pct"]
    dry_sym = None
    if Ms is not None and ywet is not None and moi_arr is not None:
        dry_sym = sessions_mod.dry_symmetric(ywet.to_numpy(float), moi_arr, Ms)
        if moist["shrink"] == "symmetric":
            notes.append(f"the mapped dry yield is already on the symmetric basis (Ms {Ms:g} %): Yld_Dry_Sy equals it "
                         "up to rounding")
    elif S["moisture"]["add_symmetric_column"]:
        notes.append("no Yld_Dry_Sy column: it needs a standard moisture, a wet yield (or wet mass + test weight) and "
                     "a moisture column")
    # the per-area values of the map: the SMS method scales them with Optimize Swath Widths (mass is conserved: the
    # map's area per record is the swept area / scale), so every mean of kept points is the SMS map's while every
    # integral (level, sessions, machines) stays the house one: unscaled yield x swept area of every processed record
    if sms_scale is not None:
        raw_a = (width_m.to_numpy(float) / 0.3048) * (dist_m.to_numpy(float) / 0.3048)
        opt_a = g["Wdth_Opt"].to_numpy(float) * g["Dist_Opt"].to_numpy(float)
        a_ratio = np.where(np.isfinite(opt_a) & (raw_a > 0), opt_a / np.where(raw_a > 0, raw_a, 1.0), 1.0)
        area_map = area_ac.to_numpy(float) * a_ratio      # the map's area: the optimised distance x width
        wet_map = ywet.to_numpy(float) * sms_scale if ywet is not None else None
        dsym_map = np.asarray(dry_sym, float) * sms_scale if dry_sym is not None else None
    else:
        area_map = area_ac.to_numpy(float)
        wet_map = ywet.to_numpy(float) if ywet is not None else None
        dsym_map = dry_sym
    g["Yld_Dry_Sy"] = dsym_map if dsym_map is not None else np.nan
    R["moisture"] = {k: moist[k] for k in ("standard_pct", "source", "shrink", "shrink_source", "n_below_standard",
                                            "share_below_standard", "inferred_pct", "n_inferred")}
    R["moisture"]["wet_yield_source"] = ywet_src
    R["moisture"]["column"] = "Yld_Dry_Sy" if dry_sym is not None else None
    R["moisture"]["rule"] = ("Yld_Dry_Sy = wet yield x (100 - M) / (100 - Ms), the symmetric dry-matter basis; Ms from "
                             "settings > decoder report > inferred from dry / wet of the export > crop table")

    # ---- sessions (machine x day, or dataset without a time of day): diagnostics, never a correction
    t_series = g["_t"] if time_ok else None
    lon_ok = np.asarray(lon_deg, float)[~pos_bad]                 # WGS 84 degrees from position_check, never the CRS x
    lon_med = float(np.nanmedian(lon_ok)) if np.isfinite(lon_ok).any() else None
    # the decoders of the John Deere formats write UTC: a decoded file opened again without its decode report (no
    # `source`, Src_Fmt still says jd_jdl / jd_gs3_rcd) is read the same way, so Harv_Day / Session and the session
    # table do not depend on the report travelling with the file (as the 3-sigma task groups)
    src_s = source
    if not (isinstance(source, dict) and source.get("time_basis")) and "Src_Fmt" in g:
        fm_s = pd.unique(g["Src_Fmt"].astype(str))
        if len(fm_s) == 1 and str(fm_s[0]) in ("jd_jdl", "jd_gs3_rcd"):
            src_s = dict(source or {}, time_basis="UTC")
    day, skey, sinfo = sessions_mod.session_keys(g, C, time_ok, t_series, g._machine, source=src_s, lon_deg=lon_med,
                                                 date_col=date_col)
    g["Harv_Day"], g["Session"] = day, skey
    s_table, s_field = sessions_mod.session_table(g, alive, skey, day, g._machine, C, y_map.to_numpy(float),
                                                  wet_map, moi_arr, dsym_map, area_map, Ms, t_series)
    offsets = (sessions_mod.session_offsets(g, alive, skey, g._machine, y_map.to_numpy(float), moi_arr, s_table,
                                            S["machines"]) if len(s_table) > 1 else [])
    rec = sessions_mod.recommendation(moist, s_table, offsets, dry_sym_available=dry_sym is not None)
    R["sessions"] = {"basis": sinfo["basis"], "n_sessions": sinfo["n_sessions"], "table": s_table, "field": s_field,
                     "offsets": offsets, "recommendation": rec,
                     "source": step_source("sessions", "Luck & Fulton 2014 (dry yield = wet x (100 - M) / "
                                                       "(100 - Ms)); thresholds: house method")}
    v2 = _v2_run(S, plan)
    if rec["map_column"] and sms_run:
        notes.append("sessions (diagnostic): the sessions diagnostic would recommend Yld_Dry_Sy (with Harv_Day as a "
                     "block): " + "; ".join(rec.get("reasons") or [rec["text"]]) + "; the SMS 3-sigma file maps the "
                     "export's dry yield as SMS does" + (" - Yld_Dry_Sy (scaled the same way) is also in the file"
                                                         if v2 and S["moisture"]["add_symmetric_column"] else ""))
    elif rec["map_column"]:
        notes.append("sessions: " + rec["text"] + ("" if v2 and S["moisture"]["add_symmetric_column"] else
                     " (Yld_Dry_Sy is in the flagged file; it goes into the clean file when the run gives the crop, "
                     "the standard moisture or the field of interest)"))
    elif rec.get("reasons"):
        notes.append("sessions: " + rec["text"])

    # ---- grower's weight (scale ticket): resolved here, before the machines, because the closure test of a paired
    # factor above max_factor needs the ticket in bu and the per-machine monitor totals on the ticket's basis. The
    # level factor itself is computed after the harmonisation (below). Legacy scale_ticket_bu: the v1 path exactly.
    tk = ticket_spec(S)
    Mset = S["machines"]
    totals = Mset.get("totals_bu") or None
    ylev = icol = cmp_basis = None
    ticket_cmp = ticket_bu = ticket_lb = ticket_std = None
    tw_used = tw_src = tk_caption = tk_area = tk_area_src = None
    summ = None
    integ_by_machine = {}
    # the ground harvested, every strip once: in an SMS 3-sigma run the optimised area (distance x width after
    # Optimize Swath Widths) of the readings that pass the import check, the clip and S2 (a reading S2 removed lies
    # on ground already cut; the readings the 3-sigma filters remove did cut their ground); in an OCCD run the
    # coverage raster of the overlap filter. The swept area (distance x logged width of every reading) counts the
    # overlaps twice: it is larger than the ground, by little with section control.
    area_swept = float(area_ac.sum())
    area_once = area_once_src = None
    if sms_scale is not None:
        not_ground = pd.Series(st["why"]).astype(str).isin(["S0", "S1", "S1b", "S2"]).to_numpy()
        area_once = float(np.nansum(np.where(not_ground, 0.0, area_map)))
        area_once_src = "the area harvested, every strip once (after Optimize Swath Widths)"
        R["sms3sigma"]["area_after_swath_ac"] = area_once
        R["sms3sigma"]["area_swept_ac"] = area_swept
    elif coverage_ac:
        area_once, area_once_src = float(coverage_ac), "the ground the readings covered, every strip once"
    if tk is not None or totals:
        basis = tk["basis"] if tk is not None else S["scale_ticket_basis"]
        if tk is not None and tk["legacy"]:
            icol = (C.get("yield_wet") or C["yield"]) if basis == "wet" else C["yield"]
            if basis == "wet" and not C.get("yield_wet"):
                warnings.append(f"scale ticket on a wet basis but no wet yield column: the export integral uses {icol!r}")
            ylev, cmp_basis = g[icol].astype(float).fillna(0), basis
            ticket_bu = ticket_cmp = tk["value"]
        else:
            # the bushel of the monitor's total: the file's own (wet mass / wet volume), never the grower's. A weight in
            # lb / kg / t divided by another bushel weight and compared with these bushels made L wrong by file /
            # other (e.g. 60 / 63 = -4.8 %): the comparison is one of mass
            tw_used, tw_src = monitor_bushel_weight(S, g, source, C)
            tw_file, tw_file_src = _file_test_weight(g, C)
            tw_decl, tw_decl_src = declared_bushel_weight(S, source)
            bfrom = (tk or {}).get("bushel_from") or "file"
            if bfrom == "declared" and tw_decl:
                tw_used, tw_src = tw_decl, tw_decl_src + " (scale_ticket.bushel_from 'declared')"
            if ywet is not None and not C.get("yield_wet") and tw:
                tw_used, tw_src = float(tw), "the weight the wet mass of this run was divided by"
            bw = bushel_check(tw_file, tw_file_src, tw_decl, tw_decl_src, used=bfrom if tw_decl else "file")
            if bw and tk is not None:
                warnings.append(bw)
            tw_g, tw_g_src, tw_g_explicit = grower_bushel_weight(S, tw_used)
            if tk is not None:
                # a yield per acre (bu/ac) becomes a total over the grower's acres when given, else the boundary's
                # area, else (no boundary) the ground harvested, every strip once (the swept area only when that is
                # unknown)
                if aoi_R is not None and aoi_R.get("area_ac"):
                    d_area, d_area_src = float(aoi_R["area_ac"]), "the field boundary"
                elif area_once:
                    d_area, d_area_src = area_once, area_once_src + " (no boundary in this cleaning)"
                else:
                    d_area, d_area_src = area_swept, "the area the readings swept; no boundary"
                summ = ticket_summary(S, tw_used, tw_src, area_ac=d_area, area_source=d_area_src,       # raises when a
                                      grower_test_weight=tw_g, grower_test_weight_source=tw_g_src,      # lb ticket has
                                      grower_explicit=tw_g_explicit)                                    # no test weight
                ticket_bu, ticket_lb, tk_caption = summ["ticket_bu"], summ["ticket_lb"], summ["caption"]
                tk_area, tk_area_src = summ["ticket_area_ac"], summ["ticket_area_source"]
                if summ["bushels_differ"]:
                    gw, fw = float(summ["test_weight_grower"]), float(summ["test_weight_used"])
                    if tk["unit"] not in ("bu", "bu/ac"):
                        notes.append(f"scale ticket: the grower's weight is compared as a mass; in bushels of the "
                                     f"monitor's total ({fw:.2f} lb/bu, {tw_src}) it is {ticket_bu:,.2f} bu. In bushels "
                                     f"of {gw:.2f} lb ({tw_g_src}) it is {summ['ticket_bu_grower']:,.2f} bu, reported "
                                     "only (a bushel other than the monitor's would change L by "
                                     f"{100 * (fw / gw - 1):+.2f} %)")
                    elif summ["grower_bushels_applied"]:
                        warnings.append(f"scale ticket: the grower's bushels weigh {gw:.2f} lb ({tw_g_src}) and the "
                                        f"monitor's {fw:.2f} lb ({tw_src}): the {summ['ticket_bu_grower']:,.2f} bu given "
                                        f"are {ticket_lb:,.0f} lb = {ticket_bu:,.2f} of the monitor's bushels "
                                        f"({100 * (gw / fw - 1):+.2f} %), and that is what L compares")
                    else:
                        warnings.append(f"scale ticket: a bushel weight of {gw:.2f} lb/bu ({tw_g_src}) differs from the "
                                        f"monitor's bushel ({fw:.2f} lb/bu, {tw_src}); the grower's weight is in "
                                        f"bushels and is compared as given. If the grower's bushels weigh {gw:.2f} lb, "
                                        "give it as scale_ticket.test_weight_lb_bu: the weight compared would be "
                                        f"{100 * (gw / fw - 1):+.2f} % from this one")
            m_tk = tk["moisture_pct"] if tk is not None else None
            # the column the cleaning will map: Yld_Lvl_Sy in an OCCD run whose sessions recommend the symmetric basis
            # (and that writes it, write_all), else Yld_Level on the file's dry yield (always in an SMS 3-sigma run)
            sym_mapped = bool(tk is not None and not sms_run and rec.get("map_column") == "Yld_Dry_Sy"
                              and dry_sym is not None and v2 and S["moisture"]["add_symmetric_column"])
            use_std = basis == "wet" and m_tk is not None
            if use_std and (Ms is None or (sym_mapped and dry_sym is None)):
                warnings.append("scale ticket: the ticket's moisture is given but "
                                + ("the standard moisture is unknown (give the crop or moisture.standard_pct)"
                                   if Ms is None else "no Yld_Dry_Sy could be computed (no wet yield or no moisture "
                                   "column)") + ": the ticket is compared with the monitor on the wet basis")
                use_std = False
            # the shrink of a wet weight to the standard moisture: the rule of the column it is compared with. The
            # file's dry yield is one-sided as SMS reports the dry total (expand dry moisture off): x min(1, (100 - m)
            # / (100 - Ms)), so a wet weight whose grain moisture is the standard and the same weight entered 'at the
            # standard moisture' are one figure (one L, Yld_Level sums to it); symmetric only against Yld_Dry_Sy
            std_rule = "symmetric" if (sym_mapped or moist.get("shrink") == "symmetric") else "one_sided"
            if basis == "wet" and not use_std:
                if ywet is not None:
                    ylev, icol = ywet.fillna(0), ywet_src
                else:
                    ylev, icol = y.fillna(0), C["yield"]
                    warnings.append("scale ticket on a wet basis but no wet yield (no wet volume column, no wet mass + "
                                    f"test weight): the monitor total uses the mapped dry yield {C['yield']!r}")
                cmp_basis = "wet"
            elif use_std and sym_mapped:
                # a wet weight with its moisture against the symmetric dry yield the map uses: dry matter against dry
                # matter (the weight and every reading brought to the standard moisture by the symmetric rule)
                ylev, icol = pd.Series(dry_sym, index=g.index).fillna(0), "Yld_Dry_Sy"
                cmp_basis = (f"dry matter, standard moisture {Ms:g} % (the wet weight at {float(m_tk):g} % and the "
                             "monitor's wet yield at its own moisture, both by the symmetric rule)")
            elif use_std:
                # a wet weight with its moisture: brought to the standard moisture by the file's own rule (one-sided
                # as SMS: grain drier than the standard is not credited) and compared with the file's dry yield, the
                # column Yld_Level is built on, so Yld_Level sums to the weight at the standard moisture
                ylev, icol = y.fillna(0), C["yield"]
                cmp_basis = (f"dry, standard moisture {Ms:g} % (the wet weight at {float(m_tk):g} % brought to it "
                             f"{'one-sided' if std_rule == 'one_sided' else 'symmetrically'}, as the file's dry yield "
                             f"{C['yield']})")
            elif sym_mapped:
                # an OCCD run whose sessions recommend the symmetric basis maps Yld_Lvl_Sy (write_all): the weight at
                # the standard moisture is compared with the column that is leveled and mapped, Yld_Dry_Sy, so the
                # mapped bu/ac sum to the weight (Yld_Level then reads the one-sided gap below it)
                ylev, icol = pd.Series(dry_sym, index=g.index).fillna(0), "Yld_Dry_Sy"
                cmp_basis = (f"dry, standard moisture {Ms:g} % (the symmetric dry yield Yld_Dry_Sy, the column the "
                             "sessions recommend and the map uses)")
            else:
                # a weight at the standard moisture is compared with the file's own dry yield: the one the import made
                # (as SMS with 'expand dry moisture' off: grain drier than the standard is not credited, the way SMS
                # reports the dry total), the app shows and Yld_Level is built on, so the leveled bu/ac sum to the
                # weight. The symmetric Yld_Dry_Sy credits the grain below the standard and reads more in a
                # dry harvest: L from it was that much too low and Yld_Level missed the weight by the same gap
                ylev, icol = y.fillna(0), C["yield"]
                how = {"one_sided": ", one-sided shrink", "symmetric": ", symmetric shrink"}.get(moist["shrink"], "")
                cmp_basis = ("dry" + (f", standard moisture {Ms:g} %" if Ms is not None else "")
                             + f" (the file's dry yield {C['yield']}{how})")
            if tk is not None:
                ticket_cmp = ticket_bu
                if use_std:
                    # the scale weighed wet grain at m_tk %: brought to the standard moisture by the rule of the column
                    # it is compared with (std_rule): one-sided against the file's dry yield (a weight drier than the
                    # standard stays as weighed), symmetric against Yld_Dry_Sy
                    shrink_tk = (100.0 - float(m_tk)) / (100.0 - float(Ms))
                    if std_rule == "one_sided":
                        shrink_tk = min(1.0, shrink_tk)
                    ticket_std = ticket_bu * shrink_tk
                    ticket_cmp = ticket_std
                    notes.append(f"scale ticket: {ticket_bu:,.2f} bu at {m_tk:g} % moisture = {ticket_std:,.2f} bu at "
                                 f"the standard moisture {Ms:g} % ({std_rule.replace('_', '-')} shrink, as the column "
                                 f"it is compared with, {icol})")
        integ_by_machine = (ylev * area_ac).groupby(g._machine.values).sum().to_dict()

    # ---- several combines (engine/clean/machines.py): the relative harmonisation needs > 1 machine and
    # machines.harmonise; per-machine tickets (machines.totals_bu) are applied whenever they are given, with or
    # without the relative step and also for a single machine (then they are its scale ticket)
    harm = pd.Series(1.0, index=g.index)
    machs = sorted(g._machine.unique())
    means = (machines_mod.machine_means(g, alive, y_map.to_numpy(float), moi_arr, area_ac.to_numpy(float),
                                        ywet.to_numpy(float) if ywet is not None else None)
             if (several or totals) else None)
    levels_m, validation, tests, ref, ticket_test = None, None, [], None, None
    minfo = {}
    applied = {m: 1.0 for m in machs}
    decision = {m: ("harmonisation switched off" if several and not harmonise else "reference") for m in machs}
    method = ("harmonisation switched off in the settings" if several and not harmonise else
              "one combine: nothing to harmonise")
    # 'validate only' (scale_ticket.use): the grower's weight validates the monitor and changes no yield, so it may
    # not confirm (and so apply) a combine factor either; the closure test is still run and reported
    validate_only = tk is not None and tk.get("use") == "validate"
    if harmonise:
        log(f"  combine factor (harvest layout and the four methods; method {Mset['method']}) ...")
        if Mset["max_factor"] <= machines_mod.DEFAULTS["min_factor"]:
            notes.append(f"machines: max_factor {100 * Mset['max_factor']:g} % is not above the default min_factor "
                         f"({100 * machines_mod.DEFAULTS['min_factor']:g} %): the smallest factor applied is "
                         f"{100 * Mset['min_factor']:g} % in this run")
        # the factor is estimated on the yield of the map, the one it is applied to (Yld_Harm = mapped yield x factor)
        # and judged on (the machine means, the seams): in an SMS 3-sigma run the yield after Optimize Swath Widths
        # (the logged yield of a narrowed reading is grain / the full header width, so the combine that overlapped
        # more read lower before S2 and machine x machine gave it a factor the map does not need); in an OCCD run the
        # mapped yield is the logged one
        tests, applied, decision, ref = machines_mod.combine_factors(g, alive, y_map.to_numpy(float), moi_arr, Mset,
                                                                     width_m=width_m.to_numpy(float),
                                                                     warnings=warnings, info=minfo)
        method = machines_mod.method_text(Mset)
        if tk is not None and not totals and ticket_cmp and Mset.get("ticket_confirms", True):
            # the grower's weight as a closure test: a significant, consistent paired factor above max_factor is
            # applied when the monitor total with it closes with the ticket (machines.ticket_closure; applied and
            # decision are updated in place, the per-machine-weights warning withdrawn); validate only: reported,
            # nothing applied
            ticket_test = machines_mod.ticket_closure(tests, applied, decision, ref, Mset, integ_by_machine,
                                                      ticket_cmp, warnings, notes, validate_only=validate_only)
            machines_mod.light_after_closure(ticket_test, applied, decision, minfo, warnings)
        minfo["advice"] = machines_mod.factor_advice(
            tests, applied, Mset, ticket_test, ticket_given=bool(tk is not None and ticket_cmp),
            ticket_skipped=("totals" if totals else "switched_off" if not Mset.get("ticket_confirms", True) else None))
    if totals:
        if not machine_basis and set(str(k) for k in totals) != set(machs):
            warnings.append(f"machines.totals_bu names {sorted(totals)} but the file names no combine (no machine "
                            f"column - settings cols.machine - and no differing sources in the dataset names): the "
                            "tickets cannot be matched to combines and are ignored; give the field total as "
                            "scale_ticket_bu")
        else:
            if ref is None:
                kept_by = pd.Series(alive).groupby(g._machine.values).sum()
                ref = str(Mset["reference"]) if str(Mset["reference"]) in machs else str(kept_by.idxmax())
            levels_m = machines_mod.per_machine_levels(totals, means, ref, warnings)
            if levels_m is not None:
                validation = machines_mod.validate_totals(levels_m, tests, ref, Mset)
                applied = {m: float(levels_m[m]["relative_to_ref"]) for m in machs}

                def _dec(m):
                    if m == ref:
                        return "reference (own ticket)"
                    v = validation.get(m, {})
                    txt = f"per-machine ticket: L_m / L_ref = {applied[m]:.4f}"
                    if v.get("paired_factor") is not None:
                        txt += (f"; paired-cell factor {v['paired_factor']:.4f} "
                                f"({'inside' if v['inside_paired_ci'] else 'outside'} its CI, agreement "
                                f"{100 * v['agreement']:+.2f} %)")
                    elif v.get("note"):
                        txt += "; " + v["note"]
                    return txt
                decision = {m: _dec(m) for m in machs}
                method = ("per-machine scale tickets: Yld_Level = y x L_m (L_m = ticket_m / wet integral_m), Yld_Harm = "
                          "y x L_m / L_ref; the paired-cell estimate is reported as validation where its pair gate holds")
                for m, v in validation.items():
                    if v.get("agreement") is not None and abs(v["agreement"]) > 0.05:
                        warnings.append(f"machine {m}: the ticket ratio L_m / L_ref ({v['ticket_ratio']:.4f}) and the "
                                        f"paired-cell factor ({v['paired_factor']:.4f}) disagree by "
                                        f"{100 * v['agreement']:+.2f} %: check the tickets' coverage and the machines' "
                                        "calibration")
    harm_on = harmonise or levels_m is not None          # Yld_Harm carries a factor other than 1 only in these cases
    if harm_on:
        harm = g._machine.map(applied).astype(float)
    if several or levels_m is not None:
        mref = (means.get(ref) or {}).get("cleaned_map_mean") if (means and ref) else None
        whole = {m: (mref / v["cleaned_map_mean"] if (mref and v.get("cleaned_map_mean")) else None)
                 for m, v in (means or {}).items()}
        R["machines"] = {"column": C["machine"], "basis": machine_basis or "one combine", "reference": ref,
                         "estimate": minfo.get("estimate"), "pairs": minfo.get("pairs") or [],
                         "unlinked": minfo.get("unlinked") or [],
                         "method_requested": minfo.get("method_requested", Mset.get("method")),
                         "method_used": minfo.get("method_used") or {}, "layout": minfo.get("layout") or [],
                         "layout_class": minfo.get("layout_class") or {}, "light": minfo.get("light") or {},
                         "methods": minfo.get("methods") or [], "adjacency_distance_m": minfo.get("D_m"),
                         "after": ("the SMS 3-sigma sequence" if sms_run else "the OCCD cascade"),
                         "whole_field_ratio": whole, "whole_field_ratio_note": (
                             "map mean of the reference / map mean of the combine over the whole field: a diagnostic, "
                             "never applied (it mixes the machines with the ground each one cut)"),
                         "method": method, "rule": method,
                         "n_points": {m: means[m]["n"] for m in machs}, "n_kept": {m: means[m]["n_kept"] for m in machs},
                         "means": means, "tests": tests, "factor_applied": applied, "decision": decision,
                         "per_machine_totals": levels_m, "validation": validation, "ticket_test": ticket_test,
                         # why a factor estimated was not applied and what would apply it, one plain sentence per
                         # combine, written from the gate AND the ticket closure test (machines.factor_advice)
                         "advice": minfo.get("advice") or {},
                         "factor_estimated_on": ("the yield of the map (after Optimize Swath Widths), the one "
                                                 "Yld_Harm is built on" if sms_run else "the mapped yield"),
                         "source": step_source("machines", "Maldaner et al. 2016 (reference machine; machine, track and "
                                                           "point factors); Carneiro & Shiratsuchi, LSU AgCenter "
                                                           "(machine normalisation); Chiocco & Wuestefeld 2019 (+/-10 % "
                                                           "bound); layout, gates and lights: house method")}
    g["Yld_Harm"] = y_map.values * harm.values

    # ---- level: scale ticket / integral of the processed set (yield x swept area over every processed record)
    g["Yld_Level"] = np.nan
    g["Yld_Lvl_Sy"] = np.nan
    lvl_sy = False
    if tk is not None or levels_m is not None:
        integral, integral_area = float((ylev * area_ac).sum()), float(area_ac.sum())
        if integral <= 0:
            raise ValueError(f"the export integral of {icol!r} is {integral}: cannot level to the scale ticket")
        if levels_m is not None:
            ticket_given = float(sum(v["ticket_bu"] for v in levels_m.values() if v.get("ticket_bu")))
            n_no_ticket = sum(1 for v in levels_m.values() if not v.get("ticket_bu"))
            # the total the leveled map must integrate to: each machine's own ticket and, for a machine without one,
            # its integral x the reference's L (its implied ticket); equal to the sum of the tickets when all have one
            ticket = float(sum(float(v["integral_bu"] or 0.0) * float(v["L"]) for v in levels_m.values()))
            if ticket_cmp and abs(ticket / float(ticket_cmp) - 1) > LEVEL_TOL:
                what = ("the sum of machines.totals_bu" if n_no_ticket == 0 else
                        f"the field total implied by machines.totals_bu (tickets given {ticket_given:,.0f} bu + the "
                        f"integral x L_ref of the {n_no_ticket} machine(s) without a ticket)")
                name = "scale_ticket_bu" if tk["legacy"] else "scale_ticket"
                warnings.append(f"{name} ({float(ticket_cmp):,.0f} bu) differs from {what} ({ticket:,.0f} bu) "
                                f"by {100 * (ticket / float(ticket_cmp) - 1):+.2f} %: the per-machine tickets are used")
        else:
            ticket = float(ticket_cmp)
        factor = ticket / integral
        R["level"] = {"export_total_bu": integral, "export_area_ac": integral_area, "scale_ticket_bu": ticket,
                      "factor": factor,
                      "source": f"integral of {icol} x distance x width over the processed set"
                                + (f" (inside {aoi_zone(A['buffer_m'])})" if pre_steps else ""),
                      "basis": basis, "integral_column": icol, "export_integral_bu": integral,
                      "export_integral_area_ac": integral_area, "factor_from_integral": factor,
                      "processed_set_integral_bu": integral, "n_points_processed": int(len(g)),
                      "literature": step_source("level", "Clarke et al. 2024 (rescale to the weighed yield); Luck & "
                                                         "Fulton 2014 (monitor within 1-3 % of the scale); bands: house "
                                                         "rule")}
        if tk is not None:
            # the ticket as given, its conversion and the monitor totals in the same units
            wet_bu = float((ywet.fillna(0) * area_ac).sum()) if ywet is not None else None
            if C.get("wet_mass_lbac"):
                tot_lb = float((g[C["wet_mass_lbac"]].astype(float).fillna(0) * area_ac).sum())
            elif wet_bu is not None and tw_used:
                tot_lb = wet_bu * float(tw_used)
            else:
                tot_lb = None
            dry_bu = float((y.fillna(0) * area_ac).sum())
            sym_bu = (float((pd.Series(dry_sym, index=g.index).fillna(0) * area_ac).sum()) if dry_sym is not None
                      else None)
            # the acres a mean yield (bu/ac) of the two totals is over: the acres of a grower's yield per acre (so the
            # grower's figure reads as given), else the ground harvested (every strip once: the area the SMS averages
            # of the map are over), else the swept area
            if tk_area:
                mon_area, mon_area_src = float(tk_area), tk_area_src
            elif area_once:
                mon_area, mon_area_src = area_once, area_once_src
            else:
                mon_area, mon_area_src = integral_area, "the area the readings swept (overlaps counted twice)"
            R["level"].update(ticket_value=tk["value"], ticket_unit=tk["unit"], ticket_lb=ticket_lb,
                              ticket_bu=(ticket_bu if ticket_bu is not None else float(tk["value"])),
                              ticket_basis=tk["basis"], ticket_moisture_pct=tk["moisture_pct"],
                              ticket_bu_at_standard=ticket_std, ticket_compared_bu=ticket, comparison_basis=cmp_basis,
                              test_weight_used=tw_used, test_weight_source=tw_src, export_total_lb=tot_lb,
                              ticket_area_ac=tk_area, ticket_area_source=tk_area_src,
                              export_total_wet_bu=wet_bu, ticket_note=tk["note"], ticket_caption=tk_caption,
                              legacy_setting=bool(tk["legacy"]), use=tk.get("use") or "adjust",
                              # the bushels: the monitor's (the file's own, the one every bu here is in) and, when
                              # another bushel weight was given, the grower's weight counted in it (reported only,
                              # unless the grower's bushels themselves were given: grower_bushels_applied)
                              test_weight_file=tw_used, test_weight_file_source=tw_src,
                              test_weight_grower=(summ or {}).get("test_weight_grower"),
                              test_weight_grower_source=(summ or {}).get("test_weight_grower_source"),
                              ticket_bu_grower=(summ or {}).get("ticket_bu_grower"),
                              grower_bushels_applied=bool((summ or {}).get("grower_bushels_applied")),
                              # the monitor's totals of the processed set on every basis, in the file's bushel
                              monitor_total_wet_bu=wet_bu, monitor_total_dry_bu=dry_bu, monitor_total_dry_sym_bu=sym_bu,
                              # the areas: swept (export_area_ac: distance x logged width, overlaps twice), harvested
                              # (every strip once) and the one a bu/ac of the two totals should be over
                              area_swept_ac=integral_area, area_once_ac=area_once, area_once_source=area_once_src,
                              monitor_area_ac=mon_area, monitor_area_source=mon_area_src,
                              rule="L = grower's weight / monitor total of the processed set on the same basis and in "
                                   "the same bushel (the file's): a comparison of mass. A weight at the standard "
                                   "moisture is compared with the file's own dry yield (one-sided, as SMS reports the "
                                   "dry total with expand dry moisture off), so Yld_Level (mapped dry yield x "
                                   "harmonisation x L) integrates to it. A wet weight with its grain moisture is "
                                   "brought to the standard moisture by the same one-sided rule (x min(1, (100 - m) / "
                                   "(100 - Ms))) and compared the same way: the same figure entered either way gives "
                                   "the same L; one average moisture for the whole weight cannot carry the per-reading "
                                   "shrink, so on a field partly above and partly below the standard this L differs "
                                   "from a dry-matter comparison by that difference. A wet weight without its "
                                   "moisture is compared with the wet yield, and Yld_Level then integrates to L x the "
                                   "monitor's dry total, the weight less the shrink of the readings above the standard "
                                   "(yld_level_integral_bu, yld_level_vs_ticket_pct); leveled_export_integral_bu is "
                                   "the leveled total on the comparison column and equals the weight compared. An OCCD "
                                   "run whose sessions recommend the symmetric basis compares with Yld_Dry_Sy and maps "
                                   "Yld_Lvl_Sy instead (symmetric both ways). Yld_Lvl_Sy = symmetric dry yield x "
                                   "harmonisation x L")
            # a Case IH card: L is against the readings (so Yld_Level sums to the weight); SMS shows the card's own
            # Summary as the dataset's weight, which can differ from its readings by a few % (card_summary_gap)
            cs = card_summary_gap(source)
            if cs is not None:
                # the Summary covers the whole card (every dataset, before the boundary and the import's limits),
                # L only the readings it processed: L x (the card's readings / its Summary) takes the gap between
                # the readings and the display's totals as even over the card (an estimate; the wet mass ratio
                # stands in for the comparison basis); the share of the card's grain L was computed on is kept
                cs["factor_against_summary"] = factor * cs["ratio"]
                cs["factor_against_summary_basis"] = ("L x the card's readings / its Summary (wet): the gap taken as "
                                                      "even over the card, an estimate")
                if tot_lb and cs["readings_wet_lb"] > 0:
                    cs["processed_share_of_readings"] = float(tot_lb) / float(cs["readings_wet_lb"])
                R["level"]["card_summary"] = cs
                if abs(cs["ratio"] - 1) > CARD_SUMMARY_TOL:
                    warnings.append(
                        f"level: the readings of this Case IH / New Holland card hold {cs['readings_wet_lb']:,.0f} lb "
                        f"wet, {100 * (cs['ratio'] - 1):+.2f} % against the card's own Summary "
                        f"({cs['summary_wet_lb']:,.0f} lb, the Estimated Weight SMS shows for it). L = {factor:.4f} "
                        f"is against the readings, so Yld_Level sums to the grower's weight; against the Summary the "
                        f"same weight gives about L = {cs['factor_against_summary']:.4f} (an estimate: the gap taken "
                        "as even over the card; the monitor's accuracy as SMS reports it)")
        if g_aside is not None and len(g_aside):
            # the records the AOI set aside still carry grain the ticket may include: integrate them with the same
            # columns so the user can see which denominator the ticket belongs to
            w_as = (g_aside[C["width_ft"]].astype(float) if C.get("width_ft")
                    else pd.Series(float(S["harvester_width_ft"]), index=g_aside.index))
            if C.get("distance_ft"):
                d_as = g_aside[C["distance_ft"]].astype(float)
            else:
                d_as = g_aside[C["speed_mph"]].astype(float) * 1.46667 * g_aside[C["duration_s"]].astype(float)
            a_as = (d_as * w_as / FT2_PER_AC)
            a_as = a_as.where(np.isfinite(a_as), 0)
            # the comparison column computed as ylev was: icol is an export column, or Yld_Dry_Sy (a dry ticket, or a
            # wet one with its moisture) / the wet mass over the test weight, which only the processed set carries
            if icol in g_aside:
                y_as = g_aside[icol].astype(float)
            else:
                y_as = (g_aside[C["yield_wet"]].astype(float) if C.get("yield_wet") else
                        g_aside[C["wet_mass_lbac"]].astype(float) / float(tw))
                if icol == "Yld_Dry_Sy":
                    y_as = pd.Series(sessions_mod.dry_symmetric(y_as, g_aside[C["moisture"]].astype(float), Ms),
                                     index=g_aside.index)
            y_as = y_as.fillna(0)
            out_bu, out_ac = float((y_as * a_as).sum()), float(a_as.sum())
            R["level"].update(set_aside_integral_bu=out_bu, set_aside_area_ac=out_ac,
                              n_points_set_aside=int(len(g_aside)), file_integral_bu=integral + out_bu,
                              file_area_ac=integral_area + out_ac,
                              factor_whole_file=((ticket / (integral + out_bu)) if integral + out_bu > 0 else None))
            if out_bu > 0.05 * integral:
                what = ("the scale ticket" if levels_m is None else
                        "the field total implied by the per-machine tickets")
                warnings.append(
                    f"level: {what} ({ticket:,.0f} bu) is divided by the integral of the processed set "
                    f"(inside {aoi_zone(A['buffer_m'])}: {integral:,.0f} bu over "
                    f"{integral_area:,.2f} ac, L = {factor:.4f}), but the records set aside by the AOI carry another "
                    f"{out_bu:,.0f} bu over {out_ac:,.2f} ac (whole file {integral + out_bu:,.0f} bu, L would be "
                    f"{ticket / (integral + out_bu):.4f}): give the ticket(s) of the field of interest only, or run "
                    "without the AOI when the ticket covers the whole export")
        if plan is not None:
            inside = g["In_AOI"].to_numpy() == 1
            R["level"].update(inside_aoi_integral_bu=float((ylev * area_ac)[inside].sum()),
                              inside_aoi_area_ac=float(area_ac[inside].sum()), n_points_outside_aoi=int((~inside).sum()),
                              inside_boundary_integral_bu=float((ylev * area_ac)[inside].sum()),
                              inside_boundary_area_ac=float(area_ac[inside].sum()),
                              n_points_outside_boundary=int((~inside).sum()))
        if C.get("dataset"):
            by = pd.DataFrame({"dataset": g[C["dataset"]].astype(str), "bu": ylev * area_ac, "ac": area_ac})
            R["level"]["export_integral_by_dataset"] = [
                {"dataset": kd, "n": int(len(v)), "area_ac": float(v.ac.sum()), "bu": float(v.bu.sum())}
                for kd, v in sorted(by.groupby("dataset", sort=False), key=lambda kv: ds_sort_key(kv[0]))]
        # Yld_Lvl_Sy (the symmetric dry yield leveled) goes with the v2 ticket (or per-machine totals) in a v2 run that
        # writes Yld_Dry_Sy (moisture.add_symmetric_column); a legacy scale_ticket_bu run writes Yld_Level only, as
        # the v1 engine did
        lvl_sy = (dry_sym is not None and v2 and bool(S["moisture"]["add_symmetric_column"])
                  and not (tk is not None and tk["legacy"]))
        if levels_m is not None:
            Lm = g._machine.map({m: v["L"] for m, v in levels_m.items()}).astype(float)
            g["Yld_Level"] = y_map.values * Lm.values
            if lvl_sy:
                g["Yld_Lvl_Sy"] = np.asarray(dsym_map, float) * Lm.values
            R["level"].update(scale_ticket_given_bu=ticket_given, n_machines_without_ticket=int(n_no_ticket),
                              scale_ticket_note=("sum of the per-machine tickets" if n_no_ticket == 0 else
                                                 f"{n_no_ticket} machine(s) without a ticket are leveled with the "
                                                 "reference's L: scale_ticket_bu is the implied field total, "
                                                 "scale_ticket_given_bu the tickets given"),
                              method="per-machine scale tickets (machines.totals_bu): Yld_Level = y x L_m",
                              per_machine={m: {k: v.get(k) for k in ("ticket_bu", "integral_bu", "L", "relative_to_ref")}
                                           for m, v in levels_m.items()},
                              leveled_export_integral_bu=float((ylev * area_ac * Lm).sum()))
        else:
            lvl = factor
            if harm_on:                     # the harmonised export total is what the leveled map must integrate to
                th_total = float((ylev * area_ac * harm).sum())
                lvl = ticket / th_total
                R["level"].update(export_total_harmonised_bu=th_total, factor_harmonised=lvl)
            g["Yld_Level"] = g["Yld_Harm"].values * lvl
            if lvl_sy:
                g["Yld_Lvl_Sy"] = np.asarray(dsym_map, float) * harm.values * lvl
            R["level"]["leveled_export_integral_bu"] = float((ylev * area_ac * harm * lvl).sum())
        miss = R["level"]["leveled_export_integral_bu"] / ticket - 1
        if abs(miss) > LEVEL_TOL:
            warnings.append(f"the leveled export integrates to {R['level']['leveled_export_integral_bu']:,.2f} bu, "
                            f"{100 * miss:+.2f} % from the scale ticket ({ticket:,.0f} bu)")
        # the integrals of the columns actually written: L is derived on the comparison column (ylev) and applied to
        # the mapped dry yield. A weight at the standard moisture is compared with that same dry yield, so Yld_Level
        # integrates to the weight (yld_level_vs_ticket_pct 0); for a wet weight (ylev: the wet yield, or Yld_Dry_Sy
        # with the ticket's moisture) Yld_Level integrates to the weight only up to the gap between the mapped dry
        # yield and the comparison column (one-sided shrink: Yld_Vol_Dr < Yld_Vol_We on the readings above the
        # standard moisture; no credit below it against Yld_Dry_Sy)
        lvl_int = float((g["Yld_Level"].astype(float).fillna(0) * area_map).sum())
        sy_int = float((g["Yld_Lvl_Sy"].astype(float).fillna(0) * area_map).sum()) if lvl_sy else None
        R["level"].update(yld_level_integral_bu=lvl_int, yld_level_vs_ticket_pct=100 * (lvl_int / ticket - 1),
                          yld_lvl_sy_integral_bu=sy_int,
                          yld_lvl_sy_vs_ticket_pct=(100 * (sy_int / ticket - 1) if sy_int is not None else None))
        # the closure: monitor total vs the grower's weight, before and after the harmonisation, and the verdict. The
        # bands are on |L - 1| (the scale seen from the monitor: the same quantity advice() checks), the text also
        # gives the monitor seen from the scale (monitor / scale - 1), so both percentages are named with their
        # direction (e.g. monitor -10.0 % from the scale, L = 1.1111 is +11.1 % from 1)
        if levels_m is not None:
            # per-machine tickets: each combine is leveled to its own ticket, so the leveled map integrates to the
            # tickets by construction; the gap worth a verdict is the field-level one (sum of the tickets / integral)
            after = R["level"]["leveled_export_integral_bu"]
            Lf = None
            Lgap = 100 * (factor - 1)
        else:
            after = R["level"].get("export_total_harmonised_bu", integral)
            Lf = R["level"].get("factor_harmonised", factor)
            Lgap = 100 * (Lf - 1)
        d0, d1 = 100 * (integral / ticket - 1), 100 * (after / ticket - 1)
        ad = abs(Lgap)
        if levels_m is not None:
            lm_txt = ", ".join(f"{m} x {v['L']:.4f}" for m, v in sorted(levels_m.items()))
            verdict = (f"the monitor total is {d0:+.2f} % from the sum of the per-machine tickets (field-level L = "
                       f"{factor:.4f}, {Lgap:+.2f} % from 1); each combine is leveled to its own ticket ({lm_txt}), so "
                       "the leveled map integrates to the tickets")
            if ad <= 1:
                verdict += ": a small level correction"
            elif ad <= 3:
                verdict += ", within the 1-3 % agreement expected after a field calibration"
            elif ad <= 10:
                verdict += "; the yield monitors' calibration should be checked"
            else:
                verdict += ("; the field-level gap is > 10 %: check the tickets' coverage (whole field? this field only? "
                            "dockage? moisture basis?) before trusting the per-machine L")
        else:
            ltxt = f"L = {Lf:.4f}"
            if ad <= 1:
                verdict = (f"the monitor total agrees with the grower's weight within 1 % ({d1:+.2f} %): {ltxt} is a "
                           "small level correction")
            elif ad <= 3:
                verdict = (f"the monitor total is {d1:+.2f} % from the grower's weight ({ltxt} is {Lgap:+.2f} % from 1), "
                           f"within the 1-3 % agreement expected after a field calibration: L levels the map to the scale")
            elif ad <= 10:
                verdict = (f"the monitor total is {d1:+.2f} % from the grower's weight ({ltxt} is {Lgap:+.2f} % from 1): "
                           "L levels the map to the scale; the yield monitor's calibration should be checked")
            else:
                verdict = (f"the monitor total is {d1:+.2f} % from the grower's weight ({ltxt} is {Lgap:+.2f} % from 1, "
                           "> 10 %): check the ticket coverage (whole field? this field only? dockage? moisture basis?) "
                           "before trusting L")
            if abs(after - integral) > 1e-9 * max(1.0, integral):
                verdict = f"before the harmonisation {d0:+.2f} %, after it {d1:+.2f} %; " + verdict
        R["level"]["closure"] = {"monitor_total_bu": integral, "ticket_bu": ticket, "diff_pct": d0,
                                 "after_harmonisation_bu": after, "diff_after_pct": d1, "verdict": verdict,
                                 # the same gaps seen from the scale ("monitor 10,500 bu vs scale 10,000 bu (-4.8 %)")
                                 "scale_vs_monitor_pct": 100 * (ticket / integral - 1),
                                 "scale_vs_harmonised_pct": (100 * (ticket / after - 1) if after > 0 else None),
                                 "level_gap_pct": Lgap,
                                 "band": ("<= 1 %" if ad <= 1 else "1-3 %" if ad <= 3 else "3-10 %" if ad <= 10 else "> 10 %"),
                                 "band_rule": "bands on |L - 1| (per-machine tickets: the field-level L), house rule",
                                 # the three numbers that belong together (the band and the verdict are theirs): the
                                 # monitor total with the combine factor applied, its gap to the weight, and the L
                                 # in the file. Without a combine factor they equal monitor_total_bu / diff_pct / factor
                                 "monitor_final_bu": after, "diff_final_pct": d1,
                                 "factor_final": (Lf if Lf is not None else factor)}
        changed = bool(levels_m is None and abs(after - integral) > 1e-9 * max(1.0, integral))
        R["level"]["factor_final"] = Lf
        # 'factor' and 'export_total_bu' are the level and the monitor total BEFORE the combine factor (kept under
        # those names for older readers); what the file carries and the band / verdict describe is factor_final with
        # monitor_total_final_bu and monitor_vs_grower_final_pct
        R["level"].update(factor_before_harmonisation=factor, monitor_total_final_bu=after,
                          monitor_vs_grower_final_pct=d1, monitor_vs_grower_before_pct=d0,
                          combine_factor_changed_total=changed)
        if tk is not None and changed and R["level"].get("export_total_lb"):
            R["level"]["export_total_harmonised_lb"] = float(R["level"]["export_total_lb"]) * (
                float((ywet.fillna(0) * area_ac * harm).sum()) / float((ywet.fillna(0) * area_ac).sum())
                if ywet is not None and float((ywet.fillna(0) * area_ac).sum()) > 0 else after / integral)
        R["level"]["lvl_sy_column"] = "Yld_Lvl_Sy" if lvl_sy else None
        if tk is not None and tk.get("use") == "validate":
            notes.append("grower's weight: validate only (scale_ticket.use): L, the closure and the ticket closure test "
                         "of the combines are reported; no leveled column (Yld_Level, Yld_Lvl_Sy) goes into the clean "
                         "file and the weight applies no combine factor")
    R["warnings"] = warnings
    R["notes"] = notes
    R["filters_effective"] = ({r["filter"]: bool(r["on"]) for r in st["log"]} if sms_run else
                              {fid: bool(F[fid]["on"]) for fid in FILTER_ORDER})
    R["v2_columns"] = v2
    diag = {"th_auto": th_auto, "th_plot": th, "sens": sens, "dose": dose, "Sc": Sc, "utm": utm,
            "several": several, "harmonise": harm_on, "C": C, "plan": plan, "Ms": Ms, "time_ok": time_ok,
            "dry_sym": dry_sym is not None, "v2_columns": R["v2_columns"], "lvl_sy": lvl_sy, "ticket": tk,
            "sms": sms_run}
    # for the figures and the comparison: the strip each reading swept and its final position (flow-delay shift or the
    # swath move); internal columns, never written
    g["_gx"], g["_gy"] = np.asarray(gx, float), np.asarray(gy, float)
    g["_w_m"], g["_d_m"], g["_hdg"] = width_m.to_numpy(float), dist_m.to_numpy(float), hdg.to_numpy(float)
    # ---- seams between neighbouring passes (engine/clean/machines.py, item 13): with several combines, the steps
    # between passes of the same combine and of different combines, before and after the factor, on the kept points
    # at their final positions; a diagnostic of the map (fig_clean_seams), never applied
    if several and "machines" in R:
        kept = np.asarray(alive, bool)
        D_s = R["machines"].get("adjacency_distance_m")
        if not D_s:
            w_ = width_m.to_numpy(float)
            w_ = w_[np.isfinite(w_) & (w_ > 0)]
            D_s = machines_mod.ADJ_WIDTH_MULT * (float(np.median(w_)) if len(w_) else machines_mod.DEFAULT_WIDTH_M)
        vb = y_map.to_numpy(float)
        Bk = machines_mod.seam_blocks(g["_gx"].to_numpy(float)[kept], g["_gy"].to_numpy(float)[kept],
                                      g["Pass_Id"].to_numpy()[kept], g["_machine"].astype(str).to_numpy()[kept],
                                      vb[kept], (vb * harm.to_numpy(float))[kept], g["_hdg"].to_numpy(float)[kept],
                                      float(D_s))
        R["machines"]["seams"] = machines_mod.seam_summary(Bk, float(D_s), R["machines"].get("factor_applied"),
                                                           int(Mset.get("min_pass_pairs") or 5))
        diag["seams"] = Bk
        # a factor estimated but not applied (above the max factor, not proven ...): the seams it would give, so
        # the user can decide whether to apply it (R["machines"]["proposed"]); a diagnostic, never applied
        fa = {str(k): float(v) for k, v in (R["machines"].get("factor_applied") or {}).items()}
        prop, gates = {}, {}
        for t_ in R["machines"].get("tests") or []:
            m_ = str(t_.get("machine"))
            f_ = t_.get("factor")
            if (t_.get("primary") and f_ is not None and np.isfinite(float(f_)) and t_.get("gate") != "applied"
                    and abs(fa.get(m_, 1.0) - 1) < 1e-12):
                prop[m_], gates[m_] = float(f_), str(t_.get("gate") or "")
        if prop:
            pf = g["_machine"].astype(str).map(lambda m: prop.get(m, 1.0)).to_numpy(float) * harm.to_numpy(float)
            Bp = machines_mod.seam_blocks(g["_gx"].to_numpy(float)[kept], g["_gy"].to_numpy(float)[kept],
                                          g["Pass_Id"].to_numpy()[kept], g["_machine"].astype(str).to_numpy()[kept],
                                          vb[kept], (vb * pf)[kept], g["_hdg"].to_numpy(float)[kept], float(D_s))
            allf = {m: fa.get(m, 1.0) * prop.get(m, 1.0) for m in set(fa) | set(prop)}
            R["machines"]["proposed"] = {
                "factors": prop, "gates": gates,
                "seams": machines_mod.seam_summary(Bp, float(D_s), allf, int(Mset.get("min_pass_pairs") or 5)),
                "note": "the factors estimated but not applied, and the seams if they were applied (a diagnostic)"}
    return g0, g, gx, gy, R, diag


# ===================================================================================================== figures
# Olds College palette (assets/brand/BRAND.md, app/brand.py): navy for lines and fits, green = kept, orange = removed, light
# blue for the field-of-interest outline and the "chosen value" markers; cool light greys for grid and axes. The
# yield maps use the RdYlGn ramp (red low, green high), the same as the app's map. BLUE stays the name of the line
# colour so that the palette tuple passed to sessions.figure keeps its shape.
INK, INK2, MUTED, GRID, BASE = "#1F2937", "#4B5563", "#6B7280", "#E5EAF1", "#C9D3E0"
NAVY, LIGHT_BLUE, GREEN = "#00358E", "#009CDE", "#00B373"
BLUE, ORANGE = NAVY, "#F5A623"
SEQ = ["#e4f2fb", "#b5dff6", "#81caef", "#30ade4", "#008fd6", "#006fc0", "#0053aa", "#003992", "#002d7b", "#002463"]
YIELD_CMAP = "RdYlGn"
SHORT = {"F0": "record integrity", "F1": "moisture", "F2": "speed", "F3": "speed change", "F4": "GNSS step",
         "F5": "short pass", "F6": "heading change", "F7": "pass ends", "F8": "overlap", "F9": "wet mass",
         "F10": "mean +/- k SD", "F11": "local median", "F5b": "short runs"}


def _style(plt):
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9.5, "axes.edgecolor": BASE,
                         "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED, "axes.titlesize": 11,
                         "axes.titlecolor": INK, "axes.titleweight": "bold", "axes.grid": True, "grid.color": GRID,
                         "grid.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False,
                         "figure.facecolor": "white", "axes.facecolor": "white", "savefig.dpi": 150})


def advice(R: dict) -> list[str]:
    """Plain checks on a cleaning result, written into the cleaning JSON (R['advice']) and the report next to the
    warnings, and shown by the app and the CLI: the cleaning removed more than REMOVED_CHECK_PCT % of the points, a
    filter removed more than half of the points that reached it, an automatic F8 threshold in the lowest overlap band
    (0-2 %), and the F8 fallback."""
    out = []
    if R.get("removed_pct") is not None and R["removed_pct"] > REMOVED_CHECK_PCT:
        out.append(f"the cleaning removed {R['removed_pct']:.2f} % of the points ({R['n_in'] - R['n_kept']:,} of "
                   f"{R['n_in']:,}; more than {REMOVED_CHECK_PCT:g} %): check the filters below and the map before "
                   "using this file")
    for c in R.get("cascade", []):
        before = c["removed"] + c["remaining"]
        if c["on"] and before and c["removed"] > 0.5 * before:
            out.append(f"{c['filter']} ({c['name']}) removed {c['removed']:,} of the {before:,} points that reached "
                       f"it ({100 * c['removed'] / before:.0f} %): check that '{c['criterion']}' fits this crop and "
                       "this file")
    f8 = R.get("f8") or {}
    if f8.get("mode") == "auto" and not f8.get("fallback") and f8.get("threshold") is not None and f8["threshold"] <= 0.02:
        out.append(f"F8: the yield already drops in the lowest overlap band, so the automatic threshold is "
                   f"{100 * f8['threshold']:.3g} %; check the swath width and the header position of each combine, "
                   "or set a manual threshold")
    if f8.get("fallback") and f8.get("threshold") is not None:
        out.append(f"F8: this field's dose-response could not set the overlap threshold; the fallback "
                   f"{100 * f8['threshold']:.3g} % was used. Look at the overlap figure and confirm or set a manual "
                   "threshold")
    L = R.get("level") or {}
    Lf = L.get("factor_final")
    if Lf is None and L.get("factor") is not None and not L.get("per_machine"):
        Lf = L.get("factor_harmonised", L.get("factor"))
    if Lf is not None and abs(float(Lf) - 1) > 0.10:            # the same quantity and band as the closure verdict
        out.append(f"level: the monitor is > 10 % off the scale (L = {float(Lf):.4f}, {100 * (float(Lf) - 1):+.2f} % "
                   "from 1): check the ticket coverage (whole field? this field only? dockage, moisture basis?) before "
                   "trusting L")
    elif L.get("per_machine") and L.get("factor") is not None and abs(float(L["factor"]) - 1) > 0.10:
        out.append(f"level: the monitor is > 10 % off the per-machine tickets (field-level L = {float(L['factor']):.4f}, "
                   f"{100 * (float(L['factor']) - 1):+.2f} % from 1): check the tickets' coverage (whole field? this "
                   "field only? dockage, moisture basis?) before trusting the per-machine L")
    return out


def swath_quads(x, y, heading_deg, dist_m, width_m):
    """(n, 4, 2) corners of the strip each reading swept, as SMS draws a yield map: the logged point on the FRONT edge,
    Distance back along the heading, Swath Width across, centred on the point (engine/clean/sms_swath.py, R1). A
    reading without a finite position, heading, distance or width (or with a non-positive one) is a row of NaN."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    h = np.radians(np.asarray(heading_deg, float))
    d = np.asarray(dist_m, float)
    w = np.asarray(width_m, float) / 2
    ux, uy = np.sin(h), np.cos(h)                        # forward
    nx, ny = np.cos(h), -np.sin(h)                       # right of travel
    bx, by = x - ux * d, y - uy * d
    q = np.stack([np.c_[x + nx * w, y + ny * w], np.c_[x - nx * w, y - ny * w],
                  np.c_[bx - nx * w, by - ny * w], np.c_[bx + nx * w, by + ny * w]], axis=1)
    with np.errstate(invalid="ignore"):
        ok = np.isfinite(q).all(axis=(1, 2)) & (d > 0) & (w > 0)
    q[~ok] = np.nan
    return q


def _map_strips(g, x0=0.0, y0=0.0):
    """(before, after) strips of the maps, moved by (-x0, -y0): before = the swept strip of every reading at its
    logged position (swept_strips, stored by clean()); after = the strip of each record's flow-delay source, the ground
    its grain came from (a record whose source lies beyond its pass is drawn as a point). Without stored strips: the
    W x D rectangles (swath_quads) at the logged and at the final positions."""
    Qs = strips_of(g)
    if Qs is None:
        return (swath_quads(g._x.values - x0, g._y.values - y0, g["_hdg"], g["_d_m"], g["_w_m"]),
                swath_quads(g["_gx"].values - x0, g["_gy"].values - y0, g["_hdg"], g["_d_m"], g["_w_m"]))
    q_log = Qs - np.array([x0, y0])
    src = g["_src"].to_numpy(int) if "_src" in g else np.arange(len(g))
    q_fin = q_log[src].copy()
    if "_beyond" in g:
        q_fin[g["_beyond"].to_numpy(bool)] = np.nan
    return q_log, q_fin


def _strips(ax, quads, x, y, values=None, cmap=None, vmin=None, vmax=None, color=None, zorder=1, s=1.2, norm=None):
    """Draw readings as their strips (one PolyCollection); a reading without a strip is drawn as a point. Coloured by
    `values` (with cmap / vmin / vmax, or cmap / norm) or in one `color`. Returns the mappable (the collection, else
    the scatter)."""
    from matplotlib.collections import PolyCollection
    quads = np.asarray(quads, float)
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    v = np.asarray(values, float) if values is not None else None
    fin = np.isfinite(v) if v is not None else np.ones(len(x), bool)
    ok = np.isfinite(quads).all(axis=(1, 2)) & fin
    rest = ~ok & fin & np.isfinite(x) & np.isfinite(y)
    mp = None
    if ok.any():
        pc = PolyCollection(quads[ok], linewidths=0, rasterized=True, zorder=zorder)
        if v is not None:
            pc.set_array(v[ok])
            pc.set_cmap(cmap)
            if norm is not None:
                pc.set_norm(norm)
            else:
                pc.set_clim(vmin, vmax)
        else:
            pc.set_facecolor(color)
        ax.add_collection(pc, autolim=False)
        mp = pc
    if rest.any():
        sc = ax.scatter(x[rest], y[rest], c=(v[rest] if v is not None else color), s=s, cmap=cmap,
                        **({"norm": norm} if norm is not None else {"vmin": vmin, "vmax": vmax}), linewidths=0,
                        rasterized=True, zorder=zorder)
        mp = mp or sc
    return mp


def _extent(ax, x, y, pad=15.0):
    """Axis limits from the readings' positions (the strips reach at most a header width beyond them)."""
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if np.isfinite(x).any() and np.isfinite(y).any():
        ax.set_xlim(np.nanmin(x) - pad, np.nanmax(x) + pad)
        ax.set_ylim(np.nanmin(y) - pad, np.nanmax(y) + pad)


def _final_geometry(g, sms):
    """(w_m, d_m) of each reading after the cleaning: the logged ones, or the optimised width / shortened distance of
    the SMS swath step where it changed them."""
    w, d = g["_w_m"].to_numpy(float), g["_d_m"].to_numpy(float)
    if sms and "Wdth_Opt" in g:
        wo = g["Wdth_Opt"].to_numpy(float) * 0.3048
        w = np.where(np.isfinite(wo), wo, w)
    if sms and "Dist_Opt" in g:
        do = g["Dist_Opt"].to_numpy(float) * 0.3048
        d = np.where(np.isfinite(do), do, d)
    return w, d


def figures(g, R, diag, ycol, tag, out):
    """The five PNG figures (map, f8, cascade, lag, sessions); returns {name: absolute path}. The maps draw each
    reading as the strip its header swept since the previous reading (swept_strips: consecutive strips tile, so a
    turn shows no fan of overlapping rectangles), before at the logged position, after at the record's flow-delay
    source (_map_strips)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    _style(plt)
    y = g[ycol].astype(float)
    keep = g.Keep.values == 1
    files = {}

    # ---- map: before / after / removed by filter group
    cmap = matplotlib.colormaps[YIELD_CMAP]
    lo, hi = np.nanpercentile(y[keep], [2, 98]) if keep.any() else (0, 1)
    x0, y0 = float(np.nanmin(g._x)), float(np.nanmin(g._y))
    x_, y_ = g._x.values - x0, g._y.values - y0
    fx, fy = g["_gx"].values - x0, g["_gy"].values - y0                   # final positions
    q_log, q_fin = _map_strips(g, x0, y0)
    fig = plt.figure(figsize=(13, 9.6))
    outer = fig.add_gridspec(2, 1, height_ratios=[1.75, 1], hspace=0.16)
    top = outer[0].subgridspec(1, 3, width_ratios=[1, 1, 0.04], wspace=0.08)
    bot = outer[1].subgridspec(1, 5, wspace=0.06)
    axA, axB, cax = fig.add_subplot(top[0]), fig.add_subplot(top[1]), fig.add_subplot(top[2])
    sc = None
    for ax, m, q, px, py, title in ((axA, np.ones(len(g), bool), q_log, x_, y_,
                                     f"Before: all {len(g):,} export readings"),
                                    (axB, keep, q_fin, fx, fy,
                                     f"After: {keep.sum():,} kept ({R['removed_pct']:.2f} % removed)")):
        mp = _strips(ax, q[m], px[m], py[m], y.values[m], cmap, lo, hi)
        sc = mp if mp is not None else sc
        _extent(ax, x_, y_)
        ax.set_aspect("equal")
        ax.set_title(title, loc="left")
        ax.set_xlabel("Easting from field corner (m)")
        ax.grid(False)
    plan = diag.get("plan")
    if plan is not None:                       # the field of interest / plan outline over both maps
        rings = aoi_mod.outline_xy(plan, diag["utm"], float(np.nanmin(g._x)), float(np.nanmin(g._y)))
        # outline_xy puts the plots first and the field (union) outline last: the last ring is the field outline of a
        # design, every ring is the field when a boundary was given (a boundary has no plot rings)
        n_plot_rings = len(rings) - 1
        for ax in (axA, axB):
            for k, (rx, ry) in enumerate(rings):
                last = k >= n_plot_rings or not plan.is_plan
                ax.plot(rx, ry, color=LIGHT_BLUE if last else INK2, lw=1.4 if last else 0.45,
                        alpha=1 if last else 0.7, zorder=5)
    axA.set_ylabel("Northing from field corner (m)")
    axB.set_yticklabels([])
    cb = fig.colorbar(sc, cax=cax)
    cb.outline.set_visible(False)
    cb.set_label(f"{ycol} (bu/ac); scale = 2nd-98th percentile of kept points", color=INK2)
    for j, (name, tags) in enumerate(GROUPS):
        ax = fig.add_subplot(bot[j])
        m = g.Rem_By.isin(tags).values
        ax.scatter(x_, y_, s=0.4, c=GRID, linewidths=0, rasterized=True)
        ax.scatter(x_[m], y_[m], s=1.6, c=ORANGE, linewidths=0, rasterized=True)
        ax.set_aspect("equal")
        ax.set_title(f"{name}\n{m.sum():,} removed", loc="left", fontsize=9.5)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.grid(False)
        for sp_ in ax.spines.values():
            sp_.set_visible(False)
    fig.suptitle(f"{tag}: yield export before and after cleaning, each reading as the strip its header swept since "
                 "the previous reading. Bottom row: points removed by each filter group (orange) over all export points "
                 "(grey)"
                 + (" Blue outline: field of interest"
                                                              + (", grey: plots" if plan.is_plan else "")
                                                              if plan is not None else ""),
                 x=0.08, y=0.94, ha="left", fontsize=11.5, color=INK)
    files["map"] = os.path.join(out, "fig_clean_map.png")
    fig.savefig(files["map"], bbox_inches="tight")
    plt.close(fig)
    # ---- the final map, the combines before / after their factor and the seams (v2.6.1)
    files.update(figures_maps(g, R, diag, tag, out, q_fin, fx, fy, x_, y_, x0, y0, g["_ymap"].to_numpy(float), ycol))

    # ---- F8: dose-response and sensitivity (three panels, one measure each)
    dose, th, sens = diag["dose"], diag["th_plot"], pd.DataFrame(diag["sens"])
    f8 = R["f8"]
    how8 = "floor" if f8.get("floor_applied") else "fallback" if f8.get("fallback") else "auto"
    head = {"auto": f"F8 threshold {th * 100:.3g} % ({how8})", "fixed":
            f"F8 threshold {th * 100:.3g} % (fixed; auto {diag['th_auto'] * 100:.3g} %)",
            "off": f"F8 off (fixed/auto threshold would be {th * 100:.3g} %)"}[f8["mode"]]
    fig, axs = plt.subplots(1, 3, figsize=(15, 4.8), gridspec_kw={"width_ratios": [1.6, 1, 1], "wspace": 0.3})
    ax = axs[0]
    up = dict(zip(DOSE_LABELS, DOSE_UPPER))
    xs = np.arange(len(dose))
    cols = [GREEN if up[b] <= th + 1e-12 else ORANGE for b in dose.index]
    ax.bar(xs, dose["vs_zero_pct"], color=cols, width=0.72, zorder=3)
    ax.axhline(0, color=BASE, lw=1)
    tol = f8["tolerance_pct"]
    ax.axhline(-tol, color=MUTED, lw=1, ls="--")
    ax.text(-0.45, -tol - 0.6, f"-{tol:g} % tolerance", color=MUTED, va="top", ha="left", fontsize=8.5)
    loc8 = f8.get("reference_mode") == "local"
    k_ = lambda v: f"{v / 1000:.2f}k" if v >= 10000 else f"{v:,}"  # noqa: E731
    ax.set_xticks(xs, [(b if b != ">35" else "> 35") + (f"\n{k_(int(r['size_used']))}\n/{k_(int(r['size']))}" if loc8
                                                       else f"\n{int(r['size']):,}") for b, r in dose.iterrows()],
                  fontsize=7.8)
    ax.set_xlabel("Footprint share over ground already cut (%) / "
                  + ("points with local controls / all points" if loc8 else "number of points"))
    ax.set_ylabel("Median yield vs local no-overlap controls (%)" if f8.get("reference_mode") == "local"
                  else "Mean yield vs no-overlap band (%)")
    vz = dose.vs_zero_pct.replace([np.inf, -np.inf], np.nan)
    if vz.notna().any():
        ax.set_ylim(min(vz.min(), -5) * 1.15 - 2, max(vz.max(), 0) + 3)
    ax.set_title("Dose-response: " + head, loc="left")
    ax.legend(handles=[Patch(color=GREEN, label=f"<= {th * 100:.3g} %"),
                       Patch(color=ORANGE, label="above the threshold" + (" (removed by F8)" if f8["mode"] != "off"
                                                                          else ""))],
              frameon=False, loc="lower left", fontsize=8.5)
    ax.grid(axis="x", visible=False)
    r_th = sens[np.isclose(sens.threshold, th)].iloc[0]
    for ax, col, ylab, ttl in ((axs[1], "removed", "Points removed by F8", "Sensitivity: points removed"),
                               (axs[2], "mean_kept", f"Mean {ycol} of points passing F8 (bu/ac)",
                                "Sensitivity: mean yield kept")):
        ax.plot(sens.threshold * 100, sens[col], color=BLUE, lw=2, marker="o", ms=5, zorder=3)
        if np.isfinite(r_th[col]):
            ax.scatter([th * 100], [r_th[col]], s=90, facecolor="white", edgecolor=LIGHT_BLUE, lw=2, zorder=4)
            lab = (f"{int(r_th.removed):,} removed at {th * 100:.3g} %" if col == "removed"
                   else f"{r_th.mean_kept:.2f} bu/ac")
            ax.annotate(lab, (th * 100, r_th[col]), xytext=(10, 8 if col == "removed" else -14),
                        textcoords="offset points", color=INK2, fontsize=8.5)
        ok_ = np.isfinite(sens[col].to_numpy(float)) & (sens.threshold.to_numpy(float) > 0)
        if ok_.any():                         # a log axis needs at least one positive, finite point
            ax.set_xscale("log")
            ax.set_xticks([1, 2, 5, 10, 15, 25, 50], ["1", "2", "5", "10", "15", "25", "50"])
        else:
            ax.text(0.5, 0.5, "no points reached F8", transform=ax.transAxes, ha="center", color=INK2)
        ax.set_xlabel("F8 threshold (% of footprint already cut)")
        ax.set_ylabel(ylab)
        ax.set_title(ttl, loc="left")
    files["f8"] = os.path.join(out, "fig_clean_f8.png")
    fig.savefig(files["f8"], bbox_inches="tight")
    plt.close(fig)

    # ---- cascade
    cas = pd.DataFrame(R["cascade"])
    fig, ax = plt.subplots(figsize=(10, 5.6))
    yy_ = np.arange(len(cas))[::-1]
    ax.barh(yy_, cas.removed, color=[ORANGE if o else GRID for o in cas.on], height=0.66, zorder=3)
    xmax = max(cas.removed.max(), 1)
    for yi, r in zip(yy_, cas.itertuples()):
        ax.text(r.removed + xmax * 0.01, yi, (f"{r.removed:,}   ({r.remaining:,} left)" if r.on else "off"),
                va="center", fontsize=8.5, color=INK2 if r.on else MUTED)
    ax.set_yticks(yy_, [f"{f}  {SHORT.get(f, '')}" for f in cas["filter"]])
    ax.set_xlim(0, xmax * 1.32)
    ax.set_xlabel("Points removed")
    ax.grid(axis="y", visible=False)
    ax.set_title(f"Cleaning cascade: {R['n_in']:,} in -> {R['n_kept']:,} kept ({R['removed_pct']:.2f} % removed)",
                 loc="left")
    files["cascade"] = os.path.join(out, "fig_clean_cascade.png")
    fig.savefig(files["cascade"], bbox_inches="tight")
    plt.close(fig)

    # ---- lag scan
    Sc, lag = diag["Sc"], R["lag"]
    fig, ax = plt.subplots(figsize=(8, 4.6))
    if len(Sc):
        ax.plot(Sc.lag, Sc.r, color=BLUE, lw=2, marker="o", ms=5, zorder=3)
        b = Sc.loc[Sc.r.idxmax()]
        ax.scatter([b.lag], [b.r], s=90, facecolor="white", edgecolor=LIGHT_BLUE, lw=2, zorder=4)
        ax.annotate(f"best {int(b.lag)} (r = {b.r:.3f})", (b.lag, b.r), xytext=(8, 8), textcoords="offset points",
                    color=INK2, fontsize=8.5)
        ax.axvline(0, color=MUTED, lw=1, ls="--")
        ax.set_ylim(Sc.r.min() - 0.02, Sc.r.max() + 0.03)
    else:
        ax.text(0.5, 0.5, "not enough opposite-direction neighbours for the scan", transform=ax.transAxes,
                ha="center", color=INK2)
    ax.set_xlabel("Shift of the yield along the pass (records)")
    ax.set_ylabel("r, opposite-direction neighbours")
    dec = (f"shift of {lag['applied_records']} records applied" if lag["applied_records"]
           else f"no correction ({lag['rule']})")
    ax.set_title("Residual flow-delay scan\n" + dec, loc="left")
    files["lag"] = os.path.join(out, "fig_clean_lag.png")
    fig.savefig(files["lag"], bbox_inches="tight")
    plt.close(fig)

    # ---- sessions: moisture and dry yield against time, coloured by session, standard moisture as a line
    C = diag["C"]
    moi = g[C["moisture"]].astype(float).values if C.get("moisture") else None
    files["sessions"] = sessions_mod.figure(
        g, keep, g["Session"].values, g["Harv_Day"].values, moi, y.values,
        g["Yld_Dry_Sy"].values if diag.get("dry_sym") else None, diag.get("Ms"),
        g["_t"].values if diag.get("time_ok") and "_t" in g else None, os.path.join(out, "fig_clean_sessions.png"),
        tag, (INK2, MUTED, BASE, BLUE, ORANGE, GRID, SEQ), _style)
    return files


def figures_sms(g, R, diag, tag, out):
    """The SMS 3-sigma figures: map (before / after on the scaled yield, removed points per step group), the step
    cascade, the limits of every 3-sigma step over the values entering it, and the sessions; {name: path}."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _style(plt)
    y = pd.Series(g["_ymap"].to_numpy(float), index=g.index)
    keep = g.Keep.values == 1
    files = {}
    rem = g.Rem_By.astype(str)
    cmap = matplotlib.colormaps[YIELD_CMAP]
    lo, hi = np.nanpercentile(y[keep], [2, 98]) if keep.any() else (0, 1)
    x0, y0 = float(np.nanmin(g._x)), float(np.nanmin(g._y))
    x_, y_ = g._x.values - x0, g._y.values - y0
    fx, fy = g["_gx"].values - x0, g["_gy"].values - y0                   # moved by Y_Offset and to the kept strip
    w_m, d_m = _final_geometry(g, True)
    q_log = _map_strips(g, x0, y0)[0]                                     # the swept strips (as logged)
    q_fin = swath_quads(fx, fy, g["_hdg"], d_m, w_m)                      # SMS's own optimised rectangles
    fig = plt.figure(figsize=(13, 9.6))
    outer = fig.add_gridspec(2, 1, height_ratios=[1.75, 1], hspace=0.16)
    top = outer[0].subgridspec(1, 3, width_ratios=[1, 1, 0.04], wspace=0.08)
    bot = outer[1].subgridspec(1, 5, wspace=0.06)
    axA, axB, cax = fig.add_subplot(top[0]), fig.add_subplot(top[1]), fig.add_subplot(top[2])
    yraw = g[diag["C"]["yield"]].astype(float)
    sc = None
    for ax, m, q, px, py, vals, title in ((axA, np.ones(len(g), bool), q_log, x_, y_, yraw,
                                           f"Before: all {len(g):,} export readings"),
                                          (axB, keep, q_fin, fx, fy, y,
                                           f"After: {keep.sum():,} kept ({R['removed_pct']:.2f} % removed), "
                                           "optimised strips")):
        mp = _strips(ax, q[m], px[m], py[m], vals.values[m], cmap, lo, hi)
        sc = mp if mp is not None else sc
        _extent(ax, x_, y_)
        ax.set_aspect("equal")
        ax.set_title(title, loc="left")
        ax.set_xlabel("Easting from field corner (m)")
        ax.grid(False)
    plan = diag.get("plan")
    if plan is not None:
        rings = aoi_mod.outline_xy(plan, diag["utm"], float(np.nanmin(g._x)), float(np.nanmin(g._y)))
        n_plot_rings = len(rings) - 1
        for ax in (axA, axB):
            for k, (rx, ry) in enumerate(rings):
                last = k >= n_plot_rings or not plan.is_plan
                ax.plot(rx, ry, color=LIGHT_BLUE if last else INK2, lw=1.4 if last else 0.45,
                        alpha=1 if last else 0.7, zorder=5)
    axA.set_ylabel("Northing from field corner (m)")
    axB.set_yticklabels([])
    cb = fig.colorbar(sc, cax=cax)
    cb.outline.set_visible(False)
    cb.set_label("dry yield (bu/ac); scale = 2nd-98th percentile of kept points", color=INK2)
    for j, (name, tags) in enumerate(sms_mod.step_groups(sms_mod.filter_order_of(R.get("sms3sigma") or {}))):
        ax = fig.add_subplot(bot[j])
        m = rem.str[:2].isin(tags).values
        ax.scatter(x_, y_, s=0.4, c=GRID, linewidths=0, rasterized=True)
        ax.scatter(x_[m], y_[m], s=1.6, c=ORANGE, linewidths=0, rasterized=True)
        ax.set_aspect("equal")
        ax.set_title(f"{name}\n{m.sum():,} removed", loc="left", fontsize=9.5)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.grid(False)
        for sp_ in ax.spines.values():
            sp_.set_visible(False)
    fig.suptitle(f"{tag}: SMS 3-sigma cleaning; before: each reading as the strip its header swept, after: SMS's own "
                 "rectangles as Optimize Swath Widths left them. Bottom row: points removed by each step (orange) over all export points "
                 "(grey)", x=0.08, y=0.94, ha="left", fontsize=11.5, color=INK)
    files["map"] = os.path.join(out, "fig_clean_map.png")
    fig.savefig(files["map"], bbox_inches="tight")
    plt.close(fig)
    # ---- the final map, the combines before / after their factor and the seams (v2.6.1)
    files.update(figures_maps(g, R, diag, tag, out, q_fin, fx, fy, x_, y_, x0, y0, y.to_numpy(float),
                              f"{diag['C']['yield']} scaled by Optimize Swath Widths"))

    # ---- the steps
    cas = pd.DataFrame(R["cascade"])
    fig, ax = plt.subplots(figsize=(10, 4.8))
    yy_ = np.arange(len(cas))[::-1]
    ax.barh(yy_, cas.removed, color=[ORANGE if o else GRID for o in cas.on], height=0.66, zorder=3)
    xmax = max(cas.removed.max(), 1)
    for yi, r in zip(yy_, cas.itertuples()):
        extra = f", {int(r.narrowed):,} narrowed" if "narrowed" in cas and np.isfinite(getattr(r, "narrowed", np.nan)) \
            else ""
        ax.text(r.removed + xmax * 0.01, yi, (f"{r.removed:,}   ({r.remaining:,} left{extra})" if r.on else "off"),
                va="center", fontsize=8.5, color=INK2 if r.on else MUTED)
    ax.set_yticks(yy_, [f"{f}  {r_name}" for f, r_name in zip(cas["filter"], cas["name"])])
    ax.set_xlim(0, xmax * 1.45)
    ax.set_xlabel("Points removed")
    ax.grid(axis="y", visible=False)
    ax.set_title(f"SMS 3-sigma steps: {R['n_in']:,} in -> {R['n_kept']:,} kept ({R['removed_pct']:.2f} % removed)",
                 loc="left")
    files["cascade"] = os.path.join(out, "fig_clean_cascade.png")
    fig.savefig(files["cascade"], bbox_inches="tight")
    plt.close(fig)

    # ---- limits: the values entering each 3-sigma step and the limits applied (one line pair per group)
    steps = [s for s in (R.get("sms3sigma") or {}).get("steps", []) if s.get("lo") is not None]
    order = [r["filter"] for r in R["cascade"]]
    rank = {f: k for k, f in enumerate(order)}
    rem_rank = rem.map(lambda v: rank.get(v, len(order))).to_numpy()
    C = diag["C"]
    # the values of a step are those of its ATTRIBUTE (the step row's), never of its id: S3 is the moisture and S4
    # the speed in the method's order, the other way round with filter_order speed_moisture
    series = {"speed": (g["_spd_s"].to_numpy(float) if "_spd_s" in g
                        else g[C["speed_mph"]].astype(float).to_numpy()),
              "moisture": (g[C["moisture"]].astype(float).to_numpy() if C.get("moisture") else None)}
    ids = [f for f in order if f in ("S3", "S4") or f.startswith("S5")]
    ids = [f for f in ids if any(s["step"] == f for s in steps)]
    if ids:
        fig, axs = plt.subplots(1, len(ids), figsize=(4.2 * len(ids), 3.8), squeeze=False)
        for ax, f in zip(axs[0], ids):
            ss = [s for s in steps if s["step"] == f]
            attr = ss[0]["attribute"]
            vals = series.get(attr) if attr in series else y.to_numpy(float)
            entering = rem_rank >= rank[f]
            v = vals[entering & np.isfinite(vals)] if vals is not None else np.array([])
            if len(v):
                lo_, hi_ = np.nanpercentile(v, [0.1, 99.9])
                pad = 0.15 * (hi_ - lo_ + 1e-9)
                rng = (min(lo_ - pad, min(s["lo"] for s in ss)), max(hi_ + pad, max(s["hi"] for s in ss)))
                ax.hist(np.clip(v, *rng), bins=60, range=rng, color=GREEN, alpha=0.8, zorder=2)
            for s in ss:
                for b in (s["lo"], s["hi"]):
                    ax.axvline(b, color=ORANGE, lw=1.4 if len(ss) == 1 else 0.9, zorder=3)
            unit = ss[0]["unit"]
            name = ss[0]["attribute"]
            txt = (f"{ss[0]['lo']:.2f}-{ss[0]['hi']:.2f} {unit}" if len(ss) == 1 else f"{len(ss)} groups")
            ax.set_title(f"{f} {name}: {txt}", loc="left", fontsize=10)
            ax.set_xlabel(f"{name} ({unit}) of the points entering {f}")
            ax.set_yticks([])
        fig.suptitle("SMS 3-sigma limits (orange): mean +/- k SD of the points still alive before each step",
                     x=0.02, ha="left", fontsize=11, color=INK)
        fig.tight_layout()
        files["limits"] = os.path.join(out, "fig_sms_limits.png")
        fig.savefig(files["limits"], bbox_inches="tight")
        plt.close(fig)

    moi = g[C["moisture"]].astype(float).values if C.get("moisture") else None
    files["sessions"] = sessions_mod.figure(
        g, keep, g["Session"].values, g["Harv_Day"].values, moi, y.values,
        g["Yld_Dry_Sy"].values if diag.get("dry_sym") else None, diag.get("Ms"),
        g["_t"].values if diag.get("time_ok") and "_t" in g else None, os.path.join(out, "fig_clean_sessions.png"),
        tag, (INK2, MUTED, BASE, BLUE, ORANGE, GRID, SEQ), _style)
    return files


# ===================================================================================================== write
# ---- the final, combine and seam maps (v2.6.1), for the OCCD and the SMS 3-sigma runs alike. The final map is the
# yield column to map read back from the clean file (diag['final'], write_all); the combine map shows which
# combine cut each strip and the yield before and after its factor; the seam map shows how much neighbouring passes
# differ, inside one combine and between combines (engine/clean/machines.py, item 13). Every value drawn is a kept
# reading's own value: no interpolation, no smoothing.
COMBINE_COLORS = ["#00358E", "#F5A623", "#009CDE", "#00B373", "#7C3AED", "#E11D48", "#0F766E", "#A16207"]
SEAM_COLORS = ["#FFE7A3", "#FDB44B", "#F4762E", "#D7263D", "#6E0B2A"]      # < 5, 5-10, 10-15, 15-20, >= 20 %
STRIP_BG = "#E6ECF3"                                                        # the kept strips under the seams
GATE_SHORT = {"ci_includes_1": "not applied: its CI includes 1", "too_few_pairs": "not applied: too few side-by-side "
              "passes", "no_link": "not applied: no link to the reference", "above_max_factor": "not applied: above "
              "10 %", "above_max_factor_unconfirmed": "not applied: above 10 %, not confirmed by the weight",
              "below_min_factor": "not applied: below 1 %", "sizes_disagree": "not applied: 30 m and 50 m disagree",
              "spatially_inconsistent": "not applied: it changes over the field"}


def _outline(axes, g, diag):
    """The field of interest (light blue) and the plots (grey) of the plan over the map axes."""
    plan = diag.get("plan")
    if plan is None:
        return
    rings = aoi_mod.outline_xy(plan, diag["utm"], float(np.nanmin(g._x)), float(np.nanmin(g._y)))
    n_plot_rings = len(rings) - 1
    for ax in axes:
        for k, (rx, ry) in enumerate(rings):
            last = k >= n_plot_rings or not plan.is_plan
            ax.plot(rx, ry, color=LIGHT_BLUE if last else INK2, lw=1.4 if last else 0.45, alpha=1 if last else 0.7,
                    zorder=5)


def _map_axes(ax, ex, ey, left=True):
    _extent(ax, ex, ey)
    ax.set_aspect("equal")
    ax.grid(False)
    ax.set_xlabel("Easting from field corner (m)")
    if left:
        ax.set_ylabel("Northing from field corner (m)")
    else:
        ax.set_yticklabels([])


def _panel_h(ex, ey, n, fig_w) -> float:
    """The height (in) of one of n equal-aspect map panels side by side across fig_w inches (the colour bar and the
    gaps take about 14 % of the width), between 2.8 and 7.5 in."""
    xr = float(np.nanmax(ex) - np.nanmin(ex)) + 30.0
    yr = float(np.nanmax(ey) - np.nanmin(ey)) + 30.0
    return float(np.clip((0.86 * fig_w / n) * (yr / xr if xr > 0 else 1.0), 2.8, 7.5))


def _align_cbar(cax, ax):
    """Give the colour bar the height of its map once the map has its equal aspect."""
    ax.apply_aspect()
    p, c = ax.get_position(), cax.get_position()
    cax.set_position([c.x0, p.y0, c.width, p.height])


def _wrap(text: str, width: int) -> str:
    import textwrap
    return "\n".join(textwrap.wrap(text, width))


def combine_order(g, keep, RM) -> tuple:
    """(the combine of every reading, the combines with kept readings: the reference first, then by kept points)."""
    mach = g["_machine"].astype(str).to_numpy()
    cnt = pd.Series(mach[keep]).value_counts()
    ref = str(RM.get("reference")) if RM.get("reference") is not None else None
    names = ([ref] if ref in cnt.index else []) + [m for m in cnt.index if m != ref]
    return mach, names


def factor_label(RM: dict, m: str) -> str:
    """The factor of combine m in a few words (the legend of the combine map)."""
    fa = {str(k): float(v) for k, v in (RM.get("factor_applied") or {}).items()}
    dec = str((RM.get("decision") or {}).get(m, ""))
    if m == str(RM.get("reference")) and abs(fa.get(m, 1.0) - 1) <= 1e-12:
        return "reference (x 1)"
    if RM.get("per_machine_totals"):
        return f"x {fa.get(m, 1.0):.4f} (its own scale ticket)"
    if abs(fa.get(m, 1.0) - 1) > 1e-12:
        return f"x {fa[m]:.4f} applied"
    if dec.startswith("harmonisation switched off"):
        return "x 1 (harmonisation switched off)"
    if (RM.get("method_used") or {}).get(m) is None and m in (RM.get("method_used") or {}):
        lc = ((RM.get("layout_class") or {}).get(m) or {}).get("local")
        return "x 1 (not applied: " + ("they never harvested side by side)" if lc == "no_contact" else
                                       "they harvested in blocks)")
    t = machines_mod.primary_tests(RM.get("tests") or []).get(m) or {}
    return "x 1 (" + GATE_SHORT.get(t.get("gate"), "not applied") + ")"


def figure_combines(g, R, diag, tag, out, q, px, py, ex, ey, before, ylab):
    """fig_clean_combines.png: which combine cut each kept strip, and the yield before and after the combine factor on
    one colour scale (only the 'before' map when no factor other than 1 was applied). None with fewer than two
    combines."""
    RM = R.get("machines") or {}
    keep = g.Keep.values == 1
    mach, names = combine_order(g, keep, RM)
    if len(names) < 2:
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    _style(plt)
    fa = {str(k): float(v) for k, v in (RM.get("factor_applied") or {}).items()}
    moved = any(abs(fa.get(m, 1.0) - 1) > 1e-12 for m in names)
    after = g["Yld_Harm"].to_numpy(float)
    before = np.asarray(before, float)
    nmap = 3 if moved else 2
    cmap = matplotlib.colormaps[YIELD_CMAP]
    lo, hi = np.nanpercentile(before[keep], [2, 98])
    fig_w = 15 if moved else 11.5
    ph = _panel_h(ex, ey, nmap, fig_w)
    ncol = 2 if len(names) > 2 else 1
    leg_h = 0.3 * int(np.ceil(len(names) / ncol))
    top_in, bottom_in = 1.15, 0.85 + leg_h
    fig_h = top_in + ph + bottom_in
    fig = plt.figure(figsize=(fig_w, fig_h))
    gs = fig.add_gridspec(1, nmap + 1, width_ratios=[1] * nmap + [0.035], wspace=0.07, top=1 - top_in / fig_h,
                          bottom=bottom_in / fig_h)
    axs = [fig.add_subplot(gs[k]) for k in range(nmap)]
    cax = fig.add_subplot(gs[nmap])
    col = {m: COMBINE_COLORS[k % len(COMBINE_COLORS)] for k, m in enumerate(names)}
    for m in names:
        sel = keep & (mach == m)
        _strips(axs[0], q[sel], px[sel], py[sel], color=col[m])
    axs[0].set_title("Which combine cut each strip", loc="left")
    sc = None
    panels = [(axs[1], before, ("Before the factor" if moved else "No combine factor applied") + f"\n{ylab}")]
    if moved:
        panels.append((axs[2], after, "After the factor: Yld_Harm\n= the reading x its combine's factor"))
    for ax, v, title in panels:
        mp = _strips(ax, q[keep], px[keep], py[keep], v[keep], cmap, lo, hi)
        sc = mp if mp is not None else sc
        ax.set_title(title, loc="left")
    for k, ax in enumerate(axs):
        _map_axes(ax, ex, ey, left=(k == 0))
    _outline(axs, g, diag)
    cb = fig.colorbar(sc, cax=cax)
    cb.outline.set_visible(False)
    cb.set_label("bu/ac (one scale for both maps)", color=INK2)
    _align_cbar(cax, axs[-1])
    n_kept = pd.Series(mach[keep]).value_counts()
    handles = [Patch(color=col[m], label=f"{m}: {factor_label(RM, m)}; {int(n_kept.get(m, 0)):,} kept points")
               for m in names]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.07, (leg_h + 0.1) / fig_h), ncol=ncol,
               frameon=False, fontsize=9.5)
    fig.suptitle(_wrap(f"{tag}: the combines and their factor. The factor is one number per combine, from the passes "
                       "they cut side by side; the reference keeps its readings. Colour scale: 2nd-98th percentile of "
                       "the kept readings before the factor", int(fig_w * 11)),
                 x=0.07, y=0.995, ha="left", va="top", fontsize=11, color=INK)
    path = os.path.join(out, "fig_clean_combines.png")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def figure_seams(g, R, diag, tag, out, q, px, py, ex, ey, x0, y0):
    """fig_clean_seams.png: the seams between neighbouring passes as 50 m stretches coloured by how much the two
    passes differ, for the passes of one combine and for the passes of different combines (before and, when a factor
    moved them, after the factor), with the share of the stretches in each class below. None without the seam
    diagnostic (one combine)."""
    RM = R.get("machines") or {}
    S, B = RM.get("seams"), diag.get("seams")
    if not S or B is None:
        return None
    import textwrap
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.collections import LineCollection
    from matplotlib.colors import BoundaryNorm, ListedColormap
    _style(plt)
    keep = g.Keep.values == 1
    cross = (B["ma"].astype(str) != B["mb"].astype(str)).to_numpy() if len(B) else np.zeros(0, bool)
    moved = bool(S["different_combines"].get("factor_moved_them"))
    L = f"{S['stretch_m']:g} m"
    panels = [("same", ~cross, "lr_before", "SAME combine", S["same_combine"]),
              ("diff", cross, "lr_before", "DIFFERENT combines" + (", before the factor" if moved else ""),
               S["different_combines"]["before"])]
    if moved:
        panels.append(("after", cross, "lr_after", "DIFFERENT combines, after the factor",
                       S["different_combines"]["after"]))
    n = len(panels)
    bounds = [0.0] + [float(c) for c in S["classes_pct"]] + [1e9]
    lcmap = ListedColormap(SEAM_COLORS)
    norm = BoundaryNorm(bounds, lcmap.N)
    fig_w = 15 if n == 3 else 11.5
    ph = _panel_h(ex, ey, n, fig_w)
    bars_h = 0.55 * n + 0.6
    top_in, gap_in, bottom_in = 1.25, 1.0, 2.05
    fig_h = top_in + ph + gap_in + bars_h + bottom_in
    fig = plt.figure(figsize=(fig_w, fig_h))
    outer = fig.add_gridspec(2, 1, height_ratios=[ph, bars_h], hspace=gap_in / ((ph + bars_h) / 2),
                             top=1 - top_in / fig_h, bottom=bottom_in / fig_h)
    top = outer[0].subgridspec(1, n + 1, width_ratios=[1] * n + [0.035], wspace=0.07)
    axs = []
    for k, (key, sel, colname, title, st) in enumerate(panels):
        ax = fig.add_subplot(top[k])
        axs.append(ax)
        _strips(ax, q[keep], px[keep], py[keep], color=STRIP_BG)
        Bs = B[sel]
        if len(Bs):
            segs = [np.asarray(ln, float) - np.array([x0, y0]) for ln in Bs["line"]]
            lc = LineCollection(segs, cmap=lcmap, norm=norm, linewidths=2.4, capstyle="butt", zorder=3)
            lc.set_array(machines_mod.seam_pct(Bs[colname]))
            ax.add_collection(lc)
        sub = (f"median {st['median_pct']:.2f} % · {st['stretches']:,} stretch{'es' if st['stretches'] != 1 else ''}"
               f" · {st['pass_pairs']} pass pair{'s' if st['pass_pairs'] != 1 else ''}"
               if st["stretches"] else "no neighbouring passes")
        ax.set_title(f"{title}\n{sub}", loc="left", fontsize=10)
        _map_axes(ax, ex, ey, left=(k == 0))
    _outline(axs, g, diag)
    cax = fig.add_subplot(top[n])
    sm_ = matplotlib.cm.ScalarMappable(norm=BoundaryNorm(list(range(len(SEAM_COLORS) + 1)), lcmap.N), cmap=lcmap)
    cb = fig.colorbar(sm_, cax=cax, ticks=[k + 0.5 for k in range(len(SEAM_COLORS))])
    edges = [f"{c:g}" for c in S["classes_pct"]]
    cb.ax.set_yticklabels([f"< {edges[0]} %"] + [f"{a}-{b} %" for a, b in zip(edges[:-1], edges[1:])]
                          + [f"≥ {edges[-1]} %"])
    cb.outline.set_visible(False)
    cb.set_label("difference: the higher pass above the lower one", color=INK2)
    _align_cbar(cax, axs[-1])
    # the share of the stretches in each class, one bar per map
    ax = fig.add_subplot(outer[1])
    labels = {"same": "same combine", "diff": "different combines" + (", before" if moved else ""),
              "after": "different combines, after"}
    yy = np.arange(n)[::-1]
    for yi, (key, sel, colname, title, st) in zip(yy, panels):
        left = 0.0
        for c, share in enumerate(st.get("class_shares_pct") or []):
            ax.barh(yi, share, left=left, color=SEAM_COLORS[c], height=0.62, edgecolor="white", linewidth=0.8,
                    zorder=3)
            if share >= 7:
                ax.text(left + share / 2, yi, f"{share:.0f} %", ha="center", va="center", fontsize=8.5,
                        color="white" if c >= 2 else INK, zorder=4)
            left += share
        ax.text(101.5, yi, (f"median {st['median_pct']:.2f} %, {st['share_above_10_pct']:.0f} % of the stretches "
                            "above 10 %" if st["stretches"] else "none"), va="center", fontsize=9, color=INK2)
    ax.set_yticks(yy, [labels[p[0]] for p in panels])
    ax.set_xlim(0, 100)
    ax.set_xlabel("share of the stretches (%)")
    ax.grid(axis="y", visible=False)
    ax.set_title(f"How much neighbouring passes differ along {L} stretches", loc="left", fontsize=10)
    fig.text(0.07, 1.35 / fig_h, "\n".join(textwrap.wrap(S.get("text") or "", int(fig_w * 14.5))), ha="left",
             va="top", fontsize=9, color=INK2)
    fig.suptitle(_wrap(f"{tag}: the seams between neighbouring passes. Each line is {L} of the seam between two "
                       "passes, coloured by how much they differ there (the mean of the kept readings on each side, "
                       "the yield as mapped)", int(fig_w * 11)), x=0.07, y=0.995, ha="left", va="top", fontsize=11,
                 color=INK)
    path = os.path.join(out, "fig_clean_seams.png")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def final_applied_text(R: dict) -> list:
    """What the yield column to map carries, in three short lines (filters, combine factor, level)."""
    col = R.get("yield_column_for_difm")
    RM, L, A = R.get("machines") or {}, R.get("level") or {}, R.get("aoi") or {}
    lines = [f"Filters: {R['n_in'] - R['n_kept']:,} of {R['n_in']:,} readings removed ({R['removed_pct']:.2f} %)"
             + (f"; {int(A['n_output_outside_aoi']):,} kept points outside the field of interest left out"
                if A.get("n_output_outside_aoi") and A.get("clip_output", True) else "")]
    leveled = col is not None and col in (R.get("level_column_for_difm"), R.get("level_sym_column_for_difm"))
    harm_col = leveled or col == "Yld_Harm" or (col is not None and col != (R.get("columns") or {}).get("yield"))
    fa = {str(k): float(v) for k, v in (RM.get("factor_applied") or {}).items()}
    on = [f"{m} x {f:.4f}" for m, f in fa.items() if abs(f - 1) > 1e-12]
    if not RM:
        lines.append("Combine factor: one combine, nothing to harmonise")
    elif not on:
        lines.append("Combine factor: none applied (every combine x 1; see the combines section)")
    elif not harm_col:
        lines.append("Combine factor: not in this column")
    else:
        lines.append("Combine factor: " + ", ".join(on) + ("; the others x 1" if len(on) < len(fa) else ""))
    if leveled and L.get("per_machine"):
        lines.append("Level: each combine leveled to its own scale ticket (per-machine L)")
    elif leveled and L.get("factor_final") is not None:
        lines.append(f"Level: x L = {float(L['factor_final']):.4f} to the grower's weight")
    elif L and L.get("use") == "validate":
        lf = L.get("factor_final")
        lines.append("Level: the grower's weight validated the monitor only"
                     + (f" (L = {float(lf):.4f}, not applied)" if lf is not None else " (not applied)"))
    elif L:
        lines.append(f"Level: not in this column ({R.get('level_column_for_difm') or 'Yld_Level'} carries it)")
    else:
        lines.append("Level: no grower's weight given, so the map is not leveled to a scale")
    return lines


def figure_final(g, R, diag, tag, out, q, px, py, ex, ey):
    """fig_clean_final.png: the map to upload, the yield column to map over the points of the clean file as
    the file holds them (diag['final']), colours centred on the field mean, with the distribution of the values as
    the legend. None when the column has no finite value."""
    Fz = diag.get("final")
    if not Fz:
        return None
    pos = np.asarray(Fz["pos"], np.int64)
    v = np.asarray(Fz["values"], float)
    ok = np.isfinite(v)
    if not ok.any():
        return None
    import textwrap
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import TwoSlopeNorm
    _style(plt)
    vv = v[ok]
    mean = float(vv.mean())
    lo, hi = (float(t) for t in np.percentile(vv, [2, 98]))
    span = max(abs(mean), 1e-9) * 1e-3
    lo, hi = min(lo, mean - span), max(hi, mean + span)
    norm = TwoSlopeNorm(vcenter=mean, vmin=lo, vmax=hi)
    cmap = matplotlib.colormaps[YIELD_CMAP]
    fig = plt.figure(figsize=(13, 8.8))
    gs = fig.add_gridspec(1, 2, width_ratios=[1, 0.3], wspace=0.16)
    ax, hx = fig.add_subplot(gs[0]), fig.add_subplot(gs[1])
    _strips(ax, q[pos], px[pos], py[pos], v, cmap, norm=norm)
    _map_axes(ax, ex, ey)
    _outline([ax], g, diag)
    col = Fz["column"]
    ax.set_title(f"{col}: the {len(v):,} points of the clean file", loc="left")
    # the legend: the distribution of the values, each bar in the colour of its value
    edges = np.linspace(lo, hi, 41)
    cnt, _ = np.histogram(np.clip(vv, lo, hi), bins=edges)
    mids = (edges[:-1] + edges[1:]) / 2
    hx.barh(mids, 100 * cnt / cnt.sum(), height=np.diff(edges), color=cmap(norm(mids)), edgecolor="none", zorder=3)
    hx.axhline(mean, color=INK, lw=1.2, zorder=4)
    hx.text(hx.get_xlim()[1] * 0.98, mean, f"mean {mean:,.2f}", ha="right", va="bottom", fontsize=8.5, color=INK,
            zorder=5)
    hx.set_ylim(lo, hi)
    hx.set_ylabel(f"{col} (bu/ac)")
    hx.set_xlabel("share of the points (%)")
    hx.grid(axis="y", visible=False)
    sec = hx.secondary_yaxis("right", functions=(lambda t: 100 * t / mean, lambda p: p * mean / 100))
    sec.set_ylabel("% of the field mean", color=INK2)
    hx.set_title("Values (the legend)", loc="left", fontsize=10)
    sd = float(vv.std(ddof=1)) if len(vv) > 1 else 0.0
    stats_ = (f"mean {mean:,.2f} · median {float(np.median(vv)):,.2f} · SD {sd:,.2f} bu/ac · CV "
              f"{100 * sd / mean:.2f} %" if mean > 0 else f"mean {mean:,.2f} bu/ac")
    note = ("Yellow = the field mean, red below it, green above (scale: 2nd-98th percentile; the end bars hold the "
            f"values beyond it). Every strip is one point of the file with its own value: {Fz['source']}.")
    lines = final_applied_text(R)
    fig.text(0.07, 0.035, "\n".join([stats_] + lines + textwrap.wrap(note, 150)), ha="left", va="top", fontsize=9.5,
             color=INK2, linespacing=1.5)
    fig.suptitle(f"{tag}: final map, the yield to map ({col}); what it carries is written below the map",
                 x=0.07, y=0.97, ha="left", fontsize=12, color=INK)
    path = os.path.join(out, "fig_clean_final.png")
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def figures_maps(g, R, diag, tag, out, q, px, py, ex, ey, x0, y0, before, ylab) -> dict:
    """The final, combine and seam maps of a run, {name: path} of those written (figure_final, figure_combines,
    figure_seams). q / px / py: the kept strips and final positions, ex / ey: the extent, (x0, y0): the origin."""
    files = {}
    for name, fn in (("final", lambda: figure_final(g, R, diag, tag, out, q, px, py, ex, ey)),
                     ("combines", lambda: figure_combines(g, R, diag, tag, out, q, px, py, ex, ey, before, ylab)),
                     ("seams", lambda: figure_seams(g, R, diag, tag, out, q, px, py, ex, ey, x0, y0))):
        p = fn()
        if p:
            files[name] = p
    return files


def _json_default(v):
    if hasattr(v, "item"):
        return v.item()
    if isinstance(v, float) and not np.isfinite(v):
        return None
    return str(v)


def write_all(path, out, tag, S, g0, g, gx, gy, R, diag, log=print):
    os.makedirs(out, exist_ok=True)
    C = diag["C"]
    # the processed set in the file's order; rows_all = its record indices in the file (every record without an AOI
    # pre-step; the records inside the field of interest + buffer with one)
    o = np.argsort(g["_row"].values, kind="stable")   # o[k] -> position in the processed table of the k-th file row kept
    G = g.iloc[o].reset_index(drop=True)
    rows_all = G["_row"].to_numpy(np.int64)
    sms = bool(diag.get("sms"))
    moved = R["lag"]["applied_records"] != 0
    if sms:          # Optimize Swath Widths moved the narrowed readings to the centre of their kept strip
        shift_m = np.hypot(np.nan_to_num(gx - g._x.values), np.nan_to_num(gy - g._y.values))
        moved = bool((shift_m > 0).any())
    # the clean file is in WGS 84: an input in another CRS (e.g. UTM) is written in EPSG:4326, with a WGS 84 .prj
    reproject = not _is_wgs84(g0.crs)
    crs_out = None
    lx, ly = g0.geometry.x.to_numpy(float)[rows_all], g0.geometry.y.to_numpy(float)[rows_all]    # logged positions
    if reproject:
        from pyproj import CRS
        crs_out = CRS.from_epsg(4326)
        lx, ly = Transformer.from_crs(g0.crs, crs_out, always_xy=True).transform(lx, ly)
        lx, ly = np.asarray(lx, float), np.asarray(ly, float)
        R["notes"].append(f"input coordinate system {g0.crs.name}: the clean and flagged files are written in "
                          "WGS 84 (EPSG:4326), the coordinate system of the maps")
    xy = None
    if moved or reproject:
        xy = np.c_[lx, ly]
        if moved:
            tr = Transformer.from_crs(diag["utm"], crs_out or g0.crs, always_xy=True)
            xx, yy = (np.asarray(v, float) for v in tr.transform(gx[o], gy[o]))
            fin = np.isfinite(xx) & np.isfinite(yy)        # points without a plausible position keep the logged one
            xy[fin, 0], xy[fin, 1] = xx[fin], yy[fin]
    bc, why = byte_copy_source(path, g0)
    if bc is None:
        R["warnings"].append(f"the input cannot be copied byte for byte ({why}): the outputs were written with GDAL, "
                             "which may change the original field definitions")
    in_names = bc[1]["names"] if bc is not None else [c for c in g0.columns if c != "geometry"]
    geo = crs_out.is_geographic if crs_out is not None else (g0.crs is None or g0.crs.is_geographic)
    cdec = 12 if geo else 4
    plan = diag.get("plan")
    # v2 columns (moisture basis, sessions, plots): always in the flagged file; in the clean file only in a v2
    # run (R['v2_columns']), so that a plain run writes the clean file exactly as before
    v2_cols = []
    if diag.get("dry_sym") and S["moisture"]["add_symmetric_column"]:
        v2_cols.append(("Yld_Dry_Sy", "N", G["Yld_Dry_Sy"].values, 15, 4))
    v2_cols.append(("Harv_Day", "C", G["Harv_Day"].values, 10, 0))
    v2_cols.append(("Session", "C", G["Session"].values, sessions_mod.SESSION_WIDTH, 0))
    if plan is not None and plan.is_plan:
        v2_cols.append(("Plot_Id", "C", G["Plot_Id"].values, 16, 0))
        if plan.rate_col is not None:
            v2_cols.append(("Tgt_Rate", "N", G["Tgt_Rate"].values, 12, 3))
    lvl_sy_cols = [("Yld_Lvl_Sy", "N", G["Yld_Lvl_Sy"].values, 15, 4)] if diag.get("lvl_sy") else []
    sms_cols = ([("Wdth_Opt", "N", G["Wdth_Opt"].values, 10, 4), ("Dist_Opt", "N", G["Dist_Opt"].values, 10, 4),
                 ("Swath_Scl", "N", G["Swath_Scl"].values, 12, 6)] if sms else [])
    extra_all = [("Pass_Id", "N", G.Pass_Id.values, 10, 0), ("Run_Id", "N", G.Run_Id.values, 10, 0),
                 ("Keep", "N", G.Keep.values, 1, 0), ("Rem_By", "C", G.Rem_By.values, 4, 0),
                 ("Overlap_f", "N", G.Overlap_f.values, 10, 6)] + sms_cols + [
                 ("Lag_rec", "N", G.Lag_rec.values, 4, 0),
                 ("Yld_Harm", "N", G.Yld_Harm.values, 15, 4), ("Yld_Level", "N", G.Yld_Level.values, 15, 4)] \
        + lvl_sy_cols + [("X_log", "N", lx, 20, cdec), ("Y_log", "N", ly, 20, cdec)] + v2_cols
    if plan is not None:
        extra_all.append(("In_AOI", "N", G["In_AOI"].values, 1, 0))
    extra_all, _ = _rename_clashes(extra_all, in_names, R["warnings"], "flagged")
    F = {"dir": out, "input": path}
    exts = (".shp", ".shx", ".dbf", ".prj", ".cpg")
    F["flagged_shp"] = os.path.join(out, tag + "_flagged.shp")
    F["writer"] = write_points(F["flagged_shp"], path, g0, rows_all, extra_all, xy, bc, crs_out)
    F["flagged_zip"] = _zip([os.path.join(out, tag + "_flagged" + e) for e in exts],
                            os.path.join(out, tag + "_flagged.zip"))
    keep_mask = G.Keep.values == 1
    n_out_outside = 0
    if plan is not None and S["aoi"]["clip_output"]:
        in_aoi = G["In_AOI"].values == 1
        n_out_outside = int((keep_mask & ~in_aoi).sum())
        keep_mask = keep_mask & in_aoi
        if n_out_outside:
            R["notes"].append(f"AOI: {n_out_outside:,} kept points outside the field of interest were left out of the "
                              "clean file (they stay in the flagged file with In_AOI = 0)")
    if "aoi" in R:
        R["aoi"]["n_output_outside_aoi"] = n_out_outside
        R["aoi"]["clip_output"] = bool(S["aoi"]["clip_output"])
    keep_pos = np.where(keep_mask)[0]                 # positions in G
    keep_rows = rows_all[keep_pos]                    # record indices in the file
    R["n_difm_ready"] = int(len(keep_rows))
    extra_ready = []
    # Yld_Harm goes into the clean file (and is named the column to map) only when it differs from the yield: a
    # combine factor other than 1 was applied. Switched on but not applied (CI includes 1, above the max ...), it
    # would be a copy of the yield under the name of a corrected one. The flagged file always carries it.
    harm_written = bool(diag["harmonise"]) and any(
        f is not None and abs(float(f) - 1) > 1e-12
        for f in ((R.get("machines") or {}).get("factor_applied") or {}).values())
    # 'validate only' (scale_ticket.use): the leveled columns stay out of the clean file (the flagged file keeps
    # Yld_Level as what adjusting would give)
    tk_w = diag.get("ticket")
    validate_only = bool(tk_w is not None and tk_w.get("use") == "validate")
    if harm_written:
        extra_ready.append(("Yld_Harm", "N", G.Yld_Harm.values[keep_pos], 15, 4))
    if "level" in R and not validate_only:
        extra_ready.append(("Yld_Level", "N", G.Yld_Level.values[keep_pos], 15, 4))
        extra_ready += [(n, t, np.asarray(v)[keep_pos], w, d) for n, t, v, w, d in lvl_sy_cols]
    if diag.get("v2_columns"):
        extra_ready += [(n, t, np.asarray(v)[keep_pos], w, d) for n, t, v, w, d in v2_cols]
    patch = None
    if sms:
        # the SMS 3-sigma clean file is what SMS exports after Optimize Swath Widths: the width column holds the
        # new width of the narrowed points; the per-area columns of the narrowed or shortened points are multiplied by
        # old / new area (W x D) / (W' x D') (Prod_ac_h and area per fuel divided by it); the shortened points carry
        # the new Distance, Speed (Distance / Duration) and distance per fuel; Y_Offset is written as 0 and every
        # point sits where SMS moved it (by its offset, then to the centre of its kept strip); every other cell is
        # byte for byte the export's. The original width and the factor are added (Wdth_Raw, Swath_Scl).
        scl = G["Swath_Scl"].to_numpy(float)[keep_pos]
        wnew = G["Wdth_Opt"].to_numpy(float)[keep_pos]
        wraw = (G[C["width_ft"]].astype(float).to_numpy() if C.get("width_ft")
                else np.full(len(G), float(S["harvester_width_ft"])))[keep_pos]
        narrowed = np.isfinite(wnew) & (wnew < wraw - 1e-6)            # the width column changes here
        changed = np.isfinite(scl) & (np.abs(scl - 1.0) > 1e-12)        # the per-area values change here
        names_in = [c for c in g0.columns if c != "geometry"]
        spec = sms_mod.patch_values(names_in, scl, wnew, C.get("width_ft"))
        for key in ("yield", "yield_wet", "wet_mass_lbac"):
            if C.get(key) and C[key] in names_in and C[key] not in spec:
                spec[C[key]] = ("mul", scl)
        patch = {}
        for col, (kind, vals) in spec.items():
            orig = pd.to_numeric(g0[col].iloc[keep_rows], errors="coerce").to_numpy(float)
            if kind == "set":
                newv = np.asarray(vals, float)
            elif kind == "mul":
                newv = orig * np.asarray(vals, float)
            else:
                newv = orig / np.asarray(vals, float)
            patch[col] = (newv, (narrowed if kind == "set" else changed) & np.isfinite(newv))
        dnew = G["Dist_Opt"].to_numpy(float)[keep_pos]
        short = np.zeros(len(keep_rows), bool)
        per_dist = []
        if C.get("distance_ft") and C["distance_ft"] in names_in:
            draw = G[C["distance_ft"]].astype(float).to_numpy()[keep_pos]
            short = np.isfinite(dnew) & (dnew < draw - 1e-6)
            if short.any():                            # SMS shortened these readings: Distance and Speed follow
                patch[C["distance_ft"]] = (dnew, short)
                if C.get("speed_mph") and C["speed_mph"] in names_in:
                    patch[C["speed_mph"]] = (G["_spd_s"].to_numpy(float)[keep_pos], short)
                up = {c.upper() for c in sms_mod.PER_DISTANCE_COLUMNS}
                for fc in names_in:                    # distance per fuel: x new / old distance, as SMS does
                    if str(fc).upper() in up:
                        fo = pd.to_numeric(g0[fc].iloc[keep_rows], errors="coerce").to_numpy(float)
                        patch[fc] = (fo * dnew / draw, short & np.isfinite(fo) & (draw > 0))
                        per_dist.append(fc)
        yoc = sms_mod.y_offset_column(names_in)
        n_yo = 0
        if yoc and S["sms3sigma"]["swath"]:           # SMS moved every reading by its offset and wrote 0
            yo = pd.to_numeric(g0[yoc].iloc[keep_rows], errors="coerce").to_numpy(float)
            patch[yoc] = (np.zeros(len(keep_rows)), np.isfinite(yo) & (yo != 0))
            n_yo = int(patch[yoc][1].sum())
        if C.get("width_ft"):
            extra_ready.append(("Wdth_Raw", "N", G[C["width_ft"]].astype(float).values[keep_pos], 10, 4))
        else:
            extra_ready.append(("Wdth_Opt", "N", wnew, 10, 4))
            R["warnings"].append("SMS 3-sigma: no width column in the export (constant harvester width): the optimised "
                                 "width is the added column Wdth_Opt; map it as the swath width")
        extra_ready.append(("Swath_Scl", "N", scl, 12, 6))
        mul_cols = sorted(c for c, (k, _) in spec.items() if k == "mul")
        div_cols = sorted(c for c, (k, _) in spec.items() if k == "div")
        n_short = int(short.sum())
        R["sms3sigma"]["difm_ready_changes"] = {
            "n_narrowed_points": int(narrowed.sum()), "n_scaled_points": int(changed.sum()),
            "n_shortened_points": n_short, "n_y_offset_zeroed": n_yo,
            "width_column": C.get("width_ft"), "scaled_columns": mul_cols, "inverse_scaled_columns": div_cols,
            "distance_columns": ([C["distance_ft"]] + ([C["speed_mph"]] if C.get("speed_mph") in names_in else [])
                                 + per_dist) if n_short else [],
            "y_offset_column": yoc if n_yo else None,
            "added": ["Wdth_Raw" if C.get("width_ft") else "Wdth_Opt", "Swath_Scl"]}
        scaled_txt = (f"; on the {int(changed.sum()):,} narrowed or shortened points the per-area values ("
                      + ", ".join(mul_cols) + ") are multiplied by Swath_Scl = old / new area (W x D) / (W' x D')"
                      + (f" and {', '.join(div_cols)} divided by it" if div_cols else "")
                      if changed.any() else
                      ("; the per-area values are left as logged (Scale Per Area off)"
                       if not S["sms3sigma"].get("scale_per_area", True) else
                       "; Optimize Swath Widths was switched off: the cells are the export's"
                       if not S["sms3sigma"].get("swath", True) else
                       "; no reading was narrowed or shortened, so no per-area value changed"))
        R["notes"].append(
            f"SMS 3-sigma: the clean file is written as an SMS export after Optimize Swath Widths: "
            f"{int(narrowed.sum()):,} narrowed points carry the new width in {C.get('width_ft') or 'Wdth_Opt'} and sit "
            "at the centre of their kept strip" + scaled_txt
            + (f"; {n_short:,} shortened points carry the new " + ", ".join(
                R["sms3sigma"]["difm_ready_changes"]["distance_columns"]) if n_short else "")
            + (f"; {yoc} is written as 0 on {n_yo:,} points, which sit where SMS moved them (the offset to the right "
               "of travel)" if n_yo else "")
            + ("; the original width is in Wdth_Raw and the factor in Swath_Scl" if C.get("width_ft") else
               f"; the export has no width column (constant harvester width {float(S['harvester_width_ft']):g} ft): "
               "the optimised width is the added column Wdth_Opt and the factor Swath_Scl")
            + "; every other cell is the export's, byte for byte")
    extra_ready, ren = _rename_clashes(extra_ready, in_names, R["warnings"], "clean")
    R["difm_ready_added_columns"] = [e[0] for e in extra_ready]
    F["difm_ready_shp"] = os.path.join(out, tag + "_clean.shp")
    wr = write_points(F["difm_ready_shp"], path, g0, keep_rows, extra_ready, xy[keep_pos] if xy is not None else None,
                      bc, crs_out, patch=patch)
    if "did not fit" in str(wr):
        R["warnings"].append("SMS 3-sigma clean file: " + str(wr).split("(", 1)[-1].rstrip(")"))
    try:                    # round trip: the original fields of the clean file keep their names, order and types
        import pyogrio
        a, b = pyogrio.read_info(path), pyogrio.read_info(F["difm_ready_shp"])
        fa, fb = list(zip(a["fields"], a["dtypes"])), list(zip(b["fields"], b["dtypes"]))
        changed = [f"{k}: {t} -> " + (f"{fb[i][0]} {fb[i][1]}" if i < len(fb) else "missing")
                   for i, (k, t) in enumerate(fa) if i >= len(fb) or fb[i] != (k, t)]
        raw = [n.upper() for n in _read_dbf(os.path.splitext(F["difm_ready_shp"])[0] + ".dbf")["names"]]
        dups = sorted({n for n in raw if raw.count(n) > 1})
        if dups:
            changed.append(f"duplicate field names {dups}")
        F["field_check"] = "ok: every original field keeps its name and type" if not changed else changed
        if changed:
            R["warnings"].append("the clean file changed original field definitions: " + "; ".join(changed))
    except Exception as e:                            # recorded, never silent
        F["field_check"] = f"not checked ({e})"
        R["warnings"].append(f"the clean-file field check could not run ({e})")
    F["difm_ready_zip"] = _zip([os.path.join(out, tag + "_clean" + e) for e in exts],
                               os.path.join(out, tag + "_clean.zip"))
    R["yield_column_for_difm"] = ren.get("Yld_Harm", "Yld_Harm") if harm_written else C["yield"]
    R["harm_column_written"] = harm_written
    if "level" in R and not validate_only:
        R["level_column_for_difm"] = ren.get("Yld_Level", "Yld_Level")
    rec = (R.get("sessions") or {}).get("recommendation") or {}
    if rec.get("map_column") and "Yld_Dry_Sy" in R["difm_ready_added_columns"] and not sms:
        R["dry_symmetric_column_for_difm"] = R["dry_symmetric_column"] = ren.get("Yld_Dry_Sy", "Yld_Dry_Sy")
    tk = diag.get("ticket")
    if "level" in R and validate_only:
        # the grower's weight validated the monitor and changed nothing: the column to map is the monitor's own
        # (with the combine factor when one was applied on the passes side by side)
        Lv = R["level"]
        Lf = Lv.get("factor_final")
        R["yield_column_note"] = (
            f"{R['yield_column_for_difm']} = the mapped dry yield"
            + (" x the combine factor" if harm_written else "")
            + ": the grower's weight was used to validate only"
            + (f" (L = {Lf:.3f}, not applied)" if Lf is not None else " (per-machine L, not applied)")
            + "; no leveled column is in the clean file")
        Lv["difm_note"] = R["yield_column_note"]
    if "level" in R and tk is not None and not tk.get("legacy") and not validate_only:
        # the grower's weight is the real level: the leveled column is the one to map; the symmetric one when the
        # sessions recommend the symmetric basis (and it could be written), else Yld_Level
        sym = (not sms and rec.get("map_column") == "Yld_Dry_Sy"
               and ren.get("Yld_Lvl_Sy", "Yld_Lvl_Sy") in R["difm_ready_added_columns"])
        col = ren.get("Yld_Lvl_Sy", "Yld_Lvl_Sy") if sym else ren.get("Yld_Level", "Yld_Level")
        L = R["level"]
        Lf = L.get("factor_final")
        v = L.get("ticket_value", tk["value"])
        unit = L.get("ticket_unit", tk["unit"])
        vtxt = (ticket_value_text(v, unit).rsplit(" ", 1)[0] if unit in ("kg", "t") else
                (f"{v:,.0f}" if v >= 100 else f"{v:,.3g}"))
        R["yield_column_for_difm"] = col
        if sym:
            R["level_sym_column_for_difm"] = col
        # what the leveled column sums to: the weight itself when it was compared with the column that is leveled (a
        # weight at the standard moisture against the file's dry yield); for a wet weight the wet yield x L sums to
        # the weight and this dry column to the weight less the shrink of the readings above the standard moisture
        # measured, never assumed: the integral of the column named against the weight compared
        gap = L.get("yld_lvl_sy_vs_ticket_pct") if sym else L.get("yld_level_vs_ticket_pct")
        what_col = ("the symmetric dry yield x L" if sym else "the file's dry yield x L")
        sums = (": the bu/ac now sum to the scale" if (gap is not None and abs(float(gap)) <= 0.05) else
                f": {L.get('integral_column')} x L sums to the weight compared, this column ({what_col}) "
                + (f"to {float(gap):+.2f} % of it" if gap is not None else "to a total not measured")
                + (" (the symmetric dry yield credits the readings below the standard moisture)" if sym else
                   " (the file's dry yield shrinks the readings above the standard moisture and does not credit "
                   "those below it)"))
        R["yield_column_note"] = (
            f"{col} = the {'symmetric dry yield (Yld_Dry_Sy)' if sym else 'mapped dry yield'}"
            + (" x the machine harmonisation" if harm_written else "")
            + f" leveled to the grower's weight {vtxt} {unit}"
            + (f" (L = {Lf:.3f})" if Lf is not None else " (per-machine L)")
            + sums
            + ("; the sessions recommend the symmetric moisture basis" if sym else
               ("; the SMS method maps the export's dry yield leveled; the symmetric leveled yield Yld_Lvl_Sy is also "
                "in the file (the sessions diagnostic would recommend it)"
                if sms and rec.get("map_column") == "Yld_Dry_Sy" and ren.get("Yld_Lvl_Sy", "Yld_Lvl_Sy")
                in R["difm_ready_added_columns"] else
               (("; the sessions recommend the symmetric basis but Yld_Dry_Sy / Yld_Lvl_Sy were not written "
                 "(moisture.add_symmetric_column is off)" if not S["moisture"]["add_symmetric_column"] else
                 "; the sessions recommend the symmetric basis but Yld_Lvl_Sy could not be written")
                if rec.get("map_column") == "Yld_Dry_Sy" else ""))))
        R["level"]["difm_note"] = R["yield_column_note"]
    R["n_in_file"] = R["n_read"]
    R["output_crs"] = "EPSG:4326" if reproject else (g0.crs.to_string() if g0.crs is not None else None)
    R["advice"] = advice(R)
    # ---- the final map (fig_clean_final): the yield column to map over the clean points, read back from the
    # clean file itself (the values as written when it cannot be read)
    fcol = R["yield_column_for_difm"]
    pos_g = o[keep_pos]                                  # the clean points as positions in the processed table
    fvals = fsrc = None
    farea = None                                         # the area of every clean point (ac), as the file holds it
    try:
        import pyogrio
        wc, dc = C.get("width_ft"), C.get("distance_ft")
        want = [fcol] + [c for c in (wc, dc) if c and c != fcol]
        fdf = pyogrio.read_dataframe(F["difm_ready_shp"], columns=want, read_geometry=False)
        fv = pd.to_numeric(fdf[fcol], errors="coerce").to_numpy(float)
        if len(fv) == len(pos_g):
            fvals, fsrc = fv, "the values read back from the clean file"
            if wc and dc and wc in fdf and dc in fdf:
                farea = (pd.to_numeric(fdf[wc], errors="coerce").to_numpy(float)
                         * pd.to_numeric(fdf[dc], errors="coerce").to_numpy(float) / FT2_PER_AC)
    except Exception:                                    # recorded in R['final_map']['values_from']
        pass
    if farea is None:
        # the processed table: the optimised width x distance in an SMS run, the logged ones otherwise
        if sms:
            farea = (G["Wdth_Opt"].to_numpy(float) * G["Dist_Opt"].to_numpy(float))[keep_pos] / FT2_PER_AC
        else:
            w_ = (G[C["width_ft"]].astype(float).to_numpy() if C.get("width_ft")
                  else np.full(len(G), float(S["harvester_width_ft"] or np.nan)))
            d_ = (G[C["distance_ft"]].astype(float).to_numpy() if C.get("distance_ft") else
                  G[C["speed_mph"]].astype(float).to_numpy() * 1.46667 * G[C["duration_s"]].astype(float).to_numpy())
            farea = (w_ * d_)[keep_pos] / FT2_PER_AC
    if fvals is None:
        icol = {v: k for k, v in ren.items()}.get(fcol, fcol)
        fvals = G[icol if icol in ("Yld_Harm", "Yld_Level", "Yld_Lvl_Sy") else "_ymap"].to_numpy(float)[keep_pos]
        fsrc = "the values written to the clean file"
    diag["final"] = {"pos": pos_g, "values": fvals, "column": fcol, "source": fsrc}
    fok = np.isfinite(fvals)
    if fok.any():
        fv_ = fvals[fok]
        fsd = float(fv_.std(ddof=1)) if len(fv_) > 1 else 0.0
        fa_ = np.asarray(farea, float) if farea is not None and len(farea) == len(fvals) else None
        okA = fok & np.isfinite(fa_) & (fa_ > 0) if fa_ is not None else None
        area_k = float(fa_[okA].sum()) if okA is not None and okA.any() else None
        R["final_map"] = {"column": fcol, "n_points": int(len(fvals)), "n_finite": int(fok.sum()),
                          "mean": float(fv_.mean()), "median": float(np.median(fv_)), "sd": fsd,
                          "cv_pct": (100 * fsd / float(fv_.mean()) if fv_.mean() > 0 else None),
                          "p2": float(np.percentile(fv_, 2)), "p98": float(np.percentile(fv_, 98)),
                          "min": float(fv_.min()), "max": float(fv_.max()), "values_from": fsrc,
                          "applied": final_applied_text(R),
                          # what the SMS Summary of the clean file shows: the area of the kept readings (distance x
                          # width as the file holds them) and the average = total / area of the column to map
                          "area_ac": area_k,
                          "average_area_weighted": (float((fvals[okA] * fa_[okA]).sum()) / area_k
                                                    if area_k else None),
                          "average_rule": "sum of (yield x distance x width) / sum of (distance x width) over the "
                                          "points of the clean file (the SMS Summary's Avg. Yield)"}
    log("  figures ...")
    F["figures"] = figures_sms(g, R, diag, tag, out) if sms else figures(g, R, diag, C["yield"], tag, out)
    F["json"] = os.path.join(out, tag + "_cleaning.json")
    F["report_pdf"] = os.path.join(out, tag + "_cleaning_report.pdf")
    R["files"] = F
    log("  report ...")
    from oced_clean._engine.report.clean_report import make_report
    make_report(R, F["report_pdf"])
    with open(F["json"], "w", encoding="utf-8") as f:
        json.dump(R, f, indent=1, default=_json_default)
    return R


def f1_origin(user_settings, S) -> dict:
    """Where the F1 window came from: 'user' (min_pct / max_pct in the settings given), 'crop' (F1_CROP_RANGES of
    settings crop), 'unknown_crop' (crop not in the table: default window) or 'default' (no crop)."""
    u = user_settings if isinstance(user_settings, dict) else {}
    f1 = (u.get("filters") or {}).get("F1") if isinstance(u.get("filters"), dict) else None
    lo, hi = S["filters"]["F1"]["min_pct"], S["filters"]["F1"]["max_pct"]
    if isinstance(f1, dict) and ("min_pct" in f1 and "max_pct" in f1):
        return {"origin": "user", "min_pct": lo, "max_pct": hi}
    if isinstance(f1, dict) and ("min_pct" in f1 or "max_pct" in f1):
        base = "crop" if S.get("crop") in F1_CROP_RANGES else "default"
        return {"origin": f"user+{base}", "crop": S.get("crop"), "min_pct": lo, "max_pct": hi,
                "user_bound": "min_pct" if "min_pct" in f1 else "max_pct"}
    if S.get("crop") in F1_CROP_RANGES:
        return {"origin": "crop", "crop": S["crop"], "min_pct": lo, "max_pct": hi}
    if S.get("crop"):
        return {"origin": "unknown_crop", "crop": S["crop"], "min_pct": lo, "max_pct": hi}
    return {"origin": "default", "min_pct": lo, "max_pct": hi}


def inputs_left_out_of(inputs_left_out=None, source=None) -> list:
    """The inputs left out when the data was opened (a damaged zip among the zips given: engine.run.open_input), one
    sentence each with the reason: those given, then those the decode source carries, each once."""
    more = source.get("inputs_left_out") if isinstance(source, dict) else None
    out = []
    for w in list(inputs_left_out or []) + list(more or []):
        if str(w) not in out:
            out.append(str(w))
    return out


def run(input_shp, out_dir, settings=None, log=print, source=None, inputs_left_out=None):
    """Clean a yield point shapefile with `settings` (merged over DEFAULT_SETTINGS) and write every output in
    out_dir. Returns the result dict (also written as <tag>_cleaning.json).

    source (optional): where the shapefile came from when it is not the user's export, e.g. engine/run.py passes the
    decoder's report for a decoded raw dataset: {"kind": "decoded", "label", "units", "calibration", "warnings", ...}.
    It is stored as R['source'], shown in the report, and its warnings are copied into R['warnings'] ('decoder: ...')
    so that the cleaning JSON and PDF that go with the clean file carry them.

    inputs_left_out (optional): the inputs left out when the data was opened (engine.run.open_input: a damaged zip
    among the zips given), one sentence each. With those of `source` they are R['inputs_left_out'] (always there, []
    when nothing was left out) and one warning each ('input: ...'), so the JSON and the PDF name them.

    settings['method']: "occd" (the cascade, the default), "sms3sigma" (the SMS 3-sigma sequence; outputs named
    <tag>_sms3s_*) or "both": the cascade in out_dir, the SMS 3-sigma run in out_dir/sms3sigma and the comparison in
    out_dir/compare (engine/clean/compare.py); the result is then the cascade's, with R['methods'] describing the
    other two."""
    S = merge_settings(settings)
    if S["method"] == "both":
        # OCED keeps no comparison module (engine/clean/compare.py of OCCD): it cleans with one method at a time
        raise ValueError("method 'both' (a cascade and an SMS 3-sigma run with their comparison) is not part of OCED: "
                         "use method 'sms3sigma'")
    return _run_single(input_shp, out_dir, settings, log=log, source=source, inputs_left_out=inputs_left_out)[0]


def _run_single(input_shp, out_dir, settings=None, log=print, source=None, inputs_left_out=None):
    """One method (occd or sms3sigma). Returns (R, g, diag, g0): the processed table g (UTM, processing order, with
    Keep / Rem_By / _row ...) is what the comparison of the two methods joins on."""
    S = merge_settings(settings)
    path = os.path.abspath(str(input_shp))
    if not path.lower().endswith(".shp"):
        raise ValueError(f"the yield export must be a point shapefile (.shp + .shx + .dbf), not "
                         f"{os.path.basename(path)}: the clean file is a byte copy of the kept records")
    for ext in (".shp", ".shx", ".dbf"):
        if not os.path.exists(os.path.splitext(path)[0] + ext):
            raise ValueError(f"missing {os.path.splitext(os.path.basename(path))[0] + ext}: a shapefile needs "
                             ".shp, .shx and .dbf")
    if S["boundary"]:
        S["boundary"] = os.path.abspath(S["boundary"])
    if S["aoi"]["file"]:
        S["aoi"]["file"] = os.path.abspath(S["aoi"]["file"])
    tag = S["tag"] or sanitise_tag(os.path.splitext(os.path.basename(path))[0]) or "yield"
    if S["method"] == "sms3sigma" and not tag.endswith(SMS_TAG_SUFFIX):
        tag += SMS_TAG_SUFFIX
    out = os.path.abspath(str(out_dir))
    import pyogrio
    info = pyogrio.read_info(path)
    available = list(info["fields"])
    marks = {m.upper() for m in CLEANED_MARKERS}
    clash = [c for c in available if str(c).upper() in marks]
    if clash:
        raise ValueError(f"{os.path.basename(path)} already holds the column(s) {', '.join(clash)} that this app's "
                         "cleaning adds: it is an output of the app (a clean or flagged file). Clean the original "
                         "export or the decoded file instead (cleaning twice removes points twice and duplicates the "
                         "added columns, which many GIS programs reject)")
    C, det = resolve_columns(available, S)
    log(f"cleaning {os.path.basename(path)} ({info['features']:,} points) -> {out}")
    g0, g, gx, gy, R, diag = clean(path, S, C, log=log, source=source)
    left = inputs_left_out_of(inputs_left_out, source)
    if source:
        R["source"] = dict(source)
        R["warnings"].extend(f"decoder: {w}" for w in (source.get("warnings") or []) if str(w) not in left)
    R["inputs_left_out"] = left          # said once, as an input of the work (not as a warning of the decoder)
    R["warnings"].extend(f"input: {w}" for w in left)
    R["f1_origin"] = f1_origin(settings, S)
    if S["method"] == "sms3sigma":                     # no F1 in the SMS method: only the crop-table check
        if R["f1_origin"]["origin"] == "unknown_crop":
            R["warnings"].append(f"crop {S['crop']!r} is not in the crop table ({sorted(CROPS)}): no standard moisture "
                                 "comes from the crop")
    elif R["f1_origin"]["origin"] == "crop":
        R["notes"].append(f"F1 moisture window {S['filters']['F1']['min_pct']:g}-{S['filters']['F1']['max_pct']:g} % "
                          f"from the crop ({S['crop']}; house convention)")
    elif R["f1_origin"]["origin"] == "default":
        R["notes"].append(f"F1 moisture window {S['filters']['F1']['min_pct']:g}-{S['filters']['F1']['max_pct']:g} %: "
                          "the small-cereal window (no crop given; set 'crop' for canola, flax, peas, lentils, corn or "
                          "soybeans)")
    elif R["f1_origin"]["origin"].startswith("user+"):
        fo = R["f1_origin"]
        R["notes"].append(f"F1 moisture window {S['filters']['F1']['min_pct']:g}-{S['filters']['F1']['max_pct']:g} %: "
                          f"{fo['user_bound']} from the settings, the other bound from "
                          + (f"the crop ({S['crop']})" if fo["origin"] == "user+crop" else "the small-cereal default"))
    elif R["f1_origin"]["origin"] == "unknown_crop":
        R["warnings"].append(f"crop {S['crop']!r} is not in the crop table ({sorted(CROPS)}): F1 uses the small-cereal "
                             f"window {S['filters']['F1']['min_pct']:g}-{S['filters']['F1']['max_pct']:g} % and no "
                             "standard moisture comes from the crop")
    R["input"] = path
    R["tag"] = tag
    R["settings"] = S
    R["columns"] = diag["C"]
    R["columns_detected"] = det
    R["columns_source"] = {k: ("settings" if k in (S.get("cols") or {}) else "detected") for k in COLUMN_KEYS}
    R = write_all(path, out, tag, S, g0, g, gx, gy, R, diag, log=log)
    lag = R["lag"]
    if R.get("method") == "sms3sigma":
        sw = (R.get("sms3sigma") or {}).get("swath") or {}
        log(f"in {R['n_in']:,} -> kept {R['n_kept']:,} ({R['removed_pct']:.2f} % removed); SMS 3-sigma, "
            f"{R['sms3sigma']['grouping']['n_groups']} group(s) ({R['sms3sigma']['grouping']['mode']}); swath "
            f"{sw.get('n_removed', 0):,} removed, {sw.get('n_narrowed', 0):,} narrowed")
    else:
        log(f"in {R['n_in']:,} -> kept {R['n_kept']:,} ({R['removed_pct']:.2f} % removed); F8 {R['f8']['mode']} "
            f"(dose-response choice {R['f8']['threshold_auto'] * 100:.3g} %); lag scan best {lag['best_records']} rec "
            f"-> applied {lag['applied_records']}")
    if "level" in R:
        Lv = R["level"]
        log(f"level: scale {Lv['scale_ticket_bu']:,.2f} bu / export integral "
            f"{Lv['export_integral_bu']:,.2f} bu = {Lv['factor']:.4f}"
            + (f" (after harmonisation {Lv['factor_harmonised']:.4f})" if "factor_harmonised" in Lv else "")
            + (f"; ticket {Lv['ticket_caption']}" if Lv.get("ticket_caption") else ""))
        if Lv.get("closure"):
            log("level: " + Lv["closure"]["verdict"])
        if R.get("yield_column_note"):
            log("yield column: " + R["yield_column_note"])
    for w in R["warnings"]:
        log("WARNING: " + w)
    log("-> " + R["files"]["difm_ready_zip"])
    return R, g, diag, g0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m engine.clean.yield_clean",
                                 description="Clean a yield point shapefile.")
    ap.add_argument("input", help="point shapefile (.shp)")
    ap.add_argument("--out", required=True, help="output folder")
    ap.add_argument("--settings", help="JSON file with settings (any subset of DEFAULT_SETTINGS)")
    a = ap.parse_args(argv)
    settings = None
    if a.settings:
        with open(a.settings, encoding="utf-8") as f:
            settings = json.load(f)
    try:
        R = run(a.input, a.out, settings)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(pd.DataFrame(R["cascade"])[["filter", "on", "removed", "remaining"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
