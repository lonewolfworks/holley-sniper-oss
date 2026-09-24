#!/usr/bin/env python3
"""
Generate a Markdown changelog between two Sniper .sniper config files.

Wraps config_parser.py's diff_tree() (a flat list of (dotted_path, old, new)
tuples) and groups it into readable sections instead of a raw field dump:
table-shaped diffs (Base Fuel VE, Target AFR, Learn, compensation-limit grids)
are summarized -- cell count, mean/min/max delta, and RPM/MAP labels when the
table's own axes are known -- rather than listed cell-by-cell; everything else
is grouped under a friendly section heading as plain "field: old -> new" lines.

Meant to run in CI (see .github/workflows/release.yml in this same folder for
a starting point) but works standalone:

    python3 generate_changelog.py OLD.sniper NEW.sniper [--out CHANGELOG.md]

STUB / first pass (2026-09-23): SECTION_MAP and TABLE_AXIS_HINTS only cover
the config sections this project has actually dealt with so far. A config
section that isn't in SECTION_MAP still gets reported (under "Other"), just
without a friendly heading -- extend the maps below as new sections come up,
rather than silently dropping anything.
"""
import argparse
import re
import statistics as stats
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config_parser import parse_sniper, diff_tree  # noqa: E402

# (path prefix, friendly section title) -- first match wins, order matters
# for the specific-before-general prefixes under "fuel."/"spark."/etc.
SECTION_MAP = [
    ("fuel.base_fuel_table_ve_pct", "Base Fuel (VE) Table"),
    ("fuel.learn_table_pct", "Learn Table"),
    ("fuel.target_afr_table", "Target AFR Table"),
    ("fuel.target_afr_setpoints", "Target AFR Setpoints"),
    ("fuel.acceleration_enrichment", "Acceleration Enrichment"),
    ("fuel.temperature_enrichment", "Temperature Enrichment"),
    ("fuel.startup_enrichment", "Startup Enrichment"),
    ("fuel.", "Fuel (other)"),
    ("spark.", "Ignition / Timing"),
    ("idle.", "Idle"),
    ("system_parameters.closed_loop_learn", "Closed Loop & Learn"),
    ("system_parameters.engine_parameters", "Engine Parameters"),
    ("system_parameters.efi_parameters", "EFI Parameters"),
    ("system_parameters.basic_io_fans_ac", "Fans / A-C / Basic I/O"),
    ("system_parameters.", "System Parameters (other)"),
]
# Pure metadata -- not a real config change, always skip.
SKIP_PATHS = {"file", "embedded_filename", "version_string"}

# For 2D table diffs: the (rpm_axis, map_axis) dotted paths to pull real axis
# values from the NEW file's own parsed dict, so cells read as "1360rpm @
# 47kPa" instead of "[9][1]". Confirmed convention (see this project's
# afr_grid()/knuckle-config investigation, 2026-09-21): table[row][col] ==
# table[map_index][rpm_index] -- row is MAP, column is RPM.
TABLE_AXIS_HINTS = {
    "fuel.base_fuel_table_ve_pct.table": ("fuel.base_fuel_table_ve_pct.rpm_axis",
                                           "fuel.base_fuel_table_ve_pct.map_axis_kpa"),
    "fuel.target_afr_table.table": ("fuel.target_afr_table.rpm_axis",
                                     "fuel.target_afr_table.map_axis_kpa"),
    "fuel.learn_table_pct.table": ("fuel.learn_table_pct.rpm_axis",
                                    "fuel.learn_table_pct.map_axis_kpa"),
}

MAX_CELLS_LISTED = 8  # a table diff with more cells than this gets summarized, not listed


def get_path(d, dotted):
    cur = d
    for part in dotted.split("."):
        cur = cur[part]
    return cur


def group_key(path):
    """Strip trailing [idx] / [idx][idx] so all cells of one table/curve share a key.
    Also strips a trailing '.<+|-> .<band label like 0-13>' segment -- compensation-
    limit-shaped tables are a dict of sign -> dict of map-band-label -> list of
    rpm-bin values, and without this every band/sign combination would otherwise
    form its own group instead of one group for the whole table."""
    key = re.sub(r"(\[\d+\])+$", "", path)
    key = re.sub(r"\.[+-]\.\d+-\d+$", "", key)
    return key


def bracket_indices(path, base):
    suffix = path[len(base):]
    return tuple(int(x) for x in re.findall(r"\[(\d+)\]", suffix))


def format_group(base, entries, new_dict):
    """entries: list of (path, old, new) all sharing group_key == base."""
    lines = []
    n_total = len(entries)

    # Generic shortcut, checked before any shape-specific logic: if every entry
    # made the exact same old->new change, that's one fact ("40.0 -> 100.0,
    # every cell"), not N lines -- true for a uniform blanket table edit
    # regardless of whether the table is a 2D grid, a compensation-limit-shaped
    # dict, or anything else.
    if n_total > 1:
        olds = {e[1] for e in entries}
        news = {e[2] for e in entries}
        if len(olds) == 1 and len(news) == 1:
            lines.append(f"- **{n_total} cell(s) changed, uniformly**: {entries[0][1]} → {entries[0][2]}")
            return lines

    axis_hint = TABLE_AXIS_HINTS.get(base)
    is_2d = axis_hint is not None and all(len(bracket_indices(p, base)) == 2 for p, _, _ in entries)

    if is_2d:
        rpm_axis = get_path(new_dict, axis_hint[0])
        map_axis = get_path(new_dict, axis_hint[1])
        cells = []
        for path, old, new in entries:
            ri, ci = bracket_indices(path, base)
            try:
                label = f"{rpm_axis[ci]}rpm @ {map_axis[ri]}kPa"
            except (IndexError, TypeError):
                label = f"[{ri}][{ci}]"
            delta = (new - old) if isinstance(new, (int, float)) and isinstance(old, (int, float)) else None
            cells.append((label, old, new, delta))

        deltas = [c[3] for c in cells if c[3] is not None]
        n = len(cells)
        if deltas:
            lines.append(f"- **{n} cell(s) changed** — delta mean {stats.fmean(deltas):+.2f}, "
                          f"range {min(deltas):+.2f} to {max(deltas):+.2f}")
        else:
            lines.append(f"- **{n} cell(s) changed**")

        if n <= MAX_CELLS_LISTED:
            for label, old, new, _ in cells:
                lines.append(f"  - {label}: {old} → {new}")
        else:
            worst = sorted(cells, key=lambda c: abs(c[3]) if c[3] is not None else 0, reverse=True)
            lines.append(f"  - Largest {min(5, n)} changes:")
            for label, old, new, delta in worst[:5]:
                dstr = f" ({delta:+.2f})" if delta is not None else ""
                lines.append(f"    - {label}: {old} → {new}{dstr}")
    elif n_total <= MAX_CELLS_LISTED:
        for path, old, new in entries:
            suffix = path[len(base):] if path != base else path
            lines.append(f"- `{suffix}`: {old} → {new}")
    else:
        # Not a recognized 2D table and not uniform (caught above) -- still
        # summarize rather than dumping every field, same discipline as the
        # 2D-table branch.
        deltas = [(new - old) for _, old, new in entries
                  if isinstance(new, (int, float)) and isinstance(old, (int, float))]
        if deltas:
            lines.append(f"- **{n_total} field(s) changed** — delta mean {stats.fmean(deltas):+.2f}, "
                          f"range {min(deltas):+.2f} to {max(deltas):+.2f}")
        else:
            lines.append(f"- **{n_total} field(s) changed**")
        lines.append(f"  - First {min(5, n_total)}:")
        for path, old, new in entries[:5]:
            suffix = path[len(base):] if path != base else path
            lines.append(f"    - `{suffix}`: {old} → {new}")
    return lines


def build_changelog(old_path, new_path):
    old = parse_sniper(old_path)
    new = parse_sniper(new_path)
    diffs = [d for d in diff_tree(old, new) if d[0] not in SKIP_PATHS]

    lines = [f"# Config changelog: `{Path(old_path).name}` → `{Path(new_path).name}`", ""]
    if not diffs:
        lines.append("No differences found.")
        return "\n".join(lines)

    groups, order = {}, []
    for path, old_v, new_v in diffs:
        key = group_key(path)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append((path, old_v, new_v))

    sectioned = {title: [] for _, title in SECTION_MAP}
    other = []
    for key in order:
        title = next((t for prefix, t in SECTION_MAP if key.startswith(prefix)), None)
        (sectioned[title] if title else other).append(key)

    for _, title in SECTION_MAP:
        keys = sectioned[title]
        if not keys:
            continue
        lines.append(f"## {title}")
        for key in keys:
            lines.extend(format_group(key, groups[key], new))
        lines.append("")

    if other:
        lines.append("## Other")
        for key in other:
            lines.extend(format_group(key, groups[key], new))
        lines.append("")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--out", help="write to this file instead of stdout")
    args = ap.parse_args()

    text = build_changelog(args.old, args.new)
    if args.out:
        Path(args.out).write_text(text)
        print(f"Wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
