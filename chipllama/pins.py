"""Pin planner for the PIC24FJ256GB110 (100-pin): PPS (DS39897C sec. 10.4, p.136-147), fixed I2C pins,
ANx = RBx (p.273), JTAG (RA0/1/4/5) and ICS pairs.  Deterministic: produces conflicts + an init snippet."""
from __future__ import annotations

import re

from .uart import RP_PIN, PIN_RP, OUTPUT_RP_MAX, UART_RX, UART_TX_FN, rpor_field
from .spi import OUT_FN as SPI_OUT, IN_REG as SPI_IN
from .i2c import PINS as I2C_PINS

OC_FN = {n: 17 + n for n in range(1, 10)}          # Table 10-3: OC1 = 18 ... OC9 = 26
JTAG = {"RA0": "TMS", "RA1": "TCK", "RA4": "TDI", "RA5": "TDO"}
ICS = {"PGx1": ("RB1", "RB0"), "PGx2": ("RB6", "RB7"), "PGx3": ("RB5", "RB4")}   # (PGEC, PGED), DS39897C p.7 + p.20
_PIN = re.compile(r"^R[A-G]\d{1,2}$")


def functions() -> list[dict]:
    out = []
    for n in UART_TX_FN:
        out += [{"fn": f"U{n}TX", "kind": "pps_out"}, {"fn": f"U{n}RX", "kind": "pps_in"}]
    for n in SPI_OUT:
        out += [{"fn": f"SCK{n}OUT", "kind": "pps_out"}, {"fn": f"SDO{n}", "kind": "pps_out"},
                {"fn": f"SDI{n}", "kind": "pps_in"}]
    out += [{"fn": f"OC{n}", "kind": "pps_out"} for n in OC_FN]
    for n, (scl, sda, *_r) in I2C_PINS.items():
        out += [{"fn": f"SCL{n}", "kind": "fixed", "pin": scl}, {"fn": f"SDA{n}", "kind": "fixed", "pin": sda}]
    out += [{"fn": f"AN{k}", "kind": "fixed", "pin": f"RB{k}"} for k in range(16)]
    out += [{"fn": "GPIO_OUT", "kind": "gpio"}, {"fn": "GPIO_IN", "kind": "gpio"}]
    return out


def _norm(pin: str) -> str | None:
    p = (pin or "").strip().upper()
    m = re.fullmatch(r"RPI?(\d{1,2})", p)
    if m:
        return RP_PIN.get(int(m.group(1)))
    return p if _PIN.match(p) else None


def plan(items: list[dict], ics: str = "PGx2", jtag: bool = False) -> dict:
    """items: [{"fn": "U1TX", "pin": "RF4"|"RP10"}, ...] -> {rows, issues, code, ok}"""
    fmap = {f["fn"]: f for f in functions()}
    rows, issues, used = [], [], {}
    pps_out, pps_in, ana, tris_out, tris_in = [], [], [], [], []

    def add(sev, msg, fn=None):
        issues.append({"severity": sev, "fn": fn, "msg": msg})

    for it in items:
        fn = (it.get("fn") or "").upper()
        f = fmap.get(fn)
        raw = it.get("pin") or (f or {}).get("pin") or ""
        pin = _norm(raw)
        if not f:
            add("error", f"unknown function {fn!r}", fn); continue
        if not pin:
            add("error", f"{fn}: {raw!r} is not a pin of this device (use RB5 or RP18 form)", fn); continue
        rp = PIN_RP.get(pin)
        if f["kind"] == "fixed" and pin != f["pin"]:
            add("error", f"{fn} is not remappable: it is fixed on {f['pin']} (p.18-20/273)", fn); pin = f["pin"]
        if f["kind"] in ("pps_out", "pps_in") and rp is None:
            add("error", f"{fn} needs a remappable pin; {pin} has no RPn/RPIn (p.136)", fn)
        elif f["kind"] == "pps_out" and rp > OUTPUT_RP_MAX:
            add("error", f"{fn} is an output but {pin} is RPI{rp} (input-only, p.136)", fn)
        if pin in used:
            add("error", f"{pin} is assigned to both {used[pin]} and {fn}", fn)
        used.setdefault(pin, fn)
        if pin in JTAG and jtag:
            add("warning", f"{pin} is JTAG {JTAG[pin]} while JTAGEN=ON: set JTAGEN=OFF (p.288)", fn)
        if pin in ICS.get(ics, ()):
            add("warning", f"{pin} is {('PGEC','PGED')[ICS[ics].index(pin)]}{ics[-1]} - shared with your PICkit; "
                           "it works but debugging may disturb it", fn)
        if pin.startswith("RB") and not fn.startswith("AN"):
            ana.append(int(pin[2:]))
        if fn.startswith("AN"):
            tris_in.append(pin)
        rows.append({"fn": fn, "pin": pin, "rp": None if rp is None else (f"RPI{rp}" if rp > OUTPUT_RP_MAX else f"RP{rp}"),
                     "kind": f["kind"]})
        if f["kind"] == "pps_out" and rp is not None and rp <= OUTPUT_RP_MAX:
            num = _out_fn(fn)
            reg, fld = rpor_field(rp)
            pps_out.append(f"    {reg}bits.{fld} = {num};   /* {fn} -> RP{rp} ({pin}) */")
            tris_out.append(pin)
        elif f["kind"] == "pps_in" and rp is not None:
            reg, fld = _in_reg(fn)
            pps_in.append(f"    {reg}bits.{fld} = {rp};   /* {fn} <- {pin} */")
            tris_in.append(pin)
        elif fn == "GPIO_OUT":
            tris_out.append(pin)
        elif fn == "GPIO_IN":
            tris_in.append(pin)
    code = ["void pins_init(void)", "{"]
    if ana:
        code.append(f"    AD1PCFGL |= 0x{sum(1 << b for b in set(ana)):04X};   /* RB{','.join(map(str, sorted(set(ana))))} digital (p.273) */")
    for p in dict.fromkeys(tris_out):
        code.append(f"    TRIS{p[1]}bits.TRIS{p[1]}{p[2:]} = 0;")
    for p in dict.fromkeys(tris_in):
        code.append(f"    TRIS{p[1]}bits.TRIS{p[1]}{p[2:]} = 1;")
    if pps_in or pps_out:
        code += ["    __builtin_write_OSCCONL(OSCCON & 0xBF);   /* unlock PPS (p.136) */", *pps_in, *pps_out,
                 "    __builtin_write_OSCCONL(OSCCON | 0x40);   /* lock PPS */"]
    code.append("}")
    return {"rows": rows, "issues": issues, "ok": not any(i["severity"] == "error" for i in issues),
            "code": "\n".join(code), "pages": [136, 138, 147, 273]}


def _out_fn(fn):
    if m := re.fullmatch(r"U(\d)TX", fn):
        return UART_TX_FN[int(m.group(1))]
    if m := re.fullmatch(r"SCK(\d)OUT", fn):
        return SPI_OUT[int(m.group(1))]["SCK"]
    if m := re.fullmatch(r"SDO(\d)", fn):
        return SPI_OUT[int(m.group(1))]["SDO"]
    return OC_FN[int(fn[2:])]


def _in_reg(fn):
    if m := re.fullmatch(r"U(\d)RX", fn):
        return UART_RX[int(m.group(1))]
    return SPI_IN[int(fn[3:])]["SDI"]


def peripherals(rows: list[dict]) -> list[str]:
    out = set()
    for r in rows:
        fn = r["fn"]
        out.add("uart" if re.fullmatch(r"U\d[TR]X", fn) else "spi" if fn[:3] in ("SCK", "SDO", "SDI")
                else "i2c" if fn[:3] in ("SCL", "SDA") else "adc" if fn.startswith("AN")
                else "oc" if fn.startswith("OC") else "gpio")
    return sorted(out)
