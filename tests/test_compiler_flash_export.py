import subprocess
from pathlib import Path
from types import SimpleNamespace

from chipllama.compiler import compile_c, parse_diagnostics, find_tool
from chipllama.config import Settings
from chipllama.export import export_project
from chipllama.flash import build_command, flash, find_ipecmd, part_name
from chipllama.timers import TimerSpec, solve
from conftest import GOOD_T1

GCC_ERR = """main.c: In function 'timer1_init':
main.c:12:5: error: 'T1CONbit' undeclared (first use in this function)
main.c:14:9: warning: unused variable 'x' [-Wunused-variable]
"""


def test_parse_diagnostics():
    d = parse_diagnostics(GCC_ERR)
    assert [(x.severity, x.line) for x in d] == [("error", 12), ("warning", 14)]
    assert parse_diagnostics("C:\\Users\\me\\AppData\\Local\\Temp\\chipllama_x\\main.c:3:1: error: oops")[0].line == 3


def test_compile_skipped_when_toolchain_missing(monkeypatch):
    monkeypatch.setattr("chipllama.compiler.find_tool", lambda *a, **k: None)
    r = compile_c(GOOD_T1, Settings())
    assert r.status == "skipped" and r.ok and "xc16-gcc not found" in r.reason


def test_compile_invokes_xc16_with_mcpu(monkeypatch, tmp_path):
    monkeypatch.setattr("chipllama.compiler.find_tool", lambda *a, **k: "C:/xc16/bin/xc16-gcc.exe")
    got = {}

    def runner(cmd, **kw):
        got["cmd"] = cmd
        return SimpleNamespace(returncode=1, stdout="", stderr=GCC_ERR)

    r = compile_c(GOOD_T1, Settings(), runner=runner, workdir=str(tmp_path))
    assert "-mcpu=24FJ256GB110" in got["cmd"] and "-c" in got["cmd"]
    assert r.status == "error" and "undeclared" in r.feedback_text()


def test_link_builds_hex(monkeypatch, tmp_path):
    monkeypatch.setattr("chipllama.compiler.find_tool", lambda n, c="": f"/x/{n}")
    cmds = []

    def runner(cmd, **kw):
        cmds.append(cmd)
        if cmd[0].endswith("xc16-bin2hex"):
            (tmp_path / "main.hex").write_text(":00000001FF\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    r = compile_c(GOOD_T1, Settings(), runner=runner, link=True, workdir=str(tmp_path))
    assert r.status == "ok" and r.hex_path.endswith("main.hex") and cmds[1][0].endswith("xc16-bin2hex")
    link = cmds[0]
    assert "-mcpu=24FJ256GB110" in link and "-omf=elf" in link and "-c" not in link
    assert any(a.startswith("-Wl,--script=p24FJ256GB110.gld,--heap=0") for a in link)


def test_link_without_hex_is_not_flashable(monkeypatch, tmp_path):
    monkeypatch.setattr("chipllama.compiler.find_tool", lambda n, c="": f"/x/{n}")
    r = compile_c(GOOD_T1, Settings(), runner=lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""),
                  link=True, workdir=str(tmp_path))
    assert r.hex_path is None


# ----- flashing (PICkit 5, ipecmd)
def test_part_name_drops_pic_prefix():
    assert part_name("PIC24FJ256GB110") == "24FJ256GB110"


def test_ipecmd_command_defaults_to_pickit5():
    cmd = build_command("ipecmd.exe", "C:/p/main.hex", Settings())
    assert cmd == ["ipecmd.exe", "-P24FJ256GB110", "-TPPK5", "-FC:/p/main.hex", "-M", "-Y", "-OL"]


def test_ipecmd_tool_options():
    assert "-TPPK4" in build_command("i", "m.hex", Settings(ipecmd_tool="PICkit 4".replace(" ", "")))
    assert "-TSBUR123" in build_command("i", "m.hex", Settings(ipecmd_serial="BUR123"))
    assert "-W" in build_command("i", "m.hex", Settings(ipecmd_power_target=True))


def test_flash_is_off_by_default_and_needs_confirmation(tmp_path):
    hexf = tmp_path / "main.hex"
    hexf.write_text(":00000001FF\n")
    ran = []
    runner = lambda *a, **k: ran.append(a) or SimpleNamespace(returncode=0, stdout="", stderr="")
    assert flash(str(hexf), Settings(), confirmed=True, runner=runner).status == "disabled"          # env flag off
    assert flash(str(hexf), Settings(enable_flash=True), confirmed=False, runner=runner).status == "disabled"  # no --yes
    assert not ran


def test_flash_runs_when_enabled_and_confirmed(tmp_path, monkeypatch):
    hexf = tmp_path / "main.hex"
    hexf.write_text(":00000001FF\n")
    monkeypatch.setattr("chipllama.flash.find_ipecmd", lambda *_: "ipecmd.exe")
    runner = lambda cmd, **k: SimpleNamespace(returncode=0, stdout="Programming/Verify complete", stderr="")
    r = flash(str(hexf), Settings(enable_flash=True), confirmed=True, runner=runner)
    assert r.status == "ok" and "-TPPK5" in r.command and "complete" in r.output
    monkeypatch.setattr("chipllama.flash.find_ipecmd", lambda *_: None)
    assert flash(str(hexf), Settings(enable_flash=True), confirmed=True).status == "skipped"


# ----- MPLAB X export
def test_export_project(tmp_path):
    spec = TimerSpec(timer=1, period_s=0.01)
    paths = export_project(GOOD_T1, tmp_path / "proj", "PIC24FJ256GB110", spec, solve(spec))
    names = {p.name for p in paths}
    assert {"main.c", "build.bat", "Makefile", "OPEN_IN_MPLABX.md"} == names
    md = (tmp_path / "proj" / "OPEN_IN_MPLABX.md").read_text()
    assert "PICkit 5" in md and "-P24FJ256GB110 -TPPK5" in md and "PR=19999" in md
    bat = (tmp_path / "proj" / "build.bat").read_bytes()
    assert b"-mcpu=24FJ256GB110" in bat and b"\r\n" in bat
    assert (tmp_path / "proj" / "main.c").read_text().startswith("#include <xc.h>")
