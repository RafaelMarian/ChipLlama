import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chipllama.config import Settings  # noqa: E402
from chipllama.compiler import CompileResult  # noqa: E402
from chipllama.models.base import Message  # noqa: E402

GOOD_T1 = """\
#include <xc.h>
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
int main(void) { timer1_init(); while (1) { } }
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
