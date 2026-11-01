import io, json
from pathlib import Path
from chipllama.models.base import ChatModel
from chipllama.vision_debug import (ambiguity_note, confusable, enhance_image, format_report, robust_extract, vision_debug)

INTENT = "Write LATA = 0xBBAA and read it back"


class Scripted(ChatModel):
    """Returns canned replies; extraction calls are recognised by prompt text."""
    def __init__(self, reads, explain="ok"):
        self.reads, self.calls, self.explain = list(reads), [], explain
    def complete(self, msgs, **kw):
        self.calls.append(msgs)
        if msgs[0].role == "system":
            return self.explain
        return json.dumps(self.reads.pop(0))


def regs(lata, porta="0x82AA", **extra):
    return {"kind": "registers", "registers": {"LATA": lata, "PORTA": porta, "TRISA": "0x0000", **extra}, "confidence": 0.9}


def png():
    from PIL import Image
    b = io.BytesIO(); Image.new("RGB", (40, 20), "white").save(b, "PNG"); return b.getvalue()


def test_confusables():
    assert confusable("8", "E") and confusable("0", "D") and not confusable("8", "1")
    assert "8/E" in ambiguity_note("LATA", [0x82AA, 0xE2AA]) or "E/8" in ambiguity_note("LATA", [0x82AA, 0xE2AA])


def test_enhance_upscales():
    from PIL import Image
    out = enhance_image(png(), 2)
    assert Image.open(io.BytesIO(out)).size == (80, 40)
    assert enhance_image(b"not an image") == b"not an image"
    c = Image.open(io.BytesIO(enhance_image(png(), 3, [0.0, 0.0, 0.5, 0.5])))
    assert c.size[0] < 120


def test_single_pass_costs_one_call():
    m = Scripted([regs("0xE2AA")])
    ex = robust_extract(m, [png()], passes=1)
    assert len(m.calls) == 1 and not ex.candidates


def test_agreement_no_tiebreak():
    m = Scripted([regs("0x82AA"), regs("0x82AA")])
    ex = robust_extract(m, [png()], passes=3)
    assert len(m.calls) == 2 and not ex.disputed and "transcribe exactly" in m.calls[1][0].text.lower()


def test_disagreement_two_passes_disputed():
    ex = robust_extract(Scripted([regs("0xE2AA"), regs("0x82AA")]), [png()], passes=2)
    assert ex.disputed == {"LATA": [0xE2AA, 0x82AA]} and ex.confidence <= 0.4


def test_tiebreak_majority_vote_only_disputed():
    m = Scripted([regs("0xE2AA"), regs("0x82AA"), {"kind": "registers", "registers": {"LATA": "0x82AA"}}])
    ex = robust_extract(m, [png()], passes=3)
    assert len(m.calls) == 3 and "LATA" in m.calls[2][0].text and "PORTA" not in m.calls[2][0].text
    assert ex.other["LATA"] == 0x82AA and not ex.disputed and ex.candidates["LATA"] == [0xE2AA, 0x82AA]


def test_tiebreak_split_stays_disputed():
    m = Scripted([regs("0xE2AA"), regs("0x82AA"), {"kind": "registers", "registers": {"LATA": "0xB2AA"}}])
    ex = robust_extract(m, [png()], passes=3)
    assert ex.disputed["LATA"] == [0xE2AA, 0x82AA, 0xB2AA]


def test_real_case_written_value_resolves_misread():
    # the live failure: pass 1 read 0xE2AA (8->E); pass 2 reads 0x82AA. 0xBBAA & 0xC6FF = 0x82AA -> not a bug
    m = Scripted([regs("0xE2AA"), regs("0x82AA")], explain="not a bug")
    rep = vision_debug([png()], INTENT, model=m, device="PIC24FJ256GB110")
    assert rep.verdict.startswith("OK"), rep.verdict
    assert rep.extraction.other["LATA"] == 0x82AA
    assert any("read as 0xE2AA or 0x82AA" in n and "chose 0x82AA" in n for n in rep.notes)


def test_implemented_mask_resolves_without_intent():
    rep = vision_debug([png()], "", model=Scripted([regs("0xE2AA", porta="0xE2AA"), regs("0x82AA", porta="0x82AA")]),
                       device="PIC24FJ256GB110", explain=False)
    assert rep.extraction.registers.get("LATA", rep.extraction.other.get("LATA")) == 0x82AA


def test_bug_on_disputed_value_is_uncertain():
    # unknown write explanation: both readings inconsistent with written 0x0003 -> rests on disputed value -> UNCERTAIN
    a = {"kind": "registers", "registers": {"LATA": "0x0008"}, "confidence": 0.9}
    b = {"kind": "registers", "registers": {"LATA": "0x000B"}, "confidence": 0.9}
    rep = vision_debug([png()], "Write LATA = 0x0003", model=Scripted([a, b]), device="PIC24FJ256GB110", explain=False)
    assert rep.verdict.startswith("UNCERTAIN"), rep.verdict
    assert any("read as 0x0008 or 0x000B" in r.value for r in rep.analysis.rows)
    assert "pass 2" in format_report(rep)


def test_bug_with_clear_values_still_bug():
    a = {"kind": "registers", "registers": {"LATA": "0x0001"}, "confidence": 0.9}
    rep = vision_debug([png()], "Write LATA = 0x0003", model=Scripted([a, a]), device="PIC24FJ256GB110", explain=False)
    assert rep.verdict.startswith("BUG")


def test_settings_vision_passes(tmp_path):
    from chipllama.config import save_settings
    import pytest
    save_settings({"vision_passes": 3}, tmp_path / "s.json")
    with pytest.raises(ValueError):
        save_settings({"vision_passes": 5}, tmp_path / "s.json")
