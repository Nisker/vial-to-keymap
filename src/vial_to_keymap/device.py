"""Vial keyboard USB HID communication."""

from __future__ import annotations

import json
import lzma
import struct
import sys
from typing import Any

try:
    if sys.platform.startswith("linux"):
        try:
            import hidraw as hid  # type: ignore[import]
            _HID_STYLE = "hidraw"
        except ImportError:
            import hid  # type: ignore[import]
            _HID_STYLE = "hid"
    else:
        import hid  # type: ignore[import]
        _HID_STYLE = "hid"
except ImportError as exc:
    raise ImportError(
        "No HID library found.  Install python-hid:\n"
        "  pip install hid"
    ) from exc


VIAL_SERIAL_MAGIC = "vial:f64c2b3c"
VIAL_USAGE_PAGE = 0xFF60
VIAL_USAGE = 0x61
MSG_LEN = 32
BUFFER_FETCH_CHUNK = 28

# VIA commands
CMD_VIA_GET_PROTOCOL_VERSION = 0x01
CMD_VIA_GET_LAYER_COUNT = 0x11
CMD_VIA_KEYMAP_GET_BUFFER = 0x12
CMD_VIA_MACRO_GET_COUNT = 0x0C

# Vial-specific commands (all prefixed with CMD_VIA_VIAL_PREFIX)
CMD_VIA_VIAL_PREFIX = 0xFE
CMD_VIAL_GET_KEYBOARD_ID = 0x00
CMD_VIAL_GET_SIZE = 0x01
CMD_VIAL_GET_DEFINITION = 0x02

# Dynamic entry ops (vial_protocol >= 4 only)
CMD_VIAL_DYNAMIC_ENTRY_OP = 0x0D
DYNAMIC_VIAL_GET_NUMBER_OF_ENTRIES = 0x00
DYNAMIC_VIAL_COMBO_GET = 0x03

VIAL_PROTOCOL_DYNAMIC = 4


def _is_rawhid(desc: dict) -> bool:
    return desc.get("usage_page") == VIAL_USAGE_PAGE and desc.get("usage") == VIAL_USAGE


def find_vial_devices() -> list[dict]:
    """Return a list of HID device descriptors for connected Vial keyboards."""
    devices = []
    for desc in hid.enumerate():
        serial = desc.get("serial_number", "") or ""
        if VIAL_SERIAL_MAGIC in serial and _is_rawhid(desc):
            devices.append(desc)
    return devices


class VialDevice:
    """Represents a connected Vial keyboard and exposes its configuration data."""

    def __init__(self, descriptor: dict) -> None:
        self.desc = descriptor
        # Support both old-style hid.device() and new-style hid.Device(path=…)
        path = descriptor["path"]
        if hasattr(hid, "Device"):
            # python-hid >= 1.0  (hid.Device)
            self._dev = hid.Device(path=path)
            self._dev.nonblocking = False
        else:
            # older hidapi / hidraw binding (hid.device())
            self._dev = hid.device()  # type: ignore[attr-defined]
            self._dev.open_path(path)
            self._dev.set_nonblocking(False)

        self.name: str = descriptor.get("product_string", "Unknown keyboard")
        self.via_protocol: int | None = None
        self.vial_protocol: int | None = None
        self.keyboard_uid: int | None = None
        self.definition: dict | None = None
        self.rows: int = 0
        self.cols: int = 0
        self.layer_count: int = 0
        self.macro_count: int = 0

    def close(self) -> None:
        self._dev.close()

    def __enter__(self) -> "VialDevice":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _send(self, msg: bytes, timeout_ms: int = 2000) -> bytes:
        padded = msg + b"\x00" * (MSG_LEN - len(msg))
        self._dev.write(b"\x00" + padded)  # report ID 0 prepended
        # python-hid >= 1.0 uses `timeout` (milliseconds); older uses `timeout_ms`
        try:
            resp = self._dev.read(MSG_LEN, timeout=timeout_ms)
        except TypeError:
            resp = self._dev.read(MSG_LEN, timeout_ms=timeout_ms)  # type: ignore[call-arg]
        if not resp:
            raise TimeoutError("No response from keyboard – is it still connected?")
        return bytes(resp)

    # ------------------------------------------------------------------ #
    # High-level API                                                       #
    # ------------------------------------------------------------------ #

    def get_via_protocol(self) -> int:
        """Read the VIA protocol version (should be 9 for Vial)."""
        data = self._send(struct.pack("B", CMD_VIA_GET_PROTOCOL_VERSION))
        self.via_protocol = struct.unpack(">H", data[1:3])[0]
        return self.via_protocol

    def get_keyboard_id(self) -> tuple[int, int]:
        """Read the Vial protocol version and 64-bit keyboard UID."""
        data = self._send(struct.pack("BB", CMD_VIA_VIAL_PREFIX, CMD_VIAL_GET_KEYBOARD_ID))
        self.vial_protocol = struct.unpack("<I", data[0:4])[0]
        self.keyboard_uid = struct.unpack("<Q", data[4:12])[0]
        return self.vial_protocol, self.keyboard_uid

    def get_layout_definition(self) -> dict:
        """
        Fetch the LZMA-compressed keyboard definition from the firmware,
        decompress it, and return the parsed JSON dict.

        Also sets self.rows, self.cols, and self.name from the definition.
        """
        # Step 1: get compressed byte count
        data = self._send(struct.pack("BB", CMD_VIA_VIAL_PREFIX, CMD_VIAL_GET_SIZE))
        total = struct.unpack("<I", data[0:4])[0]
        if total == 0:
            raise RuntimeError("Keyboard reported definition size of 0")

        # Step 2: read 32-byte pages
        compressed = b""
        block = 0
        remaining = total
        while remaining > 0:
            data = self._send(
                struct.pack("<BBI", CMD_VIA_VIAL_PREFIX, CMD_VIAL_GET_DEFINITION, block)
            )
            chunk = min(remaining, MSG_LEN)
            compressed += data[:chunk]
            block += 1
            remaining -= chunk

        self.definition = json.loads(lzma.decompress(compressed))
        self.rows = self.definition["matrix"]["rows"]
        self.cols = self.definition["matrix"]["cols"]
        if "name" in self.definition:
            self.name = self.definition["name"]
        return self.definition

    def get_layer_count(self) -> int:
        """Read the number of keymap layers configured on this keyboard."""
        data = self._send(struct.pack("B", CMD_VIA_GET_LAYER_COUNT))
        self.layer_count = data[1]
        return self.layer_count

    def get_macro_count(self) -> int:
        """Read the number of macros supported by this keyboard."""
        data = self._send(struct.pack("B", CMD_VIA_MACRO_GET_COUNT))
        self.macro_count = data[1]
        return self.macro_count

    def get_keymap(self) -> list[list[list[int]]]:
        """
        Bulk-read all keycodes from the keyboard.

        Returns a 3-dimensional list:  keymap[layer][row][col] → uint16 keycode
        Requires get_layout_definition() and get_layer_count() to have been called.
        """
        if not (self.rows and self.cols and self.layer_count):
            raise RuntimeError(
                "Call get_layout_definition() and get_layer_count() before get_keymap()"
            )

        total_bytes = self.layer_count * self.rows * self.cols * 2
        raw = bytearray()
        offset = 0
        while len(raw) < total_bytes:
            chunk = min(BUFFER_FETCH_CHUNK, total_bytes - len(raw))
            data = self._send(struct.pack(">BHB", CMD_VIA_KEYMAP_GET_BUFFER, offset, chunk))
            raw += data[4 : 4 + chunk]
            offset += chunk

        keymap: list[list[list[int]]] = []
        for layer in range(self.layer_count):
            layer_data: list[list[int]] = []
            for row in range(self.rows):
                row_data: list[int] = []
                for col in range(self.cols):
                    idx = (layer * self.rows * self.cols + row * self.cols + col) * 2
                    kc = struct.unpack(">H", raw[idx : idx + 2])[0]
                    row_data.append(kc)
                layer_data.append(row_data)
            keymap.append(layer_data)
        return keymap

    def get_combos(self) -> list[tuple[int, int, int, int, int]]:
        """
        Read all combo entries from the keyboard.

        Requires vial_protocol >= 4 (VIAL_PROTOCOL_DYNAMIC).  Returns an empty
        list for older firmware.

        Each entry is a 5-tuple of uint16 keycodes:
        ``(trigger0, trigger1, trigger2, trigger3, output)``

        Unused trigger slots are ``0x0000`` (KC_NO).  Combos where every trigger
        key is KC_NO are omitted (they are empty/disabled slots).
        """
        if (self.vial_protocol or 0) < VIAL_PROTOCOL_DYNAMIC:
            return []

        # Query counts: data[0]=tap_dance, data[1]=combo, data[2]=key_override
        data = self._send(struct.pack("BBB", CMD_VIA_VIAL_PREFIX, CMD_VIAL_DYNAMIC_ENTRY_OP,
                                     DYNAMIC_VIAL_GET_NUMBER_OF_ENTRIES))
        combo_count = data[1]
        if combo_count == 0:
            return []

        combos: list[tuple[int, int, int, int, int]] = []
        for idx in range(combo_count):
            data = self._send(struct.pack("BBBB", CMD_VIA_VIAL_PREFIX, CMD_VIAL_DYNAMIC_ENTRY_OP,
                                          DYNAMIC_VIAL_COMBO_GET, idx))
            # data[0] = status (0 = OK); data[1:11] = 5 × uint16 LE
            keys = struct.unpack("<HHHHH", data[1:11])
            # Skip entirely-empty slots (all trigger keys are KC_NO)
            if keys[0] == 0 and keys[1] == 0 and keys[2] == 0 and keys[3] == 0:
                continue
            combos.append(keys)
        return combos
