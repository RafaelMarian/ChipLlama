"""Microchip EDC device files (`<DEVICE>.PIC` XML from MPLAB X device family packs).

Every PIC (8-bit PIC10/12/16/18, 16-bit PIC24/dsPIC30/33, 32-bit PIC32) ships one of these in its
`<Family>_DFP/<ver>/edc/` folder. They list each configuration word (DCRDef) with its settings and
allowed values (DCRFieldDef / DCRFieldSemantic) and every SFR with its bitfields (SFRDef / SFRFieldDef).
Bit positions are implicit: fields are listed LSB first, `AdjustPoint offset=n` skips n unused bits.

Only the XML is read; nothing from the packs is redistributed.
"""
from __future__ import annotations

import glob
import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from pathlib import Path

NS = "{http://crownking/edc}"
SCHEMA_VERSION = 1


def _a(el, name, default=""):
    return el.get(NS + name, default)


def _int(s, default=0) -> int:
    try:
        return int(str(s), 0)
    except (TypeError, ValueError):
        return default


def _tag(el) -> str:
    return el.tag.replace(NS, "")


def _true(s) -> bool:
    return str(s).lower() == "true"


# ------------------------------------------------------------------ data model
@dataclass
class ConfigValue:
    name: str
    desc: str
    value: int | None = None          # field value this setting encodes (None if not a simple equality)


@dataclass
class ConfigSetting:
    name: str                          # pragma name (cname), e.g. FNOSC
    word: str                          # configuration word, e.g. CONFIG2 / FOSCSEL / CONFIG1H
    desc: str
    lsb: int
    width: int
    values: list[ConfigValue] = field(default_factory=list)
    default: str | None = None         # value name selected by the erased/default word
    range: tuple[int, int] | None = None   # numeric setting (e.g. BSLIM, DMTIVTL) instead of named values
    hidden: bool = False

    def allowed(self) -> list[str]:
        return [v.name for v in self.values]

    def accepts(self, v: str) -> bool:
        if v in self.allowed():
            return True
        if self.range is not None:
            n = _int(v.rstrip("uUlL"), None)
            return n is not None and self.range[0] <= n <= self.range[1]
        return False


@dataclass
class ConfigWord:
    name: str
    address: int
    default: int
    width: int


@dataclass
class SFRField:
    name: str
    lsb: int
    width: int
    desc: str = ""


@dataclass
class SFR:
    name: str
    address: int
    width: int
    fields: list[SFRField] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    reset: str = ""

    def field_names(self) -> set[str]:
        out = set()
        for f in self.fields:
            out.add(f.name)
            if f.width > 1:     # XC headers also expose single bits TCKPS0, TCKPS1 ...
                out.update(f"{f.name}{i}" for i in range(f.width))
        return out


@dataclass
class Device:
    name: str
    arch: str                     # EDC arch: 16xxxx, 18xxxx, 24xxxx, 30xxxx, 33xxxx, 32xxxx ...
    family: str                   # PIC10/PIC12/PIC16/PIC18/PIC24/dsPIC30/dsPIC33/PIC32
    compiler: str                 # xc8 | xc16 | xc32
    words: list[ConfigWord] = field(default_factory=list)
    settings: list[ConfigSetting] = field(default_factory=list)
    sfrs: dict[str, SFR] = field(default_factory=dict)
    program_bytes: int = 0
    data_bytes: int = 0
    eeprom_bytes: int = 0
    icsp_pairs: list[int] = field(default_factory=list)   # PGEC1/PGED1 -> 1 ...
    datasheet_id: str = ""
    source: str = ""              # path of the .PIC file, or "built-in"

    # -------------------------------------------------- helpers
    def setting(self, name: str) -> ConfigSetting | None:
        for s in self.settings:
            if s.name == name:
                return s
        return None

    def visible_settings(self) -> list[ConfigSetting]:
        return [s for s in self.settings if not s.hidden]

    def sfr(self, name: str) -> SFR | None:
        if name in self.sfrs:
            return self.sfrs[name]
        for r in self.sfrs.values():
            if name in r.aliases:
                return r
        return None

    @property
    def mcpu(self) -> str:
        return compiler_cpu(self.name)

    def to_json(self) -> dict:
        d = asdict(self)
        d["_schema"] = SCHEMA_VERSION
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Device":
        d = dict(d)
        d.pop("_schema", None)
        d["words"] = [ConfigWord(**w) for w in d.get("words", [])]
        sets = []
        for s in d.get("settings", []):
            s = dict(s)
            s["values"] = [ConfigValue(**v) for v in s.get("values", [])]
            if s.get("range") is not None:
                s["range"] = tuple(s["range"])
            sets.append(ConfigSetting(**s))
        d["settings"] = sets
        d["sfrs"] = {k: SFR(**{**v, "fields": [SFRField(**f) for f in v.get("fields", [])]})
                     for k, v in d.get("sfrs", {}).items()}
        return cls(**d)


# ------------------------------------------------------------------ family / compiler
def family_of(name: str, arch: str = "") -> str:
    n = name.upper()
    for pre, fam in (("DSPIC33", "dsPIC33"), ("DSPIC30", "dsPIC30"), ("PIC24", "PIC24"), ("PIC32", "PIC32"),
                     ("PIC18", "PIC18"), ("PIC16", "PIC16"), ("PIC12", "PIC12"), ("PIC10", "PIC10")):
        if n.startswith(pre):
            return fam
    a = arch[:2]
    return {"16": "PIC16", "18": "PIC18", "24": "PIC24", "30": "dsPIC30", "33": "dsPIC33", "32": "PIC32"}.get(a, "unknown")


def compiler_for(family_or_name: str) -> str:
    f = family_or_name if family_or_name in FAMILIES else family_of(family_or_name)
    return FAMILIES.get(f, "xc16")


FAMILIES = {"PIC10": "xc8", "PIC12": "xc8", "PIC16": "xc8", "PIC18": "xc8", "PIC24": "xc16",
            "dsPIC30": "xc16", "dsPIC33": "xc16", "PIC32": "xc32"}


def compiler_cpu(name: str) -> str:
    """Device name as the compiler wants it: xc8-cc -mcpu=18F45K22, xc16-gcc -mcpu=33CK256MP506,
    xc32-gcc -mprocessor=32MX795F512L."""
    n = name.upper()
    for pre in ("DSPIC", "PIC"):
        if n.startswith(pre):
            return n[len(pre):]
    return n


# ------------------------------------------------------------------ parser
_WHEN = re.compile(r"\(\s*field\s*&\s*(0x[0-9a-fA-F]+|\d+)\s*\)\s*==\s*(0x[0-9a-fA-F]+|\d+)")


def _walk_fields(mode_el, field_tag):
    """Yield (element, lsb) for field definitions in one mode, honouring AdjustPoint gaps."""
    pos = 0
    for ch in mode_el:
        t = _tag(ch)
        if t == "AdjustPoint":
            pos += _int(_a(ch, "offset"), 0)
        elif t == field_tag:
            w = _int(_a(ch, "nzwidth"), 1)
            yield ch, pos
            pos += w


def _semantic_value(when: str, width: int):
    m = _WHEN.search(when or "")
    if not m:
        return None, None
    return _int(m.group(1)), _int(m.group(2))


def parse_edc(path: str | os.PathLike) -> Device:
    path = str(path)
    root = ET.parse(path).getroot()
    name = _a(root, "name")
    arch = _a(root, "arch")
    fam = family_of(name, arch)
    dev = Device(name=name, arch=arch, family=fam, compiler=compiler_for(fam),
                 datasheet_id=_a(root, "dsid"), source=path)

    # --- configuration words: first occurrence of each word wins (dual-partition dsPICs repeat them)
    seen_words: set[str] = set()
    for dcr in root.iter(NS + "DCRDef"):
        wname = _a(dcr, "cname") or _a(dcr, "name")
        if wname in seen_words:
            continue
        seen_words.add(wname)
        width = _int(_a(dcr, "nzwidth"), 8)
        wdef = _int(_a(dcr, "default") or _a(dcr, "factorydefault"), (1 << width) - 1)
        dev.words.append(ConfigWord(wname, _int(_a(dcr, "_addr")), wdef, width))
        mode = dcr.find(f"{NS}DCRModeList/{NS}DCRMode")
        if mode is None:
            continue
        for fd, lsb in _walk_fields(mode, "DCRFieldDef"):
            w = _int(_a(fd, "nzwidth"), 1)
            fval = (wdef >> lsb) & ((1 << w) - 1)
            st = ConfigSetting(name=_a(fd, "cname") or _a(fd, "name"), word=wname, desc=_a(fd, "desc"),
                               lsb=lsb, width=w, hidden=_true(_a(fd, "ishidden")) or _true(_a(fd, "islanghidden")))
            for sem in fd:
                t = _tag(sem)
                if t == "DCRFieldSemantic":
                    if _true(_a(sem, "islanghidden")):
                        continue
                    mask, val = _semantic_value(_a(sem, "when"), w)
                    cv = ConfigValue(_a(sem, "cname"), _a(sem, "desc"), val if mask == (1 << w) - 1 or mask is None else val)
                    st.values.append(cv)
                    if st.default is None and mask is not None and (fval & mask) == val:
                        st.default = cv.name
                elif t == "DCRFieldValueRange":
                    st.range = (_int(_a(sem, "min")), _int(_a(sem, "max"), (1 << w) - 1))
            if st.range is not None and st.default is None:
                st.default = hex(fval)
            if not st.values and st.range is None:
                st.hidden = True
            dev.settings.append(st)

    # --- SFRs (fields from every mode, de-duplicated)
    for tag in ("SFRDef", "JoinedSFRDef"):
        for sd in root.iter(NS + tag):
            rn = _a(sd, "cname") or _a(sd, "name")
            if not rn or rn in dev.sfrs:
                continue
            r = SFR(rn, _int(_a(sd, "_addr")), _int(_a(sd, "nzwidth"), 8), reset=_a(sd, "por"))
            for al in sd.iter():
                if _tag(al) in ("LegacyAlias", "MigrationAlias") and (_a(al, "cname") or _a(al, "name")):
                    r.aliases.append(_a(al, "cname") or _a(al, "name"))
            seen = set()
            if tag == "SFRDef":
                for mode in sd.iter(NS + "SFRMode"):
                    for fd, lsb in _walk_fields(mode, "SFRFieldDef"):
                        fn = _a(fd, "cname") or _a(fd, "name")
                        if not fn or fn in seen or _true(_a(fd, "islanghidden")):
                            continue
                        seen.add(fn)
                        r.fields.append(SFRField(fn, lsb, _int(_a(fd, "nzwidth"), 1), _a(fd, "desc")))
            dev.sfrs[rn] = r

    # --- memory sizes (first subspace only for multi-partition parts)
    ps = root.find(f"{NS}ProgramSpace")
    if ps is not None:
        first = ps.find(f"{NS}ProgramSubspace")
        scope = first if first is not None else ps
        for cs in scope.iter(NS + "CodeSector"):
            if _a(cs, "regionid") in ("program", "code") or not _true(_a(cs, "isreserved")):
                dev.program_bytes += max(0, _int(_a(cs, "endaddr")) - _int(_a(cs, "beginaddr")))
        for es in ps.iter(NS + "EEDataSector"):
            dev.eeprom_bytes += max(0, _int(_a(es, "endaddr")) - _int(_a(es, "beginaddr")))
    if dev.family in ("PIC24", "dsPIC30", "dsPIC33"):
        dev.program_bytes = dev.program_bytes // 2 * 3   # 24-bit words at 2 PC units each -> 3 bytes
    ds = root.find(f"{NS}DataSpace")
    if ds is not None:
        for g in ds.iter():
            if _tag(g) in ("GPRDataSector", "DPRDataSector", "EmulatorZone") and _tag(g) != "EmulatorZone":
                dev.data_bytes += max(0, _int(_a(g, "endaddr")) - _int(_a(g, "beginaddr")))
    # --- ICSP pairs from pin names (PGEC1/PGED1, PGC1/PGD1, ICSPCLK)
    pairs = set()
    for vp in root.iter(NS + "VirtualPin"):
        m = re.fullmatch(r"(?:PGE?[CD]|ICSP(?:CLK|DAT))(\d?)", _a(vp, "name"))
        if m:
            pairs.add(int(m.group(1) or 1))
    dev.icsp_pairs = sorted(pairs)
    return dev


# ------------------------------------------------------------------ pack discovery
def _vkey(p: str):
    return tuple(int(x) for x in re.findall(r"\d+", p)) or (0,)


def default_pack_roots() -> list[str]:
    roots = []
    for pf in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        hits = glob.glob(os.path.join(pf, "Microchip", "MPLABX", "v*", "packs"))
        roots += sorted(hits, key=lambda h: _vkey(Path(h).parent.name), reverse=True)
    for d in ("/opt/microchip/mplabx", "/Applications/microchip/mplabx"):
        hits = glob.glob(os.path.join(d, "v*", "packs"))
        roots += sorted(hits, key=lambda h: _vkey(Path(h).parent.name), reverse=True)
    home = os.path.expanduser("~/.mchp_packs")
    if os.path.isdir(home):
        roots.append(home)
    return roots


def pack_roots(configured: str = "") -> list[str]:
    if configured:
        return [r for r in configured.split(os.pathsep) if r]
    return default_pack_roots()


_INDEX: dict[tuple, dict[str, str]] = {}


def index_devices(configured: str = "", refresh: bool = False) -> dict[str, str]:
    """{DEVICE: path to newest .PIC}. Newest MPLAB X / newest pack version wins."""
    roots = tuple(pack_roots(configured))
    if roots in _INDEX and not refresh:
        return _INDEX[roots]
    best: dict[str, tuple] = {}
    for root in roots:
        pats = [os.path.join(root, "*", "*_DFP", "*", "edc", "*.PIC"),   # packs/Microchip/<Fam>_DFP/<ver>/edc
                os.path.join(root, "*_DFP", "*", "edc", "*.PIC"),
                os.path.join(root, "*.PIC")]                              # a plain folder of .PIC files
        for pat in pats:
            for p in glob.glob(pat):
                dev = Path(p).stem.upper()
                ver = _vkey(Path(p).parent.parent.name) if Path(p).parent.name.lower() == "edc" else (0,)
                if dev not in best or ver > best[dev][0]:
                    best[dev] = (ver, p)
    _INDEX[roots] = {k: v[1] for k, v in sorted(best.items())}
    return _INDEX[roots]


def search_devices(query: str = "", configured: str = "", limit: int = 200) -> list[dict]:
    q = query.upper().strip()
    out = []
    for name, p in index_devices(configured).items():
        if q and q not in name:
            continue
        fam = family_of(name)
        out.append({"name": name, "family": fam, "compiler": compiler_for(fam), "path": p})
        if len(out) >= limit:
            break
    return out


def find_edc(device: str, configured: str = "") -> str | None:
    return index_devices(configured).get(device.upper())


def load_device(device: str, configured: str = "", cache_dir: str = ".cache", path: str | None = None) -> Device | None:
    """Parse (and cache as JSON) the EDC file of `device`. None if no pack has it."""
    path = path or find_edc(device, configured)
    if not path or not os.path.isfile(path):
        return None
    st = os.stat(path)
    cdir = Path(cache_dir) / "edc"
    cfile = cdir / f"{Path(path).stem.upper()}-{int(st.st_mtime)}-{st.st_size}.json"
    if cfile.is_file():
        try:
            d = json.loads(cfile.read_text(encoding="utf-8"))
            if d.get("_schema") == SCHEMA_VERSION:
                dev = Device.from_json(d)
                dev.source = path
                return dev
        except (OSError, ValueError, TypeError):
            pass
    dev = parse_edc(path)
    try:
        cdir.mkdir(parents=True, exist_ok=True)
        cfile.write_text(json.dumps(dev.to_json()), encoding="utf-8")
    except OSError:
        pass
    return dev
