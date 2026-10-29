"""Register database for PIC24FJ256GB110 (DS39897C).

Every register/bit position below was read from the datasheet text (page numbers
are DS39897C page numbers, which equal the PDF page index for this document).
Registers not listed here are simply unknown to the checker.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEVICE = "PIC24FJ256GB110"
DATASHEET = "DS39897C"


@dataclass(frozen=True)
class Field:
    name: str
    lsb: int
    width: int = 1
    desc: str = ""

    @property
    def mask(self) -> int:
        return ((1 << self.width) - 1) << self.lsb


@dataclass(frozen=True)
class Register:
    name: str
    address: int
    page: int  # datasheet page of the bit-level register description
    fields: tuple[Field, ...]
    reset: int = 0
    note: str = ""

    def field(self, name: str) -> Field | None:
        for f in self.fields:
            if f.name.upper() == name.upper():
                return f
        return None

    @property
    def implemented_mask(self) -> int:
        m = 0
        for f in self.fields:
            m |= f.mask
        return m

    def decode(self, value: int) -> dict[str, int]:
        return {f.name: (value & f.mask) >> f.lsb for f in self.fields}


def _tcon(name: str, addr: int, page: int, t32: bool, tsync: bool) -> Register:
    fields = [
        Field("TON", 15, 1, "Timer on"),
        Field("TSIDL", 13, 1, "Stop in Idle mode"),
        Field("TGATE", 6, 1, "Gated time accumulation (only effective when TCS=0)"),
        Field("TCKPS", 4, 2, "Prescale: 0=1:1 1=1:8 2=1:64 3=1:256"),
        Field("TCS", 1, 1, "Clock source: 0=internal FOSC/2, 1=external T1CK/TxCK pin"),
    ]
    if t32:
        fields.append(Field("T32", 3, 1, "32-bit mode (Timer2/3 or Timer4/5 pair)"))
    if tsync:
        fields.append(Field("TSYNC", 2, 1, "External clock synchronisation (only when TCS=1)"))
    return Register(name, addr, page, tuple(fields), reset=0)


def _irq(name: str, addr: int, page: int, bits: dict[str, int]) -> Register:
    return Register(name, addr, page, tuple(Field(n, b, 1) for n, b in bits.items()), reset=0)


REGISTERS: dict[str, Register] = {}


def _add(r: Register) -> None:
    REGISTERS[r.name] = r


_add(_tcon("T1CON", 0x0104, 162, t32=False, tsync=True))
_add(_tcon("T2CON", 0x0110, 166, t32=True, tsync=False))
_add(_tcon("T3CON", 0x0112, 167, t32=False, tsync=False))
# IFS0 / IEC0: same bit layout (datasheet SFR map, p. 84 / p. 90)
_irq_bits = {"INT0": 0, "IC1": 1, "OC1": 2, "T1": 3, "IC2": 5, "OC2": 6, "T2": 7, "T3": 8,
             "SPF1": 9, "SPI1": 10, "U1RX": 11, "U1TX": 12, "AD1": 13}
_add(_irq("IFS0", 0x0084, 84, {k + "IF": v for k, v in _irq_bits.items()}))
_add(_irq("IEC0", 0x0094, 90, {k + "IE": v for k, v in _irq_bits.items()}))
_add(Register("IPC0", 0x00A4, 97, (Field("T1IP", 12, 3, "Timer1 interrupt priority (0 = disabled)"),
                                    Field("OC1IP", 8, 3), Field("IC1IP", 4, 3), Field("INT0IP", 0, 3)),
              reset=0x4444))
_add(Register("IPC1", 0x00A6, 98, (Field("T2IP", 12, 3, "Timer2 interrupt priority (0 = disabled)"),
                                    Field("OC2IP", 8, 3), Field("IC2IP", 4, 3)), reset=0x4440))
_add(Register("IPC2", 0x00A8, 99, (Field("U1RXIP", 12, 3), Field("SPI1IP", 8, 3), Field("SPF1IP", 4, 3),
                                    Field("T3IP", 0, 3, "Timer3 interrupt priority (0 = disabled)")),
              reset=0x4444))

# CLKDIV (Register 8-2, p.125): POR value 0x0100 -> RCDIV=001 (FRC/2 = 4 MHz), CPDIV=00 (32 MHz)
_add(Register("CLKDIV", 0x0744, 125, (Field("ROI", 15, 1, "Recover on interrupt"),
                                       Field("DOZE", 12, 3, "CPU:peripheral clock ratio"),
                                       Field("DOZEN", 11, 1, "DOZE enable"),
                                       Field("RCDIV", 8, 3, "FRC postscaler: 0=8MHz 1=4MHz 2=2MHz ... 7=31.25kHz"),
                                       Field("CPDIV", 6, 2, "32 MHz branch postscaler: 0=32 1=16 2=8 3=4 MHz")),
              reset=0x0100))


# ---- UART1 (chapter 17: UxMODE p.202, UxSTA p.204, BRG equation p.200)
_add(Register("U1MODE", 0x0220, 202, (Field("UARTEN", 15), Field("USIDL", 13), Field("IREN", 12), Field("RTSMD", 11),
                                       Field("UEN", 8, 2), Field("WAKE", 7), Field("LPBACK", 6), Field("ABAUD", 5),
                                       Field("RXINV", 4), Field("BRGH", 3, 1, "1 = high speed (4x), 0 = standard (16x)"),
                                       Field("PDSEL", 1, 2), Field("STSEL", 0)), reset=0))
_add(Register("U1STA", 0x0222, 204, (Field("UTXISEL1", 15), Field("UTXINV", 14), Field("UTXISEL0", 13),
                                      Field("UTXBRK", 11), Field("UTXEN", 10), Field("UTXBF", 9), Field("TRMT", 8),
                                      Field("URXISEL", 6, 2), Field("ADDEN", 5), Field("RIDLE", 4), Field("PERR", 3),
                                      Field("FERR", 2), Field("OERR", 1), Field("URXDA", 0)), reset=0x0110))
# ---- Peripheral Pin Select (section 10.4; RPINR18 p.147, RPORn pp.151-159, output function table 10-3 p.138)
_add(Register("RPINR18", 0x06A4, 147, (Field("U1CTSR", 8, 6), Field("U1RXR", 0, 6)), reset=0x3F3F))
_RPOR_PAGES = [151, 152, 152, 153, 153, 154, 154, 155, 155, 156, 156, 157, 157, 158, 158, 159]
for _n in range(16):
    _add(Register(f"RPOR{_n}", 0x06C0 + 2 * _n, _RPOR_PAGES[_n],
                  (Field(f"RP{2*_n+1}R", 8, 6), Field(f"RP{2*_n}R", 0, 6)), reset=0))
# ---- OSCCON (p.123): IOLOCK<6> gates PPS writes (unlock/lock with __builtin_write_OSCCONL)
_add(Register("OSCCON", 0x0742, 123, (Field("COSC", 12, 3), Field("NOSC", 8, 3), Field("CLKLOCK", 7),
                                       Field("IOLOCK", 6), Field("LOCK", 5), Field("CF", 3), Field("POSCEN", 2),
                                       Field("SOSCEN", 1), Field("OSWEN", 0)), reset=0))
# ---- A/D port configuration (p.273): PCFGn = 1 -> ANn pin is DIGITAL; reset 0 = analog
_add(Register("AD1PCFGL", 0x032C, 273, tuple(Field(f"PCFG{i}", i) for i in range(16)), reset=0))
# ---- TRIS registers (1 = input, reset all inputs)
for _p, _a in zip("ABCDEFG", (0x02C0, 0x02C8, 0x02D0, 0x02D8, 0x02E0, 0x02E8, 0x02F0)):
    _add(Register(f"TRIS{_p}", _a, 0, tuple(Field(f"TRIS{_p}{i}", i) for i in range(16)), reset=0xFFFF))
REG_ALIASES = {"AD1PCFG": "AD1PCFGL", "AD1CHS0": "AD1CHS"}

# 16-bit data registers (no bit fields)
DATA_REGS: dict[str, int] = {"TMR1": 0x0100, "PR1": 0x0102, "TMR2": 0x0106, "TMR3": 0x010A,
                             "PR2": 0x010C, "PR3": 0x010E, "U1BRG": 0x0228}
DATA_RESET = {"PR1": 0xFFFF, "PR2": 0xFFFF, "PR3": 0xFFFF, "TMR1": 0, "TMR2": 0, "TMR3": 0, "U1BRG": 0}

# ---- the rest of the peripheral SFRs (interrupts, ports, CN, timers 4/5, IC, OC, ADC, PPS inputs):
# generated from the device pack by tools/gen_gb110_tables.py. Hand-checked entries above win.
from . import gb110_tables as _gen  # noqa: E402

# registers the debug/vision decoders model in depth (timers, UART, clock); others are reported as 'other'
CORE_NAMES = frozenset(REGISTERS) | frozenset(DATA_REGS)

_PAGE_FALLBACK = {"TRIS": 133}
for _n, _a, _pg, _f, _rst in _gen.SFRS:
    if _n not in REGISTERS:
        _add(Register(_n, _a, _pg, tuple(Field(fn, lsb, w) for fn, lsb, w in _f), reset=_rst))
for _r in list(REGISTERS.values()):
    if _r.name.startswith("TRIS") and _r.page == 0:
        REGISTERS[_r.name] = Register(_r.name, _r.address, 133, _r.fields, _r.reset)
for _n, _a, _rst in _gen.DATA:
    DATA_REGS.setdefault(_n, _a)
    DATA_RESET.setdefault(_n, _rst)
VECTORS: dict[str, int] = dict(_gen.VECTORS)      # "T1Interrupt" -> IRQ
CN_PIN: dict[str, int] = dict(_gen.CN_PIN)        # "RD6" -> 15
RPI_PIN: dict[int, str] = dict(_gen.RPI_PIN)


def implemented_pins() -> set[str]:
    """Port pins that exist on the 100-pin PIC24FJ256GB110 (from the TRISx bitfields in the pack)."""
    out = set()
    for n, _a, _pg, f, _r in _gen.SFRS:
        if n.startswith("TRIS"):
            out |= {"R" + fn[4:] for fn, _l, _w in f}
    return out


def _unique_fields() -> dict[str, tuple[str, str]]:
    """XC16 also defines `_FIELD` macros for every bit name that is unique across SFRs (e.g. _T1IF, _CNIE)."""
    seen: dict[str, list[str]] = {}
    for r in REGISTERS.values():
        for f in r.fields:
            seen.setdefault(f.name, []).append(r.name)
    return {f: (rs[0], f) for f, rs in seen.items() if len(rs) == 1}


FIELD_ALIASES = _unique_fields()

# Datasheet pages most useful as multimodal inputs per topic.
PAGES = {
    "timer1": [161, 162],        # block diagram + T1CON
    "timer2": [163, 164, 165, 166],  # 32-bit text/diagrams + T2CON
    "timer3": [165, 167],        # diagrams + T3CON
    "interrupts": [84, 90, 97, 98, 99],
    "clock": [123, 124, 125, 290],  # OSCCON, CLKDIV, CW2
}
PAGE_OF_REGISTER = {r.name: r.page for r in REGISTERS.values()}


# ---------------------------------------------------------------- any PIC via Microchip EDC files
def builtin_device():
    """PIC24FJ256GB110 as an edc.Device built from the hand-checked tables above (fallback without packs)."""
    from . import edc
    from .board import SETTINGS, COMMENTS, pragma_values, get_profile
    dflt = pragma_values(get_profile("frcpll"))
    sets = []
    for name, (word, allowed) in SETTINGS.items():
        if hasattr(allowed, "match"):
            if name == "WDTPS":
                vals = [f"PS{2**i}" for i in range(16)]
            else:
                vals = [f"WPFP{i}" for i in range(512)]
        else:
            vals = sorted(allowed)
        sets.append(edc.ConfigSetting(name, word, COMMENTS.get(name, ""), 0, 1,
                                      [edc.ConfigValue(v, "") for v in vals], dflt.get(name)))
    sfrs = {r.name: edc.SFR(r.name, r.address, 16, [edc.SFRField(f.name, f.lsb, f.width, f.desc) for f in r.fields])
            for r in REGISTERS.values()}
    for n, a in DATA_REGS.items():
        sfrs[n] = edc.SFR(n, a, 16)
    return edc.Device(DEVICE, "24xxxx", "PIC24", "xc16", settings=sets, sfrs=sfrs, program_bytes=262144 - 1536,
                      data_bytes=16384, icsp_pairs=[1, 2, 3], datasheet_id="39897", source="built-in")


_DEV_CACHE: dict = {}


def get_device(name: str | None = None, packs_dir: str = "", cache_dir: str = ".cache", path: str | None = None):
    """EDC data when a pack (or explicit path) has the device, else built-in data for PIC24FJ256GB110, else None."""
    from . import edc
    name = (name or DEVICE).upper()
    key = (name, packs_dir, path)
    if key in _DEV_CACHE:
        return _DEV_CACHE[key]
    dev = None
    try:
        dev = edc.load_device(name, packs_dir, cache_dir, path)
    except Exception:   # malformed pack file -> fall back
        dev = None
    if dev is None and name == DEVICE:
        dev = builtin_device()
    _DEV_CACHE[key] = dev
    return dev


def device_for(settings):
    return get_device(settings.device, getattr(settings, "mplab_packs_dir", ""), settings.cache_dir)


def is_builtin_family(name: str) -> bool:
    """The deterministic Timer1/clock emulator is modelled on PIC24FJ256GB110 only."""
    return name.upper() == DEVICE
