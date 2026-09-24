#!/usr/bin/env python3
"""
Parse a Holley Sniper EFI ".sniper" calibration binary into structured data
matching the Sniper app's own UI labels.

All offsets below were reverse-engineered by cross-matching real Sniper app
screenshots (~/timing/holley/*.PNG) against the raw float32 array inside the
binary -- see ~/timing/sniper-decoded.txt for the full derivation notes.
There is no official format spec; this is empirical and file-format-specific
to the Sniper 3.5-wizard-generated .sniper files this was built against.

Usage:
    python3 parse_sniper.py FILE.sniper                 # pretty summary
    python3 parse_sniper.py FILE.sniper --json           # full structured JSON
    python3 parse_sniper.py FILE.sniper --json out.json  # write JSON to file
    python3 parse_sniper.py A.sniper B.sniper --diff      # value-level diff
"""
import struct
import sys
import json
import argparse

# ---------------------------------------------------------------------------
# Shared axes (all confirmed byte-exact against Sniper UI screenshots)
# ---------------------------------------------------------------------------
MAP_AXIS_31 = [20, 23, 25, 28, 31, 34, 36, 39, 42, 44, 47, 50, 53, 55, 58, 61,
               63, 66, 69, 72, 74, 77, 80, 82, 85, 88, 91, 93, 96, 101, 105]
RPM_AXIS_31 = [400, 520, 640, 760, 880, 1000, 1120, 1240, 1360, 1480, 1600,
               1720, 1840, 1960, 2080, 2200, 2320, 2440, 2560, 2680, 2800,
               2920, 3040, 3160, 3280, 3400, 3520, 3640, 3760, 3880, 4000]
MAP_AXIS_16 = [20, 25, 31, 36, 42, 47, 53, 58, 63, 69, 74, 80, 85, 91, 96, 105]
RPM_AXIS_16 = [400, 640, 880, 1120, 1360, 1600, 1840, 2080, 2320, 2560, 2800,
               3040, 3280, 3520, 3760, 4000]
TEMP_AXIS_16 = [-40, -20, 0, 20, 40, 60, 80, 100, 120, 140, 160, 180, 200,
                220, 240, 260]
PCT_RATE_AXIS_16 = [0, 7, 13, 20, 27, 33, 40, 47, 53, 60, 67, 73, 80, 87, 93, 100]
LAUNCH_RETARD_RPM_AXIS = [0, 1333, 2666, 4000, 5333, 6666, 8000, 9333, 10666,
                          12000, 13333, 14666, 16000, 17333, 18666, 20000]
RPM_BINS_8 = ["0-500", "500-1000", "1000-1500", "1500-2000", "2000-2500",
              "2500-3000", "3000-3500", "3500-4000"]
MAP_BANDS_8 = ["0-13", "13-26", "26-39", "39-53", "53-66", "66-79", "79-92", "92-105"]


def read_floats(path):
    with open(path, "rb") as f:
        data = f.read()
    n = len(data) // 4
    return list(struct.unpack_from("<%df" % n, data, 0)), data


def table2d(floats, start, rows, cols):
    return [[round(floats[start + r * cols + c], 3) for c in range(cols)]
            for r in range(rows)]


def curve(floats, start, n=16, axis=None, decimals=3):
    vals = [round(floats[start + i], decimals) for i in range(n)]
    if axis is not None:
        return {"axis": axis, "values": vals}
    return vals


def band_table_8x8(floats, start):
    """Layout confirmed: [8 '+' rows][8 '-' rows], each row = 8 RPM-bin cols,
    rows MAP-ascending (0-13 .. 92-105). Used by both Closed Loop and Learn
    Compensation Limits tables."""
    plus = {MAP_BANDS_8[b]: [round(floats[start + b * 8 + c], 2) for c in range(8)]
            for b in range(8)}
    minus = {MAP_BANDS_8[b]: [round(floats[start + 64 + b * 8 + c], 2) for c in range(8)]
             for b in range(8)}
    return {"map_bands": MAP_BANDS_8, "rpm_bins": RPM_BINS_8, "+": plus, "-": minus}


def extract_strings(data):
    filename = None
    version = None
    # embedded filename: printable ASCII run ending in '.sniper'
    i = data.find(b".sniper")
    if i != -1:
        j = i
        while j > 0 and 32 <= data[j - 1] < 127:
            j -= 1
        filename = data[j:i + len(b".sniper")].decode("ascii", "ignore")
    i = data.find(b"Generated using Sniper")
    if i != -1:
        j = data.find(b"\x00", i)
        version = data[i:j if j != -1 else i + 80].decode("ascii", "ignore")
    return filename, version


def parse_sniper(path):
    floats, data = read_floats(path)
    filename, version = extract_strings(data)

    fuel = {
        "base_fuel_table_ve_pct": {
            "map_axis_kpa": MAP_AXIS_31, "rpm_axis": RPM_AXIS_31,
            "table": table2d(floats, 211, 31, 31),
        },
        "learn_table_pct": {
            "map_axis_kpa": MAP_AXIS_31, "rpm_axis": RPM_AXIS_31,
            "table": table2d(floats, 1234, 31, 31),
        },
        "target_afr_setpoints": {
            "idle": round(floats[2196], 1),
            "cruise": round(floats[2197], 1),
            "wot": round(floats[2198], 1),
        },
        "target_afr_table": {
            "map_axis_kpa": MAP_AXIS_16, "rpm_axis": RPM_AXIS_16,
            "table": table2d(floats, 2199, 16, 16),
        },
        "acceleration_enrichment": {
            "ae_vs_tps_rate_of_change_lbhr": curve(floats, 2455, axis=PCT_RATE_AXIS_16),
            "ae_vs_map_rate_of_change_lbhr": curve(floats, 2472, axis=PCT_RATE_AXIS_16),
            "ae_tps_vs_coolant_temp_pct": curve(floats, 2489, axis=TEMP_AXIS_16),
            "ae_correction_vs_tps_pct": curve(floats, 2505, axis=PCT_RATE_AXIS_16),
            "map_ae_vs_coolant_pct": curve(floats, 2521, axis=TEMP_AXIS_16),
            "map_ae_decay_rate_vs_coolant_msec": curve(floats, 2537, axis=TEMP_AXIS_16),
        },
        "temperature_enrichment": {
            "coolant_temp_enrichment_pct": curve(floats, 2553, axis=TEMP_AXIS_16),
            "af_ratio_offset_vs_coolant": curve(floats, 2569, axis=TEMP_AXIS_16),
            "air_temp_enrichment_pct": curve(floats, 2585, axis=TEMP_AXIS_16),
        },
        "startup_enrichment": {
            "cranking_fuel_lbhr": curve(floats, 2601, axis=TEMP_AXIS_16),
            "clear_flood_tps_pct": round(floats[2617], 1),
            "after_start_enrichment_pct": curve(floats, 2621, axis=TEMP_AXIS_16),
            "after_start_holdoff_msec": curve(floats, 2637, axis=TEMP_AXIS_16),
            "after_start_decay_time_sec": curve(floats, 2653, axis=TEMP_AXIS_16),
        },
    }

    spark = {
        "base_timing_table_deg": {
            "map_axis_kpa": MAP_AXIS_31, "rpm_axis": RPM_AXIS_31,
            "table": table2d(floats, 2679, 31, 31),
        },
        "cranking_parameters": {
            "cranking_timing_deg": round(floats[3702], 1),
            "crank_to_run_rpm": round(floats[3703], 0),
        },
        "rev_limiters": {
            "main_over_rev_high_rpm": round(floats[3705], 0),
            "rev_limiter_1_on_rpm": round(floats[3708], 0),
        },
        "launch_retard": {
            "retard_deg": curve(floats, 3714, axis=LAUNCH_RETARD_RPM_AXIS),
        },
        "timing_vs_coolant_temp_deg": curve(floats, 3746, axis=TEMP_AXIS_16),
        "timing_vs_air_temp_deg": curve(floats, 3778, axis=TEMP_AXIS_16),
        "idle_spark": {
            "p_term": round(floats[3814], 1),
            "d_term": round(floats[3815], 1),
        },
    }

    idle = {
        "target_idle_speed_rpm": curve(floats, 3823, decimals=1, axis=TEMP_AXIS_16),
        "iac_parked_position_pct": curve(floats, 3839, decimals=1, axis=TEMP_AXIS_16),
    }

    system_parameters = {
        "engine_parameters": {
            "engine_displacement_ci": round(floats[3859], 0),
            "ignition_reference_angle_deg": round(floats[3862], 1),
            "inductive_delay_usec": round(floats[3863], 1),
            "dwell_time_msec": round(floats[3866], 1),
        },
        "efi_parameters": {
            "actual_system_pressure_psi": round(floats[3885], 1),
            "rated_injector_flow_lbhr": round(floats[3887], 1),
            "rated_injector_pressure_psi": round(floats[3888], 1),
            "min_injector_opening_time_msec": round(floats[3889], 2),
            "injector_off_time_vs_battery_voltage_msec": curve(floats, 3892),
        },
        "basic_io_fans_ac": {
            "fan1_on_f": round(floats[3909], 0),
            "fan1_off_f": round(floats[3910], 0),
            "fan2_on_f": round(floats[3913], 0),
            "fan2_off_f": round(floats[3914], 0),
            "ac_shutdown_max_tps_pct": round(floats[3917], 0),
            "ac_shutdown_max_cts_f": round(floats[3918], 0),
            "iac_kick_pct": round(floats[3921], 0),
        },
        "closed_loop_learn": {
            "closed_loop_compensation_limits_pct": band_table_8x8(floats, 3940),
            "base_fuel_learn_gain_pct": round(floats[4207], 0),
            "enable_rpm_to_enter_learn_rpm": round(floats[4208], 0),
            "learn_compensation_limits_pct": band_table_8x8(floats, 4210),
        },
    }

    return {
        "file": path,
        "embedded_filename": filename,
        "version_string": version,
        "fuel": fuel,
        "spark": spark,
        "idle": idle,
        "system_parameters": system_parameters,
    }


# ---------------------------------------------------------------------------
# Diff helper -- walks two parsed structures and reports leaf-level differences
# ---------------------------------------------------------------------------
def diff_tree(a, b, path=""):
    diffs = []
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a:
            diffs += diff_tree(a.get(k), b.get(k), f"{path}.{k}" if path else k)
    elif isinstance(a, list) and isinstance(b, list):
        for i, (av, bv) in enumerate(zip(a, b)):
            diffs += diff_tree(av, bv, f"{path}[{i}]")
    else:
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if abs(a - b) > 0.01:
                diffs.append((path, a, b))
        elif a != b:
            diffs.append((path, a, b))
    return diffs


def print_summary(d):
    print(f"File: {d['file']}")
    print(f"Embedded filename: {d['embedded_filename']}")
    print(f"Version: {d['version_string']}")
    print()
    print("--- FUEL ---")
    print("Target A/F setpoints:", d["fuel"]["target_afr_setpoints"])
    print("AE vs TPS RoC (lb/hr):", d["fuel"]["acceleration_enrichment"]["ae_vs_tps_rate_of_change_lbhr"]["values"])
    print("Cranking Fuel (lb/hr):", d["fuel"]["startup_enrichment"]["cranking_fuel_lbhr"]["values"])
    print()
    print("--- SPARK ---")
    print("Cranking Timing:", d["spark"]["cranking_parameters"])
    print("Rev Limiters:", d["spark"]["rev_limiters"])
    print("Timing vs Coolant Temp:", d["spark"]["timing_vs_coolant_temp_deg"]["values"])
    print("Timing vs Air Temp:", d["spark"]["timing_vs_air_temp_deg"]["values"])
    print()
    print("--- IDLE ---")
    print("Target Idle Speed:", d["idle"]["target_idle_speed_rpm"]["values"])
    print()
    print("--- SYSTEM PARAMETERS ---")
    print("Engine:", d["system_parameters"]["engine_parameters"])
    print("EFI:", d["system_parameters"]["efi_parameters"])
    print("Fans/AC:", d["system_parameters"]["basic_io_fans_ac"])


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help=".sniper file to parse")
    ap.add_argument("file2", nargs="?", help="second .sniper file, for --diff")
    ap.add_argument("--json", nargs="?", const="-",
                     help="dump full structured JSON (to stdout, or to a file path)")
    ap.add_argument("--diff", action="store_true",
                     help="diff file vs file2 at the structured/labeled level")
    args = ap.parse_args()

    if args.diff:
        if not args.file2:
            ap.error("--diff requires a second file")
        a = parse_sniper(args.file)
        b = parse_sniper(args.file2)
        diffs = diff_tree(a, b)
        if not diffs:
            print("No structured differences found.")
        for path, av, bv in diffs:
            print(f"{path}:\n  {args.file}: {av}\n  {args.file2}: {bv}")
        return

    d = parse_sniper(args.file)
    if args.json:
        out = json.dumps(d, indent=2)
        if args.json == "-":
            print(out)
        else:
            with open(args.json, "w") as f:
                f.write(out)
            print(f"Wrote {args.json}")
    else:
        print_summary(d)


if __name__ == "__main__":
    main()
