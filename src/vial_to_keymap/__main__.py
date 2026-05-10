"""Command-line entry point for vial-to-keymap."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .device import VialDevice, find_vial_devices
from .kle import parse_kle
from .output import write_output


def _list_devices() -> None:
    devices = find_vial_devices()
    if not devices:
        print("No Vial keyboards found.")
        if sys.platform.startswith("linux"):
            print("You may need a udev rule.")
        return
    print(f"Found {len(devices)} Vial keyboard(s):\n")
    for index, dev in enumerate(devices):
        vid = dev.get("vendor_id", 0)
        pid = dev.get("product_id", 0)
        name = dev.get("product_string", "?")
        mfr = dev.get("manufacturer_string", "?")
        path = dev.get("path", b"").decode(errors="replace")
        print(f"  [{index}]  {mfr} {name}  (VID:{vid:04X} PID:{pid:04X})  {path}")


def _pick_device(index: int) -> dict:
    devices = find_vial_devices()
    if not devices:
        sys.exit(
            "No Vial keyboards found.\n"
        )
    if index >= len(devices):
        sys.exit(
            f"Device index {index} out of range – "
            f"only {len(devices)} device(s) found.  Use --list to see them."
        )
    return devices[index]


def _default_stem(name: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name).strip("_")
    return Path(safe or "keymap")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="vial-to-keymap",
        description=(
            "Connect to a Vial keyboard and export its layout + keymap as\n"
            "a keymap-drawer YAML file and companion QMK info.json layout."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  vial-to-keymap                     # auto-detect, write <keyboard>.yaml/json\n"
            "  vial-to-keymap -o my_board          # write my_board.yaml + my_board.json\n"
            "  vial-to-keymap --list               # list connected Vial keyboards\n"
            "  vial-to-keymap --device 1           # use second detected keyboard\n"
            "  keymap draw my_board.yaml > my_board.svg  # render with keymap-drawer\n"
        ),
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List connected Vial keyboards and exit.",
    )
    parser.add_argument(
        "--device", "-d",
        type=int,
        default=0,
        metavar="INDEX",
        help="Index of the Vial keyboard to use (default: 0, i.e. the first one).",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        default=None,
        help=(
            "Output filename stem (without extension).  "
            "Two files are written: FILE.yaml and FILE.json.  "
            "Defaults to the keyboard's own name."
        ),
    )
    parser.add_argument(
        "--layer-names",
        metavar="NAMES",
        default=None,
        help=(
            "Comma-separated layer names, e.g. 'Base,Nav,Sym'.  "
            "Must have one name per layer.  Defaults to 'Layer_0', 'Layer_1', …"
        ),
    )
    parser.add_argument(
        "--os-layout",
        metavar="LAYOUT",
        default="us",
        choices=["us", "us-intl", "us-intl-nodead"],
        help=(
            "Host OS keyboard layout used to resolve modifier+key symbols.  "
            "'us' (default) = standard US QWERTY.  "
            "'us-intl' = US-International: marks dead keys with * and decodes "
            "AltGr (Right Alt) combinations to their Unicode characters.  "
            "'us-intl-nodead' = US-International No Dead Keys: same AltGr "
            "decoding but without dead key markers."
        ),
    )
    parser.add_argument(
        "--layout-variant",
        type=int,
        default=0,
        metavar="N",
        help=(
            "Which layout variant to use for keyboards with multiple physical "
            "layout options (default: 0).  Pass -1 to include all key positions."
        ),
    )

    args = parser.parse_args(argv)

    if args.list:
        _list_devices()
        return

    desc = _pick_device(args.device)
    print(f"Connecting to: {desc.get('product_string', 'Vial keyboard')} …")

    with VialDevice(desc) as kb:
        print("  Reading VIA protocol version …")
        kb.get_via_protocol()
        print(f"    VIA protocol: {kb.via_protocol}")

        print("  Reading Vial keyboard ID …")
        kb.get_keyboard_id()
        print(f"    Vial protocol: {kb.vial_protocol}  UID: 0x{kb.keyboard_uid:016X}")

        print("  Fetching keyboard definition (LZMA compressed) …")
        definition = kb.get_layout_definition()
        print(f"    Name   : {kb.name}")
        print(f"    Matrix : {kb.rows} rows × {kb.cols} cols")

        print("  Reading layer count …")
        kb.get_layer_count()
        print(f"    Layers : {kb.layer_count}")

        print("  Reading macro count …")
        kb.get_macro_count()
        print(f"    Macros : {kb.macro_count}")

        print("  Bulk-reading keymap …")
        keymap = kb.get_keymap()
        print(f"    Done   : {kb.layer_count} × {kb.rows} × {kb.cols} keycodes read")

        print("  Reading combos …")
        combos = kb.get_combos()
        if combos:
            print(f"    Combos : {len(combos)} active combo(s) found")
        else:
            print("    Combos : none (firmware has no dynamic combos or none configured)")

        # Parse physical layout from KLE
        kle_rows = definition.get("layouts", {}).get("keymap", [])
        if not kle_rows:
            sys.exit("Could not find 'layouts.keymap' in keyboard definition.")

        physical_keys = parse_kle(kle_rows, default_layout_index=args.layout_variant)
        matrix_keys = [k for k in physical_keys if not k.is_encoder]
        print(f"  Physical layout: {len(matrix_keys)} keys parsed from KLE data")

        # Layer names
        layer_names: list[str] | None = None
        if args.layer_names:
            layer_names = [n.strip() for n in args.layer_names.split(",")]
            if len(layer_names) < kb.layer_count:
                # Pad with defaults
                for i in range(len(layer_names), kb.layer_count):
                    layer_names.append(f"Layer_{i}")

        # Custom keycodes
        custom_keycodes: list[dict] | None = definition.get("customKeycodes") or None

        # Determine output stem
        stem: Path
        if args.output:
            stem = Path(args.output)
        else:
            stem = _default_stem(kb.name)

        print(f"\nWriting output to: {stem}.yaml  +  {stem}.json")
        json_path, yaml_path = write_output(
            stem=stem,
            keyboard_name=kb.name,
            physical_keys=physical_keys,
            keymap=keymap,
            layer_names=layer_names,
            custom_keycodes=custom_keycodes,
            vial_protocol=kb.vial_protocol or 6,
            combos=combos or None,
            macro_count=kb.macro_count,
            os_layout=args.os_layout,
        )

    print(f"\n✓  {yaml_path}")
    print(f"✓  {json_path}")
    print(
        f"\nRender with keymap-drawer:\n"
        f"  keymap draw {yaml_path} > {stem}.svg"
    )


if __name__ == "__main__":
    main()
