"""QMK / Vial keycode decoding.

Converts raw uint16 keycode values read from a Vial keyboard into
human-readable strings (or tap/hold dicts) suitable for keymap-drawer.

Keycode ranges – Vial protocol v6 (current):
  0x0000          KC_NO    – empty / no key
  0x0001          KC_TRNS  – transparent (fall through to lower layer)
  0x0004–0x00FF   Basic HID keycodes
  0x0100–0x1FFF   Mod+key combos  (LCTL(kc), LSFT(kc), …)
  0x2000–0x3FFF   Mod-tap  MT(mod, kc)  QK_MOD_TAP
  0x4000–0x4FFF   Layer-tap LT(layer, kc)  QK_LAYER_TAP
  0x5000–0x51FF   Layer-mod LM(layer, mod)  QK_LAYER_MOD
  0x5200–0x521F   TO(layer)
  0x5220–0x523F   MO(layer)
  0x5240–0x525F   DF(layer)
  0x5260–0x527F   TG(layer)
  0x5280–0x529F   OSL(layer)
  0x52A0–0x52BF   OSM(mod)
  0x52C0–0x52DF   TT(layer)
  0x52E0–0x52FF   PDF(layer)  persistent DF
  0x5700–0x57FF   TD(x)  tap-dance
  0x7700–0x77FF   M(x)   macro
  others          shown as hex literal

Keycode ranges – Vial protocol v5 (older firmware):
  0x0000–0x1FFF   same as v6
  0x4000–0x4FFF   Layer-tap  (same as v6)
  0x5000–0x500F   TO(layer)
  0x5100–0x510F   MO(layer)
  0x5200–0x520F   DF(layer)
  0x5300–0x530F   TG(layer)
  0x5400–0x540F   OSL(layer)
  0x5500–0x551F   OSM(mod)
  0x5700–0x57FF   TD(x)
  0x5800–0x580F   TT(layer)
  0x5900–0x59FF   LM(layer, mod)
  0x5C00–0x5CFF   Magic / Audio / Backlight specials
  0x6000–0x7FFF   Mod-tap  MT(mod, kc)  QK_MOD_TAP
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Basic HID keycode table
# ---------------------------------------------------------------------------

_BASIC: dict[int, str] = {
    0x00: "KC_NO",
    0x01: "KC_TRNS",
    0x04: "A", 0x05: "B", 0x06: "C", 0x07: "D", 0x08: "E",
    0x09: "F", 0x0A: "G", 0x0B: "H", 0x0C: "I", 0x0D: "J",
    0x0E: "K", 0x0F: "L", 0x10: "M", 0x11: "N", 0x12: "O",
    0x13: "P", 0x14: "Q", 0x15: "R", 0x16: "S", 0x17: "T",
    0x18: "U", 0x19: "V", 0x1A: "W", 0x1B: "X", 0x1C: "Y", 0x1D: "Z",
    0x1E: "1", 0x1F: "2", 0x20: "3", 0x21: "4", 0x22: "5",
    0x23: "6", 0x24: "7", 0x25: "8", 0x26: "9", 0x27: "0",
    0x28: "Enter",
    0x29: "Esc",
    0x2A: "Bksp",
    0x2B: "Tab",
    0x2C: "Space",
    0x2D: "-",
    0x2E: "=",
    0x2F: "[",
    0x30: "]",
    0x31: "\\",
    0x32: "#",      # non-US hash
    0x33: ";",
    0x34: "'",
    0x35: "`",
    0x36: ",",
    0x37: ".",
    0x38: "/",
    0x39: "CapsLk",
    0x3A: "F1",  0x3B: "F2",  0x3C: "F3",  0x3D: "F4",
    0x3E: "F5",  0x3F: "F6",  0x40: "F7",  0x41: "F8",
    0x42: "F9",  0x43: "F10", 0x44: "F11", 0x45: "F12",
    0x46: "PrtSc",
    0x47: "ScrLk",
    0x48: "Pause",
    0x49: "Ins",
    0x4A: "Home",
    0x4B: "PgUp",
    0x4C: "Del",
    0x4D: "End",
    0x4E: "PgDn",
    0x4F: "Right",
    0x50: "Left",
    0x51: "Down",
    0x52: "Up",
    0x53: "NumLk",
    0x54: "KP/",
    0x55: "KP*",
    0x56: "KP-",
    0x57: "KP+",
    0x58: "KPEnt",
    0x59: "KP1", 0x5A: "KP2", 0x5B: "KP3", 0x5C: "KP4",
    0x5D: "KP5", 0x5E: "KP6", 0x5F: "KP7", 0x60: "KP8",
    0x61: "KP9", 0x62: "KP0", 0x63: "KP.",
    0x64: "\\",    # non-US backslash
    0x65: "App",
    0x66: "Power",
    0x67: "KP=",
    0x68: "F13", 0x69: "F14", 0x6A: "F15", 0x6B: "F16",
    0x6C: "F17", 0x6D: "F18", 0x6E: "F19", 0x6F: "F20",
    0x70: "F21", 0x71: "F22", 0x72: "F23", 0x73: "F24",
    # Additional HID / QMK basic keycodes
    0x85: "KP,",
    0x87: "Ro",
    0x88: "Kana",
    0x89: "¥",
    0x8A: "Henk",
    0x8B: "Mhen",
    0x90: "Lang1",
    0x91: "Lang2",
    # Modifier keys (physical)
    0xE0: "Ctrl",
    0xE1: "SFT",
    0xE2: "Alt",
    0xE3: "GUI",
    0xE4: "Ctrl",
    0xE5: "SFT",
    0xE6: "Alt",
    0xE7: "GUI",
    # Keyboard-built-in volume controls (distinct from consumer-page audio)
    0x7F: "KbMute",
    0x80: "KbVol+",
    0x81: "KbVol-",
    # System control (consumer page, mapped into basic range by QMK)
    0xA5: "Power",   # KC_SYSTEM_POWER
    0xA6: "Slp",     # KC_SYSTEM_SLEEP
    0xA7: "Wake",    # KC_SYSTEM_WAKE
    # Audio
    0xA8: "Mute",    # KC_AUDIO_MUTE
    0xA9: "VolUp",   # KC_AUDIO_VOL_UP
    0xAA: "VolDn",   # KC_AUDIO_VOL_DOWN
    # Media transport
    0xAB: "Next",    # KC_MEDIA_NEXT_TRACK
    0xAC: "Prev",    # KC_MEDIA_PREV_TRACK
    0xAD: "Stop",    # KC_MEDIA_STOP
    0xAE: "Play",    # KC_MEDIA_PLAY_PAUSE
    0xAF: "MSel",    # KC_MEDIA_SELECT
    0xB0: "Eject",   # KC_MEDIA_EJECT
    0xB1: "Mail",    # KC_MAIL
    0xB2: "Calc",    # KC_CALCULATOR
    0xB3: "MyPC",    # KC_MY_COMPUTER
    # Browser
    0xB4: "Search",  # KC_WWW_SEARCH
    0xB5: "BrHome",  # KC_WWW_HOME
    0xB6: "BrBack",  # KC_WWW_BACK
    0xB7: "BrFwd",   # KC_WWW_FORWARD
    0xB8: "BrStop",  # KC_WWW_STOP
    0xB9: "BrRef",   # KC_WWW_REFRESH
    0xBA: "BrFav",   # KC_WWW_FAVORITES
    # Media extended
    0xBB: "MFfd",    # KC_MEDIA_FAST_FORWARD
    0xBC: "MRwd",    # KC_MEDIA_REWIND
    # Display
    0xBD: "BriUp",   # KC_BRIGHTNESS_UP
    0xBE: "BriDn",   # KC_BRIGHTNESS_DOWN
    0xBF: "CtrlPnl", # KC_CONTROL_PANEL
    0xC0: "Asst",    # KC_ASSISTANT
    0xC1: "MsnCtrl", # KC_MISSION_CONTROL
    0xC2: "Launch",  # KC_LAUNCHPAD
    0xCD: "MS↑",
    0xCE: "MS↓",
    0xCF: "MS←",
    0xD0: "MS→",
    0xD1: "Btn1",
    0xD2: "Btn2",
    0xD3: "Btn3",
    0xD4: "Btn4",
    0xD5: "Btn5",
    0xD9: "WhlU",
    0xDA: "WhlD",
    0xDB: "WhlL",
    0xDC: "WhlR",
    # Mouse keys (QMK KC_MS_* = 0xF0–0xFF)
    0xF0: "MS↑",
    0xF1: "MS↓",
    0xF2: "MS←",
    0xF3: "MS→",
    0xF4: "Btn1",
    0xF5: "Btn2",
    0xF6: "Btn3",
    0xF7: "Btn4",
    0xF8: "Btn5",
    0xF9: "WhlU",
    0xFA: "WhlD",
    0xFB: "WhlL",
    0xFC: "WhlR",
    0xFD: "Acc0",
    0xFE: "Acc1",
    0xFF: "Acc2",
}

# Named special keycodes (single value, not a range)
_NAMED: dict[int, str] = {
    0x7C00: "Boot",
    0x7C01: "Reboot",
    0x7C03: "EEClr",
    0x7C16: "GEsc",
    0x7C1A: "LSPO",
    0x7C1B: "RSPC",
    0x7C18: "LCPO",
    0x7C19: "RCPC",
    0x7C1C: "LAPO",
    0x7C1D: "RAPC",
    0x7C1E: "SftEnt",
    0x7C50: "CmbOn",
    0x7C51: "CmbOff",
    0x7C52: "CmbTog",
    0x7C53: "DynRec1",
    0x7C54: "DynRec2",
    0x7C55: "DynStop",
    0x7C56: "DynPly1",
    0x7C57: "DynPly2",
    0x7C73: "CapsWrd",
    0x7C79: "RepKey",
    0x7C7A: "AltRep",
    0x7C7B: "LyrLck",
    0x7802: "BL Tog",
    0x7805: "BL Step",
    0x7806: "BL Brth",
    0x7800: "BL On",
    0x7801: "BL Off",
    0x7804: "BL Inc",
    0x7803: "BL Dec",
    0x7820: "RGB Tog",
    0x7821: "RGB Mod",
    0x7822: "RGB RMod",
    0x7823: "RGB HUI",
    0x7824: "RGB HUD",
    0x7825: "RGB SAI",
    0x7826: "RGB SAD",
    0x7827: "RGB VAI",
    0x7828: "RGB VAD",
    0x7829: "RGB SPI",
    0x782A: "RGB SPD",
    0x782B: "RGB P",
    0x782C: "RGB B",
    0x782D: "RGB R",
    0x782E: "RGB SW",
    0x782F: "RGB SN",
    0x7830: "RGB K",
    0x7831: "RGB X",
    0x7832: "RGB G",
    0x7833: "RGB T",
    0x7840: "RM On",
    0x7841: "RM Off",
    0x7842: "RM Tog",
    0x7843: "RM Nxt",
    0x7844: "RM Prv",
    0x7845: "RM HuU",
    0x7846: "RM HuD",
    0x7847: "RM SaU",
    0x7848: "RM SaD",
    0x7849: "RM VaU",
    0x784A: "RM VaD",
    0x784B: "RM SpU",
    0x784C: "RM SpD",
    0x7000: "MagSwCtCaps",
    0x7001: "MagUnCtCaps",
    0x7004: "MagCaps>Ctl",
    0x7009: "MagGui On",
    0x700A: "MagGui Off",
    0x700B: "MagGui Tog",
    0x7016: "MagAltGui Tog",
    0x701B: "MagCtGui Tog",
    0x701D: "MagCtGui Sw",
    0x7011: "NKRO On",
    0x7012: "NKRO Off",
    0x7013: "NKRO Tog",
}

# Named specials for Vial protocol v5 (different addresses for some keys)
_NAMED_V5: dict[int, str] = {
    0x5C00: "Boot",
    0x5C01: "EEClr",
    0x5C16: "GEsc",
    0x5C17: "AS Up",
    0x5C18: "AS Dn",
    0x5C19: "AS Rep",
    0x5C1A: "AS Tog",
    0x5C1B: "AS On",
    0x5C1C: "AS Off",
    0x5C1D: "AU On",
    0x5C1E: "AU Off",
    0x5C1F: "AU Tog",
    0x5C20: "Clicky",
    0x5C23: "ClkUp",
    0x5C24: "ClkDn",
    0x5C25: "ClkRst",
    0x5C26: "MU On",
    0x5C27: "MU Off",
    0x5C28: "MU Tog",
    0x5C29: "MU Mod",
    0x5CDB: "BL On",
    0x5CDC: "BL Off",
    0x5CDD: "BL Dec",
    0x5CDE: "BL Inc",
    0x5CDF: "BL Tog",
    0x5CE0: "BL Step",
    0x5CE1: "BL Brth",
    0x5CE2: "RGB Tog",
    0x5CE3: "RGB Mod",
    0x5CE4: "RGB RMod",
    0x5CE5: "RGB HUI",
    0x5CE6: "RGB HUD",
    0x5CE7: "RGB SAI",
    0x5CE8: "RGB SAD",
    0x5CE9: "RGB VAI",
    0x5CEA: "RGB VAD",
    0x5CEB: "RGB SPI",
    0x5CEC: "RGB SPD",
    0x5CED: "RGB P",
    0x5CEE: "RGB B",
    0x5CEF: "RGB R",
    0x5CF0: "RGB SW",
    0x5CF1: "RGB SN",
    0x5CF2: "RGB K",
    0x5CF3: "RGB X",
    0x5CF4: "RGB G",
    0x5CF5: "RGB T",
    0x5CD7: "LSPO",
    0x5CD8: "RSPC",
    0x5CD9: "SftEnt",
    0x5CF3: "LCPO",
    0x5CF4: "RCPC",
    0x5CF5: "LAPO",
    0x5CF6: "RAPC",
    0x5CF7: "CmbOn",
    0x5CF8: "CmbOff",
    0x5CF9: "CmbTog",
    0x5DAC: "CapsWord",
    # Backlight and RGB – Vial v5 / vial-qmk addresses (from keycodes_v5.py)
    0x5CBB: "BL On",
    0x5CBC: "BL Off",
    0x5CBD: "BL Dec",
    0x5CBE: "BL Inc",
    0x5CBF: "BL Tog",
    0x5CC0: "BL Step",
    0x5CC1: "BL Brth",
    0x5CC2: "RGB Tog",
    0x5CC3: "RGB Mod",
    0x5CC4: "RGB RMod",
    0x5CC5: "RGB HUI",
    0x5CC6: "RGB HUD",
    0x5CC7: "RGB SAI",
    0x5CC8: "RGB SAD",
    0x5CC9: "RGB VAI",
    0x5CCA: "RGB VAD",
    0x5CCB: "RGB SPI",
    0x5CCC: "RGB SPD",
    0x5CCD: "RGB P",
    0x5CCE: "RGB B",
    0x5CCF: "RGB R",
    0x5CD0: "RGB SW",
    0x5CD1: "RGB SN",
    0x5CD2: "RGB K",
    0x5CD3: "RGB X",
    0x5CD4: "RGB G",
    0x5CD5: "RGB T",
}

# ---------------------------------------------------------------------------
# US keyboard shift layer  (applied when the only modifier is Shift)
# ---------------------------------------------------------------------------

# Maps the unshifted key name (from _BASIC) to the symbol produced with Shift
# on a standard US QWERTY layout.  Letters are included so SFT(A) → "A".
_SHIFT_US: dict[str, str] = {
    # Numbers → symbols
    "1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
    "6": "^", "7": "&", "8": "*", "9": "(", "0": ")",
    # Punctuation
    "-": "_",  "=": "+",
    "[": "{",  "]": "}",  "\\": "|",
    ";": ":",  "'": '"',  "`": "~",
    ",": "<",  ".": ">",  "/": "?",
    # Letters – shift just capitalises; already stored uppercase in _BASIC
    **{chr(c): chr(c) for c in range(ord("A"), ord("Z") + 1)},
}

# ---------------------------------------------------------------------------
# US-International layout extensions
# ---------------------------------------------------------------------------

# Dead keys on US-International (standalone, no modifier).
# The key still shows its normal label but with a trailing * to indicate it
# will combine with the next keystroke rather than output immediately.
_DEAD_KEYS_US_INTL: frozenset[str] = frozenset({"'", "`"})

# Shift-layer overrides for US-International dead keys.
# Three shifted symbols become dead keys; all others are the same as US.
_SHIFT_US_INTL: dict[str, str] = {
    **_SHIFT_US,
    "6": "^*",   # SFT(6) → dead circumflex  (^ in US)
    "`": "~*",   # SFT(`) → dead tilde       (~ in US)
    "'": '"*',   # SFT(') → dead diaeresis   (" in US)
}

# AltGr (Right Alt) combinations on US-International.
# Key: _BASIC name of the unshifted key.
# Value: (unshifted character, shifted character).
_ALTGR_US_INTL: dict[str, tuple[str, str]] = {
    "A": ("á", "Á"),  "E": ("é", "É"),  "I": ("í", "Í"),
    "O": ("ó", "Ó"),  "U": ("ú", "Ú"),  "Y": ("ü", "Ü"),
    "N": ("ñ", "Ñ"),  "C": ("ç", "Ç"),  "Q": ("ä", "Ä"),
    "P": ("ö", "Ö"),  "L": ("ø", "Ø"),  "Z": ("æ", "Æ"),
    "T": ("þ", "Þ"),  "D": ("ð", "Ð"),  "S": ("ß", "ẞ"),
    "W": ("å", "Å"),
    "/": ("¿", "¿"),  "1": ("¡", "¡"),  "M": ("µ", "µ"),
}

# ---------------------------------------------------------------------------
# Modifier bit → short name  (used by mod+key and mod-tap decoding)
# ---------------------------------------------------------------------------

# The modifier bits in the upper byte of Vial v6 keycodes.
# Bit 12 (0x1000) marks "right-hand" variants.
_MOD_NAMES: list[tuple[int, str, str]] = [
    # (bitmask, left-name, right-name)
    (0x01, "Ctrl",  "Ctrl"),
    (0x02, "SFT",   "SFT"),
    (0x04, "Alt",   "Alt"),
    (0x08, "GUI",   "GUI"),
]
_RIGHT_FLAG = 0x10  # bit 4 of the 5-bit mod field


def _mod_bits_to_name(mod5: int) -> str:
    """Convert a 5-bit modifier mask to a human-readable string."""
    right = bool(mod5 & _RIGHT_FLAG)
    base = mod5 & 0x0F
    parts: list[str] = []
    for mask, lname, rname in _MOD_NAMES:
        if base & mask:
            parts.append(rname if right else lname)
    if not parts:
        return f"Mod(0x{mod5:02X})"
    return "+".join(parts)


def _basic_name(kc8: int) -> str:
    """Look up the display name for a basic (0x00-0xFF) keycode."""
    return _BASIC.get(kc8, f"0x{kc8:02X}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

KeySpec = str | dict | None


def decode_keycode(
    kc: int,
    custom_keycodes: list[dict] | None = None,
    vial_protocol: int = 6,
    macro_count: int = 0,
    os_layout: str = "us",
) -> KeySpec:
    """
    Decode a Vial uint16 keycode into a keymap-drawer key specification.

    Parameters
    ----------
    kc:
        Raw uint16 keycode from the Vial keymap buffer.
    custom_keycodes:
        Optional list of custom keycode dicts from the keyboard definition
        (used to resolve QK_KB / USER keycodes).
    vial_protocol:
        Vial protocol version reported by the keyboard (5 or 6).
        Defaults to 6; pass 5 for older firmware.
    macro_count:
        Number of macros reported by the keyboard (from CMD_VIA_MACRO_GET_COUNT).
        Macros are named ``M0``, ``M1`` … ``M(n-1)``.
    os_layout:
        Host OS keyboard layout used to resolve mod+key symbols.
        ``"us"`` (default) uses the standard US QWERTY layout.
        ``"us-intl"`` additionally marks dead keys with ``*`` and decodes
        AltGr (Right Alt) combinations to their Unicode characters.
    """
    # ---- KC_NO / KC_TRNS ---------------------------------------------------
    if kc == 0x0000:
        return None
    if kc == 0x0001:
        return {"type": "trans"}

    # ---- Basic keycodes (0x02..0x00FF) – same for all protocol versions ----
    if kc <= 0x00FF:
        name = _basic_name(kc)
        if os_layout == "us-intl" and name in _DEAD_KEYS_US_INTL:
            return name + "*"
        return name

    # ---- Named specials (exact match) – version-specific first -------------
    named = _NAMED_V5 if vial_protocol < 6 else _NAMED
    if kc in named:
        return named[kc]
    # Also check the other table as fallback
    alt_named = _NAMED if vial_protocol < 6 else _NAMED_V5
    if kc in alt_named:
        return alt_named[kc]

    # ---- Mod+key combos  0x0100–0x1FFF – same for all versions -------------
    if 0x0100 <= kc <= 0x1FFF:
        mod5 = (kc >> 8) & 0x1F
        kc8 = kc & 0xFF
        key_str = _basic_name(kc8)

        if os_layout in ("us-intl", "us-intl-nodead"):
            # Pure Right Alt (AltGr) + key → US-International character
            if mod5 == 0x14 and key_str in _ALTGR_US_INTL:
                return _ALTGR_US_INTL[key_str][0]
            # Right Alt + Shift + key → shifted US-International character
            if mod5 == 0x16 and key_str in _ALTGR_US_INTL:
                return _ALTGR_US_INTL[key_str][1]

        mod_str = _mod_bits_to_name(mod5)
        # Pure Shift + key → resolve to the actual layout symbol
        if mod_str == "SFT":
            shift_map = _SHIFT_US_INTL if os_layout == "us-intl" else _SHIFT_US
            if key_str in shift_map:
                return shift_map[key_str]
        return f"{mod_str}({key_str})"

    # ---- Protocol v6 specific ranges ---------------------------------------
    if vial_protocol >= 6:
        # Mod-tap  MT  0x2000–0x3FFF
        if 0x2000 <= kc <= 0x3FFF:
            mod5 = (kc >> 8) & 0x1F
            kc8 = kc & 0xFF
            return {"t": _basic_name(kc8), "h": _mod_bits_to_name(mod5)}

        # Layer-tap  LT  0x4000–0x4FFF
        if 0x4000 <= kc <= 0x4FFF:
            layer = (kc >> 8) & 0x0F
            kc8 = kc & 0xFF
            return {"t": _basic_name(kc8), "h": f"MO({layer})"}

        # Layer-mod  LM  0x5000–0x51FF
        if 0x5000 <= kc <= 0x51FF:
            layer = (kc >> 5) & 0x0F
            mod5 = kc & 0x1F
            return f"LM({layer},{_mod_bits_to_name(mod5)})"

        # Layer operations  0x5200–0x52FF
        if 0x5200 <= kc <= 0x521F:
            return f"TO({kc - 0x5200})"
        if 0x5220 <= kc <= 0x523F:
            return f"MO({kc - 0x5220})"
        if 0x5240 <= kc <= 0x525F:
            return f"DF({kc - 0x5240})"
        if 0x5260 <= kc <= 0x527F:
            return f"TG({kc - 0x5260})"
        if 0x5280 <= kc <= 0x529F:
            return f"OSL({kc - 0x5280})"
        if 0x52A0 <= kc <= 0x52BF:
            return f"OSM({_mod_bits_to_name(kc & 0x1F)})"
        if 0x52C0 <= kc <= 0x52DF:
            return f"TT({kc - 0x52C0})"
        if 0x52E0 <= kc <= 0x52FF:
            return f"PDF({kc - 0x52E0})"

        # Tap-dance  TD  0x5700–0x57FF
        if 0x5700 <= kc <= 0x57FF:
            return f"TD({kc - 0x5700})"

        # Macros  0x7700–0x77FF
        if 0x7700 <= kc <= 0x77FF:
            idx = kc - 0x7700
            if macro_count == 0 or idx < macro_count:
                return f"M{idx}"

        # Custom / user keycodes  0x7E00–
        if 0x7E00 <= kc <= 0x7EFF:
            if custom_keycodes:
                idx = kc - 0x7E00
                if idx < len(custom_keycodes):
                    entry = custom_keycodes[idx]
                    return entry.get("shortName") or entry.get("name", f"USER{idx:02d}")
            return f"USER{kc - 0x7E00:02d}"

    # ---- Protocol v5 specific ranges ---------------------------------------
    else:
        # Layer-tap  LT  0x4000–0x4FFF  (same position as v6)
        if 0x4000 <= kc <= 0x4FFF:
            layer = (kc >> 8) & 0x0F
            kc8 = kc & 0xFF
            return {"t": _basic_name(kc8), "h": f"MO({layer})"}

        # Layer operations  (16 layers each, 4-bit layer field)
        # Note: v5 firmware encodes TO(layer) as QK_TO|(ON_PRESS<<4)|layer = 0x5010+layer
        # Some versions use 0x5000+layer; handle both by taking the low nibble.
        if 0x5000 <= kc <= 0x501F:
            return f"TO({kc & 0x0F})"
        if 0x5100 <= kc <= 0x510F:
            return f"MO({kc - 0x5100})"
        if 0x5200 <= kc <= 0x520F:
            return f"DF({kc - 0x5200})"
        if 0x5300 <= kc <= 0x530F:
            return f"TG({kc - 0x5300})"
        if 0x5400 <= kc <= 0x540F:
            return f"OSL({kc - 0x5400})"
        if 0x5500 <= kc <= 0x551F:
            return f"OSM({_mod_bits_to_name(kc & 0x1F)})"
        if 0x5700 <= kc <= 0x57FF:
            return f"TD({kc - 0x5700})"
        if 0x5800 <= kc <= 0x580F:
            return f"TT({kc - 0x5800})"
        if 0x5900 <= kc <= 0x59FF:
            # LM(layer, mod): bits 8-4 = layer (5 bits), bits 3-0 = mod (4 bits)
            # Actually v5: QK_LAYER_MOD = 0x5900, encoding: (layer << 4) | mod?
            # From v5 source analysis: high nibble after 0x5900 = layer, low nibble = mod
            layer = (kc >> 4) & 0x0F
            mod4 = kc & 0x0F
            return f"LM({layer},{_mod_bits_to_name(mod4)})"

        # Macros  0x5F12 + n  (v5 firmware)
        if macro_count > 0 and 0x5F12 <= kc < 0x5F12 + macro_count:
            return f"M{kc - 0x5F12}"

        # Mod-tap  MT  0x6000–0x7FFF
        if 0x6000 <= kc <= 0x7FFF:
            mod5 = (kc >> 8) & 0x1F
            kc8 = kc & 0xFF
            return {"t": _basic_name(kc8), "h": _mod_bits_to_name(mod5)}

    # ---- Fallback: show as hex -------------------------------------------------
    return f"0x{kc:04X}"


# ---------------------------------------------------------------------------
# Layer-name substitution
# ---------------------------------------------------------------------------

# Matches standalone layer operations produced by decode_keycode.
_STANDALONE_LAYER_OP_RE = re.compile(r"^(MO|TG|TO|DF|OSL|TT|PDF)\((\d+)\)$")

# Only MO is a momentary hold — the others are toggles/one-shots that don't
# benefit from the "held" visual style.
_HELD_LAYER_OPS = frozenset({"MO"})

# Matches LM(n, mod) anywhere in a string.
_LM_RE = re.compile(r"LM\((\d+)(,[^)]*)\)")


def _replace_lm_refs(s: str, layer_names: list[str]) -> str:
    """Substitute layer indices inside LM(n, mod) expressions."""
    def _sub(m: re.Match) -> str:
        idx = int(m.group(1))
        name = layer_names[idx] if idx < len(layer_names) else m.group(1)
        return f"LM({name}{m.group(2)})"
    return _LM_RE.sub(_sub, s)


def _resolve_standalone_op(s: str, layer_names: list[str]) -> str | dict | None:
    """
    If *s* is a standalone layer operation like ``MO(2)`` or ``TG(1)``, return
    the substituted form.  ``MO`` becomes ``{"t": <name>, "type": "held"}``; all
    other ops (TG, TO, DF, OSL, TT, PDF) become ``OP(Name)`` strings.
    Returns ``None`` when *s* is not a standalone layer operation.
    """
    m = _STANDALONE_LAYER_OP_RE.match(s)
    if not m:
        return None
    op = m.group(1)
    idx = int(m.group(2))
    name = layer_names[idx] if idx < len(layer_names) else m.group(2)
    if op in _HELD_LAYER_OPS:
        return {"t": name, "type": "held"}
    return name


def substitute_layer_names(spec: KeySpec, layer_names: list[str]) -> KeySpec:
    """
    Replace numeric layer indices in a decoded key spec with human-readable names.

    ``MO(n)`` (momentary hold) becomes ``{t: Name, type: held}`` so
    keymap-drawer renders it with the held visual style.  All other standalone
    layer operations (``TG``, ``TO``, ``DF``, ``OSL``, ``TT``, ``PDF``) keep
    their wrapper with the index replaced by the layer name.  When a layer
    operation appears as the hold label inside a tap-hold dict, the hold field
    is replaced with the bare layer name.  ``LM(n, mod)`` keeps its wrapper
    with the index substituted.

    For example, with ``layer_names = ["Base", "Nav", "Sym"]``::

        "MO(1)"                      → {"t": "Nav", "type": "held"}
        "TG(2)"                      → "Sym"
        {"t": "Esc", "h": "MO(1)"}  → {"t": "Esc", "h": "Nav"}
        {"t": "Tab", "h": "TG(2)"}  → {"t": "Tab", "h": "Sym"}
        "LM(1,Alt)"                  → "LM(Nav,Alt)"
    """
    if spec is None:
        return None

    if isinstance(spec, str):
        resolved = _resolve_standalone_op(spec, layer_names)
        if resolved is not None:
            return resolved
        return _replace_lm_refs(spec, layer_names)

    if isinstance(spec, dict):
        result = dict(spec)
        # Hold field: resolve layer op to bare name (hold context is already visual)
        if isinstance(result.get("h"), str):
            m = _STANDALONE_LAYER_OP_RE.match(result["h"])
            if m:
                idx = int(m.group(2))
                result["h"] = layer_names[idx] if idx < len(layer_names) else m.group(2)
            else:
                result["h"] = _replace_lm_refs(result["h"], layer_names)
        if isinstance(result.get("t"), str):
            result["t"] = _replace_lm_refs(result["t"], layer_names)
        return result

    return spec


# ---------------------------------------------------------------------------
# Held-layer position detection
# ---------------------------------------------------------------------------

def get_held_layer(raw: int, vial_protocol: int) -> int | None:
    """
    Return the target layer index if *raw* is a keycode that holds a layer
    (``MO``, ``LT``, or ``TT``), otherwise return ``None``.

    This is used to mark the same physical key in the target layer as
    ``{type: held}`` instead of ``{type: trans}``.
    """
    # Layer-tap LT: 0x4000–0x4FFF for both v5 and v6; layer in bits 11–8
    if 0x4000 <= raw <= 0x4FFF:
        return (raw >> 8) & 0x0F

    if vial_protocol >= 6:
        if 0x5220 <= raw <= 0x523F:   # MO
            return raw - 0x5220
        if 0x52C0 <= raw <= 0x52DF:   # TT
            return raw - 0x52C0
    else:
        if 0x5100 <= raw <= 0x510F:   # MO v5
            return raw - 0x5100
        if 0x5800 <= raw <= 0x580F:   # TT v5
            return raw - 0x5800

    return None
