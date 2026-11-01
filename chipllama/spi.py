"""SPI master (DS39897C sec. 15/16 "SPI": SPIxSTAT p.184, SPIxCON1 p.186, SPIxCON2 p.187, Eq. 15-1 / Table 15-1 p.190).

    Fsck = Fcy / (Primary prescale * Secondary prescale)
    PPRE: 11=1:1, 10=4:1, 01=16:1, 00=64:1      SPRE: 111=1:1, 110=2:1 ... 000=8:1
    PPRE=11 together with SPRE=111 (1:1 / 1:1) is not allowed.
SPI mode (CPOL, CPHA) -> CKP = CPOL, CKE = !CPHA (CKE=1: data changes on active->idle edge).
PPS outputs (Table 10-3, p.138): SDO1=7 SCK1OUT=8 SS1OUT=9, SDO2=10 SCK2OUT=11 SS2OUT=12, SDO3=32 SCK3OUT=33 SS3OUT=34.
PPS inputs: RPINR20 SDI1R/SCK1R, RPINR21 SS1R, RPINR22 SDI2R/SCK2R, RPINR23 SS2R, RPINR28 SDI3R/SCK3R, RPINR29 SS3R.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import pinutil as pu

PPRE = {3: 1, 2: 4, 1: 16, 0: 64}
SPRE = {7 - k: k + 1 for k in range(8)}           # 7 -> 1:1 ... 0 -> 8:1
OUT_FN = {1: {"SDO": 7, "SCK": 8, "SS": 9}, 2: {"SDO": 10, "SCK": 11, "SS": 12}, 3: {"SDO": 32, "SCK": 33, "SS": 34}}
IN_REG = {1: {"SDI": ("RPINR20", "SDI1R"), "SCK": ("RPINR20", "SCK1R"), "SS": ("RPINR21", "SS1R")},
          2: {"SDI": ("RPINR22", "SDI2R"), "SCK": ("RPINR22", "SCK2R"), "SS": ("RPINR23", "SS2R")},
          3: {"SDI": ("RPINR28", "SDI3R"), "SCK": ("RPINR28", "SCK3R"), "SS": ("RPINR29", "SS3R")}}
MODE_BITS = {0: (0, 1), 1: (0, 0), 2: (1, 1), 3: (1, 0)}     # mode -> (CKP, CKE)
_WORDS = re.compile(r"\b(spi\d?|sck|mosi|miso|sdo\d?|sdi\d?)\b", re.I)


@dataclass
class SpiSpec:
    n: int = 1
    freq_hz: float = 1e6
    mode: int = 0
    bits16: bool = False
    fcy: float = 16e6
    sck_rp: int | None = None
    sdo_rp: int | None = None
    sdi_rp: int | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self):
        return {k: getattr(self, k) for k in ("n", "freq_hz", "mode", "bits16", "fcy", "sck_rp", "sdo_rp", "sdi_rp")}


def parse_request(text: str, fcy: float = 16e6, force: bool = False) -> SpiSpec | None:
    if not force and not _WORDS.search(text):
        return None
    s = SpiSpec(fcy=fcy)
    m = re.search(r"\bspi\s*([1-3])\b", text, re.I)
    if m:
        s.n = int(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(k|M)hz\b(?!\s*(?:fcy|mips|clock|fosc|cpu))", text, re.I)
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(k|M)?hz\b", text, re.I):
        after = text[m.end():m.end() + 12].lower()
        if any(w in after for w in ("fosc", "cpu", "mips", "fcy", "system")):
            continue
        f = float(m.group(1)) * {"k": 1e3, "m": 1e6, None: 1}[(m.group(2) or "").lower() or None]
        if f <= fcy:
            s.freq_hz = f
            break
    else:
        s.notes.append("no SPI clock stated: assuming 1 MHz")
    m = re.search(r"\bmode\s*([0-3])\b", text, re.I)
    if m:
        s.mode = int(m.group(1))
    s.bits16 = bool(re.search(r"\b16[- ]?bit", text))
    s.sck_rp = pu.find_pin(text, rf"SCK{s.n}?", "SCLK", "clock")
    s.sdo_rp = pu.find_pin(text, rf"SDO{s.n}?", "MOSI")
    s.sdi_rp = pu.find_pin(text, rf"SDI{s.n}?", "MISO")
    if re.search(r"\bslave\b", text, re.I):
        s.notes.append("SPI slave requested: the checker verifies master mode only")
    return s


def options(fcy: float):
    out = []
    for p, pv in PPRE.items():
        for sp, sv in SPRE.items():
            if p == 3 and sp == 7:
                continue
            out.append((fcy / (pv * sv), p, sp))
    return out


def solve(spec: SpiSpec) -> dict:
    ok = [o for o in options(spec.fcy) if o[0] <= spec.freq_hz * 1.0001]
    f, p, sp = max(ok) if ok else min(options(spec.fcy))
    ckp, cke = MODE_BITS[spec.mode]
    con1 = (int(spec.bits16) << 10) | (cke << 8) | (ckp << 6) | (1 << 5) | (sp << 2) | p
    return {"PPRE": p, "SPRE": sp, "primary": PPRE[p], "secondary": SPRE[sp], "freq": f, "CKP": ckp, "CKE": cke,
            "CON1": con1, "error_pct": (f - spec.freq_hz) / spec.freq_hz * 100}


def calc_lines(spec: SpiSpec):
    s = solve(spec)
    n = spec.n
    L = [f"SPI{n} master: target SCK {spec.freq_hz/1e3:g} kHz, mode {spec.mode}, {16 if spec.bits16 else 8}-bit, Fcy={spec.fcy/1e6:g} MHz",
         f"  Fsck = Fcy/(primary*secondary) = {spec.fcy/1e6:g} MHz/({s['primary']}*{s['secondary']}) = {s['freq']/1e3:.6g} kHz ({s['error_pct']:+.2f}%)",
         f"  SPI{n}CON1 = 0x{s['CON1']:04X}  (PPRE={s['PPRE']:02b}, SPRE={s['SPRE']:03b}, MSTEN=1, CKP={s['CKP']}, CKE={s['CKE']}"
         f"{', MODE16=1' if spec.bits16 else ''})",
         f"  SPI{n}CON2 = 0x0000 ; SPI{n}STAT = 0 then SPI{n}STATbits.SPIEN = 1 (configure CON1 before enabling)"]
    for role, rp in (("SCK", spec.sck_rp), ("SDO", spec.sdo_rp)):
        if rp is not None and rp <= 31:
            reg, f = pu.rpor_field(rp)
            L.append(f"  PPS: {reg}bits.{f} = {OUT_FN[n][role]} ({role}{n}{'OUT' if role == 'SCK' else ''}) on {pu.pin_line(rp, role)}")
    if spec.sdi_rp is not None:
        reg, f = IN_REG[n]["SDI"]
        L.append(f"  PPS: {reg}bits.{f} = {spec.sdi_rp} (SDI{n} from {pu.pin_line(spec.sdi_rp, 'SDI')})")
    L += [f"  note: {x}" for x in spec.notes]
    return L, dict(s, spec=spec.to_dict())


def check(code: str, spec: SpiSpec, rep, emu) -> None:
    n = spec.n
    st, c1 = f"SPI{n}STAT", f"SPI{n}CON1"
    g = emu.get
    rep.registers[c1] = emu.regs.get(c1, 0)
    if g(st, "SPIEN") != 1:
        rep.add("error", "SPI_EN", f"{st}bits.SPIEN is not set: SPI{n} is off.", 1, 0)
    if g(c1, "MSTEN") != 1:
        rep.add("error", "SPI_MSTEN", f"{c1}bits.MSTEN = 0 makes SPI{n} a slave; set MSTEN = 1 for master (p.186).", 1, 0)
    p, sp = g(c1, "PPRE"), g(c1, "SPRE")
    if p == 3 and sp == 7:
        rep.add("error", "SPI_PRESCALE", "PPRE=11 with SPRE=111 (1:1 / 1:1) is not allowed (p.186).", "other", "1:1/1:1")
    else:
        f = spec.fcy / (PPRE[p] * SPRE[sp])
        best = solve(spec)
        if f > spec.freq_hz * 1.01:
            rep.add("error", "SPI_FREQ", f"SCK = Fcy/({PPRE[p]}*{SPRE[sp]}) = {f/1e3:.6g} kHz is faster than the "
                    f"requested {spec.freq_hz/1e3:g} kHz (Eq. 15-1, p.190). Use PPRE={best['PPRE']:02b}, SPRE={best['SPRE']:03b}.",
                    f"{best['freq']/1e3:.6g} kHz", f"{f/1e3:.6g} kHz")
        elif f < best["freq"] * 0.99:
            rep.add("warning", "SPI_FREQ_SLOW", f"SCK = {f/1e3:.6g} kHz; {best['freq']/1e3:.6g} kHz is reachable "
                    f"(PPRE={best['PPRE']:02b}, SPRE={best['SPRE']:03b}).")
        else:
            rep.add("info", "SPI_SCK", f"SCK = {f/1e3:.6g} kHz")
    ckp, cke = MODE_BITS[spec.mode]
    if (g(c1, "CKP"), g(c1, "CKE")) != (ckp, cke):
        rep.add("error", "SPI_MODE", f"SPI mode {spec.mode} needs CKP={ckp}, CKE={cke}; code has CKP={g(c1, 'CKP')}, "
                f"CKE={g(c1, 'CKE')} (CKE is the inverse of CPHA).", f"{ckp}/{cke}", f"{g(c1, 'CKP')}/{g(c1, 'CKE')}")
    if g(c1, "MODE16") != int(spec.bits16):
        rep.add("error", "SPI_WIDTH", f"{c1}bits.MODE16 = {g(c1, 'MODE16')}, request is {16 if spec.bits16 else 8}-bit.",
                int(spec.bits16), g(c1, "MODE16"))
    on = pu.first_event(emu, st, 15)
    late = [i for i, e in enumerate(emu.events) if e.target.split(".")[0] == c1 and on is not None and i > on]
    if late:
        rep.add("warning", "SPI_ORDER", f"{c1} is changed after SPIEN=1; configure SPI{n}CON1 while the module is off.")
    if g(c1, "DISSCK") or g(c1, "DISSDO"):
        rep.add("warning", "SPI_DIS", f"{c1}: DISSCK/DISSDO disables the SCK/SDO pin function.")
    sck = pu.check_output(emu, rep, OUT_FN[n]["SCK"], spec.sck_rp, f"SCK{n}OUT", "SPI_PPS_SCK")
    sdo = pu.check_output(emu, rep, OUT_FN[n]["SDO"], spec.sdo_rp, f"SDO{n}", "SPI_PPS_SDO")
    reg, fld = IN_REG[n]["SDI"]
    sdi = pu.check_input(emu, rep, reg, fld, spec.sdi_rp, f"SDI{n}", "SPI_PPS_SDI", required=spec.sdi_rp is not None)
    pu.tris_check(emu, rep, sck, 0, f"SCK{n}", "SPI_TRIS")
    pu.tris_check(emu, rep, sdo, 0, f"SDO{n}", "SPI_TRIS")
    pu.tris_check(emu, rep, sdi, 1, f"SDI{n}", "SPI_TRIS", sev="error")
    pu.pps_lock_warn(emu, rep, code)
    from .irq import vector, check_vector
    v = vector(f"SPI{n}Interrupt")
    if g(*v.iec):
        check_vector(emu, rep, v)


def prompt_text(spec: SpiSpec) -> str:
    lines, _ = calc_lines(spec)
    n = spec.n
    return (f"SPI facts (DS39897C; verified later): SPI{n}STAT p.184 (SPIEN<15>, SPIROV<6>, SPITBF<1>, SPIRBF<0>); "
            f"SPI{n}CON1 p.186 (DISSCK<12>, DISSDO<11>, MODE16<10>, SMP<9>, CKE<8>, SSEN<7>, CKP<6>, MSTEN<5>, SPRE<4:2>, PPRE<1:0>); "
            f"SPI{n}CON2 p.187 (FRMEN<15>, SPIBEN<0>). Fsck = Fcy/(primary*secondary) (Eq. 15-1, p.190). "
            "Mode0: CKP=0,CKE=1; Mode1: CKP=0,CKE=0; Mode2: CKP=1,CKE=1; Mode3: CKP=1,CKE=0. "
            f"Transfer: write SPI{n}BUF, wait for SPI{n}STATbits.SPIRBF, read SPI{n}BUF (always read to avoid SPIROV). "
            f"PPS: SDO{n}={OUT_FN[n]['SDO']}, SCK{n}OUT={OUT_FN[n]['SCK']}, SS{n}OUT={OUT_FN[n]['SS']}; "
            f"SDI{n} input {IN_REG[n]['SDI'][0]}bits.{IN_REG[n]['SDI'][1]}.\n" + "\n".join(lines))


def pages(spec: SpiSpec) -> list[int]:
    return [181, 184, 186, 187, 190, 138]


FINDING_PAGES = {"SPI_EN": [184], "SPI_MSTEN": [186], "SPI_PRESCALE": [186, 190], "SPI_FREQ": [190], "SPI_FREQ_SLOW": [190],
                 "SPI_MODE": [186], "SPI_WIDTH": [186], "SPI_ORDER": [186], "SPI_DIS": [186], "SPI_PPS_SCK": [138],
                 "SPI_PPS_SDO": [138], "SPI_PPS_SDI": [148], "SPI_TRIS": [133], "ANALOG_PIN": [273]}
