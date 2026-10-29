"""UART1 + Peripheral Pin Select knowledge for the PIC24FJ256GB110 family (DS39897C).

Facts (datasheet page numbers):
- Baud rate (Equation 17-1, p.200): BRGH=0 -> UxBRG = Fcy/(16*baud) - 1 ; BRGH=1 -> UxBRG = Fcy/(4*baud) - 1.
- UxMODE.UARTEN<15>, UxMODE.BRGH<3> (p.202); UxSTA.UTXEN<10> must be set AFTER UARTEN (p.204).
- PPS (section 10.4): U1RX input -> RPINR18.U1RXR<5:0> = RPn/RPIn number (p.147); outputs: RPORx.RPnR<5:0> =
  function number, U1TX = 3 (Table 10-3, p.138). PPS writes need OSCCON.IOLOCK=0: unlock with
  __builtin_write_OSCCONL(OSCCON & 0xBF), lock with __builtin_write_OSCCONL(OSCCON | 0x40) (p.123).
- RPn/RPIn <-> port pin map: pinout tables 1-4 (100-pin TQFP names, e.g. SDA2/RP10/PMA9/CN17/RF4).
- RBn pins are also ANn: AD1PCFGL.PCFGn = 1 makes them digital (p.273).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict, field

U1TX_FN = 3          # Table 10-3 output function number
U1RTS_FN = 4
PPS_INPUT_OFF = 0x3F

# RPn (bidirectional) and RPIn (input only) -> port pin, from DS39897C pin names
RP_PIN = {0: "RB0", 1: "RB1", 2: "RD8", 3: "RD10", 4: "RD9", 5: "RD15", 6: "RB6", 7: "RB7", 8: "RB8", 9: "RB9",
          10: "RF4", 11: "RD0", 12: "RD11", 13: "RB2", 14: "RB14", 15: "RF8", 16: "RF3", 17: "RF5", 18: "RB5",
          19: "RG8", 20: "RD5", 21: "RG6", 22: "RD3", 23: "RD2", 24: "RD1", 25: "RD4", 26: "RG7", 27: "RG9",
          28: "RB4", 29: "RB15", 30: "RF2", 31: "RF13",
          32: "RF12", 33: "RE8", 34: "RE9", 35: "RA15", 36: "RA14", 37: "RC14", 38: "RC1", 39: "RC2", 40: "RC3",
          41: "RC4", 42: "RD12", 43: "RD14"}
PIN_RP = {v: k for k, v in RP_PIN.items()}
OUTPUT_RP_MAX = 31          # RP32..RP43 are RPIn (input only)
STD_BAUDS = (300, 1200, 2400, 4800, 9600, 14400, 19200, 28800, 38400, 57600, 76800, 115200, 230400, 250000,
             460800, 500000, 921600, 1000000)
UART_PAGES = [200, 202, 204, 136, 138, 147]


def rp_name(n: int) -> str:
    return f"RPI{n}" if n > OUTPUT_RP_MAX else f"RP{n}"


def port_pin(rp: int) -> str | None:
    return RP_PIN.get(rp)


def analog_channel(pin: str | None) -> int | None:
    """RBn doubles as ANn on this family."""
    if pin and pin.startswith("RB"):
        return int(pin[2:])
    return None


def rpor_field(rp: int) -> tuple[str, str]:
    return f"RPOR{rp // 2}", f"RP{rp}R"


@dataclass
class BaudSolution:
    brgh: int
    brg: int
    actual: float
    error_pct: float

    def to_dict(self):
        return asdict(self)


def baud_from_brg(fcy: float, brg: int, brgh: int) -> float:
    return fcy / ((4 if brgh else 16) * (brg + 1))


def baud_options(fcy: float, baud: float) -> list[BaudSolution]:
    out = []
    for brgh in (0, 1):
        div = 4 if brgh else 16
        brg = max(0, min(0xFFFF, round(fcy / (div * baud)) - 1))
        act = baud_from_brg(fcy, brg, brgh)
        out.append(BaudSolution(brgh, brg, act, (act - baud) / baud * 100))
    return out


def solve_baud(fcy: float, baud: float) -> BaudSolution:
    """Lowest |error|; ties go to BRGH=0 (more samples per bit)."""
    return min(baud_options(fcy, baud), key=lambda s: (round(abs(s.error_pct), 6), s.brgh))


@dataclass
class UartSpec:
    baud: int = 9600
    uart: int = 1
    tx_rp: int | None = None
    rx_rp: int | None = None
    fcy: float = 16_000_000.0
    tolerance_pct: float = 2.0
    notes: list[str] = field(default_factory=list)

    def solution(self) -> BaudSolution:
        return solve_baud(self.fcy, self.baud)

    def to_dict(self):
        d = asdict(self)
        d["solution"] = self.solution().to_dict()
        d["tx_pin"] = port_pin(self.tx_rp) if self.tx_rp is not None else None
        d["rx_pin"] = port_pin(self.rx_rp) if self.rx_rp is not None else None
        return d


_PIN = r"(RPI?\d{1,2}|R[A-G]\d{1,2})"


def _pin_to_rp(tok: str) -> int | None:
    tok = tok.upper()
    m = re.fullmatch(r"RPI?(\d+)", tok)
    if m:
        return int(m.group(1))
    return PIN_RP.get(tok)


def _find_pin(text: str, role: str) -> int | None:
    r = role
    pats = [rf"\b(?:U1)?{r}\b\s*(?:pin\s*)?(?:on|=|:|->|to|at|@)?\s*(?:pin\s*)?{_PIN}\b",
            rf"\b{_PIN}\s*(?:\(|as|for|=|:)?\s*(?:U1)?{r}\b"]
    for p in pats:
        m = re.search(p, text, re.I)
        if m:
            return _pin_to_rp(m.group(1))
    return None


def parse_uart_request(text: str, fcy: float = 16e6) -> UartSpec | None:
    if not re.search(r"\b(uart\d?|baud|serial|bps|printf|tx|rx)\b", text, re.I):
        return None
    if not re.search(r"\b(uart\d?|baud|serial|bps)\b", text, re.I):
        return None
    spec = UartSpec(fcy=fcy)
    m = re.search(r"\buart\s*([1-4])\b|\bU([1-4])(?:TX|RX|MODE|STA|BRG)", text, re.I)
    if m:
        spec.uart = int(m.group(1) or m.group(2))
    nums = [int(x.replace(",", "").replace(".", "")) for x in re.findall(r"(?<![\w.])(\d{1,3}(?:[,.]\d{3})+|\d{3,7})(?![\w.])", text)]
    mk = re.search(r"(\d+(?:\.\d+)?)\s*k\s*(?:baud|bps)", text, re.I)
    cand = [n for n in nums if n in STD_BAUDS]
    if mk:
        spec.baud = int(float(mk.group(1)) * 1000)
    elif re.search(r"(\d{3,7})\s*(?:baud|bps|bd)\b", text, re.I):
        spec.baud = int(re.search(r"(\d{3,7})\s*(?:baud|bps|bd)\b", text, re.I).group(1))
    elif cand:
        spec.baud = cand[0]
    else:
        spec.notes.append("no baud rate stated: assuming 9600")
    spec.tx_rp = _find_pin(text, "TX")
    spec.rx_rp = _find_pin(text, "RX")
    if spec.uart != 1:
        spec.notes.append(f"UART{spec.uart} requested: the checker verifies UART1 registers only")
    if spec.tx_rp is not None and spec.tx_rp > OUTPUT_RP_MAX:
        spec.notes.append(f"{rp_name(spec.tx_rp)} is input-only: it cannot carry U1TX")
    return spec


# ---------------------------------------------------------------- checker
def check_uart(code: str, uspec: UartSpec, rep, emu=None):
    """Append UART/PPS findings to a checker.CheckReport (rep.add(sev, code, msg, expected, actual))."""
    from .cemu import emulate
    emu = emu or emulate(code)
    regs = emu.regs
    get = emu.get
    sol = uspec.solution()
    wrote = {e.target.split(".")[0] for e in emu.events}
    for r in ("U1MODE", "U1STA", "U1BRG", "RPINR18"):
        rep.registers[r] = regs.get(r, 0)
    rep.expected["U1BRG"] = sol.brg

    # --- baud
    brgh = get("U1MODE", "BRGH")
    brg = regs.get("U1BRG", 0)
    act = baud_from_brg(uspec.fcy, brg, brgh)
    err = (act - uspec.baud) / uspec.baud * 100
    if "U1BRG" not in wrote:
        rep.add("error", "BRG_UNSET", "U1BRG is never written (resets to 0 -> baud = Fcy/16 or Fcy/4).",
                sol.brg, brg)
    elif abs(err) > uspec.tolerance_pct:
        rep.add("error", "BRG",
                f"U1BRG={brg} with BRGH={brgh} gives {act:.0f} baud at Fcy={uspec.fcy/1e6:g} MHz ({err:+.2f}% vs "
                f"{uspec.baud}). Formula (p.200): BRGH=0 -> U1BRG=Fcy/(16*baud)-1, BRGH=1 -> Fcy/(4*baud)-1. "
                f"Best: BRGH={sol.brgh}, U1BRG={sol.brg} ({sol.error_pct:+.2f}%).",
                f"{uspec.baud} baud", f"{act:.0f} baud")
    else:
        rep.add("info", "BAUD", f"U1BRG={brg}, BRGH={brgh} -> {act:.0f} baud ({err:+.2f}%)")
    # --- enable bits + ordering (UTXEN must be set after UARTEN, p.204)
    if get("U1MODE", "UARTEN") != 1:
        rep.add("error", "UARTEN", "U1MODE.UARTEN is not set: UART1 is off.", 1, get("U1MODE", "UARTEN"))
    if get("U1STA", "UTXEN") != 1:
        rep.add("error", "UTXEN", "U1STA.UTXEN is not set: the transmitter never drives U1TX.", 1, get("U1STA", "UTXEN"))
    on_idx = next((i for i, e in enumerate(emu.events) if e.target.startswith("U1MODE") and (e.after >> 15) & 1), None)
    tx_idx = next((i for i, e in enumerate(emu.events) if e.target.startswith("U1STA") and (e.after >> 10) & 1), None)
    if on_idx is not None and tx_idx is not None and tx_idx < on_idx:
        rep.add("warning", "UTXEN_ORDER", "U1STA.UTXEN is set before U1MODE.UARTEN; the datasheet requires UARTEN first.")
    if get("U1MODE", "UEN") not in (0,):
        rep.add("warning", "UEN", "U1MODE.UEN != 0: RTS/CTS pins are used although flow control was not requested.")

    # --- PPS: replay events to know IOLOCK at each PPS write
    iolock = 0
    unlock_seen = lock_seen = False
    pps_writes = 0
    for e in emu.events:
        reg = e.target.split(".")[0]
        if reg == "OSCCON":
            new = (e.after >> 6) & 1
            if iolock and not new:
                unlock_seen = True
            if new and not iolock:
                lock_seen = True
            if not new:
                unlock_seen = True
            iolock = new
        elif reg == "RPINR18" or reg.startswith("RPOR"):
            pps_writes += 1
            if iolock:
                rep.add("error", "PPS_LOCKED", f"'{e.text}' runs while OSCCON.IOLOCK=1: the PPS write is ignored. "
                        "Unlock first with __builtin_write_OSCCONL(OSCCON & 0xBF).")
    if pps_writes and not unlock_seen:
        rep.add("warning", "PPS_UNLOCK", "PPS registers are written without the unlock sequence "
                "__builtin_write_OSCCONL(OSCCON & 0xBF) (works only because IOLOCK resets to 0).")
    if pps_writes and not lock_seen:
        rep.add("info", "PPS_LOCK", "PPS is not locked afterwards (__builtin_write_OSCCONL(OSCCON | 0x40)).")

    # --- TX mapping
    tx_map = [rp for rp in range(OUTPUT_RP_MAX + 1) if get(*rpor_field(rp)) == U1TX_FN]
    if uspec.tx_rp is not None:
        reg, f = rpor_field(uspec.tx_rp) if uspec.tx_rp <= OUTPUT_RP_MAX else (None, None)
        if reg is None:
            rep.add("error", "PPS_TX", f"{rp_name(uspec.tx_rp)} is input-only; U1TX needs an RPn output pin.")
        else:
            rep.registers[reg] = regs.get(reg, 0)
            v = get(reg, f)
            if v != U1TX_FN:
                rep.add("error", "PPS_TX", f"{reg}.{f} = {v}, but U1TX is output function 3 (Table 10-3, p.138): "
                        f"set {reg}bits.{f} = 3 to put U1TX on RP{uspec.tx_rp}/{port_pin(uspec.tx_rp)}.", 3, v)
        others = [rp for rp in tx_map if rp != uspec.tx_rp]
        if others:
            rep.add("warning", "PPS_TX_EXTRA", f"U1TX is also mapped to RP{others[0]}.")
    elif not tx_map:
        rep.add("error", "PPS_TX", "U1TX is not mapped to any RPn pin (RPORx.RPnR = 3).")
    tx_rp = uspec.tx_rp if uspec.tx_rp is not None else (tx_map[0] if tx_map else None)

    # --- RX mapping
    rxr = get("RPINR18", "U1RXR")
    if uspec.rx_rp is not None:
        if rxr != uspec.rx_rp:
            rep.add("error", "PPS_RX", f"RPINR18.U1RXR = {rxr}, expected {uspec.rx_rp} to read U1RX from "
                    f"{rp_name(uspec.rx_rp)}/{port_pin(uspec.rx_rp)} (p.147).", uspec.rx_rp, rxr)
    rx_rp = uspec.rx_rp if uspec.rx_rp is not None else (rxr if rxr != PPS_INPUT_OFF else None)

    # --- port direction + analog
    adpcfg = regs.get("AD1PCFGL", 0)
    for role, rp in (("TX", tx_rp), ("RX", rx_rp)):
        pin = port_pin(rp) if rp is not None else None
        if not pin:
            continue
        port, bit = pin[1], int(pin[2:])
        tris = (regs.get(f"TRIS{port}", 0xFFFF) >> bit) & 1
        if role == "TX" and tris != 0:
            rep.add("warning", "TRIS_TX", f"TRIS{port}bits.TRIS{port}{bit} = 1: make the U1TX pin {pin} an output "
                    f"(TRIS{port}bits.TRIS{port}{bit} = 0) so it idles high before the UART is enabled.", 0, tris)
        if role == "RX" and tris != 1:
            rep.add("error", "TRIS_RX", f"TRIS{port}bits.TRIS{port}{bit} = 0 drives the U1RX pin {pin} as an output.", 1, tris)
        an = analog_channel(pin)
        if an is not None and not (adpcfg >> an) & 1:
            rep.registers["AD1PCFGL"] = adpcfg
            rep.add("error" if role == "RX" else "warning", f"ANALOG_{role}",
                    f"{pin} is also AN{an} and resets ANALOG: set AD1PCFGLbits.PCFG{an} = 1 (p.273) for digital {role}.",
                    1, 0)
    return rep


def check_uart_full(code: str, uspec: UartSpec, *, profile=None, ics: str = "PGx2", rep=None, require_config=True):
    """UART-only request: UART/PPS checks + configuration bits / clock tree check."""
    from .checker import CheckReport
    from .cemu import emulate
    from . import board as boardmod
    rep = rep or CheckReport()
    emu = emulate(code)
    for w in emu.warnings:
        rep.add("warning", "UNPARSED", w)
    check_uart(code, uspec, rep, emu)
    if require_config:
        prof = profile or boardmod.get_profile("frcpll")
        boardmod.check_config(code, uspec.fcy * 2, emu.get("CLKDIV", "RCDIV"), emu.get("CLKDIV", "CPDIV"),
                              prof, ics, rep.add)
    return rep


def pages_for_uart(uspec: UartSpec) -> list[int]:
    from . import chipdb
    pages = list(UART_PAGES)
    if uspec.tx_rp is not None and uspec.tx_rp <= OUTPUT_RP_MAX:
        pages.append(chipdb.REGISTERS[rpor_field(uspec.tx_rp)[0]].page)
    return list(dict.fromkeys(pages))


def uart_prompt_text(uspec: UartSpec) -> str:
    sol = uspec.solution()
    opts = baud_options(uspec.fcy, uspec.baud)
    s = [f"UART parameters (verified later): UART1, {uspec.baud} baud 8N1, Fcy = {uspec.fcy/1e6:g} MHz.",
         "Baud math (DS39897C Eq. 17-1, p.200): BRGH=0 -> U1BRG = Fcy/(16*baud) - 1 ; BRGH=1 -> U1BRG = Fcy/(4*baud) - 1."]
    for o in opts:
        s.append(f"  BRGH={o.brgh}: U1BRG={o.brg} -> {o.actual:.0f} baud ({o.error_pct:+.2f}%)")
    s.append(f"  Use BRGH={sol.brgh}, U1BRG={sol.brg} (lowest error). Compute it with a constant expression in the code.")
    s += ["Register facts (from DS39897C):",
          "- U1MODE @0x0220 (p.202): UARTEN<15>, UEN<9:8>, BRGH<3>, PDSEL<2:1>, STSEL<0>. U1STA @0x0222 (p.204): UTXEN<10>, UTXBF<9>, TRMT<8>, URXDA<0>. U1BRG @0x0228.",
          "- Set U1MODEbits.UARTEN = 1 BEFORE U1STAbits.UTXEN = 1. Transmit: wait while U1STAbits.UTXBF, then write U1TXREG.",
          "- Peripheral Pin Select: unlock with __builtin_write_OSCCONL(OSCCON & 0xBF); map; lock with __builtin_write_OSCCONL(OSCCON | 0x40).",
          "- U1TX output function number = 3 (Table 10-3, p.138): RPORxbits.RPnR = 3. U1RX input: RPINR18bits.U1RXR = n (p.147)."]
    if uspec.tx_rp is not None:
        reg, f = rpor_field(uspec.tx_rp)
        pin = port_pin(uspec.tx_rp)
        s.append(f"- TX on RP{uspec.tx_rp} = pin {pin}: {reg}bits.{f} = 3; TRIS{pin[1]}bits.TRIS{pin[1]}{pin[2:]} = 0."
                 + (f" {pin} is also AN{analog_channel(pin)}: AD1PCFGLbits.PCFG{analog_channel(pin)} = 1." if analog_channel(pin) is not None else ""))
    if uspec.rx_rp is not None:
        pin = port_pin(uspec.rx_rp)
        s.append(f"- RX on {rp_name(uspec.rx_rp)} = pin {pin}: RPINR18bits.U1RXR = {uspec.rx_rp}; TRIS{pin[1]}bits.TRIS{pin[1]}{pin[2:]} = 1."
                 + (f" {pin} is also AN{analog_channel(pin)}: AD1PCFGLbits.PCFG{analog_channel(pin)} = 1." if analog_channel(pin) is not None else ""))
    s.append("- For delays use a hardware timer or `#define FCY 16000000UL` before `#include <libpic30.h>` and __delay_ms().")
    return "\n".join(s)
