import pytest
from conftest import CFG, ScriptedModel, compile_ok, fence

from chipllama import cli, peripherals
from chipllama.agent import run_agent
from chipllama.peripherals import check_full
from chipllama import spi, i2c, rtcc, crc, pmp, power, usb, uart
from chipllama.checker import CheckReport

MAIN = "int main(void) { CLKDIVbits.RCDIV = 1; CLKDIVbits.CPDIV = 0; init(); while (1) { } }\n"

REQ_SPI = "SPI1 master 2 MHz mode 3 SCK on RP10 MOSI on RP17 MISO on RP16"
GOOD_SPI = CFG + """
static void init(void)
{
    __builtin_write_OSCCONL(OSCCON & 0xBF);
    RPOR5bits.RP10R = 8;      /* SCK1OUT */
    RPOR8bits.RP17R = 7;      /* SDO1 */
    RPINR20bits.SDI1R = 16;   /* SDI1 <- RP16 (RF3) */
    __builtin_write_OSCCONL(OSCCON | 0x40);
    TRISFbits.TRISF4 = 0; TRISFbits.TRISF5 = 0; TRISFbits.TRISF3 = 1;
    SPI1STAT = 0;
    SPI1CON1 = 0x0063;        /* MSTEN, CKP=1, CKE=0, SPRE=000 (8:1), PPRE=11 (1:1) -> 2 MHz */
    SPI1CON2 = 0;
    SPI1STATbits.SPIEN = 1;
}
""" + MAIN

REQ_I2C = "I2C1 master 400 kHz"
GOOD_I2C = CFG + """
static void init(void)
{
    I2C1BRG = 37;             /* 16 MHz/400 kHz - 1.6 - 1 */
    I2C1CON = 0x8000;
}
""" + MAIN

REQ_RTCC = "RTCC with an alarm every second"
GOOD_RTCC = CFG + """
static void init(void)
{
    __builtin_write_OSCCONL(OSCCON | 0x02);
    __builtin_write_RTCWEN();
    RCFGCALbits.RTCEN = 0;
    ALCFGRPTbits.AMASK = 1;
    ALCFGRPTbits.CHIME = 1;
    ALCFGRPTbits.ALRMEN = 1;
    IPC15bits.RTCIP = 4;
    IFS3bits.RTCIF = 0;
    IEC3bits.RTCIE = 1;
    RCFGCALbits.RTCEN = 1;
    RCFGCALbits.RTCWREN = 0;
}
void __attribute__((interrupt, auto_psv)) _RTCCInterrupt(void) { IFS3bits.RTCIF = 0; }
""" + MAIN

REQ_CRC = "CRC-16-CCITT of a buffer"
GOOD_CRC = CFG + """
static void init(void)
{
    CRCCONbits.PLEN = 15;
    CRCXOR = 0x1020;
    CRCWDAT = 0xFFFF;
    CRCCONbits.CRCGO = 1;
}
""" + MAIN

REQ_PMP = "PMP master 8-bit with 2 address lines and chip select CS1"
GOOD_PMP = CFG + """
static void init(void)
{
    PMMODE = 0x0200;
    PMAEN = 0x4003;
    PMCON = 0x8380;
}
""" + MAIN

REQ_WDT = "watchdog 1 s and sleep until it wakes the chip"
GOOD_WDT = CFG.replace("FWDTEN = OFF", "FWDTEN = ON").replace("WDTPS = PS32768", "WDTPS = PS256") + """
static void init(void) { }
int main(void) { CLKDIVbits.RCDIV = 1; CLKDIVbits.CPDIV = 0; init(); while (1) { ClrWdt(); Sleep(); } }
"""


def codes(code, kind, req):
    sp = peripherals._mod(kind).parse_request(req, 16e6)
    rep = check_full(code, {kind: sp}, fcy=16e6)
    return rep, {f.code for f in rep.findings}


def test_calcs():
    s = spi.solve(spi.parse_request(REQ_SPI))
    assert (s["PPRE"], s["SPRE"], s["freq"], s["CKP"], s["CKE"]) == (3, 0, 2e6, 1, 0)
    assert all(not (p == 3 and sp == 7) for _, p, sp in spi.options(16e6))
    assert [i2c.brg_for(16e6, f) for f in (100e3, 400e3, 1e6)] == [157, 37, 13]
    assert i2c.PINS[1][:2] == ("RA14", "RA15")
    c = crc.parse_request("crc x^16+x^12+x^5+1")
    assert (c.length, c.xor) == (16, 0x1020)
    assert power.wdt_solve(1.0)["FWPSA"] in ("PR32", "PR128")
    assert usb.LABEL in usb.calc_lines(usb.parse_request("usb cdc"))[0][0]


@pytest.mark.parametrize("kind,code,req", [("spi", GOOD_SPI, REQ_SPI), ("i2c", GOOD_I2C, REQ_I2C), ("rtcc", GOOD_RTCC, REQ_RTCC),
                                           ("crc", GOOD_CRC, REQ_CRC), ("pmp", GOOD_PMP, REQ_PMP), ("power", GOOD_WDT, REQ_WDT)])
def test_good(kind, code, req):
    rep, _ = codes(code, kind, req)
    assert rep.ok, rep.errors


@pytest.mark.parametrize("kind,code,req,old,new,code_", [
    ("spi", GOOD_SPI, REQ_SPI, "0x0063", "0x0067", "SPI_FREQ"),
    ("spi", GOOD_SPI, REQ_SPI, "0x0063", "0x0163", "SPI_MODE"),
    ("spi", GOOD_SPI, REQ_SPI, "0x0063", "0x007F", "SPI_PRESCALE"),
    ("spi", GOOD_SPI, REQ_SPI, "RPOR5bits.RP10R = 8", "RPOR5bits.RP10R = 7", "SPI_PPS_SCK"),
    ("spi", GOOD_SPI, REQ_SPI, "SPI1STATbits.SPIEN = 1;", "", "SPI_EN"),
    ("i2c", GOOD_I2C, REQ_I2C, "I2C1BRG = 37", "I2C1BRG = 157", "I2C_BRG"),
    ("i2c", GOOD_I2C, REQ_I2C, "0x8000", "0x0000", "I2C_EN"),
    ("rtcc", GOOD_RTCC, REQ_RTCC, "__builtin_write_RTCWEN();", "", "RTCC_WREN"),
    ("rtcc", GOOD_RTCC, REQ_RTCC, "__builtin_write_OSCCONL(OSCCON | 0x02);", "", "RTCC_SOSC"),
    ("rtcc", GOOD_RTCC, REQ_RTCC, "AMASK = 1", "AMASK = 3", "RTCC_AMASK"),
    ("crc", GOOD_CRC, REQ_CRC, "PLEN = 15", "PLEN = 16", "CRC_PLEN"),
    ("crc", GOOD_CRC, REQ_CRC, "0x1020", "0x1021", "CRC_GO_NOT"),
    ("pmp", GOOD_PMP, REQ_PMP, "0x4003", "0x0003", "PMP_PMAEN"),
    ("pmp", GOOD_PMP, REQ_PMP, "0x8380", "0x0380", "PMP_EN"),
    ("power", GOOD_WDT, REQ_WDT, "ClrWdt();", "", "WDT_CLR"),
    ("power", GOOD_WDT, REQ_WDT, "PS256", "PS32768", "WDT_PERIOD"),
])
def test_rules(kind, code, req, old, new, code_):
    assert old in code
    rep, c = codes(code.replace(old, new), kind, req)
    if code_ == "CRC_GO_NOT":       # bit 0 of CRCXOR is implied: 0x1021 is accepted
        assert rep.ok, rep.errors
        return
    assert code_ in c, c


def test_uart3():
    s = uart.parse_request = None or uart.parse_uart_request("UART3 9600 baud TX on RP10 RX on RP17")
    good = ("void f(void){ __builtin_write_OSCCONL(OSCCON & 0xBF); RPOR5bits.RP10R = 28; RPINR17bits.U3RXR = 17;"
            " __builtin_write_OSCCONL(OSCCON | 0x40); TRISFbits.TRISF4=0; U3BRG = 103; U3MODEbits.UARTEN=1; U3STAbits.UTXEN=1; }")
    rep = uart.check_uart(good, s, CheckReport())
    assert rep.ok, rep.errors
    rep = uart.check_uart(good.replace("= 28", "= 3"), s, CheckReport())
    assert "PPS_TX" in {f.code for f in rep.findings}
    assert "RPINR17bits.U3RXR" in uart.uart_prompt_text(s)


def test_usb_unsupported():
    rep, c = codes(CFG + "static void init(void){}\n" + MAIN, "usb", "USB CDC device")
    assert "USB_UNSUPPORTED" in c and "USB_CLOCK" in c


def test_fcy_not_from_bus():
    assert peripherals.fcy_from_text("SPI at 2 MHz", default=None) is None
    assert peripherals.fcy_from_text("32 MHz Fosc, I2C 1 MHz") == 16e6


def test_agent(settings):
    m = ScriptedModel([fence(GOOD_SPI)])
    r = run_agent(REQ_SPI, m, settings, compile_fn=compile_ok)
    assert r.status == "verified", r.attempts[-1].check.errors
    assert "SPI1CON1" in m.calls[0][1].text


@pytest.mark.parametrize("kind,req,needle", [("spi", REQ_SPI, "SPI1CON1 = 0x0063"), ("i2c", REQ_I2C, "-> 37"),
                                             ("crc", REQ_CRC, "CRCXOR = 0x1020"), ("power", REQ_WDT, "ClrWdt"),
                                             ("usb", "usb", "UNSUPPORTED"), ("rtcc", REQ_RTCC, "AMASK = 0001"),
                                             ("pmp", REQ_PMP, "PMAEN  = 0x4003")])
def test_cli(capsys, kind, req, needle):
    assert cli.main(["calc", kind, req]) == 0
    assert needle in capsys.readouterr().out
