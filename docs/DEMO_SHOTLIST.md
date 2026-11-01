# ChipLlama live demo shot-list (OBS, ~3 minutes)

Record at 1920x1080, 30 fps. Capture the browser window + a terminal
side by side, or one at a time. Narrate each beat in one or two sentences.
Nothing below needs editing: every command runs as written.

## Setup (before recording)

```powershell
.\.venv\Scripts\Activate.ps1
.\.venv\Scripts\chipllama.exe doctor     # all green: key, XC16, packs, datasheet
.\.venv\Scripts\chipllama.exe serve      # dashboard on http://127.0.0.1:8000
```

Open the dashboard in the browser. Start OBS recording.

## Beat 1 — Generate, verified (0:00–0:50)

1. Generate tab: type `Timer1 interrupt every 10 ms, priority 5`, press
   Ctrl+Enter. Say: "Plain English in. Muse writes register-level C."
2. Point at the live progress: parsed spec, attempt 1, compiler output.
   Say: "Every register is checked against the datasheet and compiled
   with the real XC16 compiler."
3. Show the VERIFIED code. Say: "Only then is it marked verified."

Backup if the API is slow: tick "Demo: self-correction (inject fault)"
before generating — attempt 1 fails on purpose, attempt 2 fixes it.

## Beat 2 — Audit a buggy project (0:50–1:30)

1. Audit tab → "Load a buggy example" → `blinky_buggy` → Run audit.
   Say: "Five errors, two warnings, each with a datasheet page."
2. Click "Apply 7 fixes", download the zip.
   Say: "One click, patched copy, never edits in place."

## Beat 3 — Blocks + Explain + Pins (1:30–2:20)

1. Blocks tab → load `blink`. Drag one block; show the C + VERIFIED badge.
   Say: "Visual programming, deterministic C, same checker."
2. Back in Audit → "Explain code" on the buggy file.
   Say: "Every register access decoded and cited."
3. Pin planner: `U1TX=RP10`, `OC1=RC1` → show the conflict message.
   Say: "It knows RC1 is input-only."

## Beat 4 — Reverse debug from a screenshot (2:20–2:50)

1. Reverse debug tab → drop `examples/debug/sfr_timer1_prescaler_bug.png`,
   intent: `Timer1 interrupt every 10 ms, toggle RA0` → Diagnose.
   Say: "Vision reads the screenshot; ChipLlama recomputes every number
   and points at the prescaler bug."

## Outro (2:50–3:00)

Settings tab → Doctor card, all green. Say: "Muse Spark, XC16, PIC —
proven, not promised." Stop recording.

## Terminal alternative (no browser)

```powershell
chipllama generate "Timer1 interrupt every 10 ms, priority 5"
chipllama audit examples\audit\blinky_buggy
chipllama debug --image examples\debug\sfr_timer1_prescaler_bug.png --intent "Timer1 interrupt every 10 ms, toggle RA0"
```
