# -*- coding: utf-8 -*-
"""Unpack what the user gave the app and find what is in it: point exports and raw monitor datasets.

    from oced_clean._engine.formats import detect
    roots = detect.unpack(["harvest_export.zip"], "work/input")
    cands = detect.detect(roots)            # list of candidates (+ cands.notes, cands.message)

unpack(paths, dest)
    Copies each file into dest and extracts every .zip into dest/<zip stem>/, recursively (a zip of per-combine zips,
    as the John Deere Operations Center hands out raw logs, is fully unpacked). Folder structure is kept: raw formats
    are recognised by their folder layout (a .cn1 card folder, JD-Data/log, RCD/EIC/Documentation, a TASKDATA folder
    of ISOXML task data). Folders are read
    IN PLACE by default (``copy_folders=False``): a 2-3 GB monitor card is not duplicated, nothing is written into it,
    and only the zips found inside it are extracted into dest. Guards, each a ValueError with a clear message: a zip
    member whose path leaves its target folder (zip slip: absolute paths, drive letters, '..'), more than
    MAX_MEMBERS members, more than MAX_TOTAL_BYTES unpacked in total, a member with a compression ratio above
    MAX_RATIO and more than 100 MB unpacked (zip bomb), zips nested deeper than MAX_DEPTH, a corrupt zip or member
    (bad CRC, truncated data: the partial file is removed), a member that cannot be written (OSError: the message
    says it is the file system, not the zip).
    Long paths (Windows opens a file up to 259 characters): a zip whose files would land beyond PATH_LIMIT under
    dest/<zip stem>/ is unpacked into a short folder dest/z1, z2 ... instead (a cloud download of a folder of JD-Data
    zips can exceed it under the app's work folder), which the log says; when even that is too long the
    ValueError says so. A zip lying beside a folder that already holds its files (same names and sizes: the download
    of a folder that held both the zips and their unpacked copies) is not unpacked again, so its data is read once.
    Names Windows does not take (< > : " | ? * and control characters, a trailing dot or space, CON / PRN / AUX / NUL /
    COM1-9 / LPT1-9) are made legal component by component ('Harvest 10:30.shp' -> 'Harvest 10_30.shp'); '..',
    absolute paths and drive letters are still refused. When unpacking fails, what the call unpacked is removed.
    One harvest given partly zipped, partly loose (a folder holding one combine's raw logs and another's zip, or an
    upload of a zip and loose log files) would be two scan roots, so two datasets of the same field: a folder read
    in place that holds raw monitor files both loose and in its zips is copied into dest (without its zips) instead,
    so that every decoder sees the whole harvest under one root.
    Last, every .kml / .kmz of yield POINTS under the roots (an SMS "Export to Google Earth": placemarks with a Point
    and attributes; engine/formats/kml_points.py) is converted to a point shapefile with the SMS shapefile column names
    in dest/<kml stem>__kml/, which detect() then lists like any export. A boundary KML is left as it is; a damaged
    yield KML is a ValueError (a partial export would give a wrong field total).

detect(roots)
    Every point shapefile under the roots with a detectable yield column (engine/clean/yield_clean.detect_columns:
    dry or wet yield, or wet mass) becomes an "export" candidate (an Ag Leader SMS or John Deere Ops Center export, or
    a shapefile the app decoded earlier); a point layer without one (a planter or sprayer as-applied file, or other
    points of a machine) is an "export" too, with its "operation" (engine/clean/applied3s.detect_operation) and
    "yield" False; polygon and line shapefiles (boundaries, as-applied polygons, yield polygons) are ignored with a
    note, because the cleaning works on the logged points. An Operations Center export with its metadata also carries
    "monitor_product" (common.jd_monitor_product of its crop: the weight to volume and dry moisture SMS gives it). A
    folder read in place with files beyond the 259 characters Windows opens gets a note: those files are not read.
    The same export found twice (a zip next to its unpacked copy: same file stem,
    feature count and .dbf size) is listed once, and so is the same raw dataset found twice (same decoder and same
    data files by name and size, or one a subset of the other: the larger is kept). A John Deere Operations Center
    export has its crop in the '<stem>-Deere-Metadata.json' written beside it (the Crop column holds a number): the
    candidate carries it as "crop", so that the import picks the product's weight to volume and dry moisture, and a
    note warns when that file declares other units than the feet, lb/ac, bu/ac and mph the columns are read in.
    Every decoder in
    DECODERS runs its cheap ``probe`` (header reads only, no decoding) on every root; a probe that recognises its
    format gives a "raw" candidate. A probe that fails is recorded as a note, never raised: unknown content gives an
    empty list with ``.message`` listing the supported inputs. ISOXML task data (ISO 11783-10: TASKDATA.XML with its
    TLG*.BIN logs) is read by engine/formats/isoxml.py when it holds a harvest log: every TASKDATA folder under a
    root (a zip of zipped sets included) is one candidate. Task data without one (a planter, a sprayer, a setup, a
    damaged set) is named in the message (or in a note beside other candidates) with the reason
    (isoxml.explain), instead of being passed over as unknown content; what the ISOXML probe could not read of a
    readable input is a note too.

The decoders' contract is engine/formats/common.py. The decoder modules are imported lazily so that a broken
decoder only disables its own format (recorded in the notes).
"""
from __future__ import annotations

import importlib
import os
import re
import shutil
import stat
import zipfile
import zlib

from oced_clean._engine.formats import common

DECODERS = ["cnh_cn1", "jd_jdl", "jd_gs3_rcd", "isoxml"]    # module names in engine/formats (FORMAT_ID == module name)
# the vendor software's dry-yield convention, the default of each decoder (docs/formats/*.md)
VENDOR_SHRINK = {"cnh_cn1": "one_sided", "jd_jdl": "one_sided", "jd_gs3_rcd": "one_sided", "isoxml": "one_sided"}

MAX_MEMBERS = 200_000
MAX_TOTAL_BYTES = 40 * 1024 ** 3          # 40 GB unpacked in one call
MAX_RATIO = 400.0                         # unpacked / compressed per member ...
MAX_RATIO_MIN_BYTES = 100 * 1024 ** 2     # ... checked for members above 100 MB unpacked
MAX_DEPTH = 6                             # zips inside zips
PATH_LIMIT = 250                          # longest file path unpacked: Windows opens up to 259 characters (the
                                          # readers also look for names beside a file: .cpg, .qix)
PATH_LIMITED = os.name == "nt"            # ... and only Windows has the limit
_SKIP_DIRS = {"__MACOSX", ".git", "$RECYCLE.BIN", "System Volume Information"}

SUPPORTED_TEXT = (
    "Supported inputs:\n"
    "- yield POINT export as a shapefile (.shp + .shx + .dbf + .prj, zipped or not): Ag Leader SMS, John Deere "
    "Operations Center, or any point file with yield, speed, heading, distance and swath width columns;\n"
    "- planter and sprayer as-applied POINT exports (shapefile, zipped or not), or any other point layer of a "
    "machine;\n"
    "- the same points as a .kml / .kmz (SMS 'Export to Google Earth' of the yield points, zipped or not);\n"
    "- Case IH / New Holland AFS Pro 700 card: the whole '<name>.cn1' folder (zipped or the folder itself);\n"
    "- John Deere Gen4 / G5 / X9 display logs: the JD-Data folder with log/*.jdl (one zip per combine is fine);\n"
    "- John Deere GreenStar 3 (2630) card: the RCD/EIC/Documentation folder with the .fdd + .fdl pairs;\n"
    "- ISOXML harvest task data (ISO 11783, e.g. from a Case IH / New Holland display or its telematics portal): the "
    "TASKDATA folder(s) with TASKDATA.XML and the TLG*.XML / TLG*.BIN time logs (a zip of zipped sets is fine)."
)


class Candidates(list):
    """A list of candidate dicts, plus ``notes`` (what was ignored or failed) and ``message`` (for the user)."""

    def __init__(self, items=(), notes=None, message=""):
        super().__init__(items)
        self.notes = list(notes or [])
        self.message = message
        self.left_out = []                 # damaged zips of the input that were passed over (engine.run.open_input)


def decoder(format_id: str):
    """The decoder module of a FORMAT_ID (ValueError for an unknown id)."""
    if format_id not in DECODERS:
        raise ValueError(f"unknown raw format {format_id!r}; formats are {DECODERS}")
    return importlib.import_module(f"oced_clean._engine.formats.{format_id}")


def default_options(format_id: str) -> common.DecodeOptions:
    """DecodeOptions with the vendor's shrink convention (the contract's dataclass defaults to 'symmetric')."""
    mod = decoder(format_id)
    if hasattr(mod, "default_options"):
        return mod.default_options()
    if hasattr(mod, "DEFAULT_OPTIONS"):
        import dataclasses
        return dataclasses.replace(mod.DEFAULT_OPTIONS)
    return common.DecodeOptions(shrink=VENDOR_SHRINK.get(format_id, "symmetric"))


# ================================================================================================ unpack
_WIN_BAD = re.compile(r'[<>:"|?*\x00-\x1f]')
_WIN_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.I)
RAW_EXTS = (".jdl", ".fdl", ".fdd", ".tso", ".tlo", ".tlh", ".vy1")    # files of a raw monitor dataset (any decoder)


def legal_part(part: str) -> str:
    """One path component as Windows can write it: < > : " | ? * and control characters -> '_', no trailing dot or
    space, '_' before a reserved device name (CON, PRN, AUX, NUL, COM0-9, LPT0-9, with or without an extension).
    On another system the component itself."""
    if os.name != "nt":
        return part
    p = _WIN_BAD.sub("_", part).rstrip(" .")
    return "_" + p if _WIN_RESERVED.match(p) else p


def _safe_target(base: str, member: str) -> str | None:
    """Absolute path of a zip member under base, or None when it would leave base (zip slip). Windows drops trailing
    dots and spaces from a path component, so '.. ' and '...' count as '..'; every other component is made legal
    (legal_part), and the target is built on abspath(base), which Windows trims too (a zip 'Data .zip' unpacks
    into 'Data')."""
    name = member.replace("\\", "/")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        return None
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts or any(p.rstrip(" .") in ("", "..") for p in parts):
        return None
    parts = [legal_part(p) for p in parts]
    if any(p in ("", "..") for p in parts):
        return None
    base_abs = os.path.abspath(base)
    target = os.path.abspath(os.path.join(base_abs, *parts))
    try:
        common_ = os.path.commonpath([os.path.normcase(target), os.path.normcase(base_abs)])
    except ValueError:                                    # another drive, a device name: outside base
        return None
    return target if common_ == os.path.normcase(base_abs) else None


def _free_name(path: str) -> str:
    """path, or path_2, path_3 ... when it already exists."""
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{stem}_{i}{ext}"):
        i += 1
    return f"{stem}_{i}{ext}"


def _long(path: str) -> str:
    r"""path in the extended-length form of Windows (\\?\C:\... or \\?\UNC\server\share\...), which the file system
    takes beyond 260 characters (a folder beyond 248); the path itself on another system."""
    if os.name != "nt":
        return path
    p = os.path.abspath(path)
    if p.startswith("\\\\?\\"):
        return p
    return "\\\\?\\UNC\\" + p[2:] if p.startswith("\\\\") else "\\\\?\\" + p


def _targets(zpath: str, dest_dir: str, infos, budget: dict, log) -> tuple[str, list]:
    """(folder, [path of each member]) of a zip unpacked into dest_dir, with the zip-slip guard. When a file would
    land beyond PATH_LIMIT there, the zip goes into a short folder of the unpack folder instead (budget["root"]/z1,
    z2 ...; a short extension of the stem is kept, so a card zipped as '<name>.cn1.zip' stays a .cn1 folder) and the
    log says it; when even that is too long the ValueError names the real cause and what to do."""
    def paths(folder):
        out = []
        for i in infos:
            t = _safe_target(folder, i.filename)
            if t is None:
                raise ValueError(f"{os.path.basename(zpath)}: member {i.filename!r} would be written outside the "
                                 "unpack folder (zip slip): refusing to unpack")
            out.append(t)
        return out

    def longest(ts):
        return max((len(t) for i, t in zip(infos, ts) if not i.is_dir()), default=0)

    targets = paths(dest_dir)
    seen, clash = {}, []
    for i, t in zip(infos, targets):
        k = os.path.normcase(t)
        if k in seen and not i.is_dir():
            clash.append((seen[k], i.filename))
        seen.setdefault(k, i.filename)
    if clash:
        log(f"  {os.path.basename(zpath)}: {len(clash)} member(s) end on the same Windows name as another one (e.g. "
            f"{clash[0][1]!r} and {clash[0][0]!r}): the later one replaces the earlier")
    n0 = longest(targets)
    if not PATH_LIMITED or n0 <= PATH_LIMIT or not budget.get("root"):
        return dest_dir, targets
    budget["short"] = budget.get("short", 0) + 1
    ext = os.path.splitext(os.path.basename(dest_dir))[1]
    short = _free_name(os.path.join(budget["root"], f"z{budget['short']}" + (ext if 1 < len(ext) <= 5 else "")))
    targets = paths(short)
    n1 = longest(targets)
    if n1 > PATH_LIMIT:
        raise ValueError(f"{os.path.basename(zpath)}: the paths inside it are too long for Windows under the work "
                         f"folder ({n1} characters even in the short folder {short}; a file opens up to "
                         f"{PATH_LIMIT}). The zip is not damaged: unzip it into a short folder (for example C:\\data) "
                         "and open that folder, or move the workspace to a shorter path.")
    log(f"  {os.path.basename(zpath)}: unpacked into the short folder {os.path.basename(short)} (under "
        f"{os.path.basename(dest_dir)} its paths would reach {n0} characters; Windows opens a file up to 259)")
    return short, targets


def _same_as_folder(zpath: str, folder: str) -> bool:
    """True when folder already holds every file of the zip with the same size: a zip lying beside its own unpacked
    copy, whose data would otherwise be unpacked and read a second time."""
    if not os.path.isdir(folder):
        return False
    try:
        with zipfile.ZipFile(zpath) as zf:
            files = [i for i in zf.infolist() if not i.is_dir()]
    except (zipfile.BadZipFile, OSError):
        return False                                      # extract_zip reports a zip that cannot be read
    for i in files:
        t = _safe_target(folder, i.filename)
        try:
            if t is None or os.path.getsize(_long(t)) != i.file_size:
                return False
        except OSError:
            return False
    return bool(files)


class DamagedZip(ValueError):
    """A zip that cannot be read, or a member of it that cannot be unpacked, because the zip itself is damaged
    (never a guard, never the file system)."""


LEFT_OUT = "left out"       # what a note about a damaged zip passed over says (Candidates.left_out, the app warns)


def _leave_out(z: str, sub: str, e: Exception, budget: dict, log=print):
    """A damaged zip among the zips of an input (a zip inside a zip, a zip in a folder given) does not stop the
    others: what was unpacked of it is removed, so that no half set is read as a whole one, and it is noted for
    the user (budget["left_out"] -> unpack().left_out)."""
    try:
        if os.path.isdir(_long(sub)):
            shutil.rmtree(_long(sub))
    except OSError:
        pass
    msg = (f"{os.path.basename(z)}: a damaged zip among those of the input, {LEFT_OUT} ({e}); the other files of "
           "the input are read - what was in this zip is NOT in the data")
    budget.setdefault("left_out", []).append(msg)
    log("  " + msg)


def extract_zip(zpath: str, dest_dir: str, budget: dict, depth: int = 0, log=print) -> str:
    """Extract one zip into dest_dir (created) with the guards, then every zip it contains, recursively. Returns the
    folder it went into: dest_dir, or the short folder _targets chose because the paths under dest_dir are too long
    for Windows. Folders and files are created through the extended-length form (_long), so a folder path between
    248 and PATH_LIMIT is not refused either. A damaged zip raises DamagedZip (a ValueError); a damaged zip INSIDE
    this one is left out with a note (_leave_out) and the rest is unpacked."""
    if depth > MAX_DEPTH:
        raise ValueError(f"{os.path.basename(zpath)}: zips nested more than {MAX_DEPTH} levels deep")
    try:
        zf = zipfile.ZipFile(zpath)
    except zipfile.BadZipFile as e:
        raise DamagedZip(f"{os.path.basename(zpath)} is not a readable zip file ({e})") from None
    with zf:
        infos = zf.infolist()
        budget["members"] += len(infos)
        if budget["members"] > MAX_MEMBERS:
            raise ValueError(f"more than {MAX_MEMBERS:,} files inside the zip(s): refusing to unpack")
        for i in infos:
            if i.is_dir():
                continue
            if i.file_size > MAX_RATIO_MIN_BYTES and i.file_size > MAX_RATIO * max(i.compress_size, 1):
                raise ValueError(f"{os.path.basename(zpath)}: member {i.filename!r} expands {i.file_size / max(i.compress_size, 1):,.0f}"
                                 " times (zip bomb?): refusing to unpack")
            budget["bytes"] += i.file_size
            if budget["bytes"] > MAX_TOTAL_BYTES:
                raise ValueError(f"the zip(s) unpack to more than {MAX_TOTAL_BYTES / 1024 ** 3:.0f} GB: refusing")
        dest_dir, targets = _targets(zpath, dest_dir, infos, budget, log)
        try:
            os.makedirs(_long(dest_dir), exist_ok=True)
        except OSError as e:
            raise ValueError(f"{os.path.basename(zpath)}: the unpack folder {dest_dir} cannot be created ({type(e).__name__}: "
                             f"{e}); the zip itself may be fine - unzip it into a short folder and open that") from None
        inner = []
        for i, target in zip(infos, targets):
            if i.is_dir():
                try:
                    os.makedirs(_long(target), exist_ok=True)
                except OSError as e:
                    raise ValueError(f"{os.path.basename(zpath)}: folder {i.filename!r} could not be created "
                                     f"({type(e).__name__}: {e}); the file system refused {target} (the zip itself may "
                                     "be fine)") from None
                continue
            try:
                os.makedirs(_long(os.path.dirname(target)), exist_ok=True)
                with zf.open(i) as src, open(_long(target), "wb") as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
            except (zipfile.BadZipFile, zlib.error, EOFError, OSError, NotImplementedError, RuntimeError) as e:
                try:
                    if os.path.isfile(_long(target)):
                        os.remove(_long(target))
                except OSError:
                    pass
                # an OSError comes from the file system (no space, no permission, a share that went away), not from
                # the zip: saying "damaged zip" there sends the user to make a new zip that fails the same way
                why = (f"the file system refused {target}: check the free space and the permissions of the work "
                       "folder (the zip itself may be fine)" if isinstance(e, OSError) else
                       "the zip is damaged, protected by a password or uses an unsupported compression - make a new "
                       "zip")
                raise (ValueError if isinstance(e, OSError) else DamagedZip)(
                    f"{os.path.basename(zpath)}: member {i.filename!r} could not be unpacked "
                    f"({type(e).__name__}: {e}); {why}") from None
            if target.lower().endswith(".zip"):
                inner.append(target)
    budget["zips"] += 1
    for z in inner:
        stem = os.path.splitext(z)[0]
        if _same_as_folder(z, stem):
            log(f"  nested {os.path.relpath(z, dest_dir)}: the folder beside it holds the same files, not unpacked "
                "again")
            continue
        log(f"  unpacking nested {os.path.relpath(z, dest_dir)}")
        sub = _free_name(stem)
        try:
            extract_zip(z, sub, budget, depth + 1, log=log)
        except DamagedZip as e:
            _leave_out(z, sub, e, budget, log)
    return dest_dir


def _has_raw(folder: str, skip_zips_of: str | None = None) -> bool:
    """True when folder holds a file of a raw monitor dataset (RAW_EXTS, or the TASKDATA.XML of ISOXML task data)
    outside the zips."""
    for dp, dn, fn in os.walk(folder):
        dn[:] = [d for d in dn if d not in _SKIP_DIRS]
        if any(f.lower().endswith(RAW_EXTS) or f.upper() == "TASKDATA.XML" for f in fn):
            return True
    return False


def _remove_new(dest: str, before: set, log=print):
    """Remove what an unpack call created in dest (names not there before it)."""
    for x in sorted(set(os.listdir(dest)) - before):
        p = os.path.join(dest, x)
        try:
            if os.path.isdir(p):
                shutil.rmtree(_long(p))
            else:
                os.remove(_long(p))
        except OSError as e:
            log(f"could not remove {p} after the failure ({type(e).__name__}: {e})")


class Roots(list):
    """The roots unpack() returns, plus ``left_out``: the damaged zips among those of the input that were passed
    over (one plain sentence each)."""
    left_out: list = []


def unpack(paths, dest: str, copy_folders: bool = False, log=print) -> list[str]:
    """Copy / extract `paths` (files or folders) into dest. Returns the roots to scan with detect(): dest itself plus,
    with copy_folders False, every given folder (read in place). Raises ValueError on a guard or a missing path; what
    the call unpacked into dest is then removed. A zip GIVEN that is damaged is such an error; a damaged zip among
    the zips inside a zip or a folder given is left out and named in the result's ``left_out`` (Roots)."""
    if isinstance(paths, (str, os.PathLike)):
        paths = [paths]
    dest = os.path.abspath(dest)
    os.makedirs(dest, exist_ok=True)
    before = set(os.listdir(dest))
    try:
        return _unpack(paths, dest, copy_folders, log)
    except (ValueError, OSError):
        _remove_new(dest, before, log=log)
        raise


def _unpack(paths, dest, copy_folders, log):
    budget = {"members": 0, "bytes": 0, "zips": 0, "root": dest, "short": 0}
    roots = [dest]
    for p in paths:
        p = os.path.abspath(str(p).strip().strip('"'))
        if not os.path.exists(p):
            raise ValueError(f"not found: {p}")
        name = os.path.basename(p.rstrip("\\/")) or "input"
        if os.path.isdir(p):
            if copy_folders:
                tgt = _free_name(os.path.join(dest, name))
                log(f"copying folder {p}")
                shutil.copytree(p, tgt)
                zips = [os.path.join(dp, f) for dp, dn, fn in os.walk(tgt) for f in fn if f.lower().endswith(".zip")]
            else:
                log(f"reading folder in place: {p}")
                roots.append(p)
                zips = []
                for dp, dn, fn in os.walk(p):
                    dn[:] = [d for d in dn if d not in _SKIP_DIRS]
                    zips += [os.path.join(dp, f) for f in fn if f.lower().endswith(".zip")]
            unpacked = []
            for z in zips:
                if _same_as_folder(z, os.path.splitext(z)[0]):
                    log(f"{os.path.relpath(z, os.path.dirname(p))}: the folder beside it holds the same files, not "
                        "unpacked")
                    continue
                rel = os.path.relpath(os.path.splitext(z)[0], p if not copy_folders else tgt)
                sub = _free_name(os.path.join(dest, name + "__zips", rel))
                log(f"unpacking {os.path.relpath(z, os.path.dirname(p))}")
                try:
                    unpacked.append(extract_zip(z, sub, budget, 1, log=log))
                except DamagedZip as e:
                    _leave_out(z, sub, e, budget, log)
            # one harvest partly loose, partly zipped: the loose part is copied next to the unpacked zips, so that the
            # decoders see it under one root (dest) and not as a second dataset of the same field
            # (not when the work folder lies inside the folder given: a folder cannot be copied into itself)
            inside = os.path.normcase(dest).startswith(os.path.normcase(p).rstrip("\\/") + os.sep)
            if (not copy_folders and unpacked and any(_has_raw(u) for u in unpacked) and _has_raw(p)
                    and not inside):
                tgt = _free_name(os.path.join(dest, name))
                log(f"{name}: raw monitor files both loose and inside its zip(s): the loose files are copied into the "
                    "work folder and read together with the zips' (one dataset)")
                shutil.copytree(p, tgt, ignore=shutil.ignore_patterns("*.zip", "*.ZIP", *_SKIP_DIRS))
                roots.remove(p)
        elif p.lower().endswith(".zip"):
            log(f"unpacking {name}")
            extract_zip(p, _free_name(os.path.join(dest, os.path.splitext(name)[0])), budget, 0, log=log)
        else:
            # a shapefile is several files: copy every sibling with the same stem (.shx, .dbf, .prj, .cpg, ...) and
            # the Operations Center's '<stem>-Deere-Metadata.json' (the crop and the units of the export)
            files = [p]
            if p.lower().endswith(".shp"):
                stem = os.path.splitext(name)[0].lower()
                d = os.path.dirname(p)
                files = [os.path.join(d, f) for f in os.listdir(d)
                         if (os.path.splitext(f)[0].lower() == stem or f.lower() == stem + "-deere-metadata.json")
                         and os.path.isfile(os.path.join(d, f))]
            for f in files:
                tgt = os.path.join(dest, os.path.basename(f))
                if os.path.normcase(os.path.abspath(tgt)) != os.path.normcase(f):
                    shutil.copy2(f, tgt)
                    os.chmod(tgt, stat.S_IREAD | stat.S_IWRITE)
    if budget["zips"]:
        log(f"unpacked {budget['zips']} zip(s), {budget['members']:,} entries, {budget['bytes'] / 1024 ** 2:,.1f} MB")
    _convert_kml_points(roots, dest, log=log)
    roots = Roots(roots)
    roots.left_out = list(budget.get("left_out") or [])
    return roots


def _convert_kml_points(roots, dest, log=print):
    """Every KML / KMZ of yield points under the roots -> a point shapefile in dest/<kml stem>__kml/ (kml_points)."""
    found, seen = [], set()
    for r in roots:
        for dp, dn, fn in os.walk(r):
            dn[:] = [d for d in dn if d not in _SKIP_DIRS]
            for f in fn:
                p = os.path.abspath(os.path.join(dp, f))
                if f.lower().endswith((".kml", ".kmz")) and os.path.normcase(p) not in seen:
                    seen.add(os.path.normcase(p))
                    found.append(p)
    if not found:
        return
    from oced_clean._engine.formats import kml_points
    for p in found:
        if not kml_points.looks_like_points(p):
            continue                                      # a boundary or a map overlay, not yield points
        out = _free_name(os.path.join(dest, os.path.splitext(os.path.basename(p))[0] + "__kml"))
        try:
            _, _, notes = kml_points.to_shapefile(p, out, log=log)
        except OSError as e:
            raise ValueError(f"{os.path.basename(p)}: the KML could not be converted ({type(e).__name__}: {e})") from None
        for m in notes:
            log(m)


# ================================================================================================ detect
def _walk(root):
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in _SKIP_DIRS and not d.startswith("."))
        yield dp, sorted(fn)


_KML_DIR = re.compile(r"__kml(_\d+)?$")                 # the folders unpack() writes converted KML points into
# the units the Operations Center columns are read in (its metadata names them like this)
DEERE_UNITS = {"DISTANCE": "ft", "SWATHWIDTH": "ft", "WetMass": "lb1ac-1", "VRYIELDVOL": "bu1ac-1",
               "VEHICLSPEED": "mi1hr-1"}
OPERATION_SAMPLE_N = 200                                # records read to tell seeding by mass from an application


def _deere_metadata(shp: str) -> dict | None:
    """What the '<stem>-Deere-Metadata.json' beside a John Deere Operations Center shapefile export says, or None
    without one: {"crop": its CropName ('Wheat (Hard Red Spring)'; the export's Crop column holds only the crop's
    number), "crop_id": its CropId, "variety": its Product name, "field": its FieldName (None when it names none),
    "units": {column: unit} of the columns in DEERE_UNITS that it declares in another unit, "machines": {Machine
    column value (the export's machine index, '1', '2' ...): the machine's serial number (VIN) from MachineUsage} ({}
    when the metadata names none)}."""
    import json
    path = os.path.splitext(shp)[0] + "-Deere-Metadata.json"
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8-sig") as fh:
            meta = json.load(fh)
        units = {str(a.get("Name")): str(a.get("Unit")) for a in meta.get("DataAttributes") or [] if a.get("Unit")}
    except (OSError, ValueError, AttributeError, TypeError):
        return None                                        # not the metadata of an export: the file is read without
    other = {k: units[k] for k, u in DEERE_UNITS.items() if k in units and units[k] != u}
    try:
        cid = int(meta.get("CropId")) if meta.get("CropId") is not None else None
    except (TypeError, ValueError):
        cid = None
    prod = meta.get("Product") if isinstance(meta.get("Product"), dict) else {}
    return {"crop": str(meta.get("CropName") or "").strip() or None, "crop_id": cid,
            "variety": str(prod.get("ProductName") or "").strip() or None,
            "field": str(meta.get("FieldName") or "").strip() or None, "units": other,
            "machines": machine_serials(meta)}


def machine_serials(meta) -> dict:
    """{machine index as the export's Machine column holds it ('1', '2' ...): serial number (VIN)} from the
    MachineUsage of an Operations Center metadata dict; an index without a serial is left out."""
    out = {}
    mu = meta.get("MachineUsage") if isinstance(meta, dict) else None
    if isinstance(mu, dict):
        items = mu.items()
    elif isinstance(mu, list):                             # a list form: the index is the position, from 1
        items = ((str(i + 1), v) for i, v in enumerate(mu))
    else:
        return out
    for k, v in items:
        s = str((v or {}).get("MachineSerial") or "").strip() if isinstance(v, dict) else ""
        if s:
            out[str(k).strip()] = s
    return out


def _exports(root, notes):
    import pyogrio
    out = []
    for dp, fn in _walk(root):
        low = {f.lower(): f for f in fn}
        for f in fn:
            if not f.lower().endswith(".shp"):
                continue
            path = os.path.join(dp, f)
            stem = os.path.splitext(f)[0].lower()
            missing = [e for e in (".shx", ".dbf") if stem + e not in low]
            if missing:
                notes.append(f"{os.path.relpath(path, root)}: ignored, {' and '.join(missing)} missing next to it")
                continue
            try:
                info = pyogrio.read_info(path)
            except Exception as e:          # an unreadable file is reported, never fatal
                notes.append(f"{os.path.relpath(path, root)}: ignored, not readable ({e})")
                continue
            gt = str(info.get("geometry_type") or "")
            if "point" not in gt.lower() or "multipoint" in gt.lower():
                notes.append(f"{os.path.relpath(path, root)}: {gt or 'no'} geometry, ignored (OCED reads the "
                             "logged POINTS of a machine)")
                continue
            from oced_clean._engine.clean.yield_clean import detect_columns
            det = detect_columns(list(info["fields"]))
            is_yield = bool(det["yield"] or det["yield_wet"] or det["wet_mass_lbac"])
            if is_yield:
                operation, op_label = "harvest", "Yield export"
            else:
                # OCED: a planter or sprayer as-applied layer, or any other point layer of a machine; cleaned by
                # engine/clean/applied3s.py (3-sigma on the attributes chosen) instead of the yield cleaning. A sample
                # of the records lets a mass rate whose product names a crop read as seeding, as applied3s.run does
                from oced_clean._engine.clean.applied3s import detect_operation
                try:
                    sample = pyogrio.read_dataframe(path, read_geometry=False, max_features=OPERATION_SAMPLE_N)
                except Exception:           # the columns alone decide then (never fatal)
                    sample = None
                op = detect_operation(list(info["fields"]), sample)
                operation, op_label = op["operation"], op["label"]
            if stem + ".prj" not in low:
                notes.append(f"{os.path.relpath(path, root)}: no .prj next to it; the cleaning will refuse it "
                             "(it cannot measure distances without a coordinate system)")
            dbf = os.path.join(dp, low[stem + ".dbf"])
            kml = " (converted from KML)" if _KML_DIR.search(os.path.basename(dp)) else ""
            meta = _deere_metadata(path) or {}
            mon = (common.jd_monitor_product(meta.get("crop"), f"CropName / CropId of {stem}-Deere-Metadata.json "
                                             "(Operations Center)", crop_id=meta.get("crop_id"))
                   if (meta.get("crop") or meta.get("crop_id") is not None) else None)
            if meta.get("units"):
                notes.append(f"{os.path.relpath(path, root)}: its Operations Center metadata gives "
                             + ", ".join(f"{k} in {u}" for k, u in meta["units"].items())
                             + "; OCED reads these columns as "
                             + ", ".join(f"{k} in {DEERE_UNITS[k]}" for k in meta["units"])
                             + ": the areas and the yields will be wrong, export it again in imperial units")
            out.append({"kind": "export", "path": os.path.abspath(path), "n": int(info["features"]),
                        "crop": meta.get("crop"), "monitor_product": mon,
                        "machine_serials": dict(meta.get("machines") or {}),
                        "columns": list(info["fields"]), "geometry_type": gt, "crs": info.get("crs"),
                        "same_key": (stem, int(info["features"]), os.path.getsize(dbf)),
                        "operation": operation, "yield": is_yield,
                        "label": (f"Point export{kml} · {f} · {int(info['features']):,} points" if is_yield else
                                  f"{op_label}{kml} · {f} · {int(info['features']):,} points")})
    return out


def _isoxml(roots) -> tuple[int, int]:
    """(task data sets, log files) of ISOXML under the roots: every TASKDATA.XML and every TLG*.BIN (ISO 11783-10,
    the task data of ISOBUS terminals)."""
    sets = logs = 0
    for r in roots:
        if not os.path.isdir(r):
            continue
        for _, fn in _walk(r):
            for f in fn:
                u = f.upper()
                sets += u == "TASKDATA.XML"
                logs += u.startswith("TLG") and u.endswith(".BIN")
    return int(sets), int(logs)


def _isoxml_note(roots) -> str:
    """What to say about ISOXML task data under the roots that gave no harvest candidate ('' without any): that it
    was found and why no harvest data is read from it (engine/formats/isoxml.explain)."""
    n_iso, n_tlg = _isoxml(roots)
    if not n_iso:
        return ""
    why = ""
    try:
        mod = decoder("isoxml")
        why = next((w for w in (mod.explain(r) for r in roots if os.path.isdir(r)) if w), "")
    except Exception as e:                  # the reader itself failing is the reason given, never raised
        why = f"the ISOXML reader failed ({type(e).__name__}: {e})"
    return (f"ISOXML task data was found ({n_iso:,} TASKDATA.XML with {n_tlg:,} TLG log file(s): the ISO 11783 "
            "format of ISOBUS terminals), but no harvest data is read from it: "
            + (why or "it holds no harvest time log") + ".")


# data files that identify a raw dataset (a copy of the same dataset has the same names and sizes)
_RAW_DATA_EXT = {"jd_jdl": (".jdl",), "jd_gs3_rcd": (".fdl", ".fdd"), "cnh_cn1": None,    # None = every file
                 "isoxml": (".bin",)}


def _raw_signature(fid, root):
    exts = _RAW_DATA_EXT.get(fid)
    sig = set()
    for dp, fn in _walk(root):
        for f in fn:
            if f.lower().endswith(".zip") or (exts and not f.lower().endswith(exts)):
                continue
            try:
                sig.add((f.lower(), os.path.getsize(os.path.join(dp, f))))
            except OSError:
                continue
    return frozenset(sig)


def _plain(p: str) -> str:
    if p.startswith("\\\\?\\UNC\\"):
        return "\\\\" + p[8:]
    return p[4:] if p.startswith("\\\\?\\") else p


def files_beyond_limit(root: str, limit: int = 259) -> int:
    """How many files under root have a path longer than Windows opens (os.walk and glob on the plain path silently
    skip them): counted through the extended-length form. 0 on another system."""
    if not PATH_LIMITED or not os.path.isdir(root):
        return 0
    n = 0
    for dp, dn, fn in os.walk(_long(root)):
        dn[:] = [d for d in dn if d not in _SKIP_DIRS]
        base = _plain(dp)
        n += sum(len(os.path.join(base, f)) > limit for f in fn)
    return n


def detect(roots) -> Candidates:
    """Candidates found under one root (str) or several. Never raises for unknown or broken content."""
    if isinstance(roots, (str, os.PathLike)):
        roots = [roots]
    roots = [os.path.abspath(str(r)) for r in roots]
    notes, cands, seen_exports, seen_raw = [], [], set(), set()
    for r in roots:
        if not os.path.isdir(r):
            notes.append(f"{r}: not a folder")
            continue
        n_long = files_beyond_limit(r)
        if n_long:
            notes.append(f"{r}: {n_long:,} file(s) in it have a path longer than the 259 characters Windows opens and "
                         "are NOT read: copy or unzip the data into a shorter folder (for example C:\\data) and open "
                         "that")
        for c in _exports(r, notes):
            key = os.path.normcase(c["path"])
            if key in seen_exports:
                continue
            seen_exports.add(key)
            twin = next((x for x in cands if x["kind"] == "export" and x["same_key"] == c["same_key"]), None)
            if twin is not None:
                notes.append(f"{c['path']}: same export as {twin['path']} (same name, points and .dbf size), "
                             "listed once")
                continue
            cands.append(c)
    for fid in DECODERS:
        try:
            mod = decoder(fid)
        except Exception as e:
            notes.append(f"decoder {fid} could not be loaded ({type(e).__name__}: {e}); that format is unavailable")
            continue
        for r in roots:
            if not os.path.isdir(r):
                continue
            try:
                pr = mod.probe(r)
            except Exception as e:
                notes.append(f"{mod.LABEL}: the probe failed on {r} ({type(e).__name__}: {e})")
                continue
            if pr is None:
                continue
            key = (fid, os.path.normcase(os.path.abspath(pr.root)))
            # the same dataset reached from two roots (a folder and the dest it was unpacked into) counts once
            if key in seen_raw:
                continue
            seen_raw.add(key)
            cands.append({"kind": "raw", "decoder": fid, "probe": pr, "label": f"{pr.label} · {pr.summary}"})
            if getattr(mod, "PROBE_NOTES", False):         # what this probe could not read of the input, said once
                notes.extend(f"{mod.LABEL}: {w}" for w in pr.warnings if f"{mod.LABEL}: {w}" not in notes)
    # a raw dataset found again inside another candidate's folder (probes walk recursively, so the outer one already
    # covers it) is reported once per decoder
    by_fid = {}
    for c in cands:
        if c["kind"] == "raw":
            by_fid.setdefault(c["decoder"], []).append(c)
    drop = set()
    for fid, cs in by_fid.items():
        for a in cs:
            for b in cs:
                if a is b:
                    continue
                ra, rb = os.path.normcase(a["probe"].root), os.path.normcase(b["probe"].root)
                inside = ra != rb and (ra.startswith(rb.rstrip("\\/") + os.sep))
                if inside and a["probe"].n_files <= b["probe"].n_files:
                    drop.add(id(a))
    cands = [c for c in cands if id(c) not in drop]
    # the same raw dataset reached twice by different roots (a folder holding both the zips and their unpacked copy):
    # same decoder and the same data files by name and size, or one a subset of the other -> keep the larger once
    drop = set()
    raw = [c for c in cands if c["kind"] == "raw"]
    sigs = {}
    for i, a in enumerate(raw):
        for b in raw[i + 1:]:
            if a["decoder"] != b["decoder"] or id(a) in drop or id(b) in drop:
                continue
            sa = sigs.setdefault(id(a), _raw_signature(a["decoder"], a["probe"].root))
            sb = sigs.setdefault(id(b), _raw_signature(b["decoder"], b["probe"].root))
            if sa and sb and (sa <= sb or sb <= sa):
                small, big = (a, b) if len(sa) < len(sb) else (b, a)          # equal: keep the first
                drop.add(id(small))
                notes.append(f"{small['probe'].root}: same {small['decoder']} data files as {big['probe'].root}, "
                             "listed once")
    cands = [c for c in cands if id(c) not in drop]
    msg = ""
    iso = "" if any(c.get("decoder") == "isoxml" for c in cands) else _isoxml_note(roots)
    if not cands:
        msg = (iso + "\n" if iso else "Nothing usable was recognised in the input.\n") + SUPPORTED_TEXT
    elif iso:
        notes.append(iso)
    return Candidates(cands, notes, msg)
