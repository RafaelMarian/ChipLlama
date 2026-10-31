"""Find and run the family toolchains (read-only discovery; nothing is ever installed from here)."""
from __future__ import annotations

import glob
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import FQBN

WIN = platform.system() == "Windows"
EXE = ".exe" if WIN else ""
HOME = Path.home()

INSTALL = {
    "arduino-cli": "Arduino CLI: https://arduino.github.io/arduino-cli/latest/installation/ (Windows: `winget install ArduinoSA.CLI`, ~40 MB). "
                   "Then `arduino-cli core install arduino:avr` (~150 MB incl. avr-gcc). Arduino IDE 2 also bundles arduino-cli.",
    "avr-gcc": "avr-gcc comes with the Arduino AVR core: `arduino-cli core install arduino:avr` "
               "(or Arduino IDE > Boards Manager > Arduino AVR Boards). ChipLlama finds it inside the Arduino15 packages folder.",
    "arm-none-eabi-gcc": "Arm GNU Toolchain (~250 MB): https://developer.arm.com/downloads/-/arm-gnu-toolchain-downloads "
                         "(Windows: `winget install Arm.GnuArmEmbeddedToolchain`) or STM32CubeIDE (bundles it, ~1 GB).",
    "esp32-core": "ESP32 Arduino core (~1 GB incl. toolchains): `arduino-cli config add board_manager.additional_urls "
                  "https://espressif.github.io/arduino-esp32/package_esp32_index.json` then `arduino-cli core update-index` and "
                  "`arduino-cli core install esp32:esp32`.",
    "make": "GNU make: Windows `winget install ezwinports.make` (small), or use the `build.ps1`-free route: STM32CubeIDE / Arduino IDE.",
}


def _arduino15() -> list[Path]:
    c = [HOME / ".arduino15", HOME / "AppData" / "Local" / "Arduino15", HOME / "Library" / "Arduino15"]
    if os.environ.get("LOCALAPPDATA"):
        c.insert(0, Path(os.environ["LOCALAPPDATA"]) / "Arduino15")
    return [p for p in c if p.exists()]


def _newest(pattern: str) -> str | None:
    hits = sorted(glob.glob(pattern))
    return hits[-1] if hits else None


def find_arduino_cli() -> str | None:
    p = shutil.which("arduino-cli")
    if p:
        return p
    for c in ([r"C:\Program Files\Arduino CLI\arduino-cli.exe",
               str(HOME / "AppData/Local/Programs/Arduino IDE/resources/app/lib/backend/resources/arduino-cli.exe"),
               r"C:\Program Files\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"] if WIN else
              ["/usr/local/bin/arduino-cli", str(HOME / "bin/arduino-cli")]):
        if Path(c).exists():
            return c
    return None


def find_avr_gcc() -> str | None:
    for a in _arduino15():
        p = _newest(str(a / "packages/arduino/tools/avr-gcc/*/bin") + "/avr-gcc" + EXE)
        if p:
            return p
    if WIN:
        p = _newest(r"C:\Program Files*\Arduino\hardware\tools\avr\bin\avr-gcc.exe")
        if p:
            return p
    return shutil.which("avr-gcc")


def find_arm_gcc() -> str | None:
    p = shutil.which("arm-none-eabi-gcc")
    if p:
        return p
    pats = ["/opt/xpack-arm-none-eabi-gcc-*/bin/arm-none-eabi-gcc", "/opt/gcc-arm-none-eabi*/bin/arm-none-eabi-gcc"]
    if WIN:
        pats = [r"C:\Program Files*\Arm GNU Toolchain arm-none-eabi\*\bin\arm-none-eabi-gcc.exe",
                r"C:\Program Files*\GNU Arm Embedded Toolchain\*\bin\arm-none-eabi-gcc.exe",
                r"C:\ST\STM32CubeIDE*\STM32CubeIDE\plugins\*gnu-tools-for-stm32*\tools\bin\arm-none-eabi-gcc.exe"]
    for pat in pats:
        p = _newest(pat)
        if p:
            return p
    return None


def esp32_core() -> str | None:
    for a in _arduino15():
        p = _newest(str(a / "packages/esp32/hardware/esp32/*"))
        if p:
            return p
    return None


def avr_core() -> str | None:
    for a in _arduino15():
        p = _newest(str(a / "packages/arduino/hardware/avr/*"))
        if p:
            return p
    return None


def status() -> list[dict]:
    """Doctor rows: what is installed for each family (read-only)."""
    cli, avr, arm, esp, ac = find_arduino_cli(), find_avr_gcc(), find_arm_gcc(), esp32_core(), avr_core()

    def row(name, path, need, key, family):
        return {"name": name, "ok": bool(path), "path": path or "", "family": family,
                "detail": path or "not found", "fix": "" if path else INSTALL[key], "need": need}
    return [row("arduino-cli", cli, "Arduino sketches (AVR .ino, ESP32)", "arduino-cli", "avr/esp32"),
            row("Arduino AVR core", ac, "arduino:avr core for .ino on Uno/Nano/Mega", "avr-gcc", "avr"),
            row("avr-gcc", avr, "AVR bare-metal C projects", "avr-gcc", "avr"),
            row("arm-none-eabi-gcc", arm, "STM32 CMSIS projects", "arm-none-eabi-gcc", "stm32"),
            row("ESP32 Arduino core", esp, "ESP32 / ESP32-C3 sketches", "esp32-core", "esp32")]


def _run(cmd, cwd, timeout=600):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout + r.stderr)[-6000:]


def compile_files(files: dict, family: str, device: str, mode: str, name: str, mcu: str = "", out_dir: str | None = None) -> dict:
    """Compile a generated family project. Returns {ok, tool, log, hex(bytes|None), size}."""
    from .buildfiles import AVR_MCU
    tmp = Path(out_dir or tempfile.mkdtemp(prefix="cl_fam_"))
    for p, t in files.items():
        f = tmp / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(t)
    if mode == "arduino":
        cli = find_arduino_cli()
        if not cli:
            return {"ok": None, "tool": "arduino-cli", "log": "arduino-cli not found. " + INSTALL["arduino-cli"], "hex": None}
        if family == "esp32" and not esp32_core():
            return {"ok": None, "tool": "arduino-cli", "log": "ESP32 core missing. " + INSTALL["esp32-core"], "hex": None}
        out = tmp / "out"
        rc, log = _run([cli, "compile", "--fqbn", FQBN[device], "--output-dir", str(out), str(tmp / name)], tmp, 900)
        hx = next(iter(sorted(out.glob("*.ino.hex")) + sorted(out.glob("*.ino.bin"))), None) if rc == 0 else None
        return {"ok": rc == 0 and hx is not None, "tool": "arduino-cli " + FQBN[device], "log": log,
                "hex": hx.read_bytes() if hx else None, "hex_name": hx.name if hx else None}
    srcs = sorted(p for p in files if p.endswith(".c"))
    incs = ["-Ihal", "-Iboard", "-Idrivers", "-Iapp"]
    if family == "avr":
        cc = find_avr_gcc()
        if not cc:
            return {"ok": None, "tool": "avr-gcc", "log": "avr-gcc not found. " + INSTALL["avr-gcc"], "hex": None}
        m = AVR_MCU[mcu]
        rc, log = _run([cc, f"-mmcu={m}", "-Os", "-std=c99", "-Wall", "-Wextra", "-ffunction-sections", "-fdata-sections",
                        "-Wl,--gc-sections", *incs, *srcs, "-o", "app.elf"], tmp)
        oc = str(Path(cc).with_name("avr-objcopy" + EXE))
    else:
        from .project import STM
        from . import stm32_data
        cc = find_arm_gcc()
        if not cc:
            return {"ok": None, "tool": "arm-none-eabi-gcc", "log": "arm-none-eabi-gcc not found. " + INSTALL["arm-none-eabi-gcc"], "hex": None}
        sd = stm32_data(mcu)
        arch = [f"-mcpu={STM[sd['series']]['cpu']}", "-mthumb", "-mfloat-abi=soft"]
        rc, log = _run([cc, *arch, f"-D{sd['define']}", "-Os", "-std=c99", "-Wall", "-Wextra", "-ffunction-sections",
                        "-fdata-sections", "-Icmsis", *incs, *srcs, f"cmsis/{sd['startup']}", f"-T{mcu.lower()}.ld",
                        "--specs=nano.specs", "--specs=nosys.specs", "-Wl,--gc-sections", "-o", "app.elf"], tmp)
        oc = str(Path(cc).with_name("arm-none-eabi-objcopy" + EXE))
    if rc == 0:
        rc2, log2 = _run([oc, "-O", "ihex", "app.elf", "app.hex"], tmp)
        log += log2
        rc = rc or rc2
    hx = tmp / "app.hex"
    size = ""
    sz = Path(oc).with_name(Path(oc).name.replace("objcopy", "size"))
    if rc == 0 and sz.exists():
        size = _run([str(sz), "app.elf"], tmp)[1]
    return {"ok": rc == 0 and hx.exists(), "tool": Path(cc).name, "log": log + size,
            "hex": hx.read_bytes() if hx.exists() else None, "hex_name": "app.hex", "warnings": log.count("warning:")}


def compile_result(res: dict, out_dir: str | None = None) -> dict:
    return compile_files(res["files"], res["family"], res["board"], res["mode"], res["name"], res["mcu"], out_dir)
