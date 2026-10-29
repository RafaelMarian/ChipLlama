from pathlib import Path
from chipllama import dfp, compiler
from chipllama.config import Settings


def _pack(root, fam, ver, dev, layout):
    p = Path(root) / "Microchip" / fam / ver
    (p / "edc").mkdir(parents=True)
    (p / "edc" / f"{dev}.PIC").write_text("<x/>")
    cpu = dev[3:] if dev.startswith("PIC32") else dev
    if layout == "v4":
        (p / "xc32" / cpu).mkdir(parents=True)
    elif layout == "v5":
        (p / "xc32" / "device_files").mkdir(parents=True)
        (p / "xc32" / "device_files" / f"{cpu}.info").write_text("")
    elif layout == "xc16":
        (p / "xc16").mkdir(parents=True)
    return p


def test_xc32_v5_old_pack_warns(tmp_path):
    _pack(tmp_path, "PIC32MX_DFP", "1.7.380", "PIC32MX795F512L", "v4")
    val, warn = dfp.select("xc32", "PIC32MX795F512L", r"C:\Program Files\Microchip\xc32\v5.10\bin\xc32-gcc.exe", str(tmp_path))
    assert "device_files" in warn and "Pack" in warn and val.endswith("1.7.380")


def test_xc32_v5_new_pack_root(tmp_path):
    _pack(tmp_path, "PIC32MX_DFP", "1.7.380", "PIC32MX795F512L", "v4")
    new = _pack(tmp_path, "PIC32MX_DFP", "1.8.500", "PIC32MX795F512L", "v5")
    val, warn = dfp.select("xc32", "PIC32MX795F512L", "/opt/microchip/xc32/v5.10/bin/xc32-gcc", str(tmp_path))
    assert warn == "" and val == str(new)        # pack root, no xc32 suffix


def test_xc32_v4_old_pack_ok(tmp_path):
    p = _pack(tmp_path, "PIC32MX_DFP", "1.7.380", "PIC32MX795F512L", "v4")
    assert dfp.select("xc32", "PIC32MX795F512L", "/x/xc32/v4.35/bin/xc32-gcc", str(tmp_path)) == (str(p), "")


def test_xcdsc_preferred(tmp_path, monkeypatch):
    s = Settings(device="dsPIC33CK256MP506", xc_dsc_gcc=__file__)
    assert compiler.find_compiler(s)[0] == "xc-dsc"
    s.dspic_compiler = "xc16"
    assert compiler.find_compiler(s)[0] == "xc16"
    assert compiler.find_compiler(Settings(device="PIC24FJ256GB110", xc_dsc_gcc=__file__))[0] == "xc16"


def test_xcdsc_cmd_has_mdfp(tmp_path):
    _pack(tmp_path, "dsPIC33CK-MP_DFP", "1.13.0", "DSPIC33CK256MP506", "xc16")
    gcc = tmp_path / "xc-dsc" / "v3.31" / "bin" / "xc-dsc-gcc"
    gcc.parent.mkdir(parents=True); gcc.write_text("")
    s = Settings(device="dsPIC33CK256MP506", xc_dsc_gcc=str(gcc), mplab_packs_dir=str(tmp_path))
    seen = {}

    class P:
        returncode, stdout, stderr = 0, "", ""

    def run(cmd, **k):
        seen["cmd"] = cmd
        return P()
    compiler.compile_c("int main(void){return 0;}", s, runner=run)
    assert any(a.startswith("-mdfp=") and a.endswith("xc16") for a in seen["cmd"])
