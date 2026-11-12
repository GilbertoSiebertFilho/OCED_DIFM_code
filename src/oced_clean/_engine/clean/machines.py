# -*- coding: utf-8 -*-
"""Several combines in one field: the harvest layout, four factor methods, paired-cell harmonisation with a block
bootstrap, and per-machine scale tickets.

    from oced_clean._engine.clean import machines
    tests, applied, decision, ref = machines.combine_factors(g, alive, y, moi, settings["machines"], width_m=w,
                                                             warnings=warnings, info=info)
    cells = machines.paired_cells(frame, "_machine", "A", "B", size_m=30, plot_col="Plot_Id")
    est = machines.ratio_estimate(cells, reps=1000, seed=2026, ci=0.95)

Problem. Two combines on one field read the same grain differently: a yield monitor's level error after a field
calibration is typically 1-3 % (Luck & Fulton 2014; Gauci et al. 2023), more outside the calibrated flow range
(15.8-17.2 % at 3.2 km/h, Gauci et al. 2023). A trial
analysed on two machines' readings therefore mixes a machine effect with the treatment effect, and the response
curve sees a step wherever the machines meet. Maldaner et al. (2016, ISPA proceedings, not refereed) take the machine
with the largest harvested area as the reference and test three factors on fields of two combines: machine by machine
(mean of all points / mean of all points), track by track (the means of the ONE pair of closest paths) and point by
point (the arithmetic mean of the point ratios on that one path pair); with more combines, the nearest combine is
corrected first, then the one nearest the corrected set. They judge the factors by the drop of the SD / CV and by
variograms, with no weighed reference, and conclude that one factor fails when the difference between the machines
changes over the field. On pseudo-combines with a known factor (one combine split in two) a single path pair can put
the factor far off, and the whole-field means are off when each machine cut its own block (the ground differs).
Chiocco & Wuestefeld (2019) search a +/-10 % gain
on nearest positions across adjacent paths and note that harmonisation fixes consistency, not level. Neither gives a
significance or materiality gate: the gates below are a house method. This module is the v2 rule of Section 4.14 of
the literature review, with the network of v2.5.

Method (the numbers are house conventions unless cited)
  1. Reference = the machine with the most kept points (machines.reference "auto") or the id given.
  2. Pairs. For EVERY pair of machines, the kept points with <= pair_max_overlap (2 %, PAIR_MAX_OVERLAP) of their
     footprint already cut are binned into 30 m and 50 m square cells; a cell is a pair when BOTH machines have >= 5
     such points in it (so the two machines cut neighbouring passes of the same, uncut ground). The overlap rule is
     needed because the pass that follows another carries the overlap below the F8 threshold and its kept records read
     low: pairing every kept record of two combines that work side by side measures that overlap, not the machines
     (alternate passes of one combine labelled A / B, with no yield change, would get a factor). The 2 % edge is the
     0-2 % band, which reads like the no-overlap band (house rule). When a plan with plots
     exists and machines.same_plot_only, the cells are also split by Plot_Id, so a pair always compares the same
     treatment (the rate effect cannot enter the factor). Every cell carries its pass pair (the modal Pass_Id of each
     machine in the cell). The ratio of a pair is exp(mean(log(y_a / y_b))) over its cells (the geometric-mean ratio:
     a multiplicative level error is symmetric in log space), with a CI by a block bootstrap over its PASS PAIRS
     (bootstrap_reps resamples of the pass pairs with replacement, the cells of a pass pair move together; seed fixed):
     neighbouring cells of one pass pair are not independent, so a cell-level test would overstate the precision. A
     pair is LINKED when it has >= min_pairs (30) cells and >= min_pass_pairs (5) pass pairs at BOTH sizes.
  3. The network (machines.network, default true). u_m = log(y_ref / y_m) is the log factor of machine m (u_ref = 0).
     Every linked pair (a, b) observes u_b - u_a = mean log(y_a / y_b), with weight 1 / its bootstrap variance; u is the
     weighted least-squares solution (network_solve), at 30 m and at 50 m, and its CI comes from re-solving the network
     on the bootstrap draws of every pair. A machine that never met the reference gets its factor through its
     neighbours (the "nearest combine" of Maldaner et al. 2016, with every link used at once and no order to choose);
     a machine with no chain of linked pairs to the reference gets no factor. When every linked pair involves the
     reference (two machines always) the solution is the direct estimate, byte for byte the v2.4 one. The paired
     t-test p and the mean moisture difference against the reference are kept as
     diagnostics ("p_ttest", "moisture_diff", m - ref): a level difference that comes with a moisture difference may be
     a moisture-sensor difference.
  4. The 30 m factor is applied (new column Yld_Harm = y x f) only if: both CIs exclude 1, |f30 / f50 - 1| <= 2 %
     (the factor must not depend on the cell size), min_factor (1 %) < |f - 1| <= max_factor (10 %), and (item 5) one
     factor fits the whole field. Above max_factor nothing is applied unless the field's scale ticket confirms it
     (item 8): a > 10 % step is beyond what a relative factor should repair (Chiocco & Wuestefeld 2019 bound their
     search at +/-10 %). Below min_factor the step is inside the calibration noise.
  5. Spatial check (machines.spatial_check, default true; spatial_check()). Every contact cell of the machine estimates
     its log factor (the partner's log factor + the cell's log ratio); the cells are split in two halves at the median
     of their position along the main axis of the cell centres. One factor fits when the two halves agree within
     SPATIAL_AGREE (5 %) or their difference is not significant (bootstrap CI over the pass pairs of each half includes
     0); each half needs >= 10 cells and >= 3 pass pairs, else the check is not made. This is the failure Maldaner et
     al. (2016) report (the machines' difference changes over the field).
  6. Per-machine totals (machines.totals_bu = {machine: wet bu}; settings file or CLI only, the app gives the field's
     ticket alone) make the relative step unnecessary: every machine is
     leveled to its own ticket, L_m = ticket_m / integral_m on the wet basis, Yld_Level = y x L_m, and
     Yld_Harm = y x (L_m / L_ref). The paired-cell estimate is then reported as a validation: agreement =
     (L_m / L_ref) / f_paired - 1, which should be within the paired CI. With only a field total the relative
     harmonisation runs first and L is computed on the harmonised integral (engine/clean/yield_clean.py).

  7. Gates on the estimate itself. A reference id whose records the cascade removed (fewer kept points than min_pairs
     x 5) falls back to the machine with the most kept points, with a warning. The validation of per-machine tickets
     (validate_totals) uses the 30 m paired estimate only where it passes the pair gate (gated()); otherwise it reports
     "no paired estimate: too few pairs" and claims no disagreement (the ungated estimate is kept as
     paired_factor_ungated and nothing more).
  8. Ticket closure test (ticket_closure; settings machines.ticket_confirms, default true, and machines.ticket_close_tol,
     default 1 %). The field's scale ticket (settings scale_ticket) is a second, independent measure of the level.
     When a machine's paired factor was NOT applied only because |f - 1| > max_factor (its CIs exclude 1, the 30 m
     and 50 m factors agree and one factor fits the field), the monitor total of the processed set is recomputed with that factor applied: when it
     closes with the ticket within ticket_close_tol while the unharmonised total differs by more than 3 x that, the
     factor is applied with the decision "applied: the paired factor is above max_factor but the grower's weight
     confirms it (harmonised total within x % of the scale)"; otherwise nothing is applied and the decision says why.
     For example a combine that reads 20 % low against the reference is not applied under the 10 % gate; when the
     total with it applied closes with the ticket, it is applied and the level factor moves with it. The other
     machines (CIs including 1) are unchanged. The test reads the primary test of the method used (primary_tests:
     the 30 m cells test, or the one test of machine, track or point x point). When the grower's weight is given to
     validate only (settings scale_ticket.use 'validate'; ticket_closure(validate_only=True)) the test is computed and
     reported the same way but applies nothing: a weight the user asked only to compare must not change the yield to
     map. Why a factor was not applied, and what would apply it, is one plain sentence per combine written from the
     gate and this test together (factor_advice): a factor the scale refuted is never followed by 'raise the max
     factor'.
  9. Harvest layout (layout_pair; v2.6). How two combines shared the field decides which factor can be trusted, so it
     is measured first, on the kept points: adj_a = the share of the passes of combine a with >= ADJ_MIN_POINTS (10)
     points within D = ADJ_WIDTH_MULT (1.5) x the median header width of a point of b, adj_b the same for b, adjacency
     = their mean; mixing = the share of the 100 m cells holding >= 5 points of both among those holding as many of
     either (a diagnostic). Classes: adjacency >= 50 % interleaved passes, 10-50 % mixed, < 10 % blocks, 0 no contact
     (house thresholds from the evidence of item 11). A combine's local layout is that of its best-connected partner
     (the local methods reach the reference through the network), its whole-field layout that of its pair with the
     reference (machine x machine compares the two whole-field means).
 10. The four methods (combine_factors; every one reported, whatever the choice): "cells" = items 2-5; "machine" =
     machine x machine, the mean of every kept point of the reference over the mean of every kept point of the
     combine (Maldaner et al. 2016 eq. 1; the normalisation x_i / x_m of Carneiro & Shiratsuchi, LSU AgCenter, is the
     same ratio), CI by resampling the passes of each combine, >= min_pass_pairs passes each, no network (ratios of
     whole-field means are transitive) and no spatial check; "track" = track x track, the kept points with <= 2 %
     overlap (same plot when a plan exists) of two combines matched within D, every pass pair with >= TRACK_MIN_POINTS
     (10) matched points on each side gives log(mean of its reference-side points / mean of its points), f = the
     geometric mean over ALL such pass pairs (Maldaner et al. use the one closest pair), CI by bootstrap over pass
     pairs; "point" = point x point, every point of the combine against the nearest point of the other within D, f =
     exp(mean log ratio) over all of them (Maldaner et al. 2016 eq. 5 takes the arithmetic mean of the ratios on one
     path pair), CI by a block bootstrap over pass pairs. Track and point need >= min_pass_pairs pass pairs, solve the
     network of item 3 with their own pair estimates and pass the spatial check of item 5 on their units; all three
     pass the gates of item 4 except the cell-size agreement (one estimate), and a factor above max_factor is a
     ticket closure candidate (item 8).
 11. machines.method: "auto" (default) = the paired cells where the combine met another on interleaved or mixed
     passes, NO factor where it harvested in blocks or never met (the field's scale ticket alone levels the yield);
     "cells", "machine", "track" or "point" force a method. Every method carries a traffic light by layout (LIGHTS):
     cells and track green on interleaved and mixed passes, yellow in blocks; point green interleaved, yellow mixed,
     red in blocks; machine x machine green only on interleaved passes, yellow mixed, red in blocks; red without
     contact. A yellow or red light on an applied factor goes into the decision, a red one also into the warnings.
     Evidence: the engine itself on pseudo-combines (single-combine data sets split into A / B by several harvest
     layouts - alternate passes, bands of passes, random, halves - with B multiplied by a known factor). On
     interleaved passes every method finds the factor, the cells with the smallest errors and the fewest false
     factors; on mixed passes the local methods stay close; in blocks the cells and the tracks never pass their
     gates, while machine x machine and point can apply factors far off; without contact there is no local
     estimate. The whole-field ratio is what the machines differ by PLUS what their ground differs by: it is
     unbiased only when both combines cut the same mix of ground (interleaved passes), the assumption of the
     normalisation of Carneiro & Shiratsuchi and of Maldaner et al.'s machine factor.
 12. The SMS 3-sigma method gets the same factor after its sequence (yield_clean: the combine factor is a step on the
     kept points after the cleaning, OCCD or SMS 3-sigma).
 13. Seams (seam_blocks, seam_summary; v2.6.1; a diagnostic of the map, never applied). Stripes on a yield map are steps
     between neighbouring passes; with several combines the question is whether the steps between passes of
     DIFFERENT combines are larger than those between passes of the SAME combine (the field's own pass-to-pass step:
     ground, direction of travel, overlap, flow delay). Every kept point (at its final position) is linked to the
     nearest point of another pass within D (item 9) that lies ACROSS the track (the link at >= 60 deg from the
     point's heading, so the next pass along the same line is not a neighbour); a pass pair with >= SEAM_MIN_POINTS
     (10) linked points on each side is a seam. Each seam is cut into stretches of SEAM_BLOCK_M (50 m) along its first
     pass (cumulative distance in record order; the points of the other pass go to the stretch of their nearest point
     across); a stretch with >= SEAM_BLOCK_MIN_POINTS (5) points on each side gives its step ln(mean of one side /
     mean of the other) on the yield as mapped before the combine factor and on Yld_Harm. The difference of a
     stretch is 100 (exp|step| - 1) %: how much the higher side reads above the lower one. Reported: the median
     difference and the share above 10 % of the stretches between passes of the same combine and of different
     combines (before and after the factor), and per pair of combines the median signed difference a over b (the
     part a calibration difference would make the same on every seam). A factor is one number per combine: it
     moves only the seams between different combines and removes only the part that is the same on all of them.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats
from scipy.spatial import cKDTree

CELL_SIZES_M = (30, 50)
HARM_AGREE = 0.02                        # the 30 m and 50 m factors must agree within 2 %
MIN_POINTS_PER_CELL = 5

PAIR_MAX_OVERLAP = 0.02                  # pairs are built from kept records with <= 2 % of the footprint already cut
SPATIAL_AGREE = 0.05                     # spatial check: the two halves of the contact cells agree within 5 % ...
SPATIAL_MIN_CELLS = 10                   # ... or it is not tested: each half needs >= 10 cells
SPATIAL_MIN_PASS_PAIRS = 3               # ... and >= 3 pass pairs

TICKET_CLOSE_TOL = 0.01                  # closure test: the harmonised total must be within 1 % of the ticket ...
TICKET_OPEN_MULT = 3                     # ... while the unharmonised total is more than 3 x that away

# harvest layout (how the combines shared the field) and the factor methods (module docstring, items 9-12)
LAYOUT_INTERLEAVED = 0.50                # adjacency >= 50 %: the combines alternated passes over the field ...
LAYOUT_BLOCKS = 0.10                     # ... < 10 %: each cut its own block (0: they never met); between: mixed
ADJ_MIN_POINTS = 10                      # a pass touches the other combine with >= 10 points within D of it ...
ADJ_WIDTH_MULT = 1.5                     # ... D = 1.5 x the median header width
DEFAULT_WIDTH_M = 12.192                 # D without a width (a 40 ft header): direct calls only, the run gives it
MIX_CELL_M = 100                         # mixing: the 100 m cells with >= 5 points of both / of either
TRACK_MIN_POINTS = 10                    # track x track: a pass pair counts with >= 10 points on each side
PLOT_OFFSET_M = 1e8                      # points of different plots are never neighbours (track / point, a plan)
FACTOR_METHODS = ("auto", "cells", "machine", "track", "point")
METHOD_LABEL = {"cells": "paired cells", "machine": "machine x machine", "track": "track x track",
                "point": "point x point"}
LAYOUT_LABEL = {"interleaved": "interleaved passes", "mixed": "mixed", "blocks": "blocks",
                "no_contact": "no contact"}
# the traffic light of every method in every layout (module docstring, item 11: the pseudo-combine evidence)
LOCAL_LIGHTS = {"interleaved": "green", "mixed": "green", "blocks": "yellow", "no_contact": "red"}
LIGHTS = {"cells": LOCAL_LIGHTS, "track": LOCAL_LIGHTS,
          "point": {"interleaved": "green", "mixed": "yellow", "blocks": "red", "no_contact": "red"},
          "machine": {"interleaved": "green", "mixed": "yellow", "blocks": "red", "no_contact": "red"}}
LIGHT_WHY = {("machine", "mixed"): "the combines cut partly different ground, so the whole-field means mix the "
                                   "machines with the soil",
             ("machine", "blocks"): "each combine cut its own block, so the whole-field means compare different "
                                    "ground (the machine and the soil cannot be told apart)",
             ("machine", "no_contact"): "the combines never met, so the whole-field means compare different ground",
             ("point", "mixed"): "single points matched along few, short contacts make a noisier factor than the "
                                 "cells or the tracks",
             ("point", "blocks"): "the points matched along the edge of the blocks alone carry the factor",
             ("local", "blocks"): "the combines met only along the edge of their blocks: the estimate rests on that "
                                  "strip alone",
             ("local", "no_contact"): "the combines never harvested side by side: no local estimate"}

DEFAULTS = {"harmonise": True, "reference": "auto", "totals_bu": None, "min_pairs": 30, "min_pass_pairs": 5,
            "ci": 0.95, "min_factor": 0.01, "max_factor": 0.10, "same_plot_only": True, "bootstrap_reps": 1000,
            "seed": 2026, "pair_max_overlap": PAIR_MAX_OVERLAP, "ticket_confirms": True,
            "ticket_close_tol": TICKET_CLOSE_TOL, "network": True, "spatial_check": True, "method": "auto"}
EMPTY_CELLS = ["cx", "cy", "ya", "yb", "ma", "mb", "na", "nb", "pass_a", "pass_b", "log_ratio"]


# ===================================================================================================== pairs
def _mode(s: pd.Series):
    v = s.mode(dropna=True)
    return v.iloc[0] if len(v) else np.nan


def paired_cells(df: pd.DataFrame, group_col: str, a, b, size_m: float, plot_col: str | None = None,
                 min_points: int = MIN_POINTS_PER_CELL, y_col: str = "_yv", m_col: str = "_mv",
                 pass_col: str = "Pass_Id", ovl_col: str | None = "Overlap_f",
                 max_overlap: float | None = None) -> pd.DataFrame:
    """Cells of size_m (m) holding >= min_points points of group `a` AND of group `b` (df columns: _x, _y in metres,
    `group_col`, `y_col` yield, optional `m_col` moisture, `pass_col`; `plot_col` splits the cells by plot). When
    max_overlap is given and `ovl_col` is a column, only the records whose footprint share already cut is <= max_overlap
    enter the cells (the records below the F8 threshold but above it are kept in the map, yet they read low
    and the following pass carries them: pairing them measures the overlap, not the machine). One row per cell: cx,
    cy, [plot], ya, yb, ma, mb, na, nb, pass_a, pass_b, log_ratio = log(ya / yb); empty frame when no cell qualifies."""
    d = df[df[group_col].isin([a, b])]
    d = d[np.isfinite(d[y_col].to_numpy(float)) & (d[y_col].to_numpy(float) > 0)]
    if max_overlap is not None and ovl_col is not None and ovl_col in d:
        d = d[np.nan_to_num(d[ovl_col].to_numpy(float), nan=1.0) <= float(max_overlap)]
    if plot_col is not None and plot_col in d:
        d = d[d[plot_col].astype(str) != ""]
    if len(d) == 0:
        return pd.DataFrame(columns=EMPTY_CELLS)
    keys = {"cx": (d["_x"] // size_m).astype(int), "cy": (d["_y"] // size_m).astype(int)}
    if plot_col is not None and plot_col in d:
        keys["plot"] = d[plot_col].astype(str)
    key_cols = list(keys)
    d = d.assign(**keys)
    agg = {"y": (y_col, "mean"), "n": (y_col, "size"), "pass": (pass_col, _mode)}
    if m_col in d:
        agg["m"] = (m_col, "mean")
    s = d.groupby(key_cols + [group_col], sort=False).agg(**agg).reset_index()
    s = s[s.n >= min_points]
    if len(s) == 0:                       # no cell of either group reaches min_points: the pivot would have no 'y'
        return pd.DataFrame(columns=EMPTY_CELLS)
    w = s.pivot_table(index=key_cols, columns=group_col, values=["y", "n", "pass"] + (["m"] if "m" in s else []),
                      aggfunc="first")
    if a not in w["y"].columns or b not in w["y"].columns:
        return pd.DataFrame(columns=EMPTY_CELLS)
    out = pd.DataFrame({"ya": w["y"][a], "yb": w["y"][b], "na": w["n"][a], "nb": w["n"][b],
                        "pass_a": w["pass"][a], "pass_b": w["pass"][b]})
    # the moisture of a group's cells: none when the file has no moisture column, and none either when the column
    # holds no value for that group (a header that logs no moisture: the pivot leaves an all-empty column out)
    wm = w["m"] if ("m" in s and "m" in w.columns.get_level_values(0)) else None
    out["ma"] = wm[a] if (wm is not None and a in wm.columns) else np.nan
    out["mb"] = wm[b] if (wm is not None and b in wm.columns) else np.nan
    out = out.dropna(subset=["ya", "yb"]).reset_index()
    out["log_ratio"] = np.log(out.ya / out.yb)
    return out


def ratio_estimate(cells: pd.DataFrame, reps: int = 1000, seed: int = 2026, ci: float = 0.95) -> dict:
    """Geometric-mean ratio a/b over the cells with a block bootstrap over pass pairs. Returns {n_cells, n_pass_pairs,
    factor, ci_lo, ci_hi, p_ttest, moisture_diff (b - a), excludes_one}; factor None when there is no cell."""
    n = int(len(cells))
    if n == 0:
        return {"n_cells": 0, "n_pass_pairs": 0, "factor": None, "ci_lo": None, "ci_hi": None, "p_ttest": None,
                "moisture_diff": None, "excludes_one": False}
    lr = cells.log_ratio.to_numpy(float)
    f = float(np.exp(lr.mean()))
    pp = pd.factorize(cells.pass_a.astype(str) + "|" + cells.pass_b.astype(str))[0]
    K = int(pp.max()) + 1
    sums, counts = np.bincount(pp, weights=lr, minlength=K), np.bincount(pp, minlength=K).astype(float)
    lo = hi = None
    if K >= 2 and reps > 0:
        rng = np.random.default_rng(seed)
        idx = rng.integers(0, K, size=(int(reps), K))
        boot = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
        q = (1 - ci) / 2
        lo, hi = float(np.exp(np.quantile(boot, q))), float(np.exp(np.quantile(boot, 1 - q)))
    p = float(stats.ttest_rel(cells.yb, cells.ya).pvalue) if n >= 3 else None
    md = cells.mb - cells.ma
    return {"n_cells": n, "n_pass_pairs": K, "factor": f, "ci_lo": lo, "ci_hi": hi,
            "p_ttest": (None if p is None or not np.isfinite(p) else p),
            "moisture_diff": (float(md.mean()) if md.notna().any() else None),
            "excludes_one": bool(lo is not None and (lo > 1 or hi < 1))}


# ===================================================================================================== harmonisation
def _frame(g, alive, y, moi):
    cols = ["_x", "_y", "Pass_Id", "_machine"] + [c for c in ("Plot_Id", "Overlap_f") if c in g]
    kk = g.loc[alive, cols].copy()
    kk["_yv"] = np.asarray(y, float)[np.asarray(alive, bool)]
    if moi is not None:
        kk["_mv"] = np.asarray(moi, float)[np.asarray(alive, bool)]
    return kk


def _pairing(kk: pd.DataFrame, P: dict):
    """(plot_col, max_overlap) of the pairs: Plot_Id when machines.same_plot_only and a plan names plots, and the
    pair_max_overlap share when the frame carries Overlap_f (else None)."""
    plot_col = "Plot_Id" if (P["same_plot_only"] and "Plot_Id" in kk and (kk["Plot_Id"].astype(str) != "").any()) \
        else None
    max_ovl = P.get("pair_max_overlap")
    max_ovl = float(max_ovl) if (max_ovl is not None and "Overlap_f" in kk) else None
    return plot_col, max_ovl


def machine_means(g, alive, y, moi, area_ac, ywet) -> dict:
    """Per machine: n, n_kept, area_ac (swept area of all processed readings), wet_bu (integral of the wet yield over
    that area), mass_per_area_mean (wet_bu / area_ac), moisture_mean (all readings), cleaned_map_mean (mapped yield,
    kept points). The field mean of mass per area is the integral, NOT the mean of the cleaned map."""
    out = {}
    mach = g["_machine"].astype(str).to_numpy()
    alive = np.asarray(alive, bool)
    y = np.asarray(y, float)
    area = np.nan_to_num(np.asarray(area_ac, float))
    wet = np.nan_to_num(np.asarray(ywet, float)) if ywet is not None else None
    for m in sorted(set(mach)):
        sel = mach == m
        a = float(area[sel].sum())
        wb = float((wet[sel] * area[sel]).sum()) if wet is not None else None
        yk = y[sel & alive]
        out[m] = {"n": int(sel.sum()), "n_kept": int((sel & alive).sum()), "area_ac": a, "wet_bu": wb,
                  "mass_per_area_mean": (wb / a if (wb is not None and a > 0) else None),
                  "moisture_mean": (float(np.nanmean(np.asarray(moi, float)[sel])) if moi is not None and sel.any()
                                    else None),
                  "cleaned_map_mean": (float(np.nanmean(yk)) if len(yk) else None)}
    return out


def _link_ok(est: dict, P: dict) -> bool:
    """A pair of machines is linked when its paired cells pass the pair gate (>= min_pairs cells, >= min_pass_pairs
    pass pairs and a bootstrap CI)."""
    return (est.get("factor") is not None and est.get("ci_lo") is not None and est["n_cells"] >= int(P["min_pairs"])
            and est["n_pass_pairs"] >= int(P["min_pass_pairs"]))


def _boot_means(cells: pd.DataFrame, reps: int, seed: int):
    """Bootstrap draws of the mean log ratio of a pair's cells, resampling its pass pairs (the procedure of
    ratio_estimate); None with fewer than 2 pass pairs."""
    if len(cells) == 0 or reps <= 0:
        return None
    lr = cells.log_ratio.to_numpy(float)
    pp = pd.factorize(cells.pass_a.astype(str) + "|" + cells.pass_b.astype(str))[0]
    K = int(pp.max()) + 1
    if K < 2:
        return None
    sums, counts = np.bincount(pp, weights=lr, minlength=K), np.bincount(pp, minlength=K).astype(float)
    idx = np.random.default_rng(seed).integers(0, K, size=(int(reps), K))
    return sums[idx].sum(axis=1) / counts[idx].sum(axis=1)


def network_solve(machines_: list, ref: str, links: dict, draws: dict):
    """The combine network (module docstring, item 3). u_m = log(y_ref / y_m) is the log factor of machine m, u_ref = 0.
    Every linked pair (a, b) with mean log ratio d_ab = log(y_a / y_b) over its cells observes u_b - u_a = d_ab, with
    weight 1 / (bootstrap variance of d_ab); u = the weighted least-squares solution. links: {(a, b): d_ab}; draws:
    {(a, b): bootstrap draws of d_ab}. Returns ({machine: u}, {machine: bootstrap draws of u}, {machine: weight share
    of each pair in u}), for the machines linked to the reference (directly or through other machines)."""
    adj = {m: set() for m in machines_}
    for a, b in links:
        adj[a].add(b)
        adj[b].add(a)
    seen, todo = {ref}, [ref]
    while todo:
        x = todo.pop()
        for z in adj[x] - seen:
            seen.add(z)
            todo.append(z)
    nodes = [m for m in machines_ if m in seen and m != ref]
    obs = [p for p in links if p[0] in seen and p[1] in seen]
    if not nodes:
        return {}, {}, {}
    col = {m: i for i, m in enumerate(nodes)}
    A = np.zeros((len(obs), len(nodes)))
    for r, (a, b) in enumerate(obs):
        if b in col:
            A[r, col[b]] += 1.0
        if a in col:
            A[r, col[a]] -= 1.0
    d = np.array([links[p] for p in obs], float)
    w = np.array([1.0 / max(float(np.var(draws[p], ddof=1)), 1e-12) for p in obs])
    K = np.linalg.solve(A.T @ (A * w[:, None]), (A * w[:, None]).T)        # u = K d
    u = K @ d
    reps = min(len(draws[p]) for p in obs)
    U = np.column_stack([draws[p][:reps] for p in obs]) @ K.T
    share = {m: {f"{a}|{b}": float(abs(K[col[m], r]) / max(np.abs(K[col[m]]).sum(), 1e-12)) for r, (a, b) in
                 enumerate(obs) if abs(K[col[m], r]) > 1e-9} for m in nodes}
    return {m: float(u[col[m]]) for m in nodes}, {m: U[:, col[m]] for m in nodes}, share


def spatial_check(cell_sets: list, size_m: float, reps: int, seed: int) -> dict:
    """The spatial consistency of one machine's factor (module docstring, item 5). cell_sets: [(cells, offset, sign)]
    of the linked pairs of the machine: every cell estimates the machine's log factor as offset + sign x log_ratio
    (the partner's log factor plus the cell's ratio). The cells are split in two halves at the median of their
    position along the main axis of the cell centres; the factor of each half (geometric mean of its cells), their
    difference and its CI by a bootstrap over the pass pairs of each half. Inconsistent when the halves differ by more
    than SPATIAL_AGREE and the CI of the difference excludes 0; not tested with fewer than SPATIAL_MIN_CELLS cells or
    SPATIAL_MIN_PASS_PAIRS pass pairs in a half."""
    parts = []
    for i, (c, off, sgn) in enumerate(cell_sets):
        if c is None or len(c) == 0:
            continue
        parts.append(pd.DataFrame({"x": (c.cx.to_numpy(float) + 0.5) * size_m, "y": (c.cy.to_numpy(float) + 0.5) * size_m,
                                   "e": off + sgn * c.log_ratio.to_numpy(float),
                                   "pp": f"{i}|" + c.pass_a.astype(str) + "|" + c.pass_b.astype(str)}))
    return spatial_units(pd.concat(parts, ignore_index=True) if parts else None, reps, seed, cell_m=size_m)


def spatial_units(e: pd.DataFrame | None, reps: int, seed: int, cell_m: float | None = None) -> dict:
    """The split of spatial_check() on any units: e holds x, y (m), e (the unit's estimate of the machine's log factor)
    and pp (its bootstrap block: the pass pair). The cells pass cell_m; the track and point units (module docstring,
    item 10) pass None."""
    out = {"tested": False, "tolerance": SPATIAL_AGREE, "cell_m": cell_m, "consistent": None}
    if e is None or len(e) == 0:
        return out
    X = e[["x", "y"]].to_numpy(float)
    Xc = X - X.mean(axis=0)
    axis = np.linalg.svd(Xc, full_matrices=False)[2][0] if len(e) > 1 else np.array([1.0, 0.0])
    s = Xc @ axis
    second = s > np.median(s)
    halves, rng = [], np.random.default_rng(seed)
    for sel in (~second, second):
        h = e[sel]
        pp = pd.factorize(h.pp)[0]
        K = int(pp.max()) + 1 if len(h) else 0
        halves.append((h, pp, K))
    out.update(n_cells=[int(len(h)) for h, _, _ in halves], n_pass_pairs=[int(K) for _, _, K in halves],
               axis=[float(axis[0]), float(axis[1])])
    if min(out["n_cells"]) < SPATIAL_MIN_CELLS or min(out["n_pass_pairs"]) < SPATIAL_MIN_PASS_PAIRS:
        return out
    boots, means = [], []
    for h, pp, K in halves:
        v = h.e.to_numpy(float)
        means.append(float(v.mean()))
        sums, cnt = np.bincount(pp, weights=v, minlength=K), np.bincount(pp, minlength=K).astype(float)
        idx = rng.integers(0, K, size=(int(reps), K))
        boots.append(sums[idx].sum(axis=1) / cnt[idx].sum(axis=1))
    diff = means[1] - means[0]
    lo, hi = np.quantile(boots[1] - boots[0], [0.025, 0.975]) if reps > 0 else (np.nan, np.nan)
    excl = bool(np.isfinite(lo) and (lo > 0 or hi < 0))
    pct = float(np.exp(diff) - 1)
    out.update(tested=True, factor_half_1=float(np.exp(means[0])), factor_half_2=float(np.exp(means[1])),
               diff_pct=100 * pct, ci_pct=[100 * float(np.exp(lo) - 1), 100 * float(np.exp(hi) - 1)],
               ci_excludes_0=excl, consistent=not (abs(pct) > SPATIAL_AGREE and excl))
    return out


def harmonise(g, alive, y, moi, Mset: dict, warnings: list | None = None, info: dict | None = None):
    """The harmonisation of the module docstring. g: processed frame with _x, _y (m), Pass_Id, _machine and, when a
    plan exists, Plot_Id; alive: kept mask; y: mapped yield; moi: moisture or None; Mset: the settings["machines"]
    dict; info (optional dict) receives the pairs, the estimate (direct or network) and the machines with no link to
    the reference. Returns (tests, applied, decision, reference): tests = list of per machine per cell size dicts,
    applied = {machine: factor}, decision = {machine: text}."""
    warnings = [] if warnings is None else warnings
    info = {} if info is None else info
    P = {**DEFAULTS, **(Mset or {})}
    machines_ = sorted(g["_machine"].astype(str).unique())
    kept_by = pd.Series(np.asarray(alive, bool)).groupby(g["_machine"].astype(str).values).sum()
    ref = P["reference"]
    if ref in (None, "auto"):
        ref = str(kept_by.idxmax())
    else:
        ref = str(ref)
        if ref not in machines_:
            warnings.append(f"machines.reference {ref!r} is not a value of the machine column ({machines_}): the "
                            "machine with the most kept points is the reference")
            ref = str(kept_by.idxmax())
        elif int(kept_by.get(ref, 0)) < int(P["min_pairs"]) * MIN_POINTS_PER_CELL:
            # a reference whose records the cascade removed (or nearly all of them) can pair with nothing: every other
            # machine would read "too few pairs" without a word on the cause
            best = str(kept_by.idxmax())
            warnings.append(f"machines.reference {ref!r} has {int(kept_by.get(ref, 0)):,} kept points (fewer than "
                            f"{int(P['min_pairs']) * MIN_POINTS_PER_CELL} = min_pairs x {MIN_POINTS_PER_CELL} points per "
                            f"cell): the machine with the most kept points ({best}, {int(kept_by[best]):,}) is the "
                            "reference")
            ref = best
    kk = _frame(g, alive, y, moi)
    plot_col, max_ovl = _pairing(kk, P)
    share_ovl = {}
    if max_ovl is not None:
        ov = np.nan_to_num(kk["Overlap_f"].to_numpy(float), nan=1.0) > max_ovl
        share_ovl = pd.Series(ov).groupby(kk["_machine"].astype(str).values).mean().to_dict()
    reps, seed, ci = int(P["bootstrap_reps"]), int(P["seed"]), float(P["ci"])
    # every pair of machines: the paired cells and the ratio at both cell sizes; a pair with the reference is oriented
    # (reference, machine), so its factor is the direct reference / machine estimate
    others = [m for m in machines_ if m != ref]
    pairs = [(ref, m) for m in others] + [(a, b) for i, a in enumerate(others) for b in others[i + 1:]]
    pc = {p: {s: paired_cells(kk, "_machine", p[0], p[1], s, plot_col=plot_col, max_overlap=max_ovl)
              for s in CELL_SIZES_M} for p in pairs}
    pe = {p: {s: ratio_estimate(pc[p][s], reps=reps, seed=seed, ci=ci) for s in CELL_SIZES_M} for p in pairs}
    linked = [p for p in pairs if all(_link_ok(pe[p][s], P) for s in CELL_SIZES_M)]
    # the direct estimate when every link touches the reference (two machines always); else the network
    network = bool(P.get("network", True)) and any(ref not in p for p in linked)
    est, via, sets30 = {m: {} for m in others}, {m: [] for m in others}, {m: [] for m in others}
    if network:
        for s in CELL_SIZES_M:
            links = {p: float(np.log(pe[p][s]["factor"])) for p in linked}
            draws = {p: _boot_means(pc[p][s], reps, seed + 7919 * (k + 1)) for k, p in enumerate(linked)}
            u, U, share = network_solve(machines_, ref, links, draws)
            q = (1 - ci) / 2
            for m, um in u.items():
                mine = [p for p in linked if m in p]
                lo, hi = float(np.exp(np.quantile(U[m], q))), float(np.exp(np.quantile(U[m], 1 - q)))
                d = pe.get((ref, m), {}).get(s) or {}
                est[m][s] = {"n_cells": int(sum(pe[p][s]["n_cells"] for p in mine)),
                             "n_pass_pairs": int(sum(pe[p][s]["n_pass_pairs"] for p in mine)),
                             "factor": float(np.exp(um)), "ci_lo": lo, "ci_hi": hi, "p_ttest": d.get("p_ttest"),
                             "moisture_diff": d.get("moisture_diff"), "excludes_one": bool(lo > 1 or hi < 1),
                             "estimate": "network", "pair_weights": share[m],
                             "direct_factor": d.get("factor") if (ref, m) in linked else None}
                if s == CELL_SIZES_M[0]:
                    via[m] = sorted(p[0] if p[1] == m else p[1] for p in mine)
                    sets30[m] = [(pc[p][s], (0.0 if p[0] == ref else u[p[0]]), 1.0) if p[1] == m else
                                 (pc[p][s], (0.0 if p[1] == ref else u[p[1]]), -1.0) for p in mine]
    for m in others:
        for s in CELL_SIZES_M:
            if s not in est[m]:                      # the direct estimate (or the unlinked machine's own pair)
                est[m][s] = dict(pe[(ref, m)][s], estimate="direct")
        if not sets30[m]:
            sets30[m] = [(pc[(ref, m)][CELL_SIZES_M[0]], 0.0, 1.0)]
            via[m] = [ref] if (ref, m) in linked else []
    info.update(reference=ref, estimate="network" if network else "direct",
                pairs=[{"a": p[0], "b": p[1], "cell_m": s, "n_cells": pe[p][s]["n_cells"],
                        "n_pass_pairs": pe[p][s]["n_pass_pairs"], "factor_a_over_b": pe[p][s]["factor"],
                        "ci_lo": pe[p][s]["ci_lo"], "ci_hi": pe[p][s]["ci_hi"], "linked": p in linked}
                       for p in pairs for s in CELL_SIZES_M],
                unlinked=[m for m in others if est[m][CELL_SIZES_M[0]].get("estimate") == "direct" and network
                          or (not network and (ref, m) not in linked)])
    tests, applied, decision = [], {}, {}
    for m in machines_:
        if m == ref:
            applied[m], decision[m] = 1.0, "reference"
            continue
        e30, e50 = est[m][CELL_SIZES_M[0]], est[m][CELL_SIZES_M[1]]
        for s, e in ((CELL_SIZES_M[0], e30), (CELL_SIZES_M[1], e50)):
            e.update(machine=m, method="cells", primary=s == CELL_SIZES_M[0], cell_m=s,
                     same_plot_only=plot_col is not None, max_overlap=max_ovl, via=via[m],
                     share_overlapped=(float(share_ovl.get(m, 0.0)) if max_ovl is not None else None),
                     share_overlapped_ref=(float(share_ovl.get(ref, 0.0)) if max_ovl is not None else None))
            tests.append(e)
        if network:
            enough = e30.get("estimate") == "network"
        else:
            enough = all(e["n_cells"] >= P["min_pairs"] and e["n_pass_pairs"] >= P["min_pass_pairs"] for e in (e30, e50))
        if enough and P.get("spatial_check", True):
            e30["spatial"] = spatial_check(sets30[m], CELL_SIZES_M[0], reps, seed)
        f30, f50 = e30["factor"], e50["factor"]
        # gate: why the factor was (not) applied, as a key the ticket closure test can read ("above_max_factor" is the
        # only case it reconsiders: the estimate is significant and consistent, only its size is beyond the gate)
        if not enough:
            applied[m] = 1.0
            if network:
                decision[m] = (f"not applied: no link to the reference {ref} (no chain of neighbouring machines with >= "
                               f"{P['min_pairs']} cells and >= {P['min_pass_pairs']} pass pairs at both cell sizes; "
                               f"{e30['n_cells']} cells / {e30['n_pass_pairs']} pass pairs with {ref} at 30 m)")
                gate = "no_link"
            else:
                decision[m] = (f"not applied: too few pairs ({e30['n_cells']} cells / {e30['n_pass_pairs']} pass pairs "
                               f"at 30 m, {e50['n_cells']} / {e50['n_pass_pairs']} at 50 m; need >= {P['min_pairs']} "
                               f"cells and >= {P['min_pass_pairs']} pass pairs)")
                gate = "too_few_pairs"
            for e in (e30, e50):
                e["gate"] = gate
            continue
        both_sig = e30["excludes_one"] and e50["excludes_one"]
        agree = abs(f30 / f50 - 1) <= HARM_AGREE
        eff = abs(f30 - 1)
        sp = e30.get("spatial") or {}
        if eff > P["max_factor"]:
            applied[m] = 1.0
            # the closure test reconsiders the step only when it is significant and consistent across cell sizes and
            # over the field; otherwise the decision says why a grower's weight cannot confirm it
            unconfirmed = unconfirmed_reason(e30, e50)
            decision[m] = (f"not applied: |factor - 1| = {100 * eff:.2f} % is above machines.max_factor "
                           f"({100 * P['max_factor']:.0f} %)"
                           + (f" and {unconfirmed}, so a grower's weight is not used to confirm it" if unconfirmed else
                              " (a grower's weight for the field can confirm it: the ticket closure test)"))
            warnings.append(f"machine {m}: the paired cells put it {100 * (1 / f30 - 1):+.2f} % against {ref} "
                            f"(factor {f30:.4f}, CI {e30['ci_lo']:.4f}-{e30['ci_hi']:.4f}), more than the "
                            f"{100 * P['max_factor']:.0f} % a relative factor should repair: no harmonisation applied "
                            "unless the field's scale ticket confirms it; check the monitor's calibration")
            gate = "above_max_factor" if not unconfirmed else "above_max_factor_unconfirmed"
        elif not both_sig:
            applied[m] = 1.0
            decision[m] = (f"not applied: the {int(100 * P['ci'])} % bootstrap CI includes 1 at "
                           + (" and ".join(f"{s} m" for s, e in ((CELL_SIZES_M[0], e30), (CELL_SIZES_M[1], e50))
                                           if not e["excludes_one"])))
            gate = "ci_includes_1"
        elif not agree:
            applied[m] = 1.0
            decision[m] = f"not applied: 30 m and 50 m factors disagree ({f30:.4f} vs {f50:.4f}, > {HARM_AGREE:.0%})"
            warnings.append(f"machine {m}: significant but the 30 m and 50 m factors disagree ({f30:.4f} vs {f50:.4f}); "
                            "no harmonisation applied - check the machines' calibration")
            gate = "sizes_disagree"
        elif eff <= P["min_factor"]:
            applied[m] = 1.0
            decision[m] = f"not applied: |factor - 1| = {100 * eff:.2f} % <= machines.min_factor ({100 * P['min_factor']:.0f} %)"
            gate = "below_min_factor"
        elif sp.get("consistent") is False:
            applied[m] = 1.0
            decision[m] = (f"not applied: the factor differs over the field (halves {sp['factor_half_1']:.4f} and "
                           f"{sp['factor_half_2']:.4f}, {sp['diff_pct']:+.2f} %, more than {100 * SPATIAL_AGREE:.0f} % "
                           "and significant): one factor does not fit the whole field")
            warnings.append(f"machine {m}: its difference to the other combines changes over the field (the two halves "
                            f"of its contact cells read {sp['factor_half_1']:.4f} and {sp['factor_half_2']:.4f}): no "
                            "harmonisation applied")
            gate = "spatially_inconsistent"
        else:
            applied[m] = float(f30)
            decision[m] = (f"applied (30 m factor {f30:.4f}, CI {e30['ci_lo']:.4f}-{e30['ci_hi']:.4f}, "
                           f"{e30['n_cells']} cells / {e30['n_pass_pairs']} pass pairs"
                           + (f", network through {', '.join(via[m])}" if e30.get("estimate") == "network" else "")
                           + ")")
            gate = "applied"
        for e in (e30, e50):
            e["gate"] = gate
    return tests, applied, decision, ref


# ===================================================================================================== layout and methods
def layout_class(adjacency) -> str:
    """The harvest layout of a pair of combines from its adjacency (module docstring, item 9): "interleaved"
    (>= LAYOUT_INTERLEAVED), "mixed", "blocks" (< LAYOUT_BLOCKS) or "no_contact" (0)."""
    if adjacency is None or not np.isfinite(adjacency) or adjacency <= 0:
        return "no_contact"
    if adjacency >= LAYOUT_INTERLEAVED:
        return "interleaved"
    return "mixed" if adjacency >= LAYOUT_BLOCKS else "blocks"


def layout_pair(kk: pd.DataFrame, a, b, D: float) -> dict:
    """How combines a and b shared the field (module docstring, item 9), on the kept points kk (_x, _y in m, Pass_Id,
    _machine): adj_a = the share of the passes of a with >= ADJ_MIN_POINTS points within D m of a point of b (adj_b
    the same for b), adjacency = their mean, mixing = the share of the MIX_CELL_M m cells holding >=
    MIN_POINTS_PER_CELL points of both among those holding as many of either, layout = layout_class(adjacency)."""
    mach = kk["_machine"].astype(str)
    A, B = kk[mach == str(a)], kk[mach == str(b)]
    out = {"a": str(a), "b": str(b), "D_m": float(D), "n_passes_a": int(A.Pass_Id.nunique()),
           "n_passes_b": int(B.Pass_Id.nunique())}
    for X, Y, nm in ((A, B, "a"), (B, A, "b")):
        if len(X) == 0 or len(Y) == 0:
            out[f"adj_{nm}"] = 0.0
            continue
        dd, _ = cKDTree(Y[["_x", "_y"]].to_numpy(float)).query(X[["_x", "_y"]].to_numpy(float),
                                                                distance_upper_bound=float(D))
        hit = pd.Series(np.isfinite(dd)).groupby(X.Pass_Id.to_numpy()).sum()
        out[f"adj_{nm}"] = float((hit >= ADJ_MIN_POINTS).mean())
    both = either = 0
    d = pd.concat([A, B])
    if len(d):
        cell = ((d._x // MIX_CELL_M).astype(np.int64).astype(str) + "_"
                + (d._y // MIX_CELL_M).astype(np.int64).astype(str))
        t = pd.crosstab(cell.to_numpy(), d["_machine"].astype(str).to_numpy())
        na = t[str(a)] if str(a) in t.columns else pd.Series(0, index=t.index)
        nb = t[str(b)] if str(b) in t.columns else pd.Series(0, index=t.index)
        both = int(((na >= MIN_POINTS_PER_CELL) & (nb >= MIN_POINTS_PER_CELL)).sum())
        either = int(((na >= MIN_POINTS_PER_CELL) | (nb >= MIN_POINTS_PER_CELL)).sum())
    adj = (out["adj_a"] + out["adj_b"]) / 2
    out.update(adjacency=float(adj), mixing=(both / either if either else 0.0), n_cells_both=both,
               n_cells_either=either, layout=layout_class(adj))
    return out


def _match_population(kk: pd.DataFrame, plot_col, max_ovl) -> pd.DataFrame:
    """The pairing population of track x track and point x point: the kept points with a positive yield, <= max_ovl of
    their footprint already cut and, with a plan (plot_col), inside a plot; _qx moves every plot PLOT_OFFSET_M away
    from the others, so that points of different plots are never neighbours (the same-plot rule of the cells)."""
    yv = kk["_yv"].to_numpy(float)
    d = kk[np.isfinite(yv) & (yv > 0)]
    if max_ovl is not None and "Overlap_f" in d:
        d = d[np.nan_to_num(d["Overlap_f"].to_numpy(float), nan=1.0) <= float(max_ovl)]
    code = np.zeros(len(d))
    if plot_col is not None and plot_col in d:
        d = d[d[plot_col].astype(str) != ""]
        code = pd.factorize(d[plot_col].astype(str))[0].astype(float)
    return d.assign(_qx=d["_x"].to_numpy(float) + PLOT_OFFSET_M * code)


def _matches(pop: pd.DataFrame, a, b, D: float):
    """Nearest-point links within D m between combines a and b of the pairing population: bp = every point of b with
    its nearest point of a, ap = every point of a with its nearest point of b (y_a, y_b, pass_a, pass_b, x / y of the
    point, and for bp the moistures m_a / m_b). (None, None) when either combine has no point."""
    mach = pop["_machine"].astype(str)
    A, B = pop[mach == str(a)], pop[mach == str(b)]
    if len(A) == 0 or len(B) == 0:
        return None, None
    XA, XB = A[["_qx", "_y"]].to_numpy(float), B[["_qx", "_y"]].to_numpy(float)
    d_ba, i_ba = cKDTree(XA).query(XB, distance_upper_bound=float(D))
    d_ab, i_ab = cKDTree(XB).query(XA, distance_upper_bound=float(D))
    okb, oka = np.isfinite(d_ba), np.isfinite(d_ab)
    jb, ja = i_ba[okb], i_ab[oka]
    ya, yb = A["_yv"].to_numpy(float), B["_yv"].to_numpy(float)
    pa, pb = A["Pass_Id"].to_numpy(), B["Pass_Id"].to_numpy()
    ma = A["_mv"].to_numpy(float) if "_mv" in A else np.full(len(A), np.nan)
    mb = B["_mv"].to_numpy(float) if "_mv" in B else np.full(len(B), np.nan)
    bp = pd.DataFrame({"y_a": ya[jb], "y_b": yb[okb], "pass_a": pa[jb], "pass_b": pb[okb],
                       "x": B["_x"].to_numpy(float)[okb], "y": B["_y"].to_numpy(float)[okb],
                       "m_a": ma[jb], "m_b": mb[okb]})
    ap = pd.DataFrame({"y_a": ya[oka], "y_b": yb[ja], "pass_a": pa[oka], "pass_b": pb[ja],
                       "x": A["_x"].to_numpy(float)[oka], "y": A["_y"].to_numpy(float)[oka]})
    return bp, ap


EMPTY_UNITS = ["x", "y", "lr", "pp"]


def track_units(bp: pd.DataFrame | None, ap: pd.DataFrame | None) -> pd.DataFrame:
    """Track x track (module docstring, item 10): one unit per pass pair (a pass of a, a pass of b) along a common edge
    with >= TRACK_MIN_POINTS matched points on each side; lr = log(mean of its a points / mean of its b points), x / y
    the centre of those points, pp the pass pair (every unit is its own bootstrap block)."""
    if bp is None or ap is None or len(bp) == 0 or len(ap) == 0:
        return pd.DataFrame(columns=EMPTY_UNITS)
    keys = ["pass_a", "pass_b"]
    tb = bp.groupby(keys).agg(yb=("y_b", "mean"), nb=("y_b", "size"), xb=("x", "mean"), vb=("y", "mean"))
    ta = ap.groupby(keys).agg(ya=("y_a", "mean"), na=("y_a", "size"), xa=("x", "mean"), va=("y", "mean"))
    t = ta.join(tb, how="inner")
    t = t[(t.na >= TRACK_MIN_POINTS) & (t.nb >= TRACK_MIN_POINTS)]
    if len(t) == 0:
        return pd.DataFrame(columns=EMPTY_UNITS)
    n = (t.na + t.nb).to_numpy(float)
    return pd.DataFrame({"x": ((t.xa * t.na + t.xb * t.nb).to_numpy(float) / n),
                         "y": ((t.va * t.na + t.vb * t.nb).to_numpy(float) / n),
                         "lr": np.log(t.ya.to_numpy(float) / t.yb.to_numpy(float)),
                         "pp": [f"{i}|{j}" for i, j in t.index]})


def point_units(bp: pd.DataFrame | None) -> pd.DataFrame:
    """Point x point (module docstring, item 10): one unit per point of b, lr = log(y of its nearest a point / its own
    y), pp its pass pair (the bootstrap block)."""
    if bp is None or len(bp) == 0:
        return pd.DataFrame(columns=EMPTY_UNITS)
    return pd.DataFrame({"x": bp.x.to_numpy(float), "y": bp.y.to_numpy(float),
                         "lr": np.log(bp.y_a.to_numpy(float) / bp.y_b.to_numpy(float)),
                         "pp": (bp.pass_a.astype(str) + "|" + bp.pass_b.astype(str)).to_numpy()})


def units_estimate(U: pd.DataFrame, reps: int, seed: int, ci: float):
    """Geometric-mean ratio of the units U (lr, pp) with a CI by a block bootstrap over their pass pairs (the procedure
    of ratio_estimate). Returns (estimate, bootstrap draws of the mean log ratio or None)."""
    n = int(len(U))
    est = {"n_units": n, "n_pass_pairs": 0, "factor": None, "ci_lo": None, "ci_hi": None, "excludes_one": False}
    if n == 0:
        return est, None
    lr = U["lr"].to_numpy(float)
    pp = pd.factorize(U["pp"])[0]
    K = int(pp.max()) + 1
    est.update(n_pass_pairs=K, factor=float(np.exp(lr.mean())))
    draws = None
    if K >= 2 and reps > 0:
        sums, counts = np.bincount(pp, weights=lr, minlength=K), np.bincount(pp, minlength=K).astype(float)
        idx = np.random.default_rng(seed).integers(0, K, size=(int(reps), K))
        draws = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
        q = (1 - ci) / 2
        lo, hi = float(np.exp(np.quantile(draws, q))), float(np.exp(np.quantile(draws, 1 - q)))
        est.update(ci_lo=lo, ci_hi=hi, excludes_one=bool(lo > 1 or hi < 1))
    return est, draws


def machine_estimate(kk: pd.DataFrame, a, b, reps: int, seed: int, ci: float) -> dict:
    """Machine x machine (module docstring, item 10; Maldaner et al. 2016 eq. 1, the normalisation of Carneiro &
    Shiratsuchi): the mean of every kept point of a over the mean of every kept point of b, with a CI by resampling
    the passes of each combine (independently: the two combines cut different passes)."""
    yv = kk["_yv"].to_numpy(float)
    mach = kk["_machine"].astype(str).to_numpy()
    ok = np.isfinite(yv) & (yv > 0)
    s = {}
    for k in (str(a), str(b)):
        p = kk[ok & (mach == k)].groupby("Pass_Id")["_yv"].agg(["sum", "size"])
        s[k] = (p["sum"].to_numpy(float), p["size"].to_numpy(float))
    (sa, na), (sb, nb) = s[str(a)], s[str(b)]
    out = {"n_units": int(na.sum() + nb.sum()), "n_points_ref": int(na.sum()), "n_points_m": int(nb.sum()),
           "n_passes_ref": int(len(sa)), "n_passes_m": int(len(sb)), "n_pass_pairs": None, "factor": None,
           "ci_lo": None, "ci_hi": None, "excludes_one": False, "moisture_diff": None}
    if len(sa) == 0 or len(sb) == 0:
        return out
    out["factor"] = float((sa.sum() / na.sum()) / (sb.sum() / nb.sum()))
    if reps > 0 and len(sa) >= 2 and len(sb) >= 2:
        rng = np.random.default_rng(seed)
        ia = rng.integers(0, len(sa), size=(int(reps), len(sa)))
        ib = rng.integers(0, len(sb), size=(int(reps), len(sb)))
        draws = np.log((sa[ia].sum(axis=1) / na[ia].sum(axis=1)) / (sb[ib].sum(axis=1) / nb[ib].sum(axis=1)))
        q = (1 - ci) / 2
        lo, hi = float(np.exp(np.quantile(draws, q))), float(np.exp(np.quantile(draws, 1 - q)))
        out.update(ci_lo=lo, ci_hi=hi, excludes_one=bool(lo > 1 or hi < 1))
    if "_mv" in kk:
        mv = kk["_mv"].to_numpy(float)
        ma, mb = mv[ok & (mach == str(a))], mv[ok & (mach == str(b))]
        if np.isfinite(ma).any() and np.isfinite(mb).any():
            out["moisture_diff"] = float(np.nanmean(mb) - np.nanmean(ma))
    return out


def _unit_frame(sets: list):
    """The units of a machine's linked pairs as its own estimates of its log factor (offset + sign x lr), for
    spatial_units()."""
    parts = []
    for i, (U, off, sgn) in enumerate(sets):
        if U is None or len(U) == 0:
            continue
        parts.append(pd.DataFrame({"x": U["x"].to_numpy(float), "y": U["y"].to_numpy(float),
                                   "e": off + sgn * U["lr"].to_numpy(float),
                                   "pp": (f"{i}|" + U["pp"].astype(str)).to_numpy()}))
    return pd.concat(parts, ignore_index=True) if parts else None


def units_text(t: dict) -> str:
    """What an estimate of a method rests on, in words."""
    m = t.get("method", "cells")
    if m == "cells":
        return f"{t.get('n_cells') or 0} cells / {t.get('n_pass_pairs') or 0} pass pairs"
    if m == "machine":
        return (f"whole-field means of {t.get('n_points_ref') or 0:,} and {t.get('n_points_m') or 0:,} kept points "
                f"({t.get('n_passes_ref') or 0} and {t.get('n_passes_m') or 0} passes)")
    if m == "track":
        return f"{t.get('n_pass_pairs') or 0} pass pairs"
    return f"{t.get('n_units') or 0:,} points / {t.get('n_pass_pairs') or 0} pass pairs"


def _factor_name(t: dict) -> str:
    m = t.get("method", "cells")
    return "30 m factor" if m == "cells" else f"{METHOD_LABEL.get(m, m)} factor"


def estimate_text(t: dict) -> str:
    """'30 m factor 0.9500, CI 0.9300-0.9700, 50 cells / 10 pass pairs' (cells) or the same for another method."""
    ci = f", CI {t['ci_lo']:.4f}-{t['ci_hi']:.4f}" if t.get("ci_lo") is not None else ""
    return f"{_factor_name(t)} {t['factor']:.4f}{ci}, {units_text(t)}"


def primary_tests(tests: list) -> dict:
    """{machine: the test whose factor is the one a decision rests on}: the 30 m cells test or the one test of another
    method (tests without the key 'primary': the 30 m ones)."""
    return {t["machine"]: t for t in tests if t.get("primary", t.get("cell_m") == CELL_SIZES_M[0])}


def _other_link_ok(e: dict, min_pp: int) -> bool:
    return e.get("factor") is not None and e.get("ci_lo") is not None and int(e.get("n_pass_pairs") or 0) >= min_pp


def _gate_other(e: dict, P: dict, ref: str):
    """(gate, decision, warning or None) of a machine, track or point x point estimate: the gates of harmonise()
    without the cell-size agreement (one estimate) and, for machine x machine, without the spatial check."""
    meth, m, f = e["method"], e["machine"], e.get("factor")
    lab, min_pp = METHOD_LABEL[meth], int(P["min_pass_pairs"])
    if not e.get("enough"):
        if meth == "machine":
            return ("too_few_pairs", f"not applied: too few passes ({e.get('n_passes_ref') or 0} of {ref} and "
                                     f"{e.get('n_passes_m') or 0} of {m}; need >= {min_pp} each, and a CI)", None)
        if e.get("network_on"):
            return ("no_link", f"not applied: no {lab} link to the reference {ref} (no chain of neighbouring combines "
                               f"with >= {min_pp} pass pairs; {units_text(e)} with {ref})", None)
        return ("too_few_pairs", f"not applied: too few pairs ({units_text(e)}; need >= {min_pp} pass pairs)", None)
    eff = abs(f - 1)
    sp = e.get("spatial") or {}
    if eff > P["max_factor"]:
        unconfirmed = unconfirmed_reason(e, None)
        txt = (f"not applied: |factor - 1| = {100 * eff:.2f} % is above machines.max_factor "
               f"({100 * P['max_factor']:.0f} %)"
               + (f" and {unconfirmed}, so a grower's weight is not used to confirm it" if unconfirmed else
                  " (a grower's weight for the field can confirm it: the ticket closure test)"))
        w = (f"machine {m}: the {lab} estimate puts it {100 * (1 / f - 1):+.2f} % against {ref} (factor {f:.4f}, CI "
             f"{e['ci_lo']:.4f}-{e['ci_hi']:.4f}), more than the {100 * P['max_factor']:.0f} % a relative factor should "
             "repair: no harmonisation applied unless the field's scale ticket confirms it; check the monitor's "
             "calibration")
        return ("above_max_factor" if not unconfirmed else "above_max_factor_unconfirmed"), txt, w
    if not e["excludes_one"]:
        return "ci_includes_1", f"not applied: the {int(100 * P['ci'])} % bootstrap CI includes 1", None
    if eff <= P["min_factor"]:
        return ("below_min_factor", f"not applied: |factor - 1| = {100 * eff:.2f} % <= machines.min_factor "
                                    f"({100 * P['min_factor']:.0f} %)", None)
    if sp.get("consistent") is False:
        return ("spatially_inconsistent",
                f"not applied: the factor differs over the field (halves {sp['factor_half_1']:.4f} and "
                f"{sp['factor_half_2']:.4f}, {sp['diff_pct']:+.2f} %, more than {100 * SPATIAL_AGREE:.0f} % and "
                "significant): one factor does not fit the whole field",
                f"machine {m}: its difference to the other combines changes over the field ({lab}: the two halves read "
                f"{sp['factor_half_1']:.4f} and {sp['factor_half_2']:.4f}): no harmonisation applied")
    return ("applied", "applied (" + estimate_text(e)
            + (f", network through {', '.join(e.get('via') or [])}" if e.get("estimate") == "network" else "") + ")",
            None)


def _other_methods(kk: pd.DataFrame, ref: str, machines_: list, P: dict, D: float) -> dict:
    """Machine x machine, track x track and point x point of every combine against the reference (module docstring,
    item 10): {method: {machine: test}}. Track and point solve the network of neighbouring combines as the cells do
    (machine x machine needs none: ratios of whole-field means are transitive)."""
    reps, seed, ci = int(P["bootstrap_reps"]), int(P["seed"]), float(P["ci"])
    q = (1 - ci) / 2
    min_pp = int(P["min_pass_pairs"])
    plot_col, max_ovl = _pairing(kk, P)
    pop = _match_population(kk, plot_col, max_ovl)
    others = [m for m in machines_ if m != ref]
    pairs = [(ref, m) for m in others] + [(a, b) for i, a in enumerate(others) for b in others[i + 1:]]
    out = {"machine": {}, "track": {}, "point": {}}
    for m in others:
        e = machine_estimate(kk, ref, m, reps, seed, ci)
        e.update(machine=m, method="machine", primary=True, cell_m=None, n_cells=None, unit="kept points",
                 estimate="direct", via=[ref], same_plot_only=False, max_overlap=None, p_ttest=None,
                 enough=bool(e["ci_lo"] is not None and min(e["n_passes_ref"], e["n_passes_m"]) >= min_pp))
        out["machine"][m] = e
    units, mdiff = {"track": {}, "point": {}}, {}
    for p in pairs:
        bp, ap = _matches(pop, p[0], p[1], D)
        units["track"][p], units["point"][p] = track_units(bp, ap), point_units(bp)
        if p[0] == ref and bp is not None and len(bp):
            dm = bp.m_b - bp.m_a
            mdiff[p[1]] = float(dm.mean()) if dm.notna().any() else None
    for meth in ("track", "point"):
        U = units[meth]
        pe = {p: units_estimate(U[p], reps, seed, ci)[0] for p in pairs}
        linked = [p for p in pairs if _other_link_ok(pe[p], min_pp)]
        network = bool(P.get("network", True)) and any(ref not in p for p in linked)
        est, via, sets = {}, {}, {}
        if network:
            links = {p: float(U[p]["lr"].to_numpy(float).mean()) for p in linked}
            draws = {p: units_estimate(U[p], reps, seed + 7919 * (k + 1), ci)[1] for k, p in enumerate(linked)}
            u, Ud, share = network_solve(machines_, ref, links, draws)
            for m, um in u.items():
                mine = [p for p in linked if m in p]
                lo, hi = float(np.exp(np.quantile(Ud[m], q))), float(np.exp(np.quantile(Ud[m], 1 - q)))
                est[m] = {"n_units": int(sum(pe[p]["n_units"] for p in mine)),
                          "n_pass_pairs": int(sum(pe[p]["n_pass_pairs"] for p in mine)), "factor": float(np.exp(um)),
                          "ci_lo": lo, "ci_hi": hi, "excludes_one": bool(lo > 1 or hi < 1), "estimate": "network",
                          "pair_weights": share[m],
                          "direct_factor": pe[(ref, m)]["factor"] if (ref, m) in linked else None}
                via[m] = sorted(p[0] if p[1] == m else p[1] for p in mine)
                sets[m] = [(U[p], (0.0 if p[0] == ref else u[p[0]]), 1.0) if p[1] == m else
                           (U[p], (0.0 if p[1] == ref else u[p[1]]), -1.0) for p in mine]
        for m in others:
            if m not in est:                         # the direct estimate (or the unlinked machine's own pair)
                est[m] = dict(pe[(ref, m)], estimate="direct")
                via[m] = [ref] if (ref, m) in linked else []
                sets[m] = [(U[(ref, m)], 0.0, 1.0)]
            e = est[m]
            e.update(machine=m, method=meth, primary=True, cell_m=None, n_cells=None,
                     unit=("pass pairs" if meth == "track" else "points"), via=via[m], p_ttest=None,
                     moisture_diff=mdiff.get(m), same_plot_only=plot_col is not None, max_overlap=max_ovl,
                     D_m=float(D), network_on=network)
            e["enough"] = (e["estimate"] == "network") if network else _other_link_ok(e, min_pp)
            if e["enough"] and P.get("spatial_check", True):
                e["spatial"] = spatial_units(_unit_frame(sets[m]), reps, seed)
            out[meth][m] = e
    for meth in out:
        for e in out[meth].values():
            e["gate"], e["decision"], e["warning"] = _gate_other(e, P, ref)
    return out


def _light_note(m: str, meth: str, layout: str, factor: float, decision: dict, warnings: list) -> None:
    """The traffic light of an applied factor: a yellow or red light is added to the decision, a red one also as a
    warning."""
    light = LIGHTS[meth][layout]
    if light == "green":
        return
    why = LIGHT_WHY.get((meth, layout)) or LIGHT_WHY[("local", layout)]
    decision[m] += f"; {light} light: {why}"
    if light == "red":
        warnings.append(f"machine {m}: the {METHOD_LABEL[meth]} factor {factor:.4f} is applied on a field harvested "
                        f"in {LAYOUT_LABEL[layout]} (red light): {why}; check it against the field's scale ticket and "
                        "the monitors' calibration")


def light_after_closure(ticket_test: dict | None, applied: dict, decision: dict, info: dict,
                        warnings: list) -> None:
    """The traffic light of a factor the ticket closure test applied (combine_factors() adds it only to the factors
    its own gates applied); applied / decision / warnings are updated in place."""
    if not ticket_test or not ticket_test.get("applied"):
        return
    for m in ticket_test.get("machines") or []:
        meth = (info.get("method_used") or {}).get(m)
        lc = (info.get("layout_class") or {}).get(m)
        if meth and lc:
            _light_note(m, meth, lc["whole" if meth == "machine" else "local"], applied[m], decision, warnings)


def combine_factors(g, alive, y, moi, Mset: dict, width_m=None, warnings: list | None = None,
                    info: dict | None = None):
    """The combine factors of the module docstring (items 9-12): the harvest layout of every pair of combines, the four
    methods for every combine (paired cells = harmonise(), machine x machine, track x track, point x point) and the
    factor of the method machines.method chooses ("auto": the paired cells where the combines met on interleaved or
    mixed passes, no factor where they harvested in blocks or never met). width_m: the header width of every record
    (m; D = ADJ_WIDTH_MULT x its median). Returns (tests, applied, decision, reference) as harmonise(): tests are the
    tests of the method used (the 30 m and 50 m cells tests, or the one test of another method); info receives what
    harmonise() gives plus the layout, the method used, the traffic light and the comparison of the methods."""
    warnings = [] if warnings is None else warnings
    info = {} if info is None else info
    P = {**DEFAULTS, **(Mset or {})}
    req = str(P.get("method") or "auto").strip().lower()
    if req not in FACTOR_METHODS:
        raise ValueError(f"machines.method must be one of {list(FACTOR_METHODS)}, not {P.get('method')!r}")
    wc = []
    tests_c, applied_c, decision_c, ref = harmonise(g, alive, y, moi, P, warnings=wc, info=info)
    machines_ = sorted(g["_machine"].astype(str).unique())
    others = [m for m in machines_ if m != ref]
    kk = _frame(g, alive, y, moi)
    w = np.asarray(width_m, float).ravel() if width_m is not None else np.array([], float)
    w = w[np.isfinite(w) & (w > 0)]
    D = ADJ_WIDTH_MULT * (float(np.median(w)) if len(w) else DEFAULT_WIDTH_M)
    pairs = [(ref, m) for m in others] + [(a, b) for i, a in enumerate(others) for b in others[i + 1:]]
    lay = {p: layout_pair(kk, p[0], p[1], D) for p in pairs}
    cls = {}
    for m in others:
        best = max((lay[p] for p in pairs if m in p), key=lambda r: r["adjacency"])
        cls[m] = {"local": best["layout"], "adjacency_local": best["adjacency"],
                  "partner": best["b"] if best["a"] == m else best["a"],
                  "whole": lay[(ref, m)]["layout"], "adjacency_ref": lay[(ref, m)]["adjacency"]}
    alt = _other_methods(kk, ref, machines_, P, D)
    c30 = {t["machine"]: t for t in tests_c if t.get("cell_m") == CELL_SIZES_M[0]}

    def side(meth):
        return "whole" if meth == "machine" else "local"

    rows = []
    for m in others:
        for meth in ("cells", "machine", "track", "point"):
            t = c30[m] if meth == "cells" else alt[meth][m]
            rows.append({"machine": m, "method": meth, "label": METHOD_LABEL[meth], "factor": t.get("factor"),
                         "ci_lo": t.get("ci_lo"), "ci_hi": t.get("ci_hi"),
                         "n_units": t.get("n_cells") if meth == "cells" else t.get("n_units"),
                         "unit": "cells (30 m)" if meth == "cells" else t.get("unit"),
                         "n_pass_pairs": t.get("n_pass_pairs"), "estimate": t.get("estimate"), "gate": t.get("gate"),
                         "would_apply": t.get("gate") == "applied", "layout": cls[m][side(meth)],
                         "light": LIGHTS[meth][cls[m][side(meth)]], "used": False})
    used, tests, applied, decision = {}, [], {ref: 1.0}, {ref: "reference"}
    out_w = [w_ for w_ in wc if not any(w_.startswith(f"machine {m}:") for m in others)]
    for m in others:
        c = cls[m]
        meth = ("cells" if c["local"] in ("interleaved", "mixed") else None) if req == "auto" else req
        used[m] = meth
        if meth is None:
            applied[m] = 1.0
            why = (f"{m} never harvested side by side with another combine (no pass within {D:.0f} m of one)"
                   if c["local"] == "no_contact" else
                   f"the combines harvested in blocks (adjacency of {m}'s passes {100 * c['adjacency_local']:.0f} %, "
                   f"below {100 * LAYOUT_BLOCKS:.0f} %)")
            decision[m] = (f"not applied: {why}; no method gives a reliable factor for this layout, the field's scale "
                           "ticket levels the whole field (machines.method = machine applies the whole-field ratio, at "
                           "your own risk)")
            sig = [r for r in rows if r["machine"] == m and r["factor"] is not None and r["ci_lo"] is not None
                   and (r["ci_lo"] > 1 or r["ci_hi"] < 1) and abs(r["factor"] - 1) > P["min_factor"]]
            if sig:
                out_w.append(f"machine {m}: {why}, so no combine factor is applied; the methods still read a "
                             f"difference against {ref} (" + ", ".join(f"{r['label']} {r['factor']:.4f}" for r in sig)
                             + "): only the field's scale ticket levels the yield; check the monitors' calibration")
            for t in tests_c:
                if t["machine"] == m:
                    t["gate_cells"], t["gate"] = t.get("gate"), "layout"
                    tests.append(t)
            continue
        if meth == "cells":
            applied[m], decision[m] = applied_c[m], decision_c[m]
            tests += [t for t in tests_c if t["machine"] == m]
            out_w += [w_ for w_ in wc if w_.startswith(f"machine {m}:")]
            ok = c30[m].get("gate") == "applied"
        else:
            t = alt[meth][m]
            ok = t["gate"] == "applied"
            applied[m] = float(t["factor"]) if ok else 1.0
            decision[m] = t["decision"]
            tests.append(t)
            if t.get("warning"):
                out_w.append(t["warning"])
        if ok:
            _light_note(m, meth, c[side(meth)], applied[m], decision, out_w)
    warnings.extend(out_w)
    applied, decision = {m: applied[m] for m in machines_}, {m: decision[m] for m in machines_}
    for r in rows:
        r["used"] = used.get(r["machine"]) == r["method"]
    info.update(method_requested=req, method_used=used, D_m=float(D), layout=[lay[p] for p in pairs],
                layout_class=cls, light={m: (LIGHTS[used[m]][cls[m][side(used[m])]] if used[m] else None)
                                         for m in others}, methods=rows)
    return tests, applied, decision, ref


def unconfirmed_reason(e30: dict, e50: dict | None = None) -> str | None:
    """Why a paired factor above max_factor is NOT a candidate for the ticket closure test: the CI includes 1 at one
    of the cell sizes, the 30 m and 50 m factors disagree by more than HARM_AGREE, or the factor differs over the
    field (spatial_check). None when it is a candidate (significant at both sizes, consistent). e50 None: a method
    with one estimate (machine, track or point x point: its CI and the spatial check only)."""
    if e50 is None:
        if not e30.get("excludes_one"):
            return "the bootstrap CI includes 1"
    else:
        not_sig = [s for s, e in ((CELL_SIZES_M[0], e30), (CELL_SIZES_M[1], e50)) if not e.get("excludes_one")]
        if not_sig:
            return "the bootstrap CI includes 1 at " + " and ".join(f"{s} m" for s in not_sig)
        f30, f50 = e30.get("factor"), e50.get("factor")
        if f30 and f50 and abs(f30 / f50 - 1) > HARM_AGREE:
            return f"the 30 m and 50 m factors disagree ({f30:.4f} vs {f50:.4f}, > {HARM_AGREE:.0%})"
    sp = e30.get("spatial") or {}
    if sp.get("consistent") is False:
        return (f"the factor differs over the field (halves {sp['factor_half_1']:.4f} and {sp['factor_half_2']:.4f}, "
                f"{sp['diff_pct']:+.2f} %)")
    return None


def ticket_closure(tests: list, applied: dict, decision: dict, ref: str, Mset: dict | None, integrals: dict,
                   ticket_bu: float | None, warnings: list | None = None, notes: list | None = None,
                   validate_only: bool = False) -> dict | None:
    """The ticket closure test of the module docstring (item 8). tests / applied / decision / ref: the output of
    harmonise(); integrals: {machine: monitor total in bu of its processed readings, on the ticket's basis};
    ticket_bu: the field's scale ticket in bu on the same basis. Candidates are the machines whose 30 m test carries
    gate == "above_max_factor". The candidates are tried together and, when that does not close, one at a time.
    When the total with the factor(s) applied is within ticket_close_tol of the ticket while the total without them is
    more than TICKET_OPEN_MULT x that away, `applied` and `decision` are updated IN PLACE, the "request per-machine
    weights" warning of the confirmed machine(s) is withdrawn and a note is added. Returns the ticket_test dict
    {ticket_bu, tolerance, candidates, without: {total_bu, diff_pct}, with: {total_bu, diff_pct}, applied, machines,
    rule, verdict, validate_only, would_apply, why}, or None when there is no ticket or no monitor total.

    validate_only (the grower's weight validates the monitor and must change no yield: settings scale_ticket.use
    'validate'): the test is computed and reported the same way, but `applied` is left as it is; a factor the weight
    would have confirmed is named in would_apply, the verdict and the decision, and stays unapplied. Every primary
    test gets its outcome in test['ticket']: 'confirmed', 'would_confirm' (validate only), 'not_confirmed' or
    'not_a_candidate'."""
    warnings = [] if warnings is None else warnings
    notes = [] if notes is None else notes
    P = {**DEFAULTS, **(Mset or {})}
    tol = float(P["ticket_close_tol"])
    if not ticket_bu or ticket_bu <= 0 or not integrals:
        return None
    integ = {str(m): float(v or 0.0) for m, v in integrals.items()}
    f30 = primary_tests(tests)                   # the 30 m cells test, or the one test of another method
    f50 = {t["machine"]: t for t in tests if t.get("cell_m") == CELL_SIZES_M[1]}
    cands = sorted(m for m, t in f30.items() if t.get("gate") == "above_max_factor" and t.get("factor"))

    def total(extra):
        return float(sum(integ[m] * float(extra.get(m, applied.get(m, 1.0))) for m in integ))

    without = total({})
    if without <= 0:
        return None
    d0 = without / ticket_bu - 1
    rule = (f"a paired factor above max_factor ({100 * P['max_factor']:.0f} %) is applied when the monitor total with it "
            f"is within {100 * tol:g} % of the grower's weight while the total without it is more than "
            f"{100 * TICKET_OPEN_MULT * tol:g} % away; a factor above max_factor whose CI includes 1 at one cell size "
            f"or whose 30 m / 50 m estimates disagree is not a candidate")
    # the factors above max_factor the weight is not allowed to confirm, with the reason (the same the decision gives)
    rejected = {}
    for m, t in sorted(f30.items()):
        if t.get("gate") == "above_max_factor_unconfirmed" and t.get("factor") and m in integ:
            why = (unconfirmed_reason(t, (f50.get(m) or {}) if t.get("method", "cells") == "cells" else None)
                   or "the estimate is not confirmed at both cell sizes")
            w_m = total({m: float(t["factor"])})
            d_m = w_m / ticket_bu - 1
            rejected[m] = {"factor": float(t["factor"]), "reason": why, "with_total_bu": w_m, "with_diff_pct": 100 * d_m,
                           # the weight sits nearer this machine's level than the reference's when applying the
                           # factor (which moves the other machines towards the reference) opens the gap further
                           "weight_nearer_this_machine": bool(abs(d0) > TICKET_OPEN_MULT * tol and abs(d_m) > abs(d0))}
    res = {"ticket_bu": float(ticket_bu), "tolerance": tol, "candidates": cands, "reference": ref,
           "without": {"total_bu": without, "diff_pct": 100 * d0}, "with": None, "applied": False, "machines": [],
           "rejected": rejected, "rule": rule, "verdict": "", "validate_only": bool(validate_only), "would_apply": [],
           "why": None}
    for m in rejected:
        f30[m]["ticket"] = "not_a_candidate"
    if not cands:
        res["with"] = dict(res["without"])
        if rejected:
            parts = [f"candidate {m} rejected: {r['reason']} ({_factor_name(f30[m])} {r['factor']:.4f}; with it the "
                     f"monitor total would be {r['with_diff_pct']:+.2f} % from the weight)" for m, r in rejected.items()]
            hint = [m for m, r in rejected.items() if r["weight_nearer_this_machine"]]
            res["verdict"] = ("no paired factor the grower's weight may confirm: " + "; ".join(parts)
                              + f"; the weight is {100 * (ticket_bu / without - 1):+.2f} % from the monitor total"
                              + (f" and sits nearer {', '.join(hint)}'s level than the reference {ref}'s: try "
                                 f"machines.reference = {hint[0]}" if hint else "")
                              + "; check the monitors' calibration")
        else:
            res["verdict"] = "no paired factor above max_factor to test against the grower's weight"
        return res
    trials = [cands] + ([[m] for m in cands] if len(cands) > 1 else [])
    chosen, with_total = None, None
    for sel in trials:
        w = total({m: f30[m]["factor"] for m in sel})
        d = w / ticket_bu - 1
        if with_total is None:
            with_total, d_all = w, d                 # the joint result is what is reported when nothing closes
        if abs(d) <= tol and abs(d0) > TICKET_OPEN_MULT * tol:
            chosen, with_total, d_all = sel, w, d
            break
    res["with"] = {"total_bu": with_total, "diff_pct": 100 * d_all}
    if chosen and validate_only:
        # the weight would confirm the factor, but it was given to validate the monitor, not to change the yields
        res["would_apply"] = list(chosen)
        res["why"] = (f"the monitor total is {100 * d0:+.2f} % from the grower's weight without the factor and "
                      f"{100 * d_all:+.2f} % with it")
        res["verdict"] = (res["why"] + f": the grower's weight would confirm the paired factor of {', '.join(chosen)}, "
                          "but it is used to validate only, so the factor is NOT applied (adjust the yields to the "
                          "grower's weight, or raise the max factor, to apply it)")
        for m in cands:
            f30[m]["ticket"] = "would_confirm" if m in chosen else "not_confirmed"
        for m in chosen:
            decision[m] += ("; the grower's weight would confirm it (harmonised total within "
                            f"{100 * abs(d_all):.2f} % of the scale) but validates only: not applied")
        notes.append(f"machines: {res['verdict']} (ticket closure test, tolerance {100 * tol:g} %)")
    elif chosen:
        res["applied"], res["machines"] = True, list(chosen)
        for m in cands:
            f30[m]["ticket"] = "confirmed" if m in chosen else "not_confirmed"
        for m in chosen:
            t = f30[m]
            applied[m] = float(t["factor"])
            decision[m] = (f"applied: the paired factor is above max_factor but the grower's weight confirms it "
                           f"(harmonised total within {100 * abs(d_all):.2f} % of the scale); " + estimate_text(t))
            warnings[:] = [w_ for w_ in warnings
                           if not (w_.startswith(f"machine {m}:") and "a relative factor should repair" in w_)]
        res["verdict"] = (f"the monitor total is {100 * d0:+.2f} % from the grower's weight without the factor and "
                          f"{100 * d_all:+.2f} % with it: the paired factor of {', '.join(chosen)} is confirmed and "
                          "applied")
        notes.append(f"machines: {res['verdict']} (ticket closure test, tolerance {100 * tol:g} %)")
    else:
        if abs(d0) <= TICKET_OPEN_MULT * tol:
            why = (f"the monitor total already agrees with the grower's weight ({100 * d0:+.2f} %, within "
                   f"{100 * TICKET_OPEN_MULT * tol:g} %), so the weight cannot confirm a {100 * (1 - f30[cands[0]]['factor']):+.0f} % step")
        else:
            why = (f"with the factor the monitor total is {100 * d_all:+.2f} % from the grower's weight (without it "
                   f"{100 * d0:+.2f} %), not within {100 * tol:g} %")
        res["verdict"] = f"not confirmed: {why}; the factor stays unapplied, check the monitor's calibration"
        res["why"] = why
        for m in cands:
            decision[m] += f"; the grower's weight does not confirm it ({why})"
            f30[m]["ticket"] = "not_confirmed"
    return res


# what stopped a factor, in plain words (the gate keys of harmonise() / _gate_other(); {mx} = max_factor in %,
# {mn} = min_factor in %). 'above_max_factor' is a factor that IS proven (CI excludes 1, the cell sizes agree, one
# factor fits the field) and only too large; 'above_max_factor_unconfirmed' one that is too large and NOT proven.
GATE_PLAIN = {
    "above_max_factor_unconfirmed": "above the max factor ({mx:g} %) and not proven by the passes side by side ({why}): "
                                    "the grower's weight is not used to confirm it",
    "ci_includes_1": "not proven: its {ci:g} % interval includes 1",
    "sizes_disagree": "the 30 m and the 50 m cells disagree",
    "below_min_factor": "below {mn:g} %: nothing to correct",
    "spatially_inconsistent": "one factor does not fit both halves of the field",
    "no_link": "it never harvested side by side with the reference",
    "too_few_pairs": "too few passes side by side with the reference",
    "layout": "the method does not hold for how the combines shared the field (blocks)",
}


def factor_advice(tests: list, applied: dict, Mset: dict | None, ticket_test: dict | None = None,
                  ticket_given: bool = False, ticket_skipped: str | None = None) -> dict:
    """{combine: one plain sentence} for every combine whose factor was estimated and NOT applied: what stopped it and
    what, if anything, would apply it. Written from the gate AND the ticket closure test, so that a factor the scale
    refuted is never followed by 'raise the max factor to apply it'. ticket_given: a grower's weight was given.
    ticket_skipped: why ticket_test is None although a weight was given - 'totals' (per-machine totals,
    machines.totals_bu, level each combine instead) or 'switched_off' (machines.ticket_confirms false)."""
    P = {**DEFAULTS, **(Mset or {})}
    mx, mn, ci = 100 * float(P["max_factor"]), 100 * float(P["min_factor"]), 100 * float(P["ci"])
    f50 = {t["machine"]: t for t in tests if t.get("cell_m") == CELL_SIZES_M[1]}
    out = {}
    for m, t in primary_tests(tests).items():
        if t.get("factor") is None or abs(float(applied.get(m, 1.0)) - 1) > 1e-12:
            continue
        gate = str(t.get("gate") or "")
        if gate == "above_max_factor":
            proven = f"above the max factor ({mx:g} %) but proven by the passes side by side"
            if ticket_test is None and not ticket_given:
                txt = (proven + ": raise the max factor to apply it, or give the grower's weight, which can confirm it")
            elif ticket_test is None and ticket_skipped == "totals":
                txt = (proven + "; per-machine totals (machines.totals_bu) were given, so each combine is leveled to "
                       "its own total and the field weight is not used to confirm a factor: the totals decide")
            elif ticket_test is None:
                txt = (proven + "; the grower's weight was not allowed to confirm it (machines.ticket_confirms off): "
                       "raise the max factor to apply it, or let the weight confirm it")
            elif m in (ticket_test.get("would_apply") or []):
                txt = (proven + f", and the grower's weight would confirm it ({ticket_test.get('why')}); the weight is "
                       "used to validate only, so it is not applied: adjust the yields to the grower's weight, or "
                       "raise the max factor, to apply it")
            elif ticket_test.get("machines") or ticket_test.get("would_apply"):
                # a subset of the candidates closed with the weight: this one was not in it
                ok_ = ", ".join(map(str, ticket_test.get("machines") or ticket_test.get("would_apply")))
                verb = "confirmed" if ticket_test.get("machines") else "would confirm"
                txt = (f"above the max factor ({mx:g} %); the grower's weight {verb} the factor of {ok_} only (the "
                       f"total closes with {'it' if ',' not in ok_ else 'them'} alone), not this one: do not raise the "
                       "max factor on this evidence, check the monitor's calibration")
            else:
                txt = (f"above the max factor ({mx:g} %) and the grower's weight did NOT confirm it "
                       f"({ticket_test.get('why') or ticket_test.get('verdict')}): do not raise the max factor on "
                       "this evidence, check the monitor's calibration")
        elif gate == "above_max_factor_unconfirmed":
            why = (unconfirmed_reason(t, (f50.get(m) or {}) if t.get("method", "cells") == "cells" else None)
                   or "the estimate is not confirmed")
            txt = GATE_PLAIN[gate].format(mx=mx, why=why)
        else:
            txt = GATE_PLAIN.get(gate, gate or "not applied").format(mx=mx, mn=mn, ci=ci)
        out[m] = txt
        t["advice"] = txt
    return out


def gated(test: dict | None, Mset: dict | None = None) -> bool:
    """True when a paired-cell test passes the pair gate (>= min_pairs cells, >= min_pass_pairs pass pairs, a CI):
    only such an estimate is used for a decision or a validation."""
    if not test or test.get("factor") is None or test.get("ci_lo") is None:
        return False
    if test.get("method", "cells") != "cells":          # machine, track or point x point: its own link rule
        return bool(test.get("enough"))
    P = {**DEFAULTS, **(Mset or {})}
    return test["n_cells"] >= int(P["min_pairs"]) and test["n_pass_pairs"] >= int(P["min_pass_pairs"])


def method_text(P: dict) -> str:
    P = {**DEFAULTS, **(P or {})}
    req = str(P.get("method") or "auto").strip().lower()
    ov = P.get("pair_max_overlap")
    ov_txt = (f" with <= {100 * float(ov):g} % of the footprint already cut" if ov is not None else "")
    lay = (f"harvest layout of every pair of combines from the adjacency of their passes (the share of passes with >= "
           f"{ADJ_MIN_POINTS} kept points within {ADJ_WIDTH_MULT:g} x the header width of the other combine): >= "
           f"{100 * LAYOUT_INTERLEAVED:.0f} % interleaved, {100 * LAYOUT_BLOCKS:.0f}-{100 * LAYOUT_INTERLEAVED:.0f} % "
           f"mixed, < {100 * LAYOUT_BLOCKS:.0f} % blocks; ")
    ticket = (f"; a significant, consistent factor above {100 * P['max_factor']:g} % is still applied when the grower's "
              f"weight confirms it (monitor total with the factor within {100 * float(P['ticket_close_tol']):g} % of "
              f"the scale, without it more than {100 * TICKET_OPEN_MULT * float(P['ticket_close_tol']):g} % away)"
              if P.get("ticket_confirms", True) else "")
    gates = (f"applied only if the {int(100 * P['ci'])} % CI excludes 1 and {100 * P['min_factor']:g} % < |factor - 1| "
             f"<= {100 * P['max_factor']:g} %")
    spatial = (f", and one factor fits the whole field (two halves within {100 * SPATIAL_AGREE:g} % or not "
               "significantly different)" if P.get("spatial_check", True) else "")
    net = (", every pair of combines that harvested side by side solved together (weighted least squares)"
           if P.get("network", True) else "")
    if req == "machine":
        return (lay + "method machine x machine (chosen in the settings; red light in blocks): the mean of every kept "
                "point of the reference / the mean of every kept point of the combine (Maldaner et al. 2016, eq. 1), "
                f"CI by resampling the passes of each combine (>= {P['min_pass_pairs']} passes each); " + gates + ticket)
    if req in ("track", "point"):
        unit = (f"adjacent pass pairs with >= {TRACK_MIN_POINTS} matched points on each side, the ratio of the pass "
                "means over their common edge" if req == "track" else
                "every point of the combine and the nearest point of the other combine, the ratio of the two yields")
        return (lay + f"method {METHOD_LABEL[req]} (chosen in the settings): kept points{ov_txt} (same plot when a plan "
                f"exists) matched within {ADJ_WIDTH_MULT:g} x the header width; {unit}; geometric mean, CI by block "
                f"bootstrap over pass pairs ({P['bootstrap_reps']} reps, >= {P['min_pass_pairs']} pass pairs){net}; "
                + gates + spatial + ticket)
    sel = ("method auto: the paired cells where the combines met on interleaved or mixed passes, no factor where they "
           "harvested in blocks or never met; " if req == "auto" else "method paired cells (chosen in the settings); ")
    return lay + sel + _cells_text(P, ov_txt)


def _cells_text(P: dict, ov_txt: str) -> str:
    return (f"geometric-mean ratio between two machines over paired {CELL_SIZES_M[0]} m and {CELL_SIZES_M[1]} m cells "
            f"with >= {MIN_POINTS_PER_CELL} kept points{ov_txt} of both (same plot when a plan exists), CI by block "
            f"bootstrap over pass pairs ({P['bootstrap_reps']} reps); "
            + ("every pair of machines that harvested side by side (>= "
               f"{P['min_pairs']} cells and >= {P['min_pass_pairs']} pass pairs at both sizes) enters one weighted "
               "least-squares solution of the log factors with the reference at 1, so a machine is linked to the "
               "reference directly or through its neighbours; " if P.get("network", True) else
               f"each machine against the reference (>= {P['min_pairs']} cells and >= {P['min_pass_pairs']} pass pairs "
               "at both sizes); ")
            + f"the 30 m factor is applied only if both {int(100 * P['ci'])} % CIs exclude 1, the two factors agree "
            f"within {HARM_AGREE:.0%} and {100 * P['min_factor']:g} % < |factor - 1| <= {100 * P['max_factor']:g} %"
            + (f", and one factor fits the whole field (the two halves of the machine's contact cells within "
               f"{100 * SPATIAL_AGREE:g} % or not significantly different)" if P.get("spatial_check", True) else "")
            + "; paired t-test p and moisture difference are diagnostics"
            + (f"; a significant, consistent factor above {100 * P['max_factor']:g} % is still applied when the grower's "
               f"weight confirms it (monitor total with the factor within {100 * float(P['ticket_close_tol']):g} % of the "
               f"scale, without it more than {100 * TICKET_OPEN_MULT * float(P['ticket_close_tol']):g} % away)"
               if P.get("ticket_confirms", True) else ""))


# ===================================================================================================== seams
SEAM_BLOCK_M = 50.0                      # seams (item 13): every seam is cut into stretches of 50 m along it ...
SEAM_MIN_POINTS = 10                     # ... a pass pair is a seam with >= 10 linked points on each side ...
SEAM_BLOCK_MIN_POINTS = 5                # ... and a stretch counts with >= 5 points on each side
SEAM_ACROSS_COS = 0.5                    # a link runs across the track: >= 60 deg from the point's heading
SEAM_K = 64                              # the linked point is searched among the 64 nearest points ...
SEAM_CHUNK = 20000                       # ... for 20,000 points at a time (about 80 MB)
SEAM_CLASSES_PCT = (5, 10, 15, 20)       # the classes of the seam map and of its bars (%)
SEAM_COLUMNS = ["lo", "hi", "ma", "mb", "na", "nb", "lr_before", "lr_after", "x", "y", "line"]


def seam_links(x, y, pas, hdg, D: float) -> np.ndarray:
    """The linked point of every point (module docstring, item 13): the index of the nearest point of ANOTHER pass
    within D m whose direction from the point is >= 60 deg from the point's heading (across the track: the pass
    beside it, not the next pass along the same line), -1 when there is none. A point without a heading takes the
    nearest point of another pass; a point without a position is never linked."""
    x, y, pas = np.asarray(x, float), np.asarray(y, float), np.asarray(pas)
    n = len(x)
    j = np.full(n, -1, np.int64)
    pos = np.where(np.isfinite(x) & np.isfinite(y))[0]
    if len(pos) < 2:
        return j
    xs, ys, ps = x[pos], y[pos], pas[pos]
    h = np.radians(np.asarray(hdg, float)[pos])
    hx, hy = np.sin(h), np.cos(h)
    X = np.c_[xs, ys]
    tree = cKDTree(X)
    k = min(SEAM_K, len(pos))
    for s in range(0, len(pos), SEAM_CHUNK):
        e = min(len(pos), s + SEAM_CHUNK)
        dd, ii = tree.query(X[s:e], k=k, distance_upper_bound=float(D))
        fin = np.isfinite(dd)
        ii = np.where(fin, ii, 0)
        vx, vy = xs[ii] - xs[s:e, None], ys[ii] - ys[s:e, None]
        dist = np.hypot(vx, vy)
        with np.errstate(invalid="ignore", divide="ignore"):
            along = np.abs(vx * hx[s:e, None] + vy * hy[s:e, None]) / dist
        across = np.where(np.isfinite(hx[s:e])[:, None], np.nan_to_num(along, nan=1.0) <= SEAM_ACROSS_COS, True)
        ok = fin & (ps[ii] != ps[s:e, None]) & across & (dist > 0)
        first = ok.argmax(axis=1)
        jj = np.where(ok.any(axis=1), ii[np.arange(e - s), first], -1)
        j[pos[s:e]] = np.where(jj >= 0, pos[np.maximum(jj, 0)], -1)
    return j


def seam_blocks(x, y, pas, mach, v_before, v_after, hdg, D: float, block_m: float = SEAM_BLOCK_M) -> pd.DataFrame:
    """The stretches of every seam between neighbouring passes (module docstring, item 13), one row each: lo / hi (the
    two pass ids, lo < hi), ma / mb (their combines), na / nb (points of each side), lr_before / lr_after = log(mean of
    the lo-side points / mean of the hi-side points) on v_before (the yield as mapped) and v_after (Yld_Harm), x / y
    the centre of the stretch and line the (k, 2) centre line between the two passes (for the map). The arrays are the
    kept points in record order, with their final position (m) and heading (deg)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    pas, mach = np.asarray(pas), np.asarray(mach).astype(str)
    vb, va = np.asarray(v_before, float), np.asarray(v_after, float)
    j = seam_links(x, y, pas, hdg, D)
    i = np.where(j >= 0)[0]
    if len(i) == 0:
        return pd.DataFrame(columns=SEAM_COLUMNS)
    jj = j[i]
    pi, pj = pas[i], pas[jj]
    lo, hi = np.minimum(pi, pj), np.maximum(pi, pj)
    d = pd.DataFrame({"pt": i, "nb": jj, "lo": lo, "hi": hi, "side_lo": pi == lo})
    cnt = d.groupby(["lo", "hi"])["side_lo"].agg(["sum", "size"])
    good = cnt[(cnt["sum"] >= SEAM_MIN_POINTS) & (cnt["size"] - cnt["sum"] >= SEAM_MIN_POINTS)].index
    d = d[pd.MultiIndex.from_arrays([d["lo"], d["hi"]]).isin(good)]
    rows = []
    for (lo_, hi_), s in d.groupby(["lo", "hi"], sort=True):
        A = s[s["side_lo"]].sort_values("pt")                  # the lo-side points in record order along their pass
        Bp = s.loc[~s["side_lo"], "pt"].to_numpy()
        ap, an = A["pt"].to_numpy(), A["nb"].to_numpy()
        cum = np.r_[0.0, np.cumsum(np.hypot(np.diff(x[ap]), np.diff(y[ap])))]
        blk_a = (cum // float(block_m)).astype(np.int64)
        _, q = cKDTree(np.c_[x[ap], y[ap]]).query(np.c_[x[Bp], y[Bp]])
        blk_b = blk_a[q]                                       # a hi-side point goes to the stretch of its nearest point
        ma, mb = mach[ap[0]], mach[Bp[0]]
        for b in np.unique(blk_a):
            sa, sb = ap[blk_a == b], Bp[blk_b == b]
            if len(sa) < SEAM_BLOCK_MIN_POINTS or len(sb) < SEAM_BLOCK_MIN_POINTS:
                continue
            m_ab, m_bb, m_aa, m_ba = np.nanmean(vb[sa]), np.nanmean(vb[sb]), np.nanmean(va[sa]), np.nanmean(va[sb])
            if not (m_ab > 0 and m_bb > 0 and m_aa > 0 and m_ba > 0):
                continue
            nb_ = an[blk_a == b]
            line = np.c_[(x[sa] + x[nb_]) / 2, (y[sa] + y[nb_]) / 2]
            rows.append({"lo": lo_, "hi": hi_, "ma": ma, "mb": mb, "na": int(len(sa)), "nb": int(len(sb)),
                         "lr_before": float(np.log(m_ab / m_bb)), "lr_after": float(np.log(m_aa / m_ba)),
                         "x": float(line[:, 0].mean()), "y": float(line[:, 1].mean()), "line": line})
    return pd.DataFrame(rows, columns=SEAM_COLUMNS)


def seam_pct(lr) -> np.ndarray:
    """The difference of a stretch: how much its higher side reads above its lower side, 100 (exp|step| - 1) %."""
    return 100 * (np.exp(np.abs(np.asarray(lr, float))) - 1)


def seam_class_shares(lr) -> list:
    """The share (%) of the stretches in each class of SEAM_CLASSES_PCT (< 5, 5-10, 10-15, 15-20, >= 20 %)."""
    p = seam_pct(lr)
    if not len(p):
        return []
    k = np.searchsorted(np.asarray(SEAM_CLASSES_PCT, float), p, side="right")
    return [float(100 * np.mean(k == c)) for c in range(len(SEAM_CLASSES_PCT) + 1)]


def _seam_stats(B: pd.DataFrame, col: str) -> dict:
    p = seam_pct(B[col]) if len(B) else np.array([])
    return {"stretches": int(len(B)), "pass_pairs": int(len(B[["lo", "hi"]].drop_duplicates())) if len(B) else 0,
            "median_pct": float(np.median(p)) if len(p) else None,
            "share_above_10_pct": float(100 * np.mean(p > 10)) if len(p) else None,
            "class_shares_pct": seam_class_shares(B[col]) if len(B) else []}


def seam_summary(B: pd.DataFrame, D: float, factors: dict | None = None, min_pass_pairs: int = 5,
                 block_m: float = SEAM_BLOCK_M) -> dict:
    """R['machines']['seams'] (module docstring, item 13): the stretches between passes of the same combine and of
    different combines (before and after the factor), per pair of combines, and the plain-language reading. factors =
    the factor applied to every combine (what tells whether an 'after' differs from the 'before')."""
    B = B if B is not None else pd.DataFrame(columns=SEAM_COLUMNS)
    cross = (B["ma"].astype(str) != B["mb"].astype(str)).to_numpy() if len(B) else np.zeros(0, bool)
    same, diff = B[~cross], B[cross]
    fac = {str(k): float(v) for k, v in (factors or {}).items()}
    moved = bool(len(diff)) and any(abs(fac.get(str(m), 1.0) - 1) > 1e-12 for m in set(diff["ma"]) | set(diff["mb"]))
    pairs = []
    if len(diff):
        sw = (diff["ma"].astype(str) > diff["mb"].astype(str)).to_numpy()
        a = np.where(sw, diff["mb"], diff["ma"]).astype(str)
        b = np.where(sw, diff["ma"], diff["mb"]).astype(str)
        sb_ = np.where(sw, -diff["lr_before"], diff["lr_before"])          # oriented a over b
        sa_ = np.where(sw, -diff["lr_after"], diff["lr_after"])
        t = pd.DataFrame({"a": a, "b": b, "lo": diff["lo"].to_numpy(), "hi": diff["hi"].to_numpy(),
                          "sb": sb_, "sa": sa_})
        for (a_, b_), s in t.groupby(["a", "b"], sort=True):
            pairs.append({"a": a_, "b": b_, "stretches": int(len(s)),
                          "pass_pairs": int(len(s[["lo", "hi"]].drop_duplicates())),
                          "median_pct_before": float(np.median(seam_pct(s["sb"]))),
                          "median_pct_after": float(np.median(seam_pct(s["sa"]))),
                          "a_over_b_pct_before": float(100 * (np.exp(np.median(s["sb"])) - 1)),
                          "a_over_b_pct_after": float(100 * (np.exp(np.median(s["sa"])) - 1))})
    out = {"D_m": float(D), "stretch_m": float(block_m), "min_points_seam": SEAM_MIN_POINTS,
           "min_points_stretch": SEAM_BLOCK_MIN_POINTS, "classes_pct": list(SEAM_CLASSES_PCT),
           "same_combine": _seam_stats(same, "lr_before"),
           "different_combines": {"before": _seam_stats(diff, "lr_before"), "after": _seam_stats(diff, "lr_after"),
                                  "factor_moved_them": moved},
           "pairs": pairs,
           "rule": (f"every kept point linked to the nearest point of another pass within {D:.0f} m across the track; "
                    f"a pass pair with >= {SEAM_MIN_POINTS} linked points on each side is a seam, cut into "
                    f"{block_m:g} m stretches with >= {SEAM_BLOCK_MIN_POINTS} points on each side; difference = how "
                    "much the higher side of a stretch reads above the lower side; a diagnostic of the map, never "
                    "applied")}
    out["text"] = seam_text(out, min_pass_pairs)
    return out


def seam_text(S: dict, min_pass_pairs: int = 5) -> str:
    """The plain-language reading of the seam diagnostic (seam_summary)."""
    sc, db, da = S["same_combine"], S["different_combines"]["before"], S["different_combines"]["after"]
    L = f"{S['stretch_m']:g} m"
    same_t = (f"neighbouring passes of the SAME combine differ by a median {sc['median_pct']:.2f} % ({sc['stretches']:,} "
              f"stretches of {L})" if sc["stretches"] else "no two passes of the same combine are neighbours")
    if not db["stretches"]:
        return ("The combines never harvested neighbouring passes, so this map has no seam between combines; "
                + same_t + ".")
    moved = S["different_combines"].get("factor_moved_them")
    npp = db["pass_pairs"]
    diff_t = (f"neighbouring passes of DIFFERENT combines differ by a median {db['median_pct']:.2f} % "
              f"({db['stretches']:,} stretch{'es' if db['stretches'] != 1 else ''} of {L} along {npp} pass "
              f"pair{'s' if npp != 1 else ''}"
              + (f") before the combine factor and {da['median_pct']:.2f} % after it" if moved else
                 "; no combine factor applied)"))
    txt = diff_t[0].upper() + diff_t[1:] + "; " + same_t + ". "
    if not sc["stretches"]:
        return txt + "With no seam inside one combine there is nothing to compare the seams between combines with."
    final = da["median_pct"] if moved else db["median_pct"]
    if final <= sc["median_pct"]:
        txt += ("The seams between the combines are no larger than the field's own pass-to-pass steps: the stripes of "
                "this map are not a difference between the machines, and a factor cannot remove them (they come from "
                "the ground, the direction of travel, the overlap and the flow delay, and are in the passes of one "
                "combine too).")
    else:
        txt += (f"The seams between the combines are larger than the field's own pass-to-pass steps by "
                f"{final - sc['median_pct']:.2f} points of %. A factor is one number per combine: it removes only the "
                "part of the difference that is the same on every seam (a calibration difference), and only when "
                "the side-by-side passes prove it (the gates above).")
        if db["pass_pairs"] < int(min_pass_pairs):
            txt += (f" Only {npp} pass pair{'s' if npp != 1 else ''} of different combines "
                    f"{'are neighbours' if npp != 1 else 'is a neighbour'}: too few to tell a machine difference from "
                    "the ground under those passes.")
    return txt


# ===================================================================================================== totals
def per_machine_levels(totals: dict, means: dict, ref: str, warnings: list) -> dict | None:
    """L_m = ticket_m / wet integral_m per machine from machines.totals_bu. Machines without a ticket get the
    reference's L (warning). Returns {machine: {ticket_bu, integral_bu, L, relative_to_ref}} or None when the
    reference has no usable integral."""
    out = {}
    tk = {str(k): float(v) for k, v in (totals or {}).items()}
    unknown = sorted(set(tk) - set(means))
    if unknown:
        warnings.append(f"machines.totals_bu names machine(s) {unknown} that are not in the machine column "
                        f"({sorted(means)}): ignored")
    for m, mm in means.items():
        integ = mm.get("wet_bu")
        if m in tk and integ and integ > 0:
            out[m] = {"ticket_bu": tk[m], "integral_bu": integ, "L": tk[m] / integ}
        else:
            out[m] = {"ticket_bu": tk.get(m), "integral_bu": integ, "L": None}
    if out.get(ref, {}).get("L") is None:
        warnings.append(f"no usable scale ticket for the reference machine {ref!r} in machines.totals_bu: per-machine "
                        "leveling not possible")
        return None
    Lref = out[ref]["L"]
    for m, d in out.items():
        if d["L"] is None:
            d["L"] = Lref
            d["note"] = "no ticket for this machine: the reference's L is used"
            warnings.append(f"machine {m}: no entry in machines.totals_bu; leveled with the reference's L ({Lref:.4f})")
        d["relative_to_ref"] = d["L"] / Lref
    return out


def validate_totals(levels: dict, tests: list, ref: str, Mset: dict | None = None) -> dict:
    """Agreement between the per-ticket relative level (L_m / L_ref) and the paired-cell factor at 30 m:
    agreement = (L_m / L_ref) / f_paired - 1 per machine, with whether the ticket ratio falls inside the paired CI.
    Only a GATED paired estimate (gated(): >= min_pairs cells, >= min_pass_pairs pass pairs, a bootstrap CI) is used;
    otherwise paired_factor / agreement / inside_paired_ci are None and 'note' says why (an estimate the method itself
    calls unusable must not read as a disagreement with a correct ticket)."""
    out = {}
    P = {**DEFAULTS, **(Mset or {})}
    for m, d in levels.items():
        if m == ref:
            continue
        t30 = primary_tests(tests).get(m)
        if not gated(t30, Mset):
            why = ("no paired-cell test" if t30 is None or t30.get("factor") is None else
                   f"too few pairs ({t30['n_cells']} cells / {t30['n_pass_pairs']} pass pairs at 30 m; need >= "
                   f"{int(P['min_pairs'])} and >= {int(P['min_pass_pairs'])})"
                   if t30.get("method", "cells") == "cells" else
                   f"too few pairs ({units_text(t30)}; need >= {int(P['min_pass_pairs'])})")
            out[m] = {"ticket_ratio": d["relative_to_ref"], "paired_factor": None, "paired_ci": None, "agreement": None,
                      "inside_paired_ci": None, "note": f"no paired estimate: {why}",
                      "paired_factor_ungated": (t30.get("factor") if t30 else None)}
            continue
        agree = d["relative_to_ref"] / t30["factor"] - 1
        inside = t30["ci_lo"] <= d["relative_to_ref"] <= t30["ci_hi"]
        out[m] = {"ticket_ratio": d["relative_to_ref"], "paired_factor": t30["factor"], "paired_ci": [t30["ci_lo"],
                  t30["ci_hi"]], "agreement": float(agree), "inside_paired_ci": bool(inside)}
    return out
