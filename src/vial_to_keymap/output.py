"""Generate keymap-drawer YAML and companion QMK info.json layout file.

The output consists of two files:

``<stem>.json``
    QMK ``info.json``-style physical layout – passed to keymap-drawer via the
    ``qmk_info_json`` field so it knows where every key sits on the board.

``<stem>.yaml``
    keymap-drawer YAML with ``layout`` and ``layers`` sections, ready to pipe
    into ``keymap draw``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .keycodes import KeySpec, decode_keycode, get_held_layer, substitute_layer_names
from .kle import PhysicalKey

# ---------------------------------------------------------------------------
# Combo type alias
# ---------------------------------------------------------------------------

ComboEntry = tuple[list[int], KeySpec, list[int]]  # (sorted positions, output key spec, active layers)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize_layer_name(raw: str) -> str:
    """Turn an arbitrary string into a safe YAML map key."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", raw).strip("_") or "Layer"
    # Ensure it doesn't start with a digit
    if cleaned[0].isdigit():
        cleaned = "L" + cleaned
    return cleaned


def _yaml_scalar(value: Any) -> str:
    """Render a Python value as a YAML scalar / flow mapping."""
    if value is None:
        return "null"
    if isinstance(value, dict):
        parts = ", ".join(f"{k}: {_yaml_scalar(v)}" for k, v in value.items())
        return "{" + parts + "}"
    if isinstance(value, str):
        # Quote strings that contain YAML-special characters or look like keywords.
        # We use single-quote style; internal single-quotes are doubled.
        needs_quote = (
            value in ("true", "false", "null", "~", "yes", "no", "on", "off",
                      "=", "-", "?", ":")  # standalone YAML special values
            or not value
            or value[0] in ("&", "*", "!", "|", ">", "'", '"', "{", "}", "[", "]",
                            ",", "-", "?", "@", "`", "%", "=")
            or any(c in value for c in (":", "#", ",", "[", "]", "{", "}", "\n", "'"))
            or value[-1] in (":",)
        )
        if needs_quote:
            escaped = value.replace("'", "''")
            return f"'{escaped}'"
        return value
    return str(value)


def _yaml_row(keys: list[KeySpec]) -> str:
    """Render a list of key specs as a YAML flow sequence on one line."""
    parts = [_yaml_scalar(k) for k in keys]
    return "    - [" + ", ".join(parts) + "]"


# ---------------------------------------------------------------------------
# Physical layout JSON
# ---------------------------------------------------------------------------

def build_layout_json(physical_keys: list[PhysicalKey]) -> dict:
    """
    Build a QMK ``info.json``-style dict from the ordered physical key list.

    Only matrix keys (non-encoder) are included; the order matches the layer
    key lists produced by :func:`build_layer_rows`.
    """
    layout_entries = [
        k.to_qmk_key() for k in physical_keys if not k.is_encoder
    ]
    return {
        "layouts": {
            "LAYOUT": {
                "layout": layout_entries,
            }
        }
    }


# ---------------------------------------------------------------------------
# Layer key lists
# ---------------------------------------------------------------------------

def build_layer_rows(
    physical_keys: list[PhysicalKey],
    keymap: list[list[list[int]]],
    custom_keycodes: list[dict] | None = None,
    vial_protocol: int = 6,
    layer_names: list[str] | None = None,
    macro_count: int = 0,
    os_layout: str = "us",
) -> list[list[KeySpec]]:
    """
    Return one flat key list per layer, in physical key order.

    ``keymap[layer][row][col]`` → decoded :data:`~keycodes.KeySpec`.
    Encoder keys are skipped (they're not in the keymap buffer).
    """
    n_layers = len(keymap)
    result: list[list[KeySpec]] = [[] for _ in range(n_layers)]

    # Ensure we always have names to substitute into layer ops.
    resolved_names: list[str] = list(layer_names) if layer_names else []
    for i in range(len(resolved_names), n_layers):
        resolved_names.append(f"Layer_{i}")

    # Pre-compute which key positions (by flat index) hold-activate each layer.
    # When a key has MO(n) / LT(n,*) / TT(n), that same position in layer n
    # shows as {type: held} rather than {type: trans}.
    held_positions: dict[int, set[int]] = {}
    key_pos = 0
    for key in physical_keys:
        if key.is_encoder:
            continue
        if key.row >= 0 and key.col >= 0:
            for layer_data in keymap:
                if key.row < len(layer_data) and key.col < len(layer_data[key.row]):
                    target = get_held_layer(layer_data[key.row][key.col], vial_protocol)
                    if target is not None:
                        held_positions.setdefault(target, set()).add(key_pos)
        key_pos += 1

    key_pos = 0
    for key in physical_keys:
        if key.is_encoder:
            continue
        if key.row < 0 or key.col < 0:
            for layer_keys in result:
                layer_keys.append(None)
            key_pos += 1
            continue
        for layer_idx, layer_data in enumerate(keymap):
            if key.row < len(layer_data) and key.col < len(layer_data[key.row]):
                raw = layer_data[key.row][key.col]
                spec = decode_keycode(raw, custom_keycodes, vial_protocol, macro_count, os_layout)
                if spec == {"type": "trans"} and key_pos in held_positions.get(layer_idx, set()):
                    spec = {"type": "held"}
                spec = substitute_layer_names(spec, resolved_names)
                result[layer_idx].append(spec)
            else:
                result[layer_idx].append(None)
        key_pos += 1

    return result


def build_combo_list(
    physical_keys: list[PhysicalKey],
    keymap: list[list[list[int]]],
    combos: list[tuple[int, int, int, int, int]],
    custom_keycodes: list[dict] | None = None,
    vial_protocol: int = 6,
    layer_names: list[str] | None = None,
    macro_count: int = 0,
    os_layout: str = "us",
) -> list[ComboEntry]:
    """
    Map Vial combo entries to keymap-drawer combo specs.

    Each Vial combo entry is ``(trigger0, trigger1, trigger2, trigger3, output)``
    where unused trigger slots are ``0x0000``.

    Trigger keycodes are resolved to flat physical key indices by searching the
    base layer (layer 0).  Combos whose trigger keys cannot all be located are
    skipped with a warning.

    Active layers are computed per combo: a layer is considered active when every
    trigger position contains either the same keycode as in the base layer or
    ``KC_TRNS`` (transparent / falls through).

    Returns a list of ``(positions, output_key_spec, active_layers)`` tuples.
    """
    import sys

    if not combos or not keymap:
        return []

    # Ensure we always have names to substitute into layer ops.
    n_layers = len(keymap)
    resolved_names: list[str] = list(layer_names) if layer_names else []
    for i in range(len(resolved_names), n_layers):
        resolved_names.append(f"Layer_{i}")

    base_layer = keymap[0]

    # Build a flat list of (row, col) in physical key order (skip encoders).
    pos_to_matrix: list[tuple[int, int]] = []
    for key in physical_keys:
        if key.is_encoder:
            continue
        pos_to_matrix.append((key.row, key.col))

    # Build a map: raw_keycode → list of flat key positions in the base layer.
    kc_to_positions: dict[int, list[int]] = {}
    for pos, (row, col) in enumerate(pos_to_matrix):
        if row >= 0 and col >= 0:
            if row < len(base_layer) and col < len(base_layer[row]):
                raw = base_layer[row][col]
                kc_to_positions.setdefault(raw, []).append(pos)

    result: list[ComboEntry] = []
    for entry in combos:
        trigger_kcs = [kc for kc in entry[:4] if kc != 0x0000]
        output_kc = entry[4]

        # Resolve each trigger keycode to a flat position in the base layer.
        positions: list[int] = []
        ok = True
        used: dict[int, int] = {}
        for kc in trigger_kcs:
            candidates = kc_to_positions.get(kc, [])
            n = used.get(kc, 0)
            if n >= len(candidates):
                print(f"  Warning: combo trigger 0x{kc:04X} not found in base layer – skipping", file=sys.stderr)
                ok = False
                break
            positions.append(candidates[n])
            used[kc] = n + 1

        if not ok:
            continue

        # Build pos→kc mapping BEFORE sorting so layer checks use the right keycode per position.
        pos_to_kc: dict[int, int] = {pos: kc for kc, pos in zip(trigger_kcs, positions)}
        positions.sort()

        # Determine which layers the combo is active on.
        # A layer is active when every trigger position contains the expected
        # keycode or KC_TRNS (0x0001, transparent – falls through to below).
        active_layers: list[int] = []
        for layer_idx, layer_data in enumerate(keymap):
            active = True
            for pos in positions:
                row, col = pos_to_matrix[pos]
                if row < 0 or col < 0:
                    active = False
                    break
                if row >= len(layer_data) or col >= len(layer_data[row]):
                    active = False
                    break
                raw = layer_data[row][col]
                expected_kc = pos_to_kc[pos]
                if raw != expected_kc and raw != 0x0001:  # 0x0001 = KC_TRNS
                    active = False
                    break
            if active:
                active_layers.append(layer_idx)

        output_spec = decode_keycode(output_kc, custom_keycodes, vial_protocol, macro_count, os_layout)
        output_spec = substitute_layer_names(output_spec, resolved_names)
        result.append((positions, output_spec, active_layers))

    return result


# ---------------------------------------------------------------------------
# YAML serialisation
# ---------------------------------------------------------------------------

def _detect_row_width(physical_keys: list[PhysicalKey]) -> int:
    """
    Guess the number of keys per row from the physical layout.

    We bucket keys by their rounded Y coordinate and return the modal
    (most common) bucket size.  This drives how the flat key list is
    broken into rows in the YAML output.
    """
    matrix_keys = [k for k in physical_keys if not k.is_encoder and k.row >= 0]
    if not matrix_keys:
        return 10  # sensible default

    rows: dict[float, int] = {}
    for k in matrix_keys:
        y_bucket = round(k.y * 4) / 4  # 0.25u precision
        rows[y_bucket] = rows.get(y_bucket, 0) + 1

    counts = list(rows.values())
    # Return the most common row length
    return max(set(counts), key=counts.count)


def generate_yaml(
    keyboard_name: str,
    physical_keys: list[PhysicalKey],
    layer_keys: list[list[KeySpec]],
    layer_names: list[str] | None = None,
    layout_json_path: str = "layout.json",
    combo_list: list[ComboEntry] | None = None,
) -> str:
    """
    Render the keymap-drawer YAML as a string.

    Parameters
    ----------
    keyboard_name:
        Used only as a comment header.
    physical_keys:
        Ordered list of physical keys from the KLE parser.
    layer_keys:
        Per-layer flat key lists from :func:`build_layer_rows`.
    layer_names:
        Optional list of layer names.  Defaults to ``Layer_0``, ``Layer_1`` …
    layout_json_path:
        Filename / path of the companion layout JSON file.
    """
    if layer_names is None:
        layer_names = [f"Layer_{i}" for i in range(len(layer_keys))]
    else:
        layer_names = [_sanitize_layer_name(n) for n in layer_names]
        # Pad if needed
        for i in range(len(layer_names), len(layer_keys)):
            layer_names.append(f"Layer_{i}")

    row_width = _detect_row_width(physical_keys)

    lines: list[str] = [
        f"# Keymap for: {keyboard_name}",
        "# Generated by vial-to-keymap – edit layer names as desired.",
        "",
        "layout:",
        f"  qmk_info_json: {layout_json_path}",
        "  layout_name: LAYOUT",
        "",
        "layers:",
    ]

    for name, flat_keys in zip(layer_names, layer_keys):
        lines.append(f"  {name}:")
        # Split flat list into rows of row_width keys
        for start in range(0, len(flat_keys), row_width):
            chunk = flat_keys[start : start + row_width]
            lines.append(_yaml_row(chunk))
        lines.append("")

    if combo_list:
        n_layers = len(layer_keys)
        all_layers = list(range(n_layers))
        lines.append("combos:")
        for positions, output_spec, active_layers in combo_list:
            pos_str = "[" + ", ".join(str(p) for p in positions) + "]"

            if isinstance(output_spec, dict) and output_spec.get("type") == "held" and "t" in output_spec:
                # Layer-hold output: split into two entries.
                # 1) {type: held} scoped to the target layer only.
                # 2) Bare name scoped to all other active layers.
                target_name = output_spec["t"]
                held_layers = [i for i in active_layers if layer_names[i] == target_name]
                bare_layers = [i for i in active_layers if layer_names[i] != target_name]

                if held_layers:
                    lines.append(f"  - p: {pos_str}")
                    lines.append(f"    k: {_yaml_scalar(output_spec)}")
                    if held_layers != all_layers:
                        names_str = "[" + ", ".join(layer_names[i] for i in held_layers) + "]"
                        lines.append(f"    l: {names_str}")

                if bare_layers:
                    lines.append(f"  - p: {pos_str}")
                    lines.append(f"    k: {_yaml_scalar(target_name)}")
                    if bare_layers != all_layers:
                        names_str = "[" + ", ".join(layer_names[i] for i in bare_layers) + "]"
                        lines.append(f"    l: {names_str}")
            else:
                lines.append(f"  - p: {pos_str}")
                lines.append(f"    k: {_yaml_scalar(output_spec)}")
                if active_layers and active_layers != all_layers:
                    names_str = "[" + ", ".join(layer_names[i] for i in active_layers) + "]"
                    lines.append(f"    l: {names_str}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-level write helper
# ---------------------------------------------------------------------------

def write_output(
    stem: str | Path,
    keyboard_name: str,
    physical_keys: list[PhysicalKey],
    keymap: list[list[list[int]]],
    layer_names: list[str] | None = None,
    custom_keycodes: list[dict] | None = None,
    vial_protocol: int = 6,
    combos: list[tuple[int, int, int, int, int]] | None = None,
    macro_count: int = 0,
    os_layout: str = "us",
) -> tuple[Path, Path]:
    """
    Write ``<stem>.json`` (layout) and ``<stem>.yaml`` (keymap) to disk.

    Returns the two :class:`~pathlib.Path` objects that were written.
    """
    stem = Path(stem)
    json_path = stem.with_suffix(".json")
    yaml_path = stem.with_suffix(".yaml")

    layer_keys = build_layer_rows(physical_keys, keymap, custom_keycodes, vial_protocol, layer_names, macro_count, os_layout)

    combo_list: list[ComboEntry] | None = None
    if combos:
        combo_list = build_combo_list(physical_keys, keymap, combos, custom_keycodes, vial_protocol, layer_names, macro_count, os_layout)

    layout_dict = build_layout_json(physical_keys)
    json_path.write_text(json.dumps(layout_dict, indent=2), encoding="utf-8")

    yaml_str = generate_yaml(
        keyboard_name=keyboard_name,
        physical_keys=physical_keys,
        layer_keys=layer_keys,
        layer_names=layer_names,
        layout_json_path=json_path.name,
        combo_list=combo_list,
    )
    yaml_path.write_text(yaml_str, encoding="utf-8")

    return json_path, yaml_path
