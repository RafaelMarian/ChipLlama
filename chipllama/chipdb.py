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

# 16-bit data registers (no bit fields)
DATA_REGS: dict[str, int] = {"TMR1": 0x0100, "PR1": 0x0102, "TMR2": 0x0106, "TMR3": 0x010A,
                             "PR2": 0x010C, "PR3": 0x010E}
DATA_RESET = {"PR1": 0xFFFF, "PR2": 0xFFFF, "PR3": 0xFFFF, "TMR1": 0, "TMR2": 0, "TMR3": 0}

# Datasheet pages most useful as multimodal inputs per topic.
PAGES = {
    "timer1": [161, 162],        # block diagram + T1CON
    "timer2": [163, 164, 165, 166],  # 32-bit text/diagrams + T2CON
    "timer3": [165, 167],        # diagrams + T3CON
    "interrupts": [84, 90, 97, 98, 99],
    "clock": [123, 124, 125, 290],  # OSCCON, CLKDIV, CW2
}
PAGE_OF_REGISTER = {r.name: r.page for r in REGISTERS.values()}
