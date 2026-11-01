"""Build the M8 family data files (run once on a dev box; outputs are committed).

Sources (licenses checked, recorded in each JSON "source"/"license"):
  AVR   : Microchip ATmega DFP 3.6.299 atdf/*.atdf                        (Apache-2.0)
  STM32 : STMicroelectronics/STM32_open_pin_data  mcu/*.xml + IP/GPIO-*   (BSD-3-Clause)
          STMicroelectronics/cmsis-device-f0/f1/f4  Include + gcc startup (Apache-2.0)
          ARM-software/CMSIS_5  CMSIS/Core/Include                        (Apache-2.0)

python scripts/build_family_data.py /path/to/m8src
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "chipllama" / "data" / "families"


def strip_ns(root):
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


# ------------------------------------------------------------------ AVR (ATDF)
def avr(src: Path, dev: str):
    f = src / "atmega" / "atdf" / f"{dev}.atdf"
    root = strip_ns(ET.parse(f).getroot())
    regs, pads = {}, {}
    for mod in root.iter("module"):
        for rg in mod.findall("register-group"):
            for r in rg.findall("register"):
                name = r.get("name")
                if mod.get("name") in ("FUSE", "LOCKBIT"):
                    continue
                bits = {}
                for bf in r.findall("bitfield"):
                    m = int(bf.get("mask"), 0)
                    bits[bf.get("name")] = m
                regs[name] = {"addr": int(r.get("offset"), 0), "size": int(r.get("size", "1")), "mod": mod.get("name"),
                              "bits": bits, "caption": r.get("caption", "")}
    for inst in root.iter("instance"):
        for s in inst.iter("signal"):
            pad = s.get("pad")
            if not pad:
                continue
            g = s.get("group")
            idx = s.get("index")
            pads.setdefault(pad, []).append(f"{inst.get('name')}:{g}{idx if idx is not None else ''}")
    # fuse defaults for docs
    out = {"device": dev.upper(), "family": "avr", "source": f"Microchip ATmega_DFP 3.6.299 atdf/{dev}.atdf",
           "license": "Apache-2.0", "registers": regs, "pads": pads}
    (OUT / "avr" / f"{dev.upper()}.json").write_text(json.dumps(out, indent=0, sort_keys=True))
    print("avr", dev, len(regs), "registers", len(pads), "pads")


# ------------------------------------------------------------------ STM32 (open pin data + CMSIS)
def stm32(src: Path, mcu_xml: str, gpio_ip: str, dev: str, header: str, define: str, startup: str, series: str,
          flash_k: int, ram_k: int, core: str):
    base = src / "STM32_open_pin_data" / "mcu"
    root = strip_ns(ET.parse(base / mcu_xml).getroot())
    groot = strip_ns(ET.parse(base / "IP" / gpio_ip).getroot())
    af = {}
    for gp in groot.iter("GPIO_Pin"):
        pin = gp.get("Name").split("-")[0].split(" ")[0]
        for ps in gp.findall("PinSignal"):
            sig = ps.get("Name")
            vals = [v.text for v in ps.iter("PossibleValue") if v.text]
            rb = ps.find("RemapBlock")
            if rb is not None:
                if rb.get("DefaultRemap") == "true":
                    af.setdefault(pin, {})[sig] = "default"
                else:
                    af.setdefault(pin, {}).setdefault(sig, "remap:" + (vals[0] if vals else rb.get("Name")))
            else:
                m = re.search(r"GPIO_AF(\d+)_", vals[0]) if vals else None
                if m:
                    af.setdefault(pin, {})[sig] = int(m.group(1))
    pins = []
    for p in root.findall("Pin"):
        raw = p.get("Name")
        port = re.match(r"^(P[A-K]\d{1,2})", raw)
        sigs = [s.get("Name") for s in p.findall("Signal") if s.get("Name") != "GPIO"]
        pins.append({"pos": p.get("Position"), "name": raw, "port": port.group(1) if port else None, "type": p.get("Type"),
                     "signals": sigs, "af": af.get(port.group(1), {}) if port else {}})
    # registers from the CMSIS device header: struct members + bit masks
    htxt = (src / f"cmsis-device-{series}" / "Include" / header).read_text(errors="ignore")
    types = {}
    for m in re.finditer(r"typedef struct\s*\{(.*?)\}\s*(\w+)_TypeDef;", htxt, re.S):
        mem = re.findall(r"__IO[M]?\s+uint\d+_t\s+(\w+)(\[\d+\])?\s*;", m.group(1)) + \
              re.findall(r"__I[M]?\s+uint\d+_t\s+(\w+)(\[\d+\])?\s*;", m.group(1)) + \
              re.findall(r"__O[M]?\s+uint\d+_t\s+(\w+)(\[\d+\])?\s*;", m.group(1))
        types[m.group(2)] = sorted({a for a, _ in mem if not a.startswith("RESERVED")})
    inst = {}
    for m in re.finditer(r"#define\s+(\w+)\s+\(\((\w+)_TypeDef\s*\*\)\s*\w+\)", htxt):
        inst[m.group(1)] = m.group(2)
    bits = {}
    for m in re.finditer(r"#define\s+(\w+)_Msk\s+\((0x[0-9A-Fa-f]+)U?L?\s*<<\s*(\w+)_Pos\)", htxt):
        name = m.group(1)
        pos = re.search(rf"#define\s+{name}_Pos\s+\((\d+)U?\)", htxt)
        if pos:
            bits[name] = int(m.group(2), 16) << int(pos.group(1))
    out = {"device": dev, "family": "stm32", "series": series, "core": core, "header": header, "define": define,
           "startup": startup, "flash_k": flash_k, "ram_k": ram_k, "pins": pins, "types": types, "instances": inst,
           "bits": bits, "source": f"STM32_open_pin_data mcu/{mcu_xml} + IP/{gpio_ip} (BSD-3-Clause); "
                                    f"cmsis-device-{series} Include/{header} (Apache-2.0)", "license": "BSD-3-Clause / Apache-2.0"}
    (OUT / "stm32" / f"{dev}.json").write_text(json.dumps(out, indent=0, sort_keys=True))
    print("stm32", dev, len(pins), "pins", len(types), "types", len(bits), "bit masks")
    # ship headers + startup
    cm = OUT / "stm32" / "cmsis"
    cm.mkdir(exist_ok=True)
    inc = src / f"cmsis-device-{series}" / "Include"
    for h in (header, f"stm32{series}xx.h", f"system_stm32{series}xx.h"):
        shutil.copy(inc / h, cm / h)
    shutil.copy(src / f"cmsis-device-{series}" / "Source" / "Templates" / "gcc" / startup, cm / startup)
    lic = next((src / f"cmsis-device-{series}").glob("[Ll][Ii][Cc]*")); shutil.copy(lic, cm / f"LICENSE-cmsis-device-{series}.md")


def cmsis_core(src: Path):
    cm = OUT / "stm32" / "cmsis"
    cm.mkdir(exist_ok=True)
    core = src / "CMSIS_5" / "CMSIS" / "Core" / "Include"
    for h in ("core_cm0.h", "core_cm3.h", "core_cm4.h", "cmsis_gcc.h", "cmsis_compiler.h", "cmsis_version.h", "mpu_armv7.h"):
        shutil.copy(core / h, cm / h)
    (cm / "LICENSE-CMSIS_5.txt").write_text("CMSIS_5 Core headers - Apache-2.0 (https://github.com/ARM-software/CMSIS_5/blob/develop/LICENSE.txt)\n")
    (cm / "LICENSE-open-pin-data.txt").write_text("STM32_open_pin_data - BSD-3-Clause, Copyright (c) 2020 STMicroelectronics\n")


if __name__ == "__main__":
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "/workspace/m8src")
    for d in ("ATmega328P", "ATmega2560"):
        avr(src, d)
    cmsis_core(src)
    stm32(src, "STM32F103C(8-B)Tx.xml", "GPIO-STM32F103x8_gpio_v1_0_Modes.xml", "STM32F103C8", "stm32f103xb.h", "STM32F103xB",
          "startup_stm32f103xb.s", "f1", 64, 20, "cm3")
    stm32(src, "STM32F411R(C-E)Tx.xml", "GPIO-STM32F411_gpio_v1_0_Modes.xml", "STM32F411RE", "stm32f411xe.h", "STM32F411xE",
          "startup_stm32f411xe.s", "f4", 512, 128, "cm4")
    stm32(src, "STM32F030F4Px.xml", "GPIO-STM32F031_gpio_v1_0_Modes.xml", "STM32F030F4", "stm32f030x6.h", "STM32F030x6",
          "startup_stm32f030x6.s", "f0", 16, 4, "cm0")
