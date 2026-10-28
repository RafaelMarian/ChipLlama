import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chipllama.config import Settings  # noqa: E402
from chipllama.compiler import CompileResult  # noqa: E402
from chipllama.models.base import Message  # noqa: E402

GOOD_T1 = """\
#include <xc.h>
// CONFIG3
#pragma config WPFP = WPFP511     // Write-protect boundary (unused, WPDIS)
#pragma config WPDIS = WPDIS      // Segmented code protection disabled
#pragma config WPCFG = WPCFGDIS   // Config page not protected
#pragma config WPEND = WPENDMEM   // (only with WPDIS=WPEN)
// CONFIG2
#pragma config POSCMOD = NONE     // Primary oscillator DISABLED (no crystal on the board)
#pragma config DISUVREG = OFF     // Internal USB 3.3V regulator disabled
#pragma config IOL1WAY = OFF      // PPS registers can be rewritten
#pragma config OSCIOFNC = ON      // OSCO/RC15 pin is general I/O (not CLKO)
#pragma config FCKSM = CSDCMD     // Clock switching and fail-safe monitor disabled
#pragma config FNOSC = FRCPLL     // FRC + PLL
#pragma config PLL_96MHZ = ON     // 96 MHz PLL enabled
#pragma config PLLDIV = NODIV     // PLL prescaler -> PLL input must be 4 MHz
#pragma config IESO = OFF         // Two-speed start-up disabled
// CONFIG1
#pragma config WDTPS = PS32768    // WDT postscaler (WDT off)
#pragma config FWPSA = PR128      // WDT prescaler (WDT off)
#pragma config WINDIS = OFF       // Standard WDT
#pragma config FWDTEN = OFF       // Watchdog timer disabled
#pragma config ICS = PGx2         // Debug/programming on PGEC2/PGED2 (must match YOUR board wiring)
#pragma config GWRP = OFF         // No program-memory write protect
#pragma config GCP = OFF          // No code protection
#pragma config JTAGEN = OFF       // JTAG disabled (frees RA0/RA1/RA4/RA5)
#define FOSC 32000000UL
#define FCY  (FOSC / 2UL)
#define PRESCALE 8UL
#define PERIOD_MS 10UL
/* ticks = FCY/1000*PERIOD_MS/PRESCALE = 20000 ; PR = ticks-1 */
void timer1_init(void)
{
    T1CON = 0;                       /* stop, internal clock */
    TMR1 = 0;
    PR1 = (FCY / 1000UL * PERIOD_MS / PRESCALE) - 1UL;
    T1CONbits.TCKPS = 1;             /* 1:8 */
    IPC0bits.T1IP = 5;
    IFS0bits.T1IF = 0;
    IEC0bits.T1IE = 1;
    T1CONbits.TON = 1;
}
void __attribute__((interrupt, auto_psv)) _T1Interrupt(void)
{
    IFS0bits.T1IF = 0;
}
int main(void) { CLKDIVbits.RCDIV = 1; CLKDIVbits.CPDIV = 0; timer1_init(); while (1) { } }
"""


def fence(code: str) -> str:
    return "Here you go:\n```c\n" + code + "```\nDone."


class ScriptedModel:
    """Mock chat model: returns the scripted replies in order and records what it was sent."""
    name = "mock"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[list[Message]] = []

    def complete(self, messages):
        self.calls.append(list(messages))
        if not self.replies:
            raise AssertionError("model called more often than scripted")
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def compile_ok(code, settings, **kw):
    return CompileResult("ok", command=["xc16-gcc", "mock"])


@pytest.fixture
def settings(tmp_path):
    return Settings(datasheet_pdf=str(tmp_path / "missing.pdf"), cache_dir=str(tmp_path / "cache"))

CFG = "\n".join(l for l in GOOD_T1.splitlines() if l.startswith("#pragma config")) + "\n"


@pytest.fixture(autouse=True)
def _isolated_settings_file(tmp_path, monkeypatch):
    """Never read or write the user's real settings.json during tests."""
    monkeypatch.setenv("CHIPLLAMA_SETTINGS", str(tmp_path / "settings.json"))
