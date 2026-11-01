"""Device pinouts for the Circuit tab.

Bundled tables come straight from the Microchip device-family packs (DFP) ``edc/<DEVICE>.PIC`` ``<edc:PinList>``
(pack + version recorded in each JSON ``source``).  The PIC24FJ256GB110 table was additionally cross-checked
pin by pin against DS39897C Table 1-4 (p.17-25): all 100 port pins agree.  Any other device is parsed on demand
from an installed DFP (MPLAB X ``packs`` folder), so the Circuit tab works for every PIC whose pack is present.

5 V tolerance is only known from the datasheet; it is shipped for the GB110 (DS39897C Table 10-1, p.134)
and reported as ``None`` (unknown) for other devices.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).parent / "data" / "pinout"
_PORT = re.compile(r"^R([A-K])(\d{1,2})$")
_POWER = {"VDD": "vdd", "VSS": "vss", "AVDD": "vdd", "AVSS": "vss", "VCAP": "vcap", "VDDCORE": "vcap",
          "VCAP/VDDCORE": "vcap", "ENVREG": "envreg", "MCLR": "mclr", "VUSB": "vusb", "VBUS": "vbus",
          "NC": "nc", "VPP": "mclr", "VREGEN": "envreg", "VUSB3V3": "vusb"}

# DS39897C Table 10-1 (p.134): digital pins that tolerate 5.5 V on input
_GB110_5V = {"A": [14, 15, 0, 1, 2, 3, 4, 5, 6, 7], "C": [1, 2, 3, 4], "D": list(range(16)),
             "E": list(range(10)), "F": [12, 13, 8, 1, 2, 3, 4, 5], "G": [12, 13, 14, 15, 0, 1]}


def bundled() -> list[str]:
    return sorted(p.stem for p in DATA.glob("*.json"))


def _norm_dev(d: str) -> str:
    return (d or "").upper().replace("PIC", "PIC", 1).strip()


def _parse_edc_pins(text: str) -> tuple[list[list[str]], str | None, str]:
    m = re.search(r"<edc:PinList([^>]*)>(.*?)</edc:PinList>", text, re.S)
    if not m:
        raise ValueError("no <edc:PinList> in EDC file")
    pins = [re.findall(r'VirtualPin\s+edc:name="([^"]+)"', p) for p in re.findall(r"<edc:Pin>(.*?)</edc:Pin>", m.group(2), re.S)]
    while pins and pins[-1] == ["NC"]:
        pins.pop()
    pps = re.search(r'ppsflavor="([^"]+)"', m.group(1))
    arch = re.search(r'edc:arch="([^"]+)"', text)
    return pins, (pps.group(1) if pps else None), (arch.group(1) if arch else "")


def from_edc_file(path: str | Path) -> dict:
    p = Path(path)
    pins, pps, arch = _parse_edc_pins(p.read_text(encoding="utf-8", errors="ignore"))
    return {"device": p.stem.upper(), "arch": arch, "pps": pps, "source": f"{p} <edc:PinList>",
            "pins": [{"num": i + 1, "names": n} for i, n in enumerate(pins)]}


@lru_cache(maxsize=32)
def raw(device: str, packs: str = "") -> dict | None:
    dev = _norm_dev(device)
    f = DATA / f"{dev}.json"
    if f.is_file():
        return json.loads(f.read_text(encoding="utf-8"))
    try:                                         # installed DFP on the user's PC
        from .dfp import packs_with_device
        for pack in packs_with_device(dev, packs):
            for cand in (pack / "edc" / f"{dev}.PIC", pack / "edc" / f"{dev.replace('DSPIC', 'dsPIC')}.PIC"):
                if cand.is_file():
                    return from_edc_file(cand)
    except Exception:
        pass
    return None


def family(device: str, arch: str = "") -> str:
    d = device.upper()
    if d.startswith("PIC32"):
        return "pic32"
    if d.startswith("DSPIC33"):
        return "dspic33"
    if d.startswith("PIC24"):
        return "pic24"
    if d.startswith("PIC18"):
        return "pic18"
    if d.startswith(("PIC16", "PIC12", "PIC10")):
        return "pic16"
    return arch or "unknown"


def classify(names: list[str], device: str) -> dict:
    up = [n.upper() for n in names]
    port = next((n for n in up if _PORT.match(n)), None)
    power = next((_POWER[n.rstrip("_N")] for n in up if n.rstrip("_N") in _POWER), None)
    if port and power == "mclr":
        power = None                              # RE3/MCLR on PIC18/16: usable input when MCLRE=OFF
    rp = next((int(n[2:]) for n in up if re.fullmatch(r"RP\d+", n)), None)
    rpi = next((int(n[3:]) for n in up if re.fullmatch(r"RPI\d+", n)), None)
    an = next((int(re.sub(r"\D", "", n)) for n in up if re.fullmatch(r"AN[A-Z]?\d+", n)), None)
    cn = next((n for n in up if re.fullmatch(r"CN\d+", n)), None)
    tol = None
    if _norm_dev(device) == "PIC24FJ256GB110" and port:
        m = _PORT.match(port)
        tol = int(m.group(2)) in _GB110_5V.get(m.group(1), [])
    label = port or (names[0] if names else "?")
    return {"names": names, "label": label, "port": port, "power": power, "rp": rp, "rpi": rpi, "an": an, "cn": cn,
            "tol5v": tol}


def get(device: str, packs: str = "") -> dict:
    """{device, family, arch, pps, source, verified, count, layout, pins:[{num, label, names, ...}]}"""
    from . import families as FAM
    if FAM.is_family(device):
        return FAM.info(device)
    r = raw(device, packs)
    if r is None:
        n = 40
        pins = [{"num": i + 1, "names": [f"P{i + 1}"]} for i in range(n)]
        r = {"device": _norm_dev(device) or "GENERIC", "arch": "", "pps": None, "source": "generic placeholder - "
             "install the device pack (DFP) to get the real pinout", "pins": pins, "generic": True}
    pins = [dict(num=p["num"], **classify(p["names"], r["device"])) for p in r["pins"]]
    n = len(pins)
    layout = "quad" if n >= 44 else "dual"
    return {"device": r["device"], "family": family(r["device"], r.get("arch", "")), "arch": r.get("arch", ""),
            "pps": r.get("pps"), "source": r["source"], "generic": bool(r.get("generic")),
            "verified": r["device"] == "PIC24FJ256GB110", "count": n, "layout": layout, "pins": pins}


def by_label(info: dict) -> dict:
    return {p["label"]: p for p in info["pins"]}
