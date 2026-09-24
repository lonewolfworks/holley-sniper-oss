#!/usr/bin/env python3
"""
Given a HyperSpark-mode .sniper config, produce a "coil+" / factory-analog-
ignition variant: the ECU no longer controls spark, so Ignition Type is
switched from HyperSpark Distributor to Coil+ and nothing else is touched --
per Holley's own model, a coil+ install ignores the timing tables entirely,
so there's no reason to also edit them (confirmed empirically too, 2026-09-23:
diffing a real user-provided HyperSpark/no-ignition pair showed the *only*
required field is this one; an incidental timing-table difference in that
pair turned out to be unrelated tuning noise from a separate edit, not
something Holley's own software applies automatically on this switch --
verified by reproducing this file byte-for-byte below and diffing against
the user's real no-ignition file).

Two things make this a safe, minimal patch rather than a full rewrite:
  - Ignition Type lives at float-array index 3861 (byte offset 15444) as a
    raw int32, not a real float -- confirmed by diffing two real files that
    differed in exactly this field: 6 = HyperSpark Distributor, 0 = Coil+.
  - The file's LAST 4 BYTES are a CRC32 checksum of everything before them
    (confirmed exactly against two real files -- zlib.crc32(data[:-4]) ==
    the trailing uint32, byte-for-byte). Any patch must recompute this or
    produce a file Holley's software may not accept.

Usage:
    python3 make_coil_plus_variant.py SOURCE.sniper OUTPUT.sniper
    python3 make_coil_plus_variant.py --verify OLD.sniper NEW.sniper  # confirm
        NEW differs from OLD only in the fields this tool intends to change
"""
import argparse
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config_parser import IGNITION_TYPE_LABELS, int_at, parse_sniper  # noqa: E402

IGNITION_TYPE_BYTE_OFFSET = 15444  # float index 3861 * 4
HYPERSPARK_VALUE = 6
COIL_PLUS_VALUE = 0


def convert(source_path, out_path, target_value=COIL_PLUS_VALUE):
    data = bytearray(open(source_path, "rb").read())
    current = struct.unpack_from("<i", data, IGNITION_TYPE_BYTE_OFFSET)[0]
    current_label = IGNITION_TYPE_LABELS.get(current, f"Unknown (raw={current})")
    print(f"Source ignition type: {current} ({current_label})")
    if current != HYPERSPARK_VALUE:
        print(f"WARNING: source is not the confirmed HyperSpark value ({HYPERSPARK_VALUE}) -- "
              f"proceeding anyway, but this tool has only been validated starting from HyperSpark.")

    struct.pack_into("<i", data, IGNITION_TYPE_BYTE_OFFSET, target_value)

    # Recompute the trailing CRC32 over everything else -- see module docstring.
    new_crc = zlib.crc32(bytes(data[:-4]))
    struct.pack_into("<I", data, len(data) - 4, new_crc)

    Path(out_path).write_bytes(data)
    target_label = IGNITION_TYPE_LABELS.get(target_value, f"Unknown (raw={target_value})")
    print(f"Wrote {out_path}: ignition type -> {target_value} ({target_label}), CRC32 updated to {new_crc:#010x}")

    # Sanity check: does it still parse, and does the rest of the file match?
    reparsed = parse_sniper(out_path)
    reparsed_type = reparsed["system_parameters"]["engine_parameters"]["ignition_type_raw"]
    assert reparsed_type == target_value, "round-trip parse didn't see the patched value -- offset is wrong"
    orig = open(source_path, "rb").read()
    diffs = [i for i in range(len(orig)) if orig[i] != data[i]]
    expected = set(range(IGNITION_TYPE_BYTE_OFFSET, IGNITION_TYPE_BYTE_OFFSET + 4)) | \
        set(range(len(data) - 4, len(data)))
    unexpected = [i for i in diffs if i not in expected]
    if unexpected:
        # A non-zero exit here (rather than just a warning) matters: a caller
        # scripting this (e.g. the release workflow) should treat an
        # unexpectedly-broad change as a failed conversion, not something to
        # publish anyway.
        sys.exit(f"ERROR: {len(unexpected)} byte(s) changed outside the intended fields "
                 f"(offsets: {unexpected[:10]}{'...' if len(unexpected) > 10 else ''}) -- "
                 f"refusing to trust this output.")
    print("Verified: only the Ignition Type field and the trailing checksum changed. Everything else is byte-identical.")


def verify(old_path, new_path):
    """Report every byte offset that differs between two files, to manually
    confirm a change only touched what was intended."""
    a = open(old_path, "rb").read()
    b = open(new_path, "rb").read()
    if len(a) != len(b):
        print(f"Different lengths: {len(a)} vs {len(b)}")
        return
    diffs = [i for i in range(len(a)) if a[i] != b[i]]
    print(f"{len(diffs)} byte(s) differ: {diffs}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("output")
    ap.add_argument("--verify", action="store_true",
                     help="treat source/output as OLD/NEW and just report differing byte offsets")
    args = ap.parse_args()

    if args.verify:
        verify(args.source, args.output)
    else:
        convert(args.source, args.output)


if __name__ == "__main__":
    main()
