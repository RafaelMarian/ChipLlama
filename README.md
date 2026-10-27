# ChipLlama 🦙⚡

**A verifiable AI copilot for PIC firmware — built for the PIC24FJ256GB110 (16-bit, USB OTG), XC16, MPLAB X and PICkit 5.**

Powered by Meta's **Muse Spark** models through the Meta Model API (switchable to **Muse Spark Open on Baseten**).

> Built for the Meta Global AI Developer Hackathon. See [Built during the hackathon](#built-during-the-hackathon-started-oct-27-2026).

ChipLlama is **not a datasheet chatbot**. A chatbot answers "probably this register setting"; ChipLlama *proves* it:

```
 "Timer1 interrupt every 10 ms at 32 MHz, priority 5"
        │
        ▼
 ① request → spec (Timer1, 10 ms, Fosc 32 MHz ⇒ Fcy 16 MHz, IRQ prio 5)
 ② register-table lookup (T1CON, PR1, IFS0/IEC0/IPC0 …) + the matching DATASHEET PAGES
    rendered to PNG and sent as images (multimodal) to Muse Spark
 ③ Muse writes XC16 C with explicit bit math
 ④ xc16-gcc -mcpu=24FJ256GB110 compiles it  ──► compiler errors parsed → fed back
 ⑤ deterministic register checker: a small C-subset emulator replays the init code,
    derives the final T1CON / PR1 / IEC0 / IPC0 values and compares the behaviour
    (period, TON, TCS, IE, IP, ISR flag-clear, write order …) with a reference
    solver  ──► findings fed back
 ⑥ loop until compile + checker pass (max N iterations) → "verified"
```

The second feature is **reverse debugging**: paste a register dump or upload a screenshot of MPLAB X's *SFR* window; Muse reads the screenshot (vision), then a **deterministic** decoder/rule engine finds why e.g. the timer interrupt never fires (TON=0, PR1 still at the 0xFFFF reset value, IE off, priority 0, flag stuck…) and the model only explains the verified findings.

The model never sees the reference solution (unless you pass `--hint`), so the checker is a genuine independent check, not a string match: any prescaler/PR combination that hits the period within tolerance passes.

## What is verified, and how

| Layer | What it proves | Needs |
|---|---|---|
| Timer calculator (`timers.py`) | Reference prescaler/PR for Timer1/2/3 incl. 32-bit Timer2/3 (PR3=MSW, PR2=LSW), `Fcy = Fosc/2`, period = (PR+1)·prescale/Fcy | nothing |
| C-subset emulator + checker (`cemu.py`, `checker.py`) | Final register values after your init code; TON/TCS/TGATE/T32, period, IE, IP (0 = source disabled), ISR exists with `interrupt` attribute and clears its flag, PR loaded, write-order warnings | nothing |
| `xc16-gcc` compile (`compiler.py`) | Code builds for `-mcpu=24FJ256GB110`; diagnostics are parsed and fed back | XC16 installed (**skipped gracefully if absent**) |
| Optional flash (`flash.py`) | Programs the `.hex` via `ipecmd.exe` + PICkit 5 | MPLAB X IPE, PICkit 5, **off by default** |

Register facts (addresses, bit positions, reset values, page numbers) were extracted from the datasheet **DS39897C** text and are embedded in `chipllama/chipdb.py` with their page numbers (e.g. T1CON 0x0104 p.162, T2CON 0x0110 p.166, T3CON 0x0112 p.167, IFS0 p.84, IEC0 p.90, IPC0/1/2 p.97–99). The datasheet itself is **not bundled** (Microchip copyright) — see below.

## Quick start (Windows, PowerShell)

```powershell
git clone <this repo> ; cd ChipLlama
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt            # add requirements-dev.txt for tests
copy .env.example .env                      # then edit .env – never commit it
```

Edit `.env` (no keys are shipped):

```ini
CHIPLLAMA_BACKEND=meta
MODEL_API_KEY=...                     # Meta Model API key
# or, for Muse Spark Open on Baseten:
# CHIPLLAMA_BACKEND=baseten
# BASETEN_API_KEY=...
```

Put the datasheet at `datasheets\DS39897C.pdf` (download DS39897C "PIC24FJ256GB110 Family Data Sheet" from microchip.com). Page images are rendered with `pdftoppm` (poppler) if on PATH, otherwise with PyMuPDF (`pip install pymupdf`). Without the PDF ChipLlama still works in text-only mode (the register table is in the prompt); only the multimodal page images are missing.

Then:

```powershell
python -m chipllama doctor                       # shows keys / xc16 / datasheet / ipecmd status
python -m chipllama calc "Timer1 every 10 ms at 32 MHz"          # no AI, no key
python -m chipllama generate "Timer1 interrupt every 10 ms at 32 MHz, priority 5" --out main.c --export out\proj
python -m chipllama debug --dump examples\dead_timer_dump.txt --expected-ms 10
python -m chipllama debug --image sfr_screenshot.png --symptom "timer ISR never runs"
python -m chipllama serve                        # web UI on http://127.0.0.1:8000
```

`calc`, `debug --no-model` and the whole checker run offline without any key.

## MPLAB X IDE + PICkit 5 + XC16 workflow

ChipLlama does not replace MPLAB X — it feeds it.

1. **Install XC16** (the compiler for 16-bit PICs) and MPLAB X. Typical compiler location: `C:\Program Files\Microchip\xc16\v2.10\bin\xc16-gcc.exe` (your version number will differ). ChipLlama auto-detects `C:\Program Files\Microchip\xc16\*\bin` and `PATH`; to pin one, set `XC16_GCC=C:\Program Files\Microchip\xc16\v2.10\bin\xc16-gcc.exe` in `.env`.
2. **Generate**: `python -m chipllama generate "<request>" --export out\proj` (or the *Export MPLAB X folder* button in the web UI, which downloads a zip). With XC16 installed, `xc16-gcc -mcpu=24FJ256GB110` is run on every attempt.
3. **Open in MPLAB X** — the export contains `main.c`, `build.bat`, a `Makefile` and `OPEN_IN_MPLABX.md`. MPLAB X steps: *File → New Project → Microchip Embedded → Standalone Project → Device `PIC24FJ256GB110` → Tool: **PICkit 5** → Compiler: **XC16** → name it*, then add `main.c` under *Source Files*. Set the configuration bits / oscillator (e.g. via *Window → Target Memory Views → Configuration Bits* or the MCC) so the real Fosc matches what you told ChipLlama. Build and program with MPLAB X as usual.
4. **Optional – let ChipLlama program the chip** with MPLAB IPE's command line tool (below).

### Optional, off-by-default flash step (`ipecmd.exe`)

Disabled unless **both** `CHIPLLAMA_ENABLE_FLASH=1` is set **and** you pass an explicit confirmation (`--yes` on the CLI, `confirm=true` in the API). The UI's flash endpoint returns `disabled` otherwise.

```ini
CHIPLLAMA_ENABLE_FLASH=1
IPECMD_PATH=C:\Program Files\Microchip\MPLABX\v6.20\mplab_platform\mplab_ipe\ipecmd.exe
IPECMD_TOOL=PK5            # PICkit 5 (default)
IPECMD_SERIAL=             # optional, selects one of several tools (-TS<serial>)
IPECMD_POWER_TARGET=0      # 1 = power the target from the PICkit 5 (-W)
```

```powershell
python -m chipllama flash out\proj\main.hex --yes
# runs:  ipecmd.exe -P24FJ256GB110 -TPPK5 -Fout\proj\main.hex -M -Y -OL
```

`generate --flash --yes` links an ELF, converts it with `xc16-bin2hex` and then programs it. **Programming real hardware is your decision**: read the command, check the target supply (`-W`), and expect to power-cycle/verify yourself.

## Models and backends

* **Meta Model API (default).** `https://api.meta.ai/v1`, Bearer `MODEL_API_KEY`. Default model `muse-spark-1.3`. ChipLlama uses the OpenAI-compatible **Chat Completions** endpoint (the docs also list `/responses` and `/messages`). Images go as `{"type":"image_url","image_url":{"url":"data:image/png;base64,…"}}` parts in *user* messages. `reasoning_effort` (default `high`) is sent as a top-level field; reasoning tokens count toward `max_tokens` (`CHIPLLAMA_MAX_TOKENS`, default 16000).
* **Baseten – Muse Spark Open.** `https://inference.baseten.co/v1/chat/completions`, `BASETEN_API_KEY`, model slug `BASETEN_MODEL` (default: the documented `meta-models/muse-spark-open`). Switch with `CHIPLLAMA_BACKEND=baseten`, `--backend baseten`, or the dropdown in the UI.
* Retries with back-off on 429/5xx; 4xx errors are surfaced verbatim.

## Project layout

```
chipllama/
  chipdb.py      datasheet-verified register map (fields, reset values, page numbers)
  timers.py      request parser + Timer1/2/3 solver (16/32-bit, Fcy = Fosc/2)
  cemu.py        safe C-subset register emulator (defines, casts, |= &= bit math, REGbits.FIELD)
  checker.py     deterministic checker → findings (with the fix hints fed back to the model)
  compiler.py    xc16-gcc runner + diagnostics parser (skips gracefully)
  datasheet.py   pdftoppm / PyMuPDF page rendering → multimodal inputs
  prompts.py     system + feedback prompts
  agent.py       the verify-and-self-correct loop
  debug.py       dump parser, decoder, rule engine, vision transcription
  flash.py       optional ipecmd/PICkit 5 programming (off by default)
  export.py      MPLAB X-ready folder (main.c, build.bat, Makefile, steps)
  models/        Meta Model API + Baseten (OpenAI-compatible) clients
  server.py      FastAPI app   ·   web/index.html  single-page UI   ·   cli.py  CLI
tests/           75 tests, all model/compiler/flash calls mocked
examples/        timer1_10ms.c, dead_timer_dump.txt
datasheets/      put DS39897C.pdf here (git-ignored)
```

## Tests

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

All model, compiler and programmer calls are mocked (scripted model replies, `httpx.MockTransport`, injected runners), so the tests run offline without keys or XC16. They cover the calculator, parser, emulator, every checker rule, the agent loop (first-try pass, self-correction after a checker mismatch, self-correction after compile errors, give-up, graceful skip, no-code retry, spec extraction fallback), the wire format of both backends, the debug engine, the FastAPI endpoints, MPLAB X export, ipecmd command construction and the flash safety gates.

## Limitations (please read)

* The emulator understands **init-style straight-line C**: `#define`s, assignments, `|= &= ^=`, `REGbits.FIELD =`, `_T1IE`-style aliases, casts/suffixes. Control flow is *flattened* (statements in `if`/loops are evaluated as if executed once, in order), so exotic code can be mis-judged. The compile step and your scope/logic analyser remain the final word.
* Only the **timer + timer-interrupt** domain is verified (Timer1, Timer2, Timer3, 32-bit Timer2/3, IFS0/IEC0/IPC0–2). Other peripherals aren't checked yet.
* Default Fosc is assumed to be **32 MHz** if the request doesn't say; ChipLlama cannot see your configuration bits.
* Timer4/5 share the layout of Timer2/3 but are not wired into the checker.

## Unverified assumptions (honest list)

Verified from primary sources: Meta Model API base URL/models/Chat Completions image format/`reasoning_effort` values (dev.meta.ai docs); Baseten `meta-models/muse-spark-open` slug and endpoint; all register addresses/bits/pages (from DS39897C text, extracted with `pdftotext`); `Fcy = Fosc/2`; period formula.

**Not verified / please check on your side:**

1. **Hardware and XC16 were never exercised.** The author's build box had no XC16 and no PICkit 5, so `xc16-gcc` invocation (`-mcpu=24FJ256GB110 -O1 -Wall -c`, `xc16-bin2hex`) and the `ipecmd` run were tested only with mocks.
2. **XC16 names from general knowledge, not checked against the installed headers:** `<xc.h>`, `T1CONbits`/`IFS0bits`/`IEC0bits`/`IPC0bits` members, `_T1IE`-style aliases, ISR names `_T1Interrupt`/`_T2Interrupt`/`_T3Interrupt` with `__attribute__((interrupt, auto_psv))`. The checker's ISR-name rule would give a false negative if your XC16 version differs.
3. **ipecmd flags.** `-P<part>`, `-TP<tool>`, `-F`, `-M`, `-OL`, `-W`, `-TS<serial>` and the `PK5` selector for PICkit 5 were taken from a copy of the `ipecmd -?` help text of MPLAB X v6.15 found in a third-party GitHub gist, **not** from a Microchip page I could open. `-Y` (verify) is the least certain flag. Run `ipecmd.exe -?` on your installation to confirm before enabling flash. Part-name convention `-P24FJ256GB110` (no `PIC` prefix) comes from the same source.
4. **Baseten model id.** The task brief mentioned `meta-models/muse-spark-open-1026`; I could not find that id in any documentation, so the default is the documented `meta-models/muse-spark-open`. Override `BASETEN_MODEL` if your Baseten dashboard shows another id. Baseten has no documented JSON mode/logprobs, none are used; `reasoning_effort` is not sent unless `BASETEN_REASONING_EFFORT` is set.
5. **Meta API docs/cookbook pages** beyond the pages cited above were not retrievable (404/empty), so e.g. rate limits, `/responses`-only features and exact error bodies are untested. No call to the live API has been made by the author (no key available).
6. The default `muse-spark-1.3` and `reasoning_effort=high` reflect the docs at build time (Oct 2026) and may change.

## Built during the hackathon (started Oct 27 2026)

ChipLlama was designed and written from scratch during the Meta Global AI Developer Hackathon; development started on **Oct 27 2026** (submission deadline Nov 4 2026, 9 PM ET). Everything in this repository — the register database, solver, emulator, checker, agent loop, debug engine, model clients, server/UI/CLI and tests — was created during that window (see `git log`). Third-party material used: Microchip's public datasheet DS39897C (register facts only; not redistributed), Meta Model API / Baseten docs, and the open-source Python libraries listed in `requirements.txt`.

## Security notes

* No API keys are stored in the repo; `.env` is git-ignored and `.env.example` contains only empty placeholders.
* Flashing is opt-in twice (env flag + explicit confirm).
* Uploaded screenshots and dumps go only to the configured model backend.
