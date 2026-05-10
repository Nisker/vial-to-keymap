# vial-to-keymap

Connect to a keyboard running [Vial](https://get.vial.today/) firmware over USB and generate a complete [keymap-drawer](https://github.com/caksoylar/keymap-drawer) configuration, ready to render into an SVG image.

## Requirements

- Python ≥ 3.10
- A keyboard running Vial firmware (USB connected)
- On Linux: udev rules granting access to the HID device.

## Installation

From github:

```bash
pipx install git@github.com:Nisker/vial-to-keymap.git
```

install from source:

```bash
git clone https://github.com/Nisker/vial-to-keymap
cd vial-to-keymap
pipx install .
```

## Usage

```
vial-to-keymap [OPTIONS]
```

### Options

| Option | Description |
|--------|-------------|
| `--list`, `-l` | List connected Vial keyboards and exit |
| `--device N`, `-d N` | Use the Nth detected keyboard (default: 0) |
| `--output FILE`, `-o FILE` | Output to `FILE.yaml` and `FILE.json` (default: keyboard name) |
| `--layer-names NAMES` | Comma-separated layer names, e.g. `Base,Nav,Sym` |
| `--os-layout LAYOUT` | Host OS keyboard layout: `us` (default), `us-intl`, or `us-intl-nodead` |
| `--layout-variant N` | Physical layout variant index for keyboards with multiple options (default: 0) |

### Examples

```bash
# Auto-detect keyboard, write <KeyboardName>.yaml + <KeyboardName>.json
vial-to-keymap

# Give layers meaningful names
vial-to-keymap --layer-names "Base,Nav,Num,Sym,Fn"

# Use US-International host layout for AltGr key decoding
vial-to-keymap --os-layout us-intl

# Render the result with keymap-drawer
keymap draw my_board.yaml > my_board.svg
```

## Linux udev rules

On Linux, the HID device is not accessible to regular users by default. 

see [vial documentation](https://get.vial.today/manual/linux-udev.html)

## Output files

### `<name>.yaml`

A `keymap-drawer` YAML configuration including:
- The physical key layout (derived from the keyboard's KLE data)
- All layer keymaps with human-readable labels
- Combo definitions showing which key appears on each layer

Feed this directly to `keymap draw` to produce an SVG diagram.

### `<name>.json`

A QMK `info.json`-compatible layout file. Can be used with `keymap-drawer`'s `--qmk-info-json` flag or as a starting point for QMK configuration.

## Acknowledgements

Keycode values are sourced from [QMK Firmware](https://github.com/qmk/qmk_firmware) (GPL-2.0) and [Vial](https://github.com/vial-kb/vial-gui) (GPL-2.0).
