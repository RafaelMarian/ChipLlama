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

    def to_dict(self):
        return {"n": self.n, "code": self.code, "check": self.check.to_dict(), "compile": self.compile.to_dict(),
                "seconds": round(self.seconds, 2)}


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
                "verification_level": ("registers+compile" if self.status == "verified" and self.compiled else
                                       "registers only (compiler unavailable)" if self.status == "verified" else "none")}


def resolve_spec(request: str, model: ChatModel | None, notes: list[str]) -> TimerSpec:
    spec = parse_request(request)
    notes.extend(spec.notes)
    if spec.period_s is None and model is not None:
        try:
            txt = model.complete([Message("user", prompts.EXTRACT_SPEC + request)])
            m = re.search(r"\{.*\}", txt, re.S)
            j = json.loads(m.group(0)) if m else {}
            if j.get("period_ms"):
                spec.period_s = float(j["period_ms"]) / 1e3
            elif j.get("freq_hz"):
                spec.period_s = 1.0 / float(j["freq_hz"])
            if j.get("timer") in (1, 2, 3):
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
    if spec.period_s is None:
        raise ValueError("could not determine the timer period from the request (say e.g. 'every 10 ms')")
    return spec


def run_agent(request: str, model: ChatModel, settings: Settings, *, max_iters: int = 4, use_images: bool = True,
              hint: bool = False, spec: TimerSpec | None = None,
              compile_fn: Callable[..., CompileResult] = compile_c,
              on_event: Callable[[str, dict], None] | None = None) -> AgentResult:
    emit = on_event or (lambda *_: None)
    res = AgentResult("error", request, backend=getattr(model, "name", "?"))
    try:
        spec = spec or resolve_spec(request, model, res.notes)
        if any(n.startswith("no clock stated") for n in spec.notes):
            spec.fosc_hz = settings.profile().fosc_hz
            spec.notes = [n for n in spec.notes if not n.startswith("no clock stated")] + [
                f"no clock stated: using board profile '{settings.clock_profile}' -> Fosc = {spec.fosc_hz/1e6:g} MHz"]
        res.spec = spec
        res.solution = solve(spec)
    except ValueError as e:
        res.error = str(e)
        return res
    emit("spec", {"spec": spec.to_dict(), "reference": res.solution.to_dict()})

    try:
        dev = chipdb.device_for(settings)
    except Exception as e:   # pragma: no cover - defensive
        dev, _ = None, res.notes.append(f"device data unavailable: {e}")
    builtin = chipdb.is_builtin_family(settings.device)
    if not builtin and dev is None:
        res.error = (f"no device data for {settings.device}: install its MPLAB X device pack or set "
                     "MPLAB_PACKS_DIR to the packs folder")
        return res
    if dev is not None:
        res.notes.append(f"device data: {dev.source} ({dev.family}, {dev.compiler})")

    imgs: list[tuple[int, bytes]] = []
    note = ""
    if use_images and builtin:
        imgs, note = load_page_images(pages_for_spec(spec), settings)
        res.notes.append(note)
    image_note = (f"Attached are datasheet page images, in order: {', '.join('p%d' % p for p, _ in imgs)}." if imgs else "")
    if builtin:
        msgs = [Message("system", prompts.SYSTEM),
                Message("user", prompts.first_user_prompt(request, spec, res.solution if hint else None, image_note,
                                                          settings.profile(), settings.ics),
                        [b for _, b in imgs])]
    else:
        msgs = [Message("system", prompts.generic_system(dev)),
                Message("user", prompts.generic_user_prompt(request, spec, dev, settings.ics))]
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
        if builtin:
            chk = check_timer(code, spec, profile=settings.profile(), ics=settings.ics)
            if dev is not None and dev.source != "built-in":
                check_device(code, dev, ics=settings.ics, rep=chk, require_config=False)
        else:
            chk = check_device(code, dev, ics=settings.ics)
            chk.add("info", "TIMING_UNVERIFIED", f"timer period/clock math is not emulated for {dev.family} yet; "
                    "configuration bits and register/bitfield names were verified against the EDC data.")
        cmp_ = compile_fn(code, settings)
        att = Attempt(i, code, chk, cmp_, time.time() - t0, text)
        res.attempts.append(att)
        emit("attempt", att.to_dict())
        if chk.ok and cmp_.ok:
            res.status = "verified"
            return res
        msgs += [Message("assistant", text), Message("user", prompts.feedback_prompt(
            cmp_.feedback_text() if cmp_.status == "error" else "", chk.feedback_text()))]
    res.status = "failed"
    if not res.attempts:
        res.error = "model never produced code"
    return res
