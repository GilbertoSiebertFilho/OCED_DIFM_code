# OCED_DIFM_code: oced-clean

OCED's raw-data yield cleaning as a Python library and a command line, without the app, made to be plugged into
the DIFM platform. The Python package in this repository is called `oced-clean` (import name `oced_clean`).

Raw yield-monitor data and the field boundary go in. Two things come out: the clean readings as a point shapefile
in the layout of an Ag Leader SMS shapefile export (the same columns, in the same order, with the same field
types), and a cleaning summary. That shapefile goes where the SMS export goes today.

```
raw upload (.cn1 card, GreenStar 3 card, JD-Data .jdl logs, Operations Center export)  +  boundary
        |
        v
   oced-clean        import as SMS  ->  clip  ->  Optimize Swath Widths  ->  moisture  ->  speed  ->  dry yield
        |
        v
the shapefile for DIFM (zip, SMS shapefile export layout)  +  <tag>_sms3s_summary.json
        |
        v
the existing pipeline, where the SMS shapefile export goes today
```

The rules are those of OCED (Olds College Extract Data), the app the Olds College OFPE team cleans its trials with.
The engine inside this package (`src/oced_clean/_engine`) is copied from the OCED repository by a script and is not
edited here, so that this package and the app give the same clean file for the same input and settings; the checks
that hold it to that are under [Tests](#tests). `oced-clean version` prints the OCED version and commit the engine
comes from.

## Status

11 November 2026: first version, put here for review. **The package does not run from this repository yet.**

| Part | State |
|---|---|
| The library, the command line and the summary (`src/oced_clean/`) | written |
| The tests, on synthetic data (`tests/`) | written |
| The tools that copy the engine and compare the result with OCED (`tools/`) | written |
| The cleaning engine (`src/oced_clean/_engine/`) | **not in the repository yet** |
| OCED's synthetic-data generators the tests use (`tests/oced_synthetic/`) | **not in the repository yet** |
| The shapefile for DIFM, in the SMS shapefile export layout | **comes with the engine**; its layout and the test of it are here |
| ISOXML task data (`TASKDATA.XML`) of a harvest as a fifth source | **comes with the engine**: OCED 0.3.0 adds a reader for it. The text below still says ISOXML is not read, and is changed when the engine is in |

The engine and the generators are copied from OCED version 0.3.0 by `tools/sync_engine.py`. That version is being
published; the copy is added here as soon as it is public.

Changed on 11 November 2026, after a first upload to the platform: the file handed to DIFM is no longer OCED's full
clean file. The platform takes that file at upload, but its Define Features page does not get through it (it has
columns an SMS export does not have, and its time column is text). The same readings written in the layout of an
SMS shapefile export go through. OCED 0.3.0 writes that file, and this package hands it out as `result.difm_zip`;
see [The shapefile for DIFM](#the-shapefile-for-difm). The full clean file is still written, for checking.

Done so far, on the developer's computer, with the 0.3.0 engine before its publication:

- the route ran from the upload to the clean shapefile on synthetic uploads of all four sources (a `.cn1` card, a
  GreenStar 3 card, Gen4 `.jdl` logs, an Operations Center export);
- 46 of the first 50 tests passed, among them the comparison of this package with the engine's own command line,
  byte for byte, for every source. The other four check the copied engine itself (its manifest, and that a run
  loads no plotting library) and can only run once the copy is in;
- the 16 tests of the shapefile for DIFM were added afterwards and have not run against an engine yet: the engine
  used so far does not write that file. The checker they use was tried on hand-made files (it passes a file in the
  layout, and refuses dates written as text and columns in another order).

Still to do once the engine is in: the full test run, an install into a clean environment, the byte-for-byte
comparison with OCED on real raw data, an upload of the shapefile for DIFM to the platform, and a run on Linux
(see [Limits](#limits)).

Until then, read this document as the description of what the package does and how it is called. One thing is
still open in the interface: the file name of the shapefile for DIFM, which OCED's engine decides. Take its path
from `result.difm_zip`, not from a name pattern.

## Contents

0. [Status](#status)
1. [Install](#install)
2. [Quick start](#quick-start)
3. [Inputs](#inputs)
4. [What it does](#what-it-does)
5. [Outputs](#outputs)
6. [Calling it from the DIFM pipeline](#calling-it-from-the-difm-pipeline)
7. [Parameters](#parameters)
8. [Errors and exit codes](#errors-and-exit-codes)
9. [Limits](#limits)
10. [Tests](#tests)
11. [Updating the engine when OCED changes](#updating-the-engine-when-oced-changes)
12. [Layout of the repository](#layout-of-the-repository)

## Install

(Possible once the engine is in the repository: see [Status](#status).)

Python 3.10 or newer, in its own virtual environment:

```bash
python -m venv .venv
```

```bash
.venv/bin/pip install .
```

(On Windows the second command is `.venv\Scripts\pip install .`)

Dependencies, all installed by pip: numpy, pandas, geopandas, shapely, pyogrio, pyproj, scipy. Nothing else: no
Streamlit, no matplotlib, no PDF library, no scikit-image, no R.

`constraints-tested.txt` lists the exact versions the package was tested with. To install exactly those:

```bash
.venv/bin/pip install -c constraints-tested.txt .
```

Then run the tests on the machine that will run the package (about three minutes, synthetic data only):

```bash
.venv/bin/pip install pytest
```

```bash
.venv/bin/python -m pytest
```

What was and was not tested is in [Limits](#limits). In short: developed and tested on Windows with Python 3.14;
Linux and older Python versions are untested, and the test suite is the way to find out on your server.

## Quick start

Command line:

```bash
oced-clean clean upload.zip --boundary boundary.geojson --out work/job_123
```

It prints the steps with their limits and counts, and writes the files to `work/job_123/output/`.

Python:

```python
import oced_clean

result = oced_clean.clean_yield(["upload.zip"], "boundary.geojson", "work/job_123")
result.difm_zip         # the shapefile for DIFM, zipped: the clean readings in the SMS shapefile export layout
result.clean_zip        # the full clean file, with OCED's extra columns (for checking, not for the upload)
result.yield_column     # the column to map as the dry yield, bu/ac ("Yld_Vol_Dr" for raw data)
result.summary          # dict: what was read, the import settings, every step with its limits and counts
print(result.text())    # the same as a short text report
```

For another program (R, a shell script, a job runner), `--json` prints one JSON document on standard output and
sends the progress lines to standard error:

```bash
oced-clean clean upload.zip --boundary boundary.geojson --out work/job_123 --json --quiet
```

## Inputs

### The upload

One or more files, folders or zips. A zip may hold folders and other zips. The package finds what is inside.

| Source | What to upload | Read as |
|---|---|---|
| Case IH / New Holland (displays that write a `.cn1` card, such as the AFS Pro 700) | the card folder that ends in `.cn1`, or a zip of it | `cnh_cn1` |
| John Deere GreenStar 3 | the card (`RCD/EIC/...` with `.fdd` and `.fdl` files), or a zip of it | `jd_gs3_rcd` |
| John Deere Gen4 / G5 | the `JD-Data` folder with its `.jdl` logs, or a zip of it; several combines together is fine | `jd_jdl` |
| John Deere Operations Center | the harvest shapefile export (with its `-Deere-Metadata.json`); both the per-second export and the export with one point per header section | `point_export`, `jd_ops_center_sections` |
| A yield point shapefile | columns named as in an SMS export or an Operations Center export | `point_export` |

Not read: ISOXML task data (`TASKDATA.XML`), and any other monitor format. The error message says so when the upload
holds one. Yield points in a KML or KMZ file are converted and read as a point export.

A raw card usually holds many fields and seasons. The package reads it in **units**: one task (Case IH), or one
combine and field and season (John Deere). The boundary chooses the units: every unit of the latest season with
20 % or more of its readings inside the boundary (plus 50 m) is read. To choose by hand, use `units`, `field` or
`year`; `oced-clean inspect` lists the units of an upload.

One run cleans one harvest: one field, one crop, one season. Units of several fields, crops or seasons in one run
are refused unless `allow_mixed` is set.

When the upload holds several datasets (for example two shapefiles), the run stops and lists them. Choose one with
`candidate`.

### The boundary

A polygon file: `.shp` (with its `.shx`, `.dbf`, `.prj`), a `.zip` of a shapefile, `.geojson`, `.gpkg`, `.kml` or
`.kmz`, in any coordinate system (WGS 84 is assumed when the file names none).

- A field boundary: one polygon or multipolygon. Readings outside it are removed.
- A trial design (several plot polygons, with or without a headland polygon): the readings outside the union of
  the polygons are removed, and the clean file gets two more columns, `Plot_Id` and `Tgt_Rate`.
- A file with several separate field boundaries is refused until `boundary_feature` says which polygon is the field.

Without a boundary (`boundary=None`, `--no-boundary`) nothing is clipped. Use that only on purpose: readings of
roads, yards and neighbouring fields then enter the means the filters are built on.

## What it does

### 1. Import as SMS

Ag Leader SMS applies "Processing Settings" when it reads a harvest. The package applies the same ones, with the
values SMS itself uses for each source, so the data that is cleaned is the data SMS would hold.

| Setting | John Deere (GreenStar 3, Gen4, Operations Center) | Case IH / New Holland and other point files |
|---|---|---|
| Weight to volume (lb/bu) and dry moisture (%) | from the crop of the file | SMS's product table (below) |
| Minimum yield volume | 5 bu/ac | the product's (5 bu/ac for cereals, 10 for canola and flax) |
| Maximum yield volume | 10,000 bu/ac | the product's (300 wheat, 200 barley, 400 canola ...) |
| Expand dry moisture | off | off |

SMS's product table as the package holds it (`oced_clean._engine.sms_import.SMS_PRODUCTS`):

| Product | lb/bu | Dry moisture % | Min to max bu/ac |
|---|---|---|---|
| Wheat - Spring | 60 | 13.5 | 5 to 300 |
| Wheat - Durum | 60 | 13.0 | 5 to 300 |
| Barley | 48 | 14.5 | 5 to 200 |
| Canola | 50 | 8.5 | 10 to 400 |
| Field Peas, Chick Peas | 60 | 13.0 | 5 to 200 |
| Lentils | 60 | 14.0 | 5 to 200 |
| Oats | 32 | 14.0 | 5 to 200 |
| Flax | 60 | 10.0 | 10 to 400 |
| Rye | 56 | 14.0 | 0 to 500 |

What the import does to the readings:

- the dry yield is computed again from the wet mass, the moisture and the two values above. "Expand dry moisture
  off" means grain drier than the dry moisture is not raised to it (the dry yield equals the wet yield there);
- readings with a dry yield below the minimum or above the maximum are left out;
- nothing else changes. The summary says how many readings were left out and where every value came from.

Every value can be set by the caller (`product`, `weight_to_volume`, `dry_moisture`, `min_yield`, `max_yield`,
`expand_dry_moisture`). A value given by the caller wins.

### 2. The SMS 3-sigma cleaning

The steps, in this order. The summary lists them in the same order with the same ids.

| Id | Step | Rule |
|---|---|---|
| S1 | Clip to field boundary | A reading whose logged point is outside the boundary is removed. A point on the edge is inside. |
| S0 | Import check | A reading with no position, no yield, or a dry yield below the import's minimum is removed. After the import above this removes nothing. |
| S2 | Optimize Swath Widths | In harvest time order across all combines, each reading's rectangle (width by distance) is tested against the ground already cut. A fully overlapped reading is removed. A partly overlapped one is narrowed to the strip not yet cut and moved to its centre, and its per-area values (yield, mass per area) are multiplied by old area / new area, so grain mass is conserved. |
| S3 | Moisture | Keep mean ± 3 SD. |
| S4 | Speed | Keep mean ± 3 SD. |
| S5a | Dry yield, pass 1 | Keep mean ± 3 SD of the yield as scaled by S2. |
| S5b ... | Dry yield, pass 2 ... | Run only when the pass before it cut the SD by more than 25 %. |

How the limits are made, for S3 to S5:

- the mean and the SD are computed again before every step, on the readings still alive at that point;
- the mean is the plain mean of the points and the SD is the SD of the points (divisor n), as SMS shows them in
  Edit Legend;
- the limits are mean − 3 SD and mean + 3 SD, rounded half up to two decimals, as a person types them in SMS; a
  lower limit below zero becomes 0; a reading equal to a limit is kept;
- a reading with no value of the filtered attribute (an empty moisture or speed cell) is not removed by that
  filter; the summary counts them.

One cleaning unit per combine: with several combines, each combine gets its own mean, SD and limits in S3 to S5,
computed over all its tasks and days. With one combine there is one unit for the field. S1, S0 and S2 always run
over everything together. `group_by="task"` makes one unit per task instead (one task = one Case IH task log, or one
combine and day for John Deere logs); `group_by="field"` makes one unit of everything.

These rules are fixed. The order of moisture and speed, the 3 SD, the 25 % and the rounding are not parameters of
this package.

## Outputs

Everything lands in the work folder given as `out_dir` / `--out`:

```
<out_dir>/
  input/      the upload, unpacked
  decoded/    <tag>_decoded.shp    every reading decoded from the raw data (raw uploads only)
              <tag>_imported.shp   the readings after the import as SMS: the input of the cleaning
  output/     the shapefile for DIFM  .shp .shx .dbf .prj (.cpg) and .zip    the upload: result.difm_zip
              <tag>_sms3s_clean.shp  .shx .dbf .prj .cpg     the full clean file
              <tag>_sms3s_clean.zip                          the same five files, zipped
              <tag>_sms3s_flagged.shp ... and .zip           every reading inside the boundary, kept or not
              <tag>_sms3s_summary.json                       the cleaning summary
              <tag>_sms3s_cleaning.json                      the engine's full record of the run
```

`<tag>` is the field name and year of a raw card (`Home_Quarter_2026`), or the file name of a point file. Set it
with `tag`. `cleanup=True` / `--cleanup` removes `input/` and `decoded/` at the end.

### The shapefile for DIFM

`result.difm_zip` (and `result.difm_shp`). The kept readings, one point each: the same readings, positions and
values as the full clean file below, written the way Ag Leader SMS writes a shapefile export of a grain harvest.
The columns, in this order:

| # | Column | DBF type | Unit | Meaning |
|---|---|---|---|---|
| 1 | `Field` | C (text) | | field name |
| 2 | `Dataset` | C (text) | | task or log of the reading |
| 3 | `Product` | C (text) | | crop |
| 4 | `Obj__Id` | N, 18 wide, 0 decimals | | record number |
| 5 | `Distance_f` | N | ft | distance travelled |
| 6 | `Track_deg_` | N | degrees | heading, clockwise from north |
| 7 | `Duration_s` | N | s | time the reading stands for |
| 8 | `Elevation_` | N | ft | GNSS altitude |
| 9 | `Time` | D (date) | | local harvest date of the reading, the date only |
| 10 | `Swth_Wdth_` | N | ft | swath width after Optimize Swath Widths |
| 11 | `Y_Offset_f` | N | ft | lateral offset; 0 when there is none |
| 12 | `Moisture__` | N | % | grain moisture, wet basis |
| 13 | `Yld_Mass_W` | N | lb/ac | wet mass per area |
| 14 | `Yld_Vol_We` | N | bu/ac | wet volume |
| 15 | `Yld_Mass_D` | N | lb/ac | mass per area at the dry moisture |
| 16 | `Yld_Vol_Dr` | N | bu/ac | **dry yield: the column to analyse** |
| 17 | `Speed_mph_` | N | mph | ground speed |
| 18 | `Date` | D (date) | | local harvest date |

- Coordinates are WGS 84, and the `.prj` is the ESRI definition `GCS_WGS_1984`.
- `Time` and `Date` are true DBF date fields (type `D`, eight characters, `YYYYMMDD`), not text.
- The zip holds `.shp`, `.shx`, `.dbf`, `.prj` and, when written, `.cpg`, with nothing else and no folder.
- No other column is in it. What OCED adds (the combine id, the day, the session, the swath width as logged) is in
  the full clean file and in the summary.

`tests/test_difm_file.py` checks every line of this on a synthetic upload, reading the `.dbf` header itself.

### The full clean shapefile

Not the file to upload: the Define Features page of the platform does not get through it. It is the file to open
when a result needs checking. Points, WGS 84 (EPSG:4326), one per kept reading. For raw data the columns are these,
in this order (the names and units of an SMS point export):

| Column | Unit | Meaning |
|---|---|---|
| `Field` | | field name on the monitor |
| `Dataset` | | task or log of the reading |
| `Product` | | crop name on the monitor |
| `Obj__Id` | | record number within the dataset |
| `Time` | | time of the reading |
| `Duration_s` | s | time the reading stands for |
| `Elevation_` | ft | GNSS altitude |
| `Speed_mph_` | mph | ground speed |
| `Distance_f` | ft | distance travelled |
| `Track_deg_` | degrees | heading, clockwise from north |
| `Swth_Wdth_` | ft | swath width after Optimize Swath Widths |
| `Moisture__` | % | grain moisture, wet basis |
| `Yld_Mass_W` | lb/ac | wet mass per area |
| `Yld_Mass_D` | lb/ac | mass per area at the dry moisture |
| `Yld_Vol_We` | bu/ac | wet volume |
| `Yld_Vol_Dr` | bu/ac | **dry yield: the column to analyse** |
| `Machine` | | combine id (serial number); one value per combine |
| `Src_Fmt` | | the format the reading was decoded from |
| `Crop` | | crop key, when the whole file is one known crop |
| `Y_Offset_f` | ft | John Deere Gen4 only; written as 0 (the points are already moved) |

Added by the cleaning, after the columns above:

| Column | Meaning |
|---|---|
| `Yld_Dry_Sy` | the dry yield on another moisture basis (grain drier than the dry moisture is credited). A diagnostic of OCED. **Do not map it as the yield.** |
| `Harv_Day` | calendar day of harvest |
| `Session` | combine and day |
| `Wdth_Raw` | the swath width as logged, before Optimize Swath Widths |
| `Swath_Scl` | old area / new area of the reading in Optimize Swath Widths (1 = unchanged) |
| `Plot_Id`, `Tgt_Rate` | only when the boundary is a trial design: the plot and its target rate |

A point file that was uploaded as a shapefile (an Operations Center export, an SMS export) keeps its own columns,
names and order; only the added columns above are appended. For those files `summary["columns"]` says which column
holds what.

The flagged file has the same readings before removal, with `Keep` (1 or 0) and `Rem_By` (the id of the step that
removed the reading: `S0`, `S2`, `S3`, `S4`, `S5a` ...). The readings outside the boundary are not in it.

### The summary

`result.summary`, also written as `<tag>_sms3s_summary.json`. The example below is from the package's synthetic
test data (two made-up combines); every number in it is invented.

```
oced-clean 0.1.0 (OCED engine 0.3.0, commit ............)
input        Point export · Synthetic_two_combines.shp · 5,412 points
import       5,412 of 5,412 readings; product wheat; weight to volume 60.00 lb/bu; dry moisture 13.50 %; ...
boundary     boundary.geojson (33.17 ac)
groups       2 (machine): COMBINE-A, COMBINE-B

step what                      group                      mean       SD    lower    upper  removed     left
S1   Clip to field boundary    all                                                             288    5,124
S0   Import check              all                                                               0    5,124
S2   Optimize Swath Widths     all                                                               0    5,124
S3   Moisture                  COMBINE-A                 14.12     1.27    10.31    17.93       22    2,798
S3   Moisture                  COMBINE-B                 16.10     1.10    12.81    19.39       20    2,284
S4   Speed                     COMBINE-A                  4.57     0.65     2.61     6.53       27    2,771
S4   Speed                     COMBINE-B                  5.53     0.43     4.25     6.80       11    2,273
S5a  Dry yield, pass 1         COMBINE-A                 66.68    14.28    23.85   109.50       27    2,744
S5a  Dry yield, pass 1         COMBINE-B                 71.31    14.55    27.68   114.95       34    2,239
S5b  Dry yield, pass 2         COMBINE-A                 65.47     7.59    42.70    88.25        2    2,742
S5b  Dry yield, pass 2         COMBINE-B                 69.82     7.58    47.09    92.55        2    2,237

readings     5,412 imported, 5,124 in the boundary, 4,979 kept, 4,979 in the clean file
clean yield  Yld_Vol_Dr: mean 67.43 bu/ac, area-weighted average 67.65 bu/ac over 24.90 ac
  combine COMBINE-A: 2,742 of 2,820 readings kept, mean 65.49 bu/ac
  combine COMBINE-B: 2,237 of 2,304 readings kept, mean 69.81 bu/ac
```

The keys of the summary:

| Key | Holds |
|---|---|
| `package` | name and version of this package; `engine`: OCED version, commit, commit date |
| `input` | `dataset` (label), `kind` (`raw` or `export`), `format`, `units` (the units read, each with `share_inside_boundary`), `readings_decoded`, `decoded_totals` (area, wet lb, wet bu, dry bu), `time_basis`, `crop` |
| `import` | `settings` (one line), `values` (product, lb/bu, dry moisture, min, max, expand), `values_from` (where each value came from), `sms_product`, `readings_in`, `readings_imported`, `left_out` (reason: count) |
| `boundary` | `file`, `area_ac`, `polygons`, `trial_design`, `plots`, `readings_outside`; `null` when no boundary was given |
| `cleaning` | `order`, `k_sd`, `second_yield_pass_when_sd_drop_pct_above`, `group_by`, `groups`, `yield_passes`, `readings_in`, `readings_in_boundary`, `readings_kept`, `steps`, `combines` |
| `cleaning.steps[]` | one row per step and group, in the order they ran: `step`, `name`, `group`, `attribute`, `unit`, `readings_before`, `mean`, `sd`, `lower`, `upper`, `removed`, `remaining`, and for S3 to S5 `sd_after`, `sd_drop_pct`, `no_value` |
| `cleaning.combines[]` | per combine: `readings`, `kept`, `days`, `mean_dry_yield` |
| `result` | `yield_column`, `readings`, `area_ac`, `mean`, `average_area_weighted` (total / area, the "Avg. Yield" of an SMS summary), `median`, `sd`, `min`, `max`, `coordinate_system`, `added_columns`, `swath_changes` |
| `columns` | which column of the full clean file holds what, with its unit: `yield_volume_dry`, `yield_mass_dry`, `yield_volume_wet`, `yield_mass_wet`, `moisture`, `swath_width`, `distance`, `speed`, `heading`, `duration`, `time`, `elevation`, `harvester_id`, `dataset`. The shapefile for DIFM needs no such list: its columns are always the eighteen above |
| `files` | the paths of the shapefile for DIFM (`difm_shp`, `difm_zip`), of the full clean and flagged files and of the engine's record |
| `warnings`, `checks`, `notes` | what the engine wants a person to read. `warnings` matter: a combine whose points sit beside its strip, readings with no moisture value, a crop the table does not know |

## Calling it from the DIFM pipeline

Where it goes: after the upload of the raw harvest data and before the step that today receives the SMS shapefile
export. `result.difm_zip` is that step's yield file.

### From Python

```python
import oced_clean

def clean_harvest_upload(upload_paths, boundary_path, job_dir, crop=None):
    """Returns (path of the yield zip for the next step, summary dict)."""
    try:
        result = oced_clean.clean_yield(upload_paths, boundary_path, job_dir, product=crop, cleanup=True)
    except oced_clean.SeveralDatasetsError as e:
        # the upload holds several datasets: e.datasets lists them; ask the user, then pass candidate=<index>
        raise
    except ValueError as e:
        # a problem the user can fix; str(e) says what (wrong boundary, nothing recognised, not a harvest ...)
        raise
    return result.difm_zip, result.summary
```

Notes for the integration:

- **One folder per job.** Two runs must not share an `out_dir`. Runs in different folders can run at the same
  time; the package keeps no global state and writes nothing outside `out_dir`.
- **Define Features.** The shapefile for DIFM always has the same eighteen columns, so the mapping the user does by
  hand today is the one of an SMS export and can be filled in:

  | Feature | Column of the shapefile for DIFM |
  |---|---|
  | Yield, volume (preferred) | `Yld_Vol_Dr`, bu/ac |
  | Yield, mass | `Yld_Mass_D`, lb/ac |
  | Moisture | `Moisture__`, % |
  | Width | `Swth_Wdth_`, ft |
  | Distance | `Distance_f`, ft |
  | Speed | `Speed_mph_`, mph |
  | Heading | `Track_deg_`, degrees |
  | Elevation | `Elevation_`, ft |
  | Date | `Date` (and `Time`, the same date) |

- **Harvester id.** The SMS export layout has no column for the combine, so the shapefile for DIFM has none. The
  combine of every reading is in `Machine` of the full clean file (its serial number, for a raw upload), and
  `summary["cleaning"]["combines"]` lists the combines with their counts and means. A later step that works per
  combine needs one of the two.
- **The crop.** For John Deere data the crop comes from the file. For a Case IH / New Holland card the crop name on
  the card is matched to SMS's product table. When the platform knows the crop of the trial, pass it as `product`;
  it then decides the bushel weight and dry moisture. Mind that these two values change the dry yield: the same
  grain is a different number of bushels at another bushel weight or another dry moisture.
- **Disk.** A full card unpacks to its full size under `input/`. `cleanup=True` removes it when the run ends.
- **Time.** Decoding is the slow part. A card with many fields takes longer than one field's logs, because every
  task of the latest season is decoded once to see whether it lies in the boundary. The swath step works on a
  raster of the field and its memory grows with the field's area.
- **Showing the result.** `result.text()` is a ready report. For a page, `summary["cleaning"]["steps"]` is the
  table and `summary["warnings"]` the list to show.

### From R, or any other language

```r
out <- system2("oced-clean",
               c("clean", shQuote(upload), "--boundary", shQuote(boundary), "--out", shQuote(job_dir),
                 "--json", "--quiet", "--cleanup"),
               stdout = TRUE)
res <- jsonlite::fromJSON(paste(out, collapse = "\n"))
if (!isTRUE(res$ok)) stop(res$error)
yield_zip <- res$difm_zip
yield_col <- res$yield_column
```

The exit code is 0 on success, 2 for a problem with the input, 3 for an unexpected error. With `--json` the document
has `"ok": false` and `"error"` in the last two cases.

### Looking at an upload first

```bash
oced-clean inspect upload.zip --out work/look --boundary boundary.geojson --json
```

lists the datasets of the upload and, for a raw card, its units with the share of each inside the boundary. Nothing
is cleaned. In Python: `oced_clean.inspect_upload(inputs, work_dir, boundary)`.

## Parameters

`oced_clean.clean_yield(inputs, boundary, out_dir, **options)` and `oced-clean clean <inputs> --boundary FILE --out DIR [options]`.

| Python | Command line | Default | Meaning |
|---|---|---|---|
| `inputs` | positional | required | the uploaded file(s), folder(s) or zip(s) |
| `boundary` | `--boundary FILE` or `--no-boundary` | required | the field boundary or trial design; `None` / `--no-boundary` = no clip |
| `out_dir` | `--out DIR` | required | the work folder of this run |
| `boundary_feature` | `--boundary-feature N[,M]` | none | which polygon(s) of a file with several field boundaries, 1-based |
| `candidate` | `--candidate N` | none | which dataset of an upload that holds several, 1-based |
| `units` | `--units all` or `--units ID,ID` | by the boundary | raw data: the units to read |
| `field` | `--field NAME` | none | raw data: keep the units of this monitor field name |
| `year` | `--year Y` | latest | raw data: keep the units of this season |
| `allow_mixed` | `--allow-mixed` | off | raw data: allow several fields, crops or seasons in one run |
| `product` | `--product P` | the crop of the data | import: `wheat`, `durum`, `barley`, `canola`, `oats`, `peas`, `lentils`, `flax`, `rye`, `corn`, `soybeans`, or an SMS product name |
| `weight_to_volume` | `--weight-to-volume LB` | by source | import: lb/bu |
| `dry_moisture` | `--dry-moisture PCT` | by source | import: dry moisture, % |
| `min_yield` | `--min-yield BU` | by source | import: minimum yield volume, bu/ac (0 = none) |
| `max_yield` | `--max-yield BU` | by source | import: maximum yield volume, bu/ac |
| `expand_dry_moisture` | `--expand-dry-moisture` | off | import: raise grain drier than the dry moisture to it |
| `group_by` | `--group-by` | `auto` | cleaning units: `auto` (per combine), `task`, `machine`, `field` |
| `tag` | `--tag T` | from the data | name prefix of the output files |
| `cleanup` | `--cleanup` | off | remove `input/`, `decoded/` and `sections/` at the end |
| `log` | `--quiet`, `--json` | logging | Python: a function taking one text line (default `logging.getLogger("oced_clean").info`) |

`clean_yield` returns a `CleanResult`: `difm_shp`, `difm_zip`, `clean_shp`, `clean_zip`, `flagged_shp`,
`flagged_zip`, `yield_column`, `summary`, `summary_json`, `engine_json`, `out_dir`, and the methods `text()` and
`to_dict()`.

`oced_clean.inspect_upload(inputs, work_dir, boundary=None, boundary_feature=None)` returns
`{"datasets": [...], "notes": [...], "message": str}`.

## Errors and exit codes

A problem the user can fix is a `ValueError` whose message says what is wrong and what to do. Two subclasses:
`oced_clean.CleaningError`, and `oced_clean.SeveralDatasetsError` with the list in `.datasets`. Anything else is a
bug or a broken file.

| Situation | What happens |
|---|---|
| Nothing recognised in the upload (or ISOXML only) | `CleaningError`; the message lists what is read |
| The upload holds several datasets, none chosen | `SeveralDatasetsError` with `.datasets` |
| A planter or sprayer file | `CleaningError`: not a harvest file |
| No unit of the card has 20 % of its readings in the boundary | `CleaningError`, with the distance to the nearest unit |
| A point file with (almost) no reading inside the boundary | `ValueError`: wrong file or wrong field |
| Units of several fields, crops or seasons | `ValueError`, unless `allow_mixed` |
| Fewer than 30 readings left after the cleaning | `ValueError` naming the step that removed the most; no clean file |
| The boundary file holds several field boundaries | `CleaningError` asking for `boundary_feature` |
| An unknown `product`, a setting out of range | `ValueError` |

Command line: exit code 0 = done; 2 = one of the above (one line on standard error; with `--json` also
`{"ok": false, "error": ..., "error_type": ...}` on standard output); 3 = unexpected error, with the traceback in
`<out>/error_<time>.log`.

## Limits

Read these before relying on the package.

- **Platform.** Developed and tested on Windows 11 with Python 3.14 and the versions in `constraints-tested.txt`.
  It has not been run on Linux and not on Python 3.10 to 3.13. The code uses no syntax newer than Python 3.9 and
  no call known to be Windows-only, but file names on a monitor card are a known risk on a file system that tells upper from
  lower case. Run the test suite on the server; it holds a card written in capitals for this reason.
- **Formats.** Only the four sources in [Inputs](#inputs). ISOXML is not read. Each decoder reads the monitor
  generations OCED has met; a newer display software can change a file layout, and the decoder then either refuses
  the card or warns in the summary.
- **Same as OCED, by construction.** The package is not a second implementation: it runs OCED's engine, so it
  shares every limit of OCED. Two checks hold it to OCED's own result: the test suite on synthetic data, and
  `tools/check_equivalence.py` on real raw data ([Status](#status) says which have run).
- **OCED against SMS.** OCED was validated against Ag Leader SMS on the Olds College trials of 2026, for the raw
  data as read and for the clean result. The figures are in Olds College's internal validation report, not here.
  Optimize Swath Widths is OCED's reconstruction of what SMS does, built from SMS's own inputs and outputs; it
  follows SMS closely but is not SMS's code, and the count of narrowed readings can differ a little from SMS.
- **One harvest per run.** One field, one crop, one season. Clean two fields in two runs.
- **A field needs spread.** The limits are typed with two decimals. Data in which every reading has almost the same
  speed (a simulator, a test bench) gives limits such as 4.47 to 4.47 and loses its readings. Real harvests do not
  do this.
- **At least 30 readings** must survive, or no clean file is written.
- **Not in this package** (they are in the OCED app): the grower's scale weight and the level factor, the combine
  factor between machines, the cleaning of planter and sprayer files, the PDF report and the maps, writing the clean
  data back in the monitor's own format.
- **Options that are not established against SMS** are not exposed: moving a combine's points sideways before the
  swath step, and running the swath step per combine. The engine reports a combine whose points sit a foot or more
  beside the strip it cut as a warning in the summary; read it.
- **Time zone.** John Deere logs carry UTC. `Harv_Day` and `Session` are made from the local day estimated from the
  longitude. They do not affect which readings are kept unless `group_by="task"`.

## Tests

```bash
python -m pytest
```

Synthetic data only: every name, position and number in the tests is made up. No grower data is in this
repository, and none may be added.

| File | Checks |
|---|---|
| `tests/test_route.py` | the files written, the summary, the clip against the geometry, the import's minimum, the columns |
| `tests/test_difm_file.py` | the shapefile for DIFM: the eighteen columns in order, their DBF types read from the file header, the two date fields, the `.prj`, the zip, and that it holds the readings and values of the full clean file |
| `tests/test_rules.py` | the rules, against a second statement of them written in `tests/helpers.py`: order, mean ± 3 SD recomputed before every step, the 25 % rule, one unit per combine, per task, per field |
| `tests/test_raw_formats.py` | one synthetic upload per format through the whole route: `.cn1` card (also in capitals, also zipped), Gen4 `.jdl` logs, GreenStar 3 card, Operations Center export by section |
| `tests/test_parity.py` | `clean_yield` against the engine's own command line: the clean and flagged shapefiles byte for byte, for every format |
| `tests/test_errors.py` | every refusal in the table above |
| `tests/test_cli.py` | the command line, `--json`, the exit codes |
| `tests/test_lean.py` | a run loads no plotting, PDF or image library and writes no figure |
| `tests/test_engine_manifest.py` | the copied engine is unchanged since it was copied |

### The check against OCED on real data

`tools/check_equivalence.py` cleans the same raw data with this package and with an unmodified clone of OCED, each
in its own process, and compares the clean and the flagged shapefiles byte for byte. It needs a clone of OCED with
OCED's own requirements installed, and a `cases.json` that lists the uploads and boundaries. Real data, the cases
file and the report stay on the computer that runs the check: keep them outside the repository or in `local/`,
which git ignores.

```bash
python tools/check_equivalence.py --oced ../OCED --cases local/cases.json --work local/equivalence
```

## Updating the engine when OCED changes

The engine is never edited in this repository. To take a newer OCED:

```bash
python tools/sync_engine.py --oced ../OCED --ref main
```

```bash
python -m pytest
```

```bash
python tools/check_equivalence.py --oced ../OCED --cases local/cases.json --work local/equivalence
```

The sync reads the files from the commit itself, applies the three changes listed at the top of
`tools/sync_engine.py` (the package name in the import lines, a lazy import of scikit-image, a stub in place of the
PDF report) and writes `src/oced_clean/_engine/ENGINE_SOURCE.json` with the commit and the hash of every file. It
stops with a message if OCED changed in a place one of those changes touches, or if a copied module starts to import
a module that is not copied. `python tools/sync_engine.py --check` verifies that nobody edited the copy.

A format OCED gains comes in by itself: the sync copies every decoder the engine's own list names, and says so. Two
things are then left to do by hand, and a test fails until the first is done: a synthetic upload of the new format
in `tests/test_raw_formats.py`, and the format in [Inputs](#inputs).

After a sync, read the diff of `_engine/run.py` around `_cli`: `oced_clean/api.py` follows that function step by
step, and `tests/test_parity.py` fails if the two drift apart.

## Layout of the repository

```
src/oced_clean/
  __init__.py     the public names
  api.py          clean_yield, inspect_upload, CleanResult, the errors
  cli.py          the oced-clean command
  summary.py      the cleaning summary
  _lean.py        switches the engine's figures off
  _engine/        OCED's engine, copied by tools/sync_engine.py (do not edit); ENGINE_SOURCE.json says from where
tools/
  sync_engine.py        copy the engine from an OCED clone
  check_equivalence.py  this package against OCED on your own data
tests/            synthetic data only; tests/oced_synthetic/ holds OCED's own generators, copied by the sync
constraints-tested.txt   the versions the tests ran with
```
