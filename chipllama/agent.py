"""The verifiable agent loop:

   request -> parse spec -> (register facts + datasheet page images) -> Muse generates C
          -> checker (deterministic register emulation) + xc16-gcc compile
          -> findings/errors fed back -> regenerate ... until verified or max iterations.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from .checker import CheckReport, check_timer, check_device
from . import chipdb
from .compiler import CompileResult, compile_c
from .config import Settings
from .datasheet import load_page_images, pages_for_spec
from .models.base import ChatModel, Message, ModelError
from . import prompts
from . import uart as uartmod
from . import peripherals
from .cemu import emulate
from .faults import DEMO_LABEL, inject_fault as make_fault
from .timers import TimerSpec, TimerSolution, parse_request, solve

_FENCE = re.compile(r"```(?:c|C|cpp|xc16)?[ \t]*\n(.*?)```", re.S)


def extract_code(text: str) -> str | None:
    blocks = _FENCE.findall(text)
    if blocks:
        return max(blocks, key=len).strip("\n") + "\n"
    if "#include" in text and "{" in text:
        return text.strip() + "\n"
    return None


@dataclass
class Attempt:
    n: int
    code: str
    check: CheckReport
    compile: CompileResult
    seconds: float
    model_text: str = ""
    injected_fault: dict | None = None

    def to_dict(self):
        return {"n": self.n, "code": self.code, "check": self.check.to_dict(), "compile": self.compile.to_dict(),
                "seconds": round(self.seconds, 2), "injected_fault": self.injected_fault}


@dataclass
class AgentResult:
    status: str                       # "verified" | "failed" | "error"
    request: str
    spec: TimerSpec | None = None
    solution: TimerSolution | None = None
    attempts: list[Attempt] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: str = ""
    backend: str = ""
    uart: "uartmod.UartSpec | None" = None
    fault: dict | None = None
    peripherals: dict = field(default_factory=dict)

    @property
    def code(self) -> str:
        return self.attempts[-1].code if self.attempts else ""

    @property
    def compiled(self) -> bool:
        return bool(self.attempts) and self.attempts[-1].compile.status == "ok"

    def to_dict(self):
        return {"status": self.status, "request": self.request, "backend": self.backend,
                "spec": self.spec.to_dict() if self.spec else None,
                "reference": self.solution.to_dict() if self.solution else None,
                "attempts": [a.to_dict() for a in self.attempts], "notes": self.notes, "error": self.error,
                "compiled": self.compiled, "code": self.code,
                "uart": self.uart.to_dict() if self.uart else None,
                "peripherals": {k: v.to_dict() for k, v in self.peripherals.items()}, "demo_fault_injection": self.fault,
                "verification_level": ("registers+compile" if self.status == "verified" and self.compiled else
                                       "registers only (compiler unavailable)" if self.status == "verified" else "none")}


def resolve_spec(request: str, model: ChatModel | None, notes: list[str]) -> TimerSpec:
    spec = parse_request(request)
    notes.extend(spec.notes)
    if spec.period_s is None and model is not None and not spec.gated:
        try:
            txt = model.complete([Message("user", prompts.EXTRACT_SPEC + request)])
            m = re.search(r"\{.*\}", txt, re.S)
            j = json.loads(m.group(0)) if m else {}
            if j.get("period_ms"):
                spec.period_s = float(j["period_ms"]) / 1e3
            elif j.get("freq_hz"):
                spec.period_s = 1.0 / float(j["freq_hz"])
            if j.get("timer") in (1, 2, 3, 4, 5):
                spec.timer = int(j["timer"])
            spec.bits32 = bool(j.get("bits32", spec.bits32))
            if j.get("fosc_mhz"):
                spec.fosc_hz = float(j["fosc_mhz"]) * 1e6
            if "interrupt" in j:
                spec.interrupt = bool(j["interrupt"])
            if j.get("priority"):
                spec.priority = int(j["priority"])
            notes.append("period/timer extracted by the model (regex could not find them)")
        except (ModelError, ValueError, json.JSONDecodeError) as e:
            notes.append(f"model spec extraction failed: {e}")
    if spec.period_s is None and not spec.gated:
        raise ValueError("could not determine the timer period from the request (say e.g. 'every 10 ms')")
    return spec


_TIMER_WORDS = re.compile(r"\btimer\s*\d|\bTMR\d|\bT\dCON|\bPR\d\b|\binterrupt every\b", re.I)

# finding code -> datasheet pages the agent "re-reads" on failure
_FINDING_PAGES = {"PERIOD": "timer", "PR_UNSET": "timer", "TCKPS": "timer", "TON": "timer", "TCS": "timer",
                  "T32": "timer", "BRG": [200, 202], "BRG_UNSET": [200], "UARTEN": [202], "UTXEN": [204],
                  "PPS_TX": [138], "PPS_RX": [147], "PPS_LOCKED": [123, 136], "TRIS_RX": [], "ANALOG_RX": [273],
                  "IE": [90], "IP": [97], "ISR": [84], "ISR_FLAG": [84], "FNOSC_MISSING": [123, 124, 125],
                  "CLOCK": [123, 124, 125]}


def pages_for_findings(codes: list[str], spec: TimerSpec | None, uspec=None) -> list[int]:
    pages: list[int] = []
    table = {**_FINDING_PAGES, **peripherals.finding_pages()}
    for c in codes:
        v = table.get(c)
        if v is None:
            for k, vv in sorted(table.items(), key=lambda kv: -len(kv[0])):
                if c.startswith(k):
                    v = vv
                    break
        if v == "timer":
            v = pages_for_spec(spec)[:2] if spec else []
        if c == "PPS_TX" and uspec is not None and uspec.tx_rp is not None and uspec.tx_rp <= uartmod.OUTPUT_RP_MAX:
            v = list(v or []) + [chipdb.REGISTERS[uartmod.rpor_field(uspec.tx_rp)[0]].page]
        pages += list(v or [])
    return list(dict.fromkeys(pages))[:4]


def run_agent(request: str, model: ChatModel, settings: Settings, *, max_iters: int = 4, use_images: bool = True,
              hint: bool = False, spec: TimerSpec | None = None,
              compile_fn: Callable[..., CompileResult] = compile_c,
              on_event: Callable[[str, dict], None] | None = None, inject_fault: bool = False,
              context: str = "") -> AgentResult:
    emit = on_event or (lambda *_: None)
    res = AgentResult("error", request, backend=getattr(model, "name", "?"))
    builtin = chipdb.is_builtin_family(settings.device)
    uspec = uartmod.parse_uart_request(request, settings.profile().fosc_hz / 2) if builtin else None
    fcy0 = peripherals.fcy_from_text(request, default=None) or settings.profile().fosc_hz / 2
    pspecs = peripherals.parse_all(request, fcy0) if builtin else {}
    ptimer = peripherals.timer_spec(pspecs) if pspecs else None
    want_timer = (spec is not None or ptimer is not None
                  or (bool(_TIMER_WORDS.search(request)) and not peripherals.claims_timer(pspecs))
                  or (uspec is None and not pspecs)
                  or ("gpio" in pspecs and parse_request(request).period_s is not None))
    res.peripherals = pspecs
    for sp in pspecs.values():
        res.notes.extend(getattr(sp, "notes", []))
    res.uart = uspec
    if uspec:
        res.notes.extend(uspec.notes)
    try:
        if want_timer:
            spec = spec or ptimer or resolve_spec(request, model, res.notes)
            if any(n.startswith("no clock stated") for n in spec.notes):
                spec.fosc_hz = settings.profile().fosc_hz
                spec.notes = [n for n in spec.notes if not n.startswith("no clock stated")] + [
                    f"no clock stated: using board profile '{settings.clock_profile}' -> Fosc = {spec.fosc_hz/1e6:g} MHz"]
            res.spec = spec
            res.solution = solve(spec)
            if uspec:
                uspec.fcy = spec.fcy
    except ValueError as e:
        res.error = str(e)
        return res
    emit("spec", {"spec": spec.to_dict() if res.spec else None,
                  "reference": res.solution.to_dict() if res.solution else None,
                  "uart": uspec.to_dict() if uspec else None,
                  "peripherals": {k: v.to_dict() for k, v in pspecs.items()}, "demo_fault_injection": bool(inject_fault)})
    if inject_fault:
        res.notes.append(f"{DEMO_LABEL} enabled: the first model attempt will be corrupted on purpose to demonstrate self-correction")

    try:
        dev = chipdb.device_for(settings)
    except Exception as e:   # pragma: no cover - defensive
        dev, _ = None, res.notes.append(f"device data unavailable: {e}")
    if not builtin and dev is None:
        res.error = (f"no device data for {settings.device}: install its MPLAB X device pack or set "
                     "MPLAB_PACKS_DIR to the packs folder")
        return res
    if dev is not None:
        res.notes.append(f"device data: {dev.source} ({dev.family}, {dev.compiler})")

    imgs: list[tuple[int, bytes]] = []
    note = ""
    if use_images and builtin:
        pages = ((pages_for_spec(spec) if res.spec else []) + (uartmod.pages_for_uart(uspec) if uspec else [])
                 + peripherals.pages_all(pspecs))
        imgs, note = load_page_images(list(dict.fromkeys(pages))[:8], settings)
        res.notes.append(note)
    image_note = (f"Attached are datasheet page images, in order: {', '.join('p%d' % p for p, _ in imgs)}." if imgs else "")
    if builtin:
        msgs = [Message("system", prompts.SYSTEM),
                Message("user", prompts.first_user_prompt(request, res.spec, res.solution if hint else None, image_note,
                                                          settings.profile(), settings.ics, uspec=uspec,
                                                          pspecs=pspecs),
                        [b for _, b in imgs])]
    else:
        msgs = [Message("system", prompts.generic_system(dev)),
                Message("user", prompts.generic_user_prompt(request, spec, dev, settings.ics))]
    if context.strip():
        msgs[1].text += ("\n\nCONTEXT FROM REVERSE DEBUG OF THE REAL BOARD (deterministic, trust it; the new code must fix this):\n"
                         + context.strip()[:4000])
        res.notes.append("regenerating with reverse-debug fix context")
    for i in range(1, max_iters + 1):
        t0 = time.time()
        emit("model_call", {"n": i, "backend": getattr(model, "name", "?")})
        try:
            text = model.complete(msgs)
        except ModelError as e:
            res.error = str(e)
            res.status = "error"
            return res
        code = extract_code(text)
        if code is None:
            res.notes.append(f"attempt {i}: model returned no code block")
            msgs += [Message("assistant", text), Message("user", "No ```c code block found. Return the complete C file in one ```c block.")]
            emit("attempt", {"n": i, "error": "no code"})
            continue
        fault = None
        if inject_fault and res.fault is None:
            tnum = ((23 if res.spec.base == 2 else 45) if res.spec.bits32 else res.spec.timer) if res.spec else None
            f = make_fault(code, timer=tnum, uart=uspec is not None)
            if f is None:
                res.fault = {"label": DEMO_LABEL, "kind": "none", "description": "no injectable pattern found; attempt left unchanged", "diff": ""}
                emit("fault", dict(res.fault, n=i))
            else:
                fault = res.fault = f.to_dict()
                code = f.corrupted
                text = "```c\n" + code + "```"          # the model sees the corrupted file as its previous answer
                res.notes.append(f"{DEMO_LABEL} on attempt {i}: {f.description}")
                emit("fault", dict(fault, n=i))
        if builtin:
            if res.spec:
                chk = check_timer(code, res.spec, profile=settings.profile(), ics=settings.ics)
                if uspec:
                    uartmod.check_uart(code, uspec, chk)
                peripherals.check_all(code, pspecs, chk, emulate(code))
            elif uspec:
                chk = uartmod.check_uart_full(code, uspec, profile=settings.profile(), ics=settings.ics)
                peripherals.check_all(code, pspecs, chk, emulate(code))
            else:
                chk = peripherals.check_full(code, pspecs, fcy=fcy0, profile=settings.profile(), ics=settings.ics)
            if dev is not None and dev.source != "built-in":
                check_device(code, dev, ics=settings.ics, rep=chk, require_config=False)
        else:
            chk = check_device(code, dev, ics=settings.ics)
            chk.add("info", "TIMING_UNVERIFIED", f"timer period/clock math is not emulated for {dev.family} yet; "
                    "configuration bits and register/bitfield names were verified against the EDC data.")
        cmp_ = compile_fn(code, settings)
        att = Attempt(i, code, chk, cmp_, time.time() - t0, text, injected_fault=fault)
        res.attempts.append(att)
        emit("attempt", att.to_dict())
        if chk.ok and cmp_.ok:
            res.status = "verified"
            return res
        if i == max_iters:
            break
        codes = [f.code for f in chk.errors] + (["COMPILE"] if cmp_.status == "error" else [])
        fb_pages = pages_for_findings(codes, res.spec, uspec) if builtin else []
        fb_imgs: list[tuple[int, bytes]] = []
        if fb_pages and use_images:
            fb_imgs, _ = load_page_images(fb_pages, settings)
        emit("reflect", {"n": i, "errors": codes, "pages": fb_pages, "images_attached": [p for p, _ in fb_imgs]})
        fb = prompts.feedback_prompt(cmp_.feedback_text() if cmp_.status == "error" else "", chk.feedback_text())
        if fb_pages:
            fb += "\n\nRe-read datasheet " + ", ".join(f"p.{p}" for p in fb_pages) + (
                " (page images attached)." if fb_imgs else ".")
        msgs += [Message("assistant", text), Message("user", fb, [b for _, b in fb_imgs])]
    res.status = "failed"
    if not res.attempts:
        res.error = "model never produced code"
    return res
