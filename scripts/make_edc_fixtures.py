"""Build trimmed EDC fixtures (tests/fixtures/*.PIC) from full Microchip .PIC files.

The full pack files are Microchip's and are NOT redistributed; the fixtures keep only the
config-word definitions, a handful of SFRs and the programming pins needed by the tests.
Usage: python scripts/make_edc_fixtures.py /path/to/edc/*.PIC
"""
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

NS = "http://crownking/edc"
KEEP = {"T1CON", "TMR1", "PR1", "T2CON", "OSCCON", "OSCCON1", "OSCTUNE", "CLKDIV", "IFS0", "IEC0", "IPC0",
        "PIR1", "PIE1", "IPR1", "INTCON", "TRISA", "LATA", "PORTA", "RCON", "TMR1L", "TMR1H", "T1GCON"}


def trim(src: Path, dst: Path) -> None:
    ET.register_namespace("edc", NS)
    tree = ET.parse(src)
    root = tree.getroot()
    parents = {c: p for p in root.iter() for c in p}
    q = lambda t: f"{{{NS}}}{t}"
    for el in list(root.iter()):
        tag = el.tag.split("}")[-1]
        if tag in ("SFRDef", "JoinedSFRDef", "NMMRPlace"):
            if el.get(q("cname")) not in KEEP and el in parents:
                parents[el].remove(el)
        elif tag == "Pin":
            names = [v.get(q("name")) or "" for v in el.iter(q("VirtualPin"))]
            if not any(n.startswith(("PGC", "PGD", "PGEC", "PGED", "MCLR")) for n in names) and el in parents:
                parents[el].remove(el)
        elif tag in ("InterruptList", "Breakpoints", "DataSpace"):
            pass
    for el in list(root.iter()):   # drop bulky docs / unrelated sections
        if el.tag.split("}")[-1] in ("PowerPorts", "Interrupt", "EVTBits") and el in parents:
            parents[el].remove(el)
    dst.write_bytes(b"<?xml version='1.0' encoding='UTF-8'?>\n<!-- trimmed test fixture generated from Microchip "
                    b"EDC data by scripts/make_edc_fixtures.py -->\n" + ET.tostring(root))


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    for a in sys.argv[1:]:
        trim(Path(a), out / Path(a).name)
        print(out / Path(a).name, (out / Path(a).name).stat().st_size)
