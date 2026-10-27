import json

from chipllama import cli
from conftest import GOOD_T1, ScriptedModel, compile_ok, fence


def test_calc_and_doctor(capsys, monkeypatch, tmp_path):
    assert cli.main(["calc", "Timer1 every 10 ms at 32 MHz"]) == 0
    out = capsys.readouterr().out
    assert "19999" in out and "0x8010" in out
    monkeypatch.chdir(tmp_path)
    assert cli.main(["doctor"]) in (0, 1)
    assert "xc16-gcc" in capsys.readouterr().out


def test_generate_writes_files_and_exports(capsys, monkeypatch, tmp_path):
    m = ScriptedModel([fence(GOOD_T1)])
    monkeypatch.setattr(cli, "make_model", lambda *a, **k: m)
    monkeypatch.setattr("chipllama.agent.compile_c", compile_ok)
    monkeypatch.setenv("DATASHEET_PDF", str(tmp_path / "none.pdf"))
    out = tmp_path / "out"
    rc = cli.main(["generate", "Timer1 interrupt every 10 ms at 32 MHz priority 5",
                   "--out", str(out), "--export", str(tmp_path / "proj"), "--no-images"])
    assert rc == 0
    assert out.exists() and "TCKPS" in out.read_text() and (tmp_path / "proj" / "OPEN_IN_MPLABX.md").exists()
