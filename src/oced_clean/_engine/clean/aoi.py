# -*- coding: utf-8 -*-
"""Field of interest (AOI) and trial plan for the cleaning: read the polygon file, union it, tag the plots, clip.

    from oced_clean._engine.clean import aoi
    A = aoi.read_plan("design.geojson", id_col=None, rate_col=None)
    inside = aoi.inside(A, x_utm, y_utm, utm_crs, buffer_m=50.0)        # bool per point
    plot_id, tgt_rate = aoi.tag_plots(A, x_utm, y_utm, utm_crs)           # "" / NaN outside the plots

Why a pre-step and not a filter. A grower's export often holds more than the field of the trial: the combine logs
while it crosses a road, finishes a neighbouring quarter or unloads at the yard, and a whole task of an export can
lie outside the field's boundary (another piece of ground harvested the same day). Those records would enter the
cascade, the F8 dose-response, the F10
mean +/- k SD range and the scale-ticket integral of a field they do not belong to. The cleaning therefore (1) keeps
only the records inside the AOI buffered by aoi.buffer_m (50 m, house rule) BEFORE the cascade, logged as a pre-step
("AOI"), and (2) when aoi.clip_output is true, writes into the clean file only the kept records inside the AOI
itself (0 m). The buffer is kept for processing because the pass structure, the overlap raster, the flow-delay scan and
F7 need the headland context: a pass that leaves the polygon and comes back must stay one pass, and the ground cut
just outside the polygon still overlaps the records just inside it. Menegatti & Molin (2004) delete the points
outside the field boundary first and Owen et al. (2024) remove out-of-boundary blocks before automated cleaning; the
50 m buffer is a house convention (the F7 pass-end rule and the F5 short-pass rule act on the
records near the polygon edge, so the edge itself does not create a new pass end).

Plan or several fields. A file with more than one polygon is read as a TRIAL PLAN (every polygon a plot) only when
it looks like one (house rule, `is_plan_like`): it has a rate column (the first column whose name starts with 'tgt',
or 'rate', 'Rate_l', 'Tgt_Rate_l'), or an explicit plot column ('plot', 'Plot_Id'), or a polygon typed as headland /
border, or at least MIN_PLAN_POLYGONS (4) polygons of similar size (the largest, the headland excepted, below
HEADLAND_RATIO (5) x the median). Otherwise the polygons are SEVERAL FIELD BOUNDARIES (a boundary file of the farm,
e.g. several quarters of one farm): no Plot_Id is ever written from such a file, and the cleaning needs to know which
field is the field of interest. With `feature` (1-based index of the polygon, or a list of indices; settings
aoi.feature, CLI --aoi-feature 2 or 1,2,3, the app's selector) those polygons are the AOI, their union when there are
several (one field drawn as separate polygons: a field split by a road or a creek, or a quarter drawn in parts);
without it the Plan is returned with `needs_choice` True, `fields` listing the polygons (index, name, area) and a
warning, and the cleaning refuses to run on it (engine/clean/yield_clean.py), so the user picks the field instead of a
silent union of fields. A single feature that is a MultiPolygon (one field in several parts) is one polygon and needs
no choice; nor do polygons that all carry the same field name (one field drawn as several polygons, as a boundary
exported per ring) when they are the parts of one field: no two overlap (one inside another included) and each lies
within SAME_NAME_GAP_M of another. Their union is then the field, as SMS clips a field to every ring of its boundary.
Same-name polygons that overlap or lie farther apart, or whose name is one a drawing tool gives every new polygon
(GENERIC_NAMES, e.g. Google Earth's 'Untitled Polygon'), still ask for the choice.

Plan details. Every polygon is a plot, except the polygons whose "type" / "Type" property matches aoi.headland_types
(default "headland", "border"), or the single polygon without a type (blank, null or no type column) larger than
HEADLAND_RATIO x the median area of the other polygons (house rule: a headland polygon is many times the plot
median; a design exported with the plots typed 'trial' and the border left blank is a common export). id_col (plot id)
is detected as 'plot', 'Plot_Id', 'id', 'Obj__Id' (case-insensitive), else the row index 1..n; rate_col as above.
Each record is tagged by point-in-polygon with Plot_Id (C 16) and, when a rate column exists, Tgt_Rate (N 12.3). The
F8 dose-response is then measured on the in-plot records only (interior ground, Section 4.9 of the literature review:
overlap records are also turn, headland and width-transient records), and the machine harmonisation pairs cells of
the same plot (same treatment, Section 4.14).

Formats: .shp (with .shx/.dbf/.prj beside it), .zip holding a shapefile or any readable vector file (the polygon
layer is preferred when the zip also holds lines or points, e.g. an SMS 'Boundaries + Guidance' export), .geojson /
.json, .gpkg, .kml, .kmz; any CRS (a file without one is taken as WGS 84 with a warning). Geometries other than
(Multi)Polygon are dropped with a warning; an empty file raises ValueError.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import zipfile

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

AC_PER_M2 = 1 / 4046.8564224
ID_ALIASES = ["plot", "plot_id", "id", "obj__id", "objectid", "obj_id", "fid"]
PLOT_ALIASES = ["plot", "plot_id"]   # an explicit plot column makes a multi-polygon file a plan (a generic id does not)
RATE_ALIASES = ["rate", "rate_l", "tgt_rate_l", "tgt_rate", "target", "target_rate"]
NAME_ALIASES = ["name", "field", "field_name", "fieldname", "label", "title", "descriptio", "description"]
HEADLAND_RATIO = 5.0                 # the single untyped polygon > 5 x the median area of the others is the headland
MIN_PLAN_POLYGONS = 4                # without a rate / plot column or a typed headland: >= 4 similar polygons = plan
PLOT_ID_WIDTH = 16                   # dbf width of the Plot_Id column
VECTOR_EXT = (".shp", ".geojson", ".json", ".gpkg", ".kml", ".kmz", ".gml", ".fgb")
BLANK_TYPES = {"", "nan", "none", "null"}
SAME_NAME_GAP_M = 100.0              # same-name polygons are one field in parts when each is within this of another
GENERIC_NAMES = {"untitled polygon", "polygon", "untitled"}   # names a drawing tool gives every new polygon


def feature_list(feature) -> list | None:
    """The polygons chosen as the field of interest as a sorted list of 1-based indices: from an int, a list / tuple of
    ints or a text such as '1,2,3' or '2'; None when nothing is chosen. Raises ValueError for anything else."""
    if feature is None or (isinstance(feature, str) and not feature.strip()):
        return None
    items = feature if isinstance(feature, (list, tuple, set)) else str(feature).replace(";", ",").split(",")
    out = []
    for v in items:
        if isinstance(v, bool):
            raise ValueError(f"aoi.feature must be the 1-based index of a polygon of the file (or a list of them), not {feature!r}")
        try:
            f = float(str(v).strip())
        except ValueError:
            raise ValueError(f"aoi.feature must be the 1-based index of a polygon of the file (or a list of them), not "
                             f"{feature!r}") from None
        if f != int(f):
            raise ValueError(f"aoi.feature must hold whole polygon numbers, not {feature!r}")
        out.append(int(f))
    if not out:
        return None
    return sorted(set(out))


def feature_setting(feature):
    """aoi.feature as it is written in the settings and the JSON: an int for one polygon (as before), a list for several,
    None for none."""
    fl = feature_list(feature)
    return None if fl is None else (fl[0] if len(fl) == 1 else fl)


def feature_text(feature) -> str:
    """'polygon 2' / 'polygons 1, 2 and 3' for the messages."""
    fl = feature_list(feature) or []
    if len(fl) <= 1:
        return f"polygon {fl[0]}" if fl else "no polygon"
    return "polygons " + ", ".join(str(v) for v in fl[:-1]) + f" and {fl[-1]}"


class Plan:
    """What read_plan returns. Geometries are kept in the file's CRS (`crs`); `to_utm` projects them once per CRS.
    is_plan: the polygons are plots (trial design); needs_choice: several field boundaries and no feature chosen;
    feature: the 1-based index of the chosen polygon, or the list of the chosen polygons when there are several (None =
    all); fields: [{index, name, area_ac}] of the file's polygons when it is not a plan."""

    def __init__(self, file, gdf, id_col, rate_col, headland_mask, crs_note=None, warnings=None, is_plan=None,
                 feature=None, needs_choice=False, fields=None, n_in_file=None):
        self.file = file
        self.gdf = gdf                                      # polygons (one row per feature), columns of the file
        self.crs = gdf.crs
        self.id_col = id_col
        self.rate_col = rate_col
        self.headland = np.asarray(headland_mask, bool)      # True = headland / border polygon (not a plot)
        self.warnings = list(warnings or [])
        self.crs_note = crs_note
        self.is_plan = bool(len(gdf) > 1) if is_plan is None else bool(is_plan)
        self.feature = feature
        self.needs_choice = bool(needs_choice)
        self.fields = list(fields or [])
        self.n_in_file = int(n_in_file if n_in_file is not None else len(gdf))
        self.union = gdf.geometry.union_all()
        self._utm_cache = {}

    # ---- derived
    @property
    def n_polygons(self) -> int:
        return int(len(self.gdf))

    @property
    def n_fields(self) -> int:
        """Number of field boundaries the file holds when it is not a plan (1 for a single polygon)."""
        return 0 if self.is_plan else int(len(self.fields) or self.n_in_file)

    @property
    def plots(self) -> gpd.GeoDataFrame:
        """The plot polygons (a plan, headland polygons excluded); empty for a boundary file."""
        if not self.is_plan:
            return self.gdf.iloc[0:0]
        return self.gdf[~self.headland]

    @property
    def n_plots(self) -> int:
        return int(len(self.plots))

    @property
    def n_headland(self) -> int:
        return int(self.headland.sum()) if self.is_plan else 0

    def plot_ids(self) -> pd.Series:
        """Plot id per plot row (string, <= 16 characters): the id column, or the row index 1..n of the file."""
        P = self.plots
        if self.id_col is not None:
            ids = P[self.id_col]
            num = pd.to_numeric(ids, errors="coerce")
            if num.notna().all() and (num == num.round()).all():
                ids = num.astype(int).astype(str)
            else:
                ids = ids.astype(str)
        else:
            ids = pd.Series([str(i + 1) for i in P.index], index=P.index)
        return ids.str.slice(0, PLOT_ID_WIDTH)

    def rates(self) -> pd.Series | None:
        if self.rate_col is None:
            return None
        return pd.to_numeric(self.plots[self.rate_col], errors="coerce")

    def to_utm(self, crs):
        key = crs.to_wkt() if hasattr(crs, "to_wkt") else str(crs)
        if key not in self._utm_cache:
            self._utm_cache[key] = self.gdf.to_crs(crs)
        return self._utm_cache[key]

    def union_in(self, crs):
        return self.to_utm(crs).geometry.union_all()

    def area_ac(self, crs) -> float:
        return float(self.union_in(crs).area * AC_PER_M2)

    def summary(self, crs=None) -> dict:
        """The R['aoi'] entries that do not depend on the points."""
        r = self.rates()
        d = {"file": self.file, "crs": (self.crs.to_string() if self.crs is not None else None),
             "n_polygons": self.n_polygons, "n_in_file": self.n_in_file, "n_plots": self.n_plots,
             "headland_polygons": self.n_headland, "id_col": self.id_col, "rate_col": self.rate_col,
             "rates": sorted({float(v) for v in r.dropna()}) if r is not None else [],
             "plan": self.is_plan, "needs_choice": self.needs_choice, "feature": self.feature,
             "n_fields": self.n_fields, "fields": list(self.fields), "warnings": list(self.warnings)}
        if crs is not None:
            d["area_ac"] = self.area_ac(crs)
            if self.is_plan:
                d["plots_area_ac"] = float(self.to_utm(crs)[~self.headland].area.sum() * AC_PER_M2)
        return d


# ===================================================================================================== reading
def _geometry_kind(path: str) -> str:
    """'polygon', 'line', 'point', 'mixed' or '' (unknown) from pyogrio.read_info, without reading the features."""
    try:
        import pyogrio
        gt = str(pyogrio.read_info(path).get("geometry_type") or "").lower()
    except Exception:
        return ""
    if "polygon" in gt:
        return "polygon"
    if "line" in gt:
        return "line"
    if "point" in gt:
        return "point"
    return "mixed" if gt in ("unknown", "geometrycollection") else ""


def _find_vector_in_dir(d: str) -> tuple[str | None, list]:
    """(the vector file to read, the other vector files skipped) inside an unpacked zip / kmz: a layer whose geometry
    type is polygon first (an SMS 'Boundaries + Guidance' export puts the AB line's shapefile beside the boundary's),
    then a layer of unknown type, then by name."""
    hits = []
    for root, _, files in os.walk(d):
        for f in files:
            if f.lower().endswith(VECTOR_EXT) and not f.startswith("._"):
                hits.append(os.path.join(root, f))
    if not hits:
        return None, []
    rank = {"polygon": 0, "mixed": 1, "": 1, "line": 2, "point": 3}
    hits.sort(key=lambda p: (rank[_geometry_kind(p)], 0 if p.lower().endswith(".shp") else 1, p))
    return hits[0], hits[1:]


def _read_vector(path: str) -> tuple[gpd.GeoDataFrame, list]:
    """(gdf, notes): gpd.read_file with the quirks of zips / kmz handled; raises ValueError for an unreadable file."""
    low = path.lower()
    if low.endswith((".zip", ".kmz")):
        d = tempfile.mkdtemp(prefix="aoi_")
        try:
            with zipfile.ZipFile(path) as z:
                z.extractall(d)
            inner, skipped = _find_vector_in_dir(d)
            if inner is None:
                raise ValueError(f"{os.path.basename(path)} holds no readable polygon file "
                                 f"({', '.join(VECTOR_EXT)})")
            gdf, notes = _read_vector(inner)
            if skipped:
                notes.append(f"{os.path.basename(path)}: read {os.path.basename(inner)}; the other layer(s) in the "
                             f"archive were skipped ({', '.join(os.path.basename(x) for x in skipped[:4])}"
                             + (", ..." if len(skipped) > 4 else "") + ")")
            return gdf, notes
        finally:
            shutil.rmtree(d, ignore_errors=True)
    try:
        if low.endswith(".kml"):
            try:
                return gpd.read_file(path, driver="LIBKML"), []
            except Exception:                                  # LIBKML missing: fall back to the KML driver
                return gpd.read_file(path, driver="KML"), []
        return gpd.read_file(path), []
    except Exception as e:
        raise ValueError(f"cannot read the field / plan file {os.path.basename(path)}: {e}") from e


def _detect_id_col(columns, user) -> str | None:
    if user is not None:
        if user not in columns:
            raise ValueError(f"aoi.id_col {user!r} is not a column of the plan; columns are {list(columns)}")
        return user
    by_norm = {str(c).strip().strip("_").lower(): c for c in columns}
    for a in ID_ALIASES:
        if a in by_norm:
            return by_norm[a]
    return None


def _detect_rate_col(columns, user) -> str | None:
    if user is not None:
        if user not in columns:
            raise ValueError(f"aoi.rate_col {user!r} is not a column of the plan; columns are {list(columns)}")
        return user
    for c in columns:
        if str(c).lower().startswith("tgt"):
            return c
    by_norm = {str(c).strip().strip("_").lower(): c for c in columns}
    for a in RATE_ALIASES:
        if a in by_norm:
            return by_norm[a]
    return None


def _name_col(columns) -> str | None:
    by_norm = {str(c).strip().strip("_").lower(): c for c in columns}
    for a in NAME_ALIASES:
        if a in by_norm:
            return by_norm[a]
    return None


def _parts_of_one_field(geoms) -> tuple[bool, str, float]:
    """(True, "", union area in m2) when the polygons (in a metric CRS) are the parts of one field: no two overlap
    (one inside another included; up to 10 m2 or 1e-5 of their area is taken as the rounding of a shared edge) and
    each lies within SAME_NAME_GAP_M of another (in a chain); else (False, why not, union area)."""
    s = gpd.GeoSeries(list(geoms))
    total = float(s.area.sum())
    union = float(s.union_all().area)
    if total - union > max(10.0, 1e-5 * total):
        return False, "they overlap or one lies inside another", union
    if s.buffer(SAME_NAME_GAP_M / 2).union_all().geom_type != "Polygon":
        return False, f"they lie more than {SAME_NAME_GAP_M:g} m apart", union
    return True, "", union


def _big_one(area: np.ndarray, candidates: np.ndarray) -> np.ndarray:
    """Mask of the single polygon among `candidates` larger than HEADLAND_RATIO x the median of the OTHER polygons
    (all-False when none or several qualify)."""
    out = np.zeros(len(area), bool)
    idx = np.where(candidates)[0]
    if len(idx) < 1 or len(area) < 2:
        return out
    for i in idx:
        others = area[[j for j in range(len(area)) if j != i]]
        if area[i] > HEADLAND_RATIO * float(np.median(others)):
            out[i] = True
    if out.sum() != 1:
        return np.zeros(len(area), bool)
    return out


def is_plan_like(gdf, tcol, typed_headland: np.ndarray, rate_col, plot_col, area: np.ndarray) -> tuple[bool, str]:
    """(is_plan, reason) for a file with > 1 polygon: a rate column, an explicit plot column or a polygon typed as
    headland / border make it a plan; else >= MIN_PLAN_POLYGONS polygons of similar size (the single big one excepted,
    the rest within HEADLAND_RATIO x their median) do; otherwise the polygons are several field boundaries."""
    if rate_col is not None:
        return True, f"rate column {rate_col!r}"
    if plot_col is not None:
        return True, f"plot column {plot_col!r}"
    if typed_headland.any():
        return True, f"polygon(s) typed as headland / border in {tcol!r}"
    cand = ~_big_one(area, np.ones(len(area), bool))
    if cand.sum() >= MIN_PLAN_POLYGONS:
        a = area[cand]
        if float(a.max()) <= HEADLAND_RATIO * float(np.median(a)):
            return True, f"{int(cand.sum())} polygons of similar size (max / median {a.max() / np.median(a):.2f})"
        return False, (f"{len(area)} polygons of dissimilar size (max / median {a.max() / np.median(a):.2f}) and no "
                       "rate / plot column")
    return False, f"{len(area)} polygons and no rate / plot column (a plan needs >= {MIN_PLAN_POLYGONS})"


def read_plan(path, id_col=None, rate_col=None, headland_types=("headland", "border"), feature=None) -> Plan:
    """Read the field-of-interest / plan file. Raises ValueError when the file is missing, unreadable or holds no
    polygon, or when `feature` (a 1-based polygon index, or a list of them) is out of range. See the module docstring
    for the rules."""
    path = os.path.abspath(str(path))
    if not os.path.exists(path):
        raise ValueError(f"field / plan file not found: {path}")
    gdf, warnings = _read_vector(path)
    if "geometry" not in gdf or len(gdf) == 0:
        raise ValueError(f"{os.path.basename(path)} holds no feature")
    ok = gdf.geometry.notna() & ~gdf.geometry.is_empty & gdf.geom_type.isin(["Polygon", "MultiPolygon"])
    if (~ok).any():
        warnings.append(f"{int((~ok).sum())} feature(s) of {os.path.basename(path)} are not polygons: ignored")
        gdf = gdf[ok]
    if len(gdf) == 0:
        raise ValueError(f"{os.path.basename(path)} holds no polygon")
    gdf = gdf.reset_index(drop=True)
    crs_note = None
    if gdf.crs is None:
        gdf = gdf.set_crs("EPSG:4326")
        crs_note = f"{os.path.basename(path)} has no coordinate system: taken as WGS 84 (EPSG:4326)"
        warnings.append(crs_note)
    bad = ~gdf.geometry.is_valid
    if bad.any():
        gdf.loc[bad, "geometry"] = gdf.geometry[bad].buffer(0)
        warnings.append(f"{int(bad.sum())} invalid polygon(s) repaired with buffer(0)")
    n_in_file = len(gdf)
    cols = [c for c in gdf.columns if c != "geometry"]
    headland = np.zeros(len(gdf), bool)
    idc = ratec = None
    is_plan, fields, needs_choice, chosen = False, [], False, None
    chosen_l = feature_list(feature)                       # the polygons chosen (1-based), None = no choice
    if chosen_l is not None:
        bad_k = [k for k in chosen_l if not 1 <= k <= len(gdf)]
        if bad_k:
            raise ValueError(f"aoi.feature = {feature_setting(feature)} but {os.path.basename(path)} holds {len(gdf)} "
                             "polygon(s)")
        chosen = chosen_l[0] if len(chosen_l) == 1 else chosen_l
    if len(gdf) > 1:
        tcol = next((c for c in cols if str(c).strip().lower() == "type"), None)
        types = {str(t).strip().lower() for t in (headland_types or [])}
        area = gdf.geometry.to_crs(gdf.estimate_utm_crs()).area.to_numpy()
        typed_head = np.zeros(len(gdf), bool)
        untyped = np.ones(len(gdf), bool)
        if tcol is not None:
            tv = gdf[tcol].astype(str).str.strip().str.lower()
            typed_head = tv.isin(types).to_numpy()
            untyped = (gdf[tcol].isna() | tv.isin(BLANK_TYPES)).to_numpy()
        plot_cols = [c for c in cols if c != tcol]
        by_norm = {str(c).strip().strip("_").lower(): c for c in plot_cols}
        plotc = next((by_norm[a] for a in PLOT_ALIASES if a in by_norm), None)
        ratec_auto = _detect_rate_col([c for c in plot_cols if c != plotc], rate_col)
        is_plan, why = is_plan_like(gdf, tcol, typed_head, ratec_auto, plotc, area)
        if chosen is not None:                                 # one field of several (also allowed on a plan file)
            is_plan = False
        if is_plan:
            headland = typed_head.copy()
            big = _big_one(area, untyped & ~typed_head)
            if big.any() and not typed_head.any():
                headland |= big
                if tcol is not None:
                    warnings.append(f"the polygon without a '{tcol}' value is "
                                    f"{area[big][0] / np.median(area[~big]):.0f} x the median plot area: taken as the "
                                    "headland / border polygon")
            elif not typed_head.any():
                nb = int((area > HEADLAND_RATIO * float(np.median(area))).sum())
                if nb > 1:
                    warnings.append(f"{nb} polygons are larger than {HEADLAND_RATIO:g} x the median plot area and none "
                                    "is typed as headland: all are kept as plots (add a 'type' column with "
                                    "'headland' to mark the headland / border polygon)")
            idc = _detect_id_col(plot_cols, id_col)
            ratec = _detect_rate_col([c for c in plot_cols if c != idc], rate_col)
            if headland.all():
                raise ValueError("every polygon of the plan is marked as headland / border: no plot left")
        else:
            namec = _name_col(cols)
            fields = [{"index": i + 1, "name": (str(gdf[namec].iloc[i]) if namec else ""),
                       "area_ac": float(area[i] * AC_PER_M2)} for i in range(len(gdf))]
            names = {str(f["name"]).strip().casefold() for f in fields}
            one_name = (namec is not None and len(names) == 1 and next(iter(names)) not in BLANK_TYPES | GENERIC_NAMES
                        and gdf[namec].notna().all())
            parts, not_parts = False, ""
            if chosen is None and one_name:
                utm = gdf.geometry.to_crs(gdf.estimate_utm_crs())
                parts, not_parts, u_m2 = _parts_of_one_field(utm)
            if parts:
                # every polygon names the same field and they are its parts (no overlap, each near another): one field
                # drawn in parts, its boundary is all of them (SMS clips a field to every ring of its boundary)
                warnings.append(f"{os.path.basename(path)} holds {len(gdf)} polygons that all name the field "
                                f"{fields[0]['name']!r}: taken together as its boundary ("
                                + "; ".join(f"{f['area_ac']:,.2f} ac" for f in fields)
                                + f"; {u_m2 * AC_PER_M2:,.2f} ac in all)")
            elif chosen is None:
                needs_choice = True
                warnings.append(f"{os.path.basename(path)} holds {len(gdf)} polygons that are several field boundaries, "
                                f"not a trial plan ({why}): choose the field of interest (aoi.feature 1..{len(gdf)}: "
                                + "; ".join(f"{f['index']} = {f['name'] or 'polygon ' + str(f['index'])} "
                                            f"{f['area_ac']:,.2f} ac" for f in fields) + ")"
                                + (f"; they all carry the name {fields[0]['name']!r}, but {not_parts}: choose all of "
                                   "them for their union" if not_parts else ""))
            elif len(chosen_l) == 1:
                f = fields[chosen - 1]
                warnings.append(f"field of interest = polygon {chosen} of {len(gdf)} in {os.path.basename(path)} "
                                f"({f['name'] or 'no name'}, {f['area_ac']:,.2f} ac); the other polygons are ignored")
                gdf = gdf.iloc[[chosen - 1]].reset_index(drop=True)
                headland = np.zeros(1, bool)
            else:
                # several polygons chosen together: one field drawn in parts, the AOI is their union
                sel = [fields[k - 1] for k in chosen_l]
                u_sel = gdf.geometry.iloc[[k - 1 for k in chosen_l]].to_crs(gdf.estimate_utm_crs()).union_all()
                warnings.append(f"field of interest = {feature_text(chosen_l)} of {len(gdf)} in {os.path.basename(path)}, "
                                "together (" + "; ".join(f"{f['name'] or 'polygon ' + str(f['index'])} "
                                                         f"{f['area_ac']:,.2f} ac" for f in sel)
                                + f"; {float(u_sel.area) * AC_PER_M2:,.2f} ac in all)"
                                + ("; the other polygons are ignored" if len(chosen_l) < len(gdf) else ""))
                gdf = gdf.iloc[[k - 1 for k in chosen_l]].reset_index(drop=True)
                headland = np.zeros(len(gdf), bool)
    elif chosen_l is not None and chosen_l != [1]:
        raise ValueError(f"aoi.feature = {feature_setting(feature)} but {os.path.basename(path)} holds one polygon")
    return Plan(path, gdf, idc, ratec, headland, crs_note, warnings, is_plan=is_plan, feature=chosen,
                needs_choice=needs_choice, fields=fields, n_in_file=n_in_file)


# ===================================================================================================== points
def _points(x, y, crs) -> gpd.GeoSeries:
    x, y = np.asarray(x, float), np.asarray(y, float)
    fin = np.isfinite(x) & np.isfinite(y)
    pts = [Point(a, b) if f else None for a, b, f in zip(x, y, fin)]
    return gpd.GeoSeries(pts, crs=crs), fin


def inside(plan: Plan, x, y, crs, buffer_m: float = 0.0) -> np.ndarray:
    """True for every point (x, y in `crs`) inside the union of the plan's polygons buffered by buffer_m (m, >= 0).
    Points without a finite position are False."""
    u = plan.union_in(crs)
    if buffer_m and buffer_m > 0:
        u = u.buffer(float(buffer_m))
    pts, fin = _points(x, y, crs)
    out = np.zeros(len(pts), bool)
    if fin.any():
        out[fin] = pts[fin].within(u).to_numpy()
    return out


def tag_plots(plan: Plan, x, y, crs):
    """(plot_id, tgt_rate) per point by point-in-polygon on the plot polygons (headland polygons excluded):
    plot_id "" and tgt_rate NaN outside every plot, and everywhere when the file is not a plan. A point on a shared
    edge goes to the first plot in file order."""
    n = len(np.asarray(x))
    plot_id = np.array([""] * n, dtype=object)
    rate = np.full(n, np.nan)
    if not plan.is_plan or plan.n_plots == 0:
        return plot_id, rate
    P = plan.to_utm(crs)[~plan.headland].copy()
    P["_pid"] = plan.plot_ids().values
    rates = plan.rates()
    P["_rate"] = rates.values if rates is not None else np.nan
    pts, fin = _points(x, y, crs)
    if not fin.any():
        return plot_id, rate
    g = gpd.GeoDataFrame({"_i": np.where(fin)[0]}, geometry=pts[fin].values, crs=crs)
    j = gpd.sjoin(g, P[["_pid", "_rate", "geometry"]], how="inner", predicate="within")
    j = j[~j["_i"].duplicated(keep="first")]
    plot_id[j["_i"].to_numpy()] = j["_pid"].astype(str).to_numpy()
    rate[j["_i"].to_numpy()] = pd.to_numeric(j["_rate"], errors="coerce").to_numpy(float)
    return plot_id, rate


def outline_xy(plan: Plan, crs, x0: float = 0.0, y0: float = 0.0):
    """[(xs, ys), ...] of every exterior ring in `crs`, shifted by (-x0, -y0): for drawing the AOI on the map figure.
    Plot polygons first, the headland / union outline last."""
    rings = []
    P = plan.to_utm(crs)
    for geom in P.geometry:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for p in polys:
            xs, ys = p.exterior.coords.xy
            rings.append((np.asarray(xs) - x0, np.asarray(ys) - y0))
    u = plan.union_in(crs)
    for p in (u.geoms if u.geom_type == "MultiPolygon" else [u]):
        xs, ys = p.exterior.coords.xy
        rings.append((np.asarray(xs) - x0, np.asarray(ys) - y0))
    return rings
