"""Blocks tab: Blockly workspace JSON -> deterministic XC16 C for the PIC24FJ256GB110 + verification specs.

The generator never calls a model. Every register value comes from the same solvers the checker
uses (timers.solve, uart.solve_baud, spi/i2c/adc calc), and the result is fed straight back through
the deterministic checker with spec objects built from the blocks (not re-parsed from text), so the
VERIFIED badge means exactly what it means on the Generate tab.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import gpio, uart as uartmod, adc as adcmod, spi as spimod, i2c as i2cmod, peripherals, timers
from .board import render_pragmas

FCY = 16e6
PINS = ([f"RA{i}" for i in (0, 1, 2, 3, 4, 5, 6, 7, 9, 10, 14, 15)] + [f"RB{i}" for i in range(16)] +
        [f"RC{i}" for i in (1, 2, 3, 4, 13, 14)] + [f"RD{i}" for i in range(16)] + [f"RE{i}" for i in range(10)] +
        [f"RF{i}" for i in (0, 1, 2, 3, 4, 5, 8, 12, 13)] + [f"RG{i}" for i in (0, 1, 6, 7, 8, 9, 12, 13, 14, 15)])


def pin_meta() -> list[dict]:
    out = []
    for p in PINS:
        try:
            r = gpio._regs(gpio.PinCfg(p, False, True, False, True))
        except Exception:
            continue
        out.append({"pin": p, "cn": "CNEN" in r, "analog": "PCFG" in r,
                    "rp": uartmod.PIN_RP.get(p)})
    return out


class BlocksError(ValueError):
    pass


@dataclass
class Program:
    start: list[str] = field(default_factory=list)
    loop: list[str] = field(default_factory=list)
    every: tuple | None = None            # (ms, body)
    pin_change: list[tuple] = field(default_factory=list)   # (pin, body)
    uart_rx: list[str] | None = None
    outputs: set = field(default_factory=set)
    inputs: dict = field(default_factory=dict)   # pin -> pullup
    uart: tuple | None = None             # (baud, tx_rp, rx_rp)
    adc: set = field(default_factory=set)
    spi: tuple | None = None              # (hz, mode, sck, sdo, sdi)
    i2c: float | None = None
    delay: bool = False
    vars: set = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)


def _ident(n: str) -> str:
    s = re.sub(r"\W", "_", n.strip()) or "v"
    return ("v_" + s) if s[0].isdigit() else s


def _cstr(s: str) -> str:
    return json.dumps(s)


class Gen:
    def __init__(self, ws: dict):
        self.ws = ws
        self.p = Program()
        self.varnames = {v.get("id"): _ident(v.get("name", "v")) for v in ws.get("variables", [])}

    # ---------------------------------------------------------------- helpers
    def f(self, b, name, default=None):
        return (b.get("fields") or {}).get(name, default)

    def inp(self, b, name):
        return ((b.get("inputs") or {}).get(name) or {}).get("block")

    def var(self, b):
        v = self.f(b, "VAR", {})
        n = self.varnames.get(v.get("id")) if isinstance(v, dict) else None
        n = n or _ident(v.get("name", "v") if isinstance(v, dict) else str(v))
        self.p.vars.add(n)
        return n

    def pin(self, b, field_="PIN"):
        p = str(self.f(b, field_, "RA0")).upper()
        if p not in PINS:
            raise BlocksError(f"unknown pin {p}")
        return p

    # ---------------------------------------------------------------- expressions
    def expr(self, b, default="0"):
        if b is None:
            return default
        t = b["type"]
        if t == "math_number":
            n = float(self.f(b, "NUM", 0))
            return str(int(n)) if n == int(n) else repr(n)
        if t == "logic_boolean":
            return "1" if self.f(b, "BOOL") == "TRUE" else "0"
        if t == "variables_get":
            return self.var(b)
        if t == "math_arithmetic":
            op = {"ADD": "+", "MINUS": "-", "MULTIPLY": "*", "DIVIDE": "/", "POWER": None}[self.f(b, "OP", "ADD")]
            if op is None:
                raise BlocksError("power (^) is not supported on a 16-bit MCU without libm; use multiply")
            return f"({self.expr(self.inp(b, 'A'))} {op} {self.expr(self.inp(b, 'B'))})"
        if t == "math_modulo":
            return f"({self.expr(self.inp(b, 'DIVIDEND'))} % {self.expr(self.inp(b, 'DIVISOR'), '1')})"
        if t == "logic_compare":
            op = {"EQ": "==", "NEQ": "!=", "LT": "<", "LTE": "<=", "GT": ">", "GTE": ">="}[self.f(b, "OP", "EQ")]
            return f"({self.expr(self.inp(b, 'A'))} {op} {self.expr(self.inp(b, 'B'))})"
        if t == "logic_operation":
            op = "&&" if self.f(b, "OP") == "AND" else "||"
            return f"({self.expr(self.inp(b, 'A'))} {op} {self.expr(self.inp(b, 'B'))})"
        if t == "logic_negate":
            return f"(!{self.expr(self.inp(b, 'BOOL'))})"
        if t == "cl_pin_read":
            p = self.pin(b)
            self.p.inputs.setdefault(p, False)
            return f"PORT{p[1]}bits.{p}"
        if t == "cl_adc_read":
            ch = int(self.f(b, "CH", 0))
            if not 0 <= ch <= 15:
                raise BlocksError("ADC channel must be AN0..AN15")
            self.p.adc.add(ch)
            return f"adc_read({ch})"
        if t == "cl_spi_transfer":
            if not self.p.spi:
                self.p.spi = (1e6, 0, 10, 17, 16)
                self.p.warnings.append("SPI used without 'SPI begin': defaulting to 1 MHz mode 0, SCK RP10, SDO RP17, SDI RP16")
            return f"spi1_xfer({self.expr(self.inp(b, 'VALUE'))})"
        if t == "cl_uart_available":
            return "U1STAbits.URXDA"
        if t == "cl_uart_read":
            return "U1RXREG"
        raise BlocksError(f"block '{t}' cannot be used as a value")

    # ---------------------------------------------------------------- statements
    def stmts(self, b, ind="    ") -> list[str]:
        out = []
        while b is not None:
            out += self.stmt(b, ind)
            b = (b.get("next") or {}).get("block")
        return out

    def stmt(self, b, ind) -> list[str]:
        t, p = b["type"], self.p
        if t == "cl_pin_mode":
            pin, mode = self.pin(b), self.f(b, "MODE", "OUTPUT")
            if mode == "OUTPUT":
                p.outputs.add(pin)
            else:
                p.inputs[pin] = p.inputs.get(pin, False) or mode == "INPUT_PULLUP"
            return []
        if t == "cl_pin_write":
            pin = self.pin(b)
            p.outputs.add(pin)
            return [f"{ind}LAT{pin[1]}bits.LAT{pin[1:]} = {1 if self.f(b, 'VALUE', 'HIGH') == 'HIGH' else 0};"]
        if t == "cl_pin_toggle":
            pin = self.pin(b)
            p.outputs.add(pin)
            return [f"{ind}LAT{pin[1]}bits.LAT{pin[1:]} ^= 1;"]
        if t == "cl_delay_ms":
            p.delay = True
            return [f"{ind}__delay_ms({int(self.f(b, 'MS', 100))});"]
        if t == "cl_delay_us":
            p.delay = True
            return [f"{ind}__delay_us({int(self.f(b, 'US', 10))});"]
        if t == "cl_uart_begin":
            p.uart = (int(self.f(b, "BAUD", 115200)), int(self.f(b, "TX", 10)), int(self.f(b, "RX", 17)))
            return []
        if t in ("cl_uart_print", "cl_uart_println", "cl_uart_print_num"):
            if not p.uart:
                p.uart = (115200, 10, 17)
                p.warnings.append("print used without 'UART begin': defaulting to UART1 115200, TX RP10, RX RP17")
            nl = "\\r\\n" if t == "cl_uart_println" or self.f(b, "NL") == "TRUE" else ""
            if t == "cl_uart_print_num":
                return [f"{ind}uart_print_num({self.expr(self.inp(b, 'VALUE'))});"] + (
                    [f'{ind}uart_print("\\r\\n");'] if nl else [])
            return [f"{ind}uart_print({_cstr(str(self.f(b, 'TEXT', '')))[:-1]}{nl}\");"]
        if t == "cl_spi_begin":
            p.spi = (float(self.f(b, "KHZ", 1000)) * 1e3, int(self.f(b, "MODE", 0)), int(self.f(b, "SCK", 10)),
                     int(self.f(b, "SDO", 17)), int(self.f(b, "SDI", 16)))
            return []
        if t in ("cl_spi_transfer", "cl_spi_send"):
            return [f"{ind}(void){self.expr(dict(b, type='cl_spi_transfer'))};"]
        if t == "cl_i2c_begin":
            p.i2c = float(self.f(b, "KHZ", 100)) * 1e3
            return []
        if t == "cl_i2c_write":
            if p.i2c is None:
                p.i2c = 100e3
                p.warnings.append("I2C used without 'I2C begin': defaulting to I2C1 100 kHz")
            addr = int(str(self.f(b, "ADDR", "0x48")), 0)
            if not 0 <= addr < 128:
                raise BlocksError("I2C address must be 7-bit (0x00..0x7F)")
            return [f"{ind}i2c1_write(0x{addr:02X}, {self.expr(self.inp(b, 'VALUE'))});"]
        if t == "variables_set":
            return [f"{ind}{self.var(b)} = {self.expr(self.inp(b, 'VALUE'))};"]
        if t == "math_change":
            return [f"{ind}{self.var(b)} += {self.expr(self.inp(b, 'DELTA'), '1')};"]
        if t == "controls_if":
            out, n = [], 0
            while self.inp(b, f"IF{n}") is not None or f"IF{n}" in (b.get("inputs") or {}):
                kw = "if" if n == 0 else "} else if"
                out.append(f"{ind}{kw} ({self.expr(self.inp(b, f'IF{n}'))}) {{")
                out += self.stmts(self.inp(b, f"DO{n}"), ind + "    ")
                n += 1
            if n == 0:
                return []
            if "ELSE" in (b.get("inputs") or {}):
                out.append(f"{ind}}} else {{")
                out += self.stmts(self.inp(b, "ELSE"), ind + "    ")
            return out + [f"{ind}}}"]
        if t == "controls_repeat_ext":
            i = f"i_{len(self.p.vars)}"
            self.p.vars.add(i)
            return ([f"{ind}for ({i} = 0; {i} < {self.expr(self.inp(b, 'TIMES'))}; {i}++) {{"] +
                    self.stmts(self.inp(b, "DO"), ind + "    ") + [f"{ind}}}"])
        if t == "controls_whileUntil":
            c = self.expr(self.inp(b, "BOOL"))
            c = c if self.f(b, "MODE", "WHILE") == "WHILE" else f"!{c}"
            return [f"{ind}while ({c}) {{"] + self.stmts(self.inp(b, "DO"), ind + "    ") + [f"{ind}}}"]
        if t == "controls_flow_statements":
            return [f"{ind}{'break' if self.f(b, 'FLOW') == 'BREAK' else 'continue'};"]
        raise BlocksError(f"block '{t}' is not supported inside a sequence")

    # ---------------------------------------------------------------- top level
    def run(self) -> Program:
        p = self.p
        for b in (self.ws.get("blocks") or {}).get("blocks", []):
            t = b["type"]
            body = self.stmts(self.inp(b, "DO"), "    " if t == "cl_on_start" else ("        " if t == "cl_forever" else "    "))
            if t == "cl_on_start":
                p.start += body
            elif t == "cl_forever":
                p.loop += body
            elif t == "cl_every":
                if p.every:
                    raise BlocksError("only one 'every' block (it uses Timer1)")
                ms = float(self.f(b, "MS", 500))
                if not 0.001 <= ms <= 1000:
                    raise BlocksError("'every' period must be 1 us .. 1000 ms (Timer1, 16-bit)")
                p.every = (ms, body)
            elif t == "cl_on_pin_change":
                pin = self.pin(b)
                p.inputs[pin] = p.inputs.get(pin, False) or self.f(b, "PULLUP", "TRUE") == "TRUE"
                p.pin_change.append((pin, body))
            elif t == "cl_on_uart_rx":
                p.uart = p.uart or (115200, 10, 17)
                p.uart_rx = body
            else:
                p.warnings.append(f"loose block '{t}' outside an event is ignored")
        if not any(b.get("type", "").startswith("cl_on_") or b.get("type") in ("cl_forever", "cl_every")
                   for b in (self.ws.get("blocks") or {}).get("blocks", [])):
            raise BlocksError("add an event block (on start, forever, every ..., when pin changes) to begin")
        both = set(p.outputs) & set(p.inputs)
        if both:
            raise BlocksError(f"pin(s) {', '.join(sorted(both))} used as both input and output")
        return p


# ------------------------------------------------------------------------- C emission
def _specs(p: Program):
    tspec = uspec = None
    pspecs = {}
    if p.every:
        tspec = timers.TimerSpec(timer=1, period_s=p.every[0] / 1000, fosc_hz=2 * FCY, interrupt=True, priority=4)
    if p.uart:
        uspec = uartmod.UartSpec(baud=p.uart[0], uart=1, tx_rp=p.uart[1], rx_rp=p.uart[2], fcy=FCY)
    pins = [gpio.PinCfg(x, True) for x in sorted(p.outputs)]
    cn = {x for x, _ in p.pin_change}
    pins += [gpio.PinCfg(x, False, pullup=pu, cn=x in cn) for x, pu in sorted(p.inputs.items())]
    if pins:
        pspecs["gpio"] = gpio.GpioSpec(pins=pins, cn_priority=4 if cn else None)
    if p.adc:
        pspecs["adc"] = adcmod.AdcSpec(channels=sorted(p.adc), fcy=FCY)
    if p.spi:
        hz, mode, sck, sdo, sdi = p.spi
        pspecs["spi"] = spimod.SpiSpec(n=1, freq_hz=hz, mode=mode, fcy=FCY, sck_rp=sck, sdo_rp=sdo, sdi_rp=sdi)
    if p.i2c:
        pspecs["i2c"] = i2cmod.I2cSpec(n=1, freq_hz=p.i2c, fcy=FCY)
    return tspec, uspec, pspecs


def intent(p: Program) -> str:
    parts = []
    if p.every:
        parts.append(f"Timer1 interrupt every {p.every[0]:g} ms, priority 4")
    for x in sorted(p.outputs):
        parts.append(f"{x} output")
    for x, pu in sorted(p.inputs.items()):
        cn = any(x == y for y, _ in p.pin_change)
        parts.append(f"{x} input" + (" with pull-up" if pu else "") + (" and change notification interrupt, priority 4" if cn else ""))
    if p.uart:
        parts.append(f"UART1 {p.uart[0]} baud TX on RP{p.uart[1]} RX on RP{p.uart[2]}" + (" with receive interrupt" if p.uart_rx is not None else ""))
    if p.adc:
        parts.append("ADC manual sampling of " + ", ".join(f"AN{c}" for c in sorted(p.adc)))
    if p.spi:
        parts.append(f"SPI1 master {p.spi[0]/1e3:g} kHz mode {p.spi[1]} SCK on RP{p.spi[2]} MOSI on RP{p.spi[3]} MISO on RP{p.spi[4]}")
    if p.i2c:
        parts.append(f"I2C1 master {p.i2c/1e3:g} kHz")
    return "; ".join(parts) or "empty program"


def emit(p: Program, profile, ics: str = "PGx2") -> str:
    tspec, uspec, pspecs = _specs(p)
    L = ["/* Generated by ChipLlama Blocks - deterministic, no AI. PIC24FJ256GB110, Fcy = 16 MHz (FRC+PLL). */",
         "#include <xc.h>", render_pragmas(profile, ics).rstrip(), "", "#define FCY 16000000UL"]
    if p.delay or p.uart:
        L.append("#include <libpic30.h>")
    L.append("")
    for v in sorted(p.vars):
        L.append(f"volatile int {v};")
    if p.vars:
        L.append("")
    init = []
    pps_out, pps_in = [], []
    # ---- GPIO
    g = pspecs.get("gpio")
    if g:
        for pc in g.pins:
            r = gpio._regs(pc)
            if pc.output:
                init.append(f"    LAT{pc.name[1]}bits.LAT{pc.name[1:]} = 0;")
            if "PCFG" in r:
                init.append(f"    {r['PCFG'][0]}bits.{r['PCFG'][1]} = 1;   /* {pc.name} digital */")
            init.append(f"    {r['TRIS'][0]}bits.{r['TRIS'][1]} = {r['TRIS'][2]};   /* {pc.name} {'output' if pc.output else 'input'} */")
            if pc.pullup and "CNPU" in r:
                init.append(f"    {r['CNPU'][0]}bits.{r['CNPU'][1]} = 1;   /* pull-up */")
            if pc.cn:
                if "CNEN" not in r:
                    raise BlocksError(f"{pc.name} has no change-notification input")
                init.append(f"    {r['CNEN'][0]}bits.{r['CNEN'][1]} = 1;   /* CN on {pc.name} */")
        if g.cn_pins:
            init += ["    IPC4bits.CNIP = 4;", "    IFS1bits.CNIF = 0;", "    IEC1bits.CNIE = 1;"]
    # ---- UART1
    if uspec:
        bs = uartmod.solve_baud(FCY, uspec.baud)
        fld = uartmod.rpor_field(uspec.tx_rp)
        pps_out.append(f"    {fld[0]}bits.{fld[1]} = 3;   /* RP{uspec.tx_rp} -> U1TX */")
        pps_in.append(f"    RPINR18bits.U1RXR = {uspec.rx_rp};   /* U1RX <- RP{uspec.rx_rp} */")
        tx, rx = uartmod.port_pin(uspec.tx_rp), uartmod.port_pin(uspec.rx_rp)
        for pin, d in ((tx, 0), (rx, 1)):
            if pin and pin[1] in "ABCDEFG":
                init.append(f"    TRIS{pin[1]}bits.TRIS{pin[1:]} = {d};")
                an = uartmod.analog_channel(pin)
                if an is not None:
                    init.append(f"    AD1PCFGLbits.PCFG{an} = 1;")
        init += ["    U1MODE = 0;", f"    U1MODEbits.BRGH = {bs.brgh};",
                 f"    U1BRG = {bs.brg};   /* {bs.actual:.0f} baud ({bs.error_pct:+.2f}%) */"]
        if p.uart_rx is not None:
            init += ["    IPC2bits.U1RXIP = 4;", "    IFS0bits.U1RXIF = 0;", "    IEC0bits.U1RXIE = 1;"]
        init += ["    U1MODEbits.UARTEN = 1;", "    U1STAbits.UTXEN = 1;"]
    # ---- SPI1
    if "spi" in pspecs:
        sp = pspecs["spi"]
        _, d = spimod.calc_lines(sp)
        for rp, fn, nm in ((sp.sck_rp, 8, "SCK1OUT"), (sp.sdo_rp, 7, "SDO1")):
            fld = uartmod.rpor_field(rp)
            pps_out.append(f"    {fld[0]}bits.{fld[1]} = {fn};   /* RP{rp} -> {nm} */")
            pin = uartmod.port_pin(rp)
            if pin:
                init.append(f"    TRIS{pin[1]}bits.TRIS{pin[1:]} = 0;")
        pps_in.append(f"    RPINR20bits.SDI1R = {sp.sdi_rp};   /* SDI1 <- RP{sp.sdi_rp} */")
        pin = uartmod.port_pin(sp.sdi_rp)
        if pin:
            init.append(f"    TRIS{pin[1]}bits.TRIS{pin[1:]} = 1;")
        init += ["    SPI1STAT = 0;", f"    SPI1CON1 = 0x{d['CON1']:04X};   /* master, mode {sp.mode}, {d['freq']/1e3:g} kHz */",
                 "    SPI1CON2 = 0;", "    SPI1STATbits.SPIEN = 1;"]
    # ---- I2C1
    if "i2c" in pspecs:
        _, d = i2cmod.calc_lines(pspecs["i2c"])
        init += [f"    I2C1BRG = {d['I2CxBRG']};   /* {d['fscl']/1e3:.0f} kHz */",
                 f"    I2C1CON = 0x{0x8000 if p.i2c > 100e3 else 0x8200:04X};"]
    # ---- ADC
    if "adc" in pspecs:
        _, d = adcmod.calc_lines(pspecs["adc"])
        r = d["registers"]
        for c in sorted(p.adc):
            pin = f"RB{c}"
            init.append(f"    TRISBbits.TRISB{c} = 1;   /* AN{c} */")
        init += [f"    AD1PCFGL = 0x{r['AD1PCFGL']:04X};", f"    AD1CON1 = 0x{r['AD1CON1'] & 0x7FFF:04X};",
                 f"    AD1CON2 = 0x{r['AD1CON2']:04X};", f"    AD1CON3 = 0x{r['AD1CON3']:04X};",
                 f"    AD1CHS = {r['AD1CHS']};", "    AD1CON1bits.ADON = 1;"]
    # ---- Timer1
    if tspec:
        sol = timers.solve(tspec)
        init += ["    T1CON = 0;", "    TMR1 = 0;", f"    PR1 = {sol.period_reg};   /* {p.every[0]:g} ms at 1:{sol.prescale} */",
                 f"    T1CONbits.TCKPS = {sol.tckps};", "    IPC0bits.T1IP = 4;", "    IFS0bits.T1IF = 0;",
                 "    IEC0bits.T1IE = 1;", "    T1CONbits.TON = 1;"]
    pps = []
    if pps_out or pps_in:
        pps = ["    __builtin_write_OSCCONL(OSCCON & 0xBF);   /* unlock PPS */"] + pps_in + pps_out + [
            "    __builtin_write_OSCCONL(OSCCON | 0x40);   /* lock PPS */"]
    L += ["static void hw_init(void)", "{"] + pps + init + ["}", ""]
    # ---- helpers
    if uspec:
        L += ["static void uart_putc(char c) { while (U1STAbits.UTXBF); U1TXREG = c; }",
              "static void uart_print(const char *s) { while (*s) uart_putc(*s++); }",
              "static void uart_print_num(int v)", "{", "    char b[7]; int i = 0; unsigned int u = v < 0 ? -v : v;",
              "    if (v < 0) uart_putc('-');", "    do { b[i++] = '0' + u % 10; u /= 10; } while (u);",
              "    while (i) uart_putc(b[--i]);", "}", ""]
    if "adc" in pspecs:
        L += ["static unsigned int adc_read(unsigned int ch)", "{", "    AD1CHS = ch;", "    AD1CON1bits.SAMP = 1;",
              "    while (!AD1CON1bits.DONE);", "    return ADC1BUF0;", "}", ""]
    if "spi" in pspecs:
        L += ["static unsigned char spi1_xfer(unsigned char b)", "{", "    SPI1BUF = b;",
              "    while (!SPI1STATbits.SPIRBF);", "    return SPI1BUF;", "}", ""]
    if "i2c" in pspecs:
        L += ["static void i2c1_wait(void) { while (I2C1CON & 0x001F); while (I2C1STATbits.TRSTAT); }",
              "static void i2c1_write(unsigned char addr, unsigned char data)", "{",
              "    I2C1CONbits.SEN = 1; while (I2C1CONbits.SEN);", "    I2C1TRN = addr << 1; i2c1_wait();",
              "    I2C1TRN = data; i2c1_wait();", "    I2C1CONbits.PEN = 1; while (I2C1CONbits.PEN);", "}", ""]
    # ---- ISRs
    if tspec:
        L += ["void __attribute__((interrupt, auto_psv)) _T1Interrupt(void)", "{"] + p.every[1] + ["    IFS0bits.T1IF = 0;", "}", ""]
    if p.pin_change:
        L += ["void __attribute__((interrupt, auto_psv)) _CNInterrupt(void)", "{"]
        for pin, body in p.pin_change:
            L += [f"    /* {pin} changed */"] + body
        L += ["    IFS1bits.CNIF = 0;", "}", ""]
    if p.uart_rx is not None:
        L += ["void __attribute__((interrupt, auto_psv)) _U1RXInterrupt(void)", "{"] + p.uart_rx + [
            "    IFS0bits.U1RXIF = 0;", "}", ""]
    L += ["int main(void)", "{"] + ["    " + x for x in profile.clock_code] + ["    hw_init();"] + p.start + [
        "    while (1) {"] + p.loop + ["    }", "}", ""]
    return "\n".join(L)


def generate(ws: dict | str, settings) -> dict:
    """{'c', 'intent', 'warnings', 'verified', 'findings', 'error'}"""
    if isinstance(ws, str):
        ws = json.loads(ws or "{}")
    try:
        prog = Gen(ws).run()
        prof = settings.profile()
        code = emit(prog, prof, settings.ics)
    except BlocksError as e:
        return {"c": "", "intent": "", "warnings": [], "verified": False, "findings": [], "error": str(e)}
    rep = verify(code, prog, settings)
    return {"c": code, "intent": intent(prog), "warnings": prog.warnings, "verified": rep.ok,
            "findings": [f.to_dict() for f in rep.findings if f.code != "UNPARSED"], "error": ""}


def verify(code: str, prog: Program, settings):
    from .checker import check_timer
    from .cemu import emulate
    tspec, uspec, pspecs = _specs(prog)
    prof = settings.profile()
    if tspec:
        chk = check_timer(code, tspec, profile=prof, ics=settings.ics)
        if uspec:
            uartmod.check_uart(code, uspec, chk)
        peripherals.check_all(code, pspecs, chk, emulate(code))
    elif uspec:
        chk = uartmod.check_uart_full(code, uspec, profile=prof, ics=settings.ics)
        peripherals.check_all(code, pspecs, chk, emulate(code))
    else:
        chk = peripherals.check_full(code, pspecs, fcy=FCY, profile=prof, ics=settings.ics)
    return chk
