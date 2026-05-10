"""KLE (Keyboard Layout Editor) JSON parser for Vial keyboard definitions.

Vial stores the physical layout as KLE JSON inside the LZMA-compressed
definition blob.  Each key's label string encodes the matrix position as
``"row,col"`` in label index 0, an encoder marker ``"e"`` in index 4, and a
layout-option tag ``"variantIdx,option"`` in index 8.

This module parses that KLE data into a flat, ordered list of
:class:`PhysicalKey` objects that can be used to build both the
keymap-drawer physical layout and the per-layer keycode lists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PhysicalKey:
    """A single physical key with its layout position and matrix address."""

    # Position in key-units (KLE coordinate space = QMK info.json space)
    x: float
    y: float
    w: float = 1.0
    h: float = 1.0
    # Rotation (degrees, around rx/ry)
    r: float = 0.0
    rx: float = 0.0
    ry: float = 0.0
    # Matrix address
    row: int = -1
    col: int = -1
    # True when this key is an encoder (not part of the keymap buffer)
    is_encoder: bool = False
    encoder_id: int = -1

    def to_qmk_key(self) -> dict[str, Any]:
        """Return a QMK info.json key dict (only non-default fields included)."""
        d: dict[str, Any] = {"x": self.x, "y": self.y}
        if self.w != 1.0:
            d["w"] = self.w
        if self.h != 1.0:
            d["h"] = self.h
        if self.r != 0.0:
            d["r"] = self.r
            d["rx"] = self.rx
            d["ry"] = self.ry
        return d


def parse_kle(kle_rows: list[Any], default_layout_index: int = 0) -> list[PhysicalKey]:
    """
    Parse a KLE JSON row-array into an ordered list of :class:`PhysicalKey`.

    Parameters
    ----------
    kle_rows:
        The value of ``definition["layouts"]["keymap"]`` from the Vial JSON.
    default_layout_index:
        When a keyboard has multiple layout variants (e.g. split / non-split
        spacebar) each key variant is tagged.  Only keys belonging to *this*
        variant index are included.  Pass ``-1`` to include all keys.
    """
    keys: list[PhysicalKey] = []

    # Running KLE state
    cur_x: float = 0.0
    cur_y: float = 0.0
    cur_w: float = 1.0
    cur_h: float = 1.0
    cur_r: float = 0.0
    cur_rx: float = 0.0
    cur_ry: float = 0.0
    next_w: float = 1.0
    next_h: float = 1.0

    for row in kle_rows:
        row_has_key = False

        for item in row:
            if isinstance(item, dict):
                # Property object: update running state
                if "r" in item:
                    cur_r = item["r"]
                if "rx" in item:
                    cur_rx = item["rx"]
                    cur_x = cur_rx
                    # When rx establishes a new rotation group but ry is omitted
                    # (implying same ry), reset the y cursor to cur_ry so the new
                    # cluster starts at the correct vertical origin.
                    if "ry" not in item:
                        cur_y = cur_ry
                if "ry" in item:
                    cur_ry = item["ry"]
                    cur_y = cur_ry
                if "x" in item:
                    cur_x += item["x"]
                if "y" in item:
                    cur_y += item["y"]
                if "w" in item:
                    next_w = item["w"]
                if "h" in item:
                    next_h = item["h"]
                # w2/h2 (second rect for stepped/non-rectangular keys) ignored
            elif isinstance(item, str):
                # Key label string
                row_has_key = True
                labels = item.split("\n")

                def _label(idx: int) -> str:
                    return labels[idx].strip() if idx < len(labels) else ""

                is_encoder = _label(4) == "e"
                layout_tag = _label(8)  # e.g. "0,1" = variant 0, option 1

                # Filter by layout variant
                if layout_tag and default_layout_index >= 0:
                    try:
                        variant_idx, option = (int(v) for v in layout_tag.split(","))
                        if option != 0 and variant_idx == default_layout_index:
                            # Non-default option for this variant → skip
                            cur_x += next_w
                            next_w = 1.0
                            next_h = 1.0
                            continue
                    except ValueError:
                        pass

                key = PhysicalKey(
                    x=cur_x,
                    y=cur_y,
                    w=next_w,
                    h=next_h,
                    r=cur_r,
                    rx=cur_rx,
                    ry=cur_ry,
                    is_encoder=is_encoder,
                )

                raw_pos = _label(0)
                if raw_pos and "," in raw_pos:
                    try:
                        r, c = (int(v) for v in raw_pos.split(","))
                        if is_encoder:
                            key.encoder_id = r
                        else:
                            key.row = r
                            key.col = c
                    except ValueError:
                        pass

                keys.append(key)
                cur_x += next_w
                next_w = 1.0
                next_h = 1.0

        if row_has_key:
            cur_y += 1.0
            cur_x = cur_rx  # reset to rotation origin at end of each row

    return keys
