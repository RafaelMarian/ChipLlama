"""Tiny C-subset register emulator.

It does NOT try to be a C compiler. It extracts #defines, splits the file into
functions, and linearly "executes" assignments to known SFRs so the checker can
reason about the *final register state* the init code would produce.
Control flow is flattened (if/for/while headers are stripped). Anything it does
not understand is ignored and listed in ``Emulation.ignored``.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from . import chipdb

MASK32 = 0xFFFFFFFF


class EvalError(Exception):
    pass


# ---------------------------------------------------------------- preprocessing
def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


def collect_defines(src: str) -> dict[str, str]:
    defs: dict[str, str] = {}
    for m in re.finditer(r"^[ \t]*#[ \t]*define[ \t]+([A-Za-z_]\w*)(?!\()[ \t]+(.+?)[ \t]*$", src, re.M):
        defs[m.group(1)] = m.group(2).rstrip("\\").strip()
    return defs


_CASTS = re.compile(r"\(\s*(?:const\s+|volatile\s+)*(?:unsigned\s+|signed\s+)?"
                    r"(?:uint(?:8|16|32)_t|int(?:8|16|32)_t|char|short|int|long(?:\s+long)?|float|double|"
                    r"unsigned|uint|word|WORD|u16|u8|u32)\s*\)")
_INT_SUFFIX = re.compile(r"\b(0[xX][0-9a-fA-F]+|0[bB][01]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)([uUlLfF]*)\b")


def _to_pyexpr(expr: str) -> str:
    expr = _CASTS.sub(" ", expr)
    expr = expr.replace("&&", " and ").replace("||", " or ")

    def num(m: re.Match) -> str:
        v = m.group(1)
        if v.lower().startswith("0b"):
            return str(int(v, 2))
        if re.fullmatch(r"0[0-7]+", v):  # C octal
            return str(int(v, 8))
        return v

    return _INT_SUFFIX.sub(num, expr)


class _Eval(ast.NodeVisitor):
    def __init__(self, names):
        self.names = names

    def visit_Expression(self, n):
        return self.visit(n.body)

    def visit_Constant(self, n):
        if isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
            return n.value
        raise EvalError("bad constant")

    def visit_Name(self, n):
        v = self.names(n.id)
        if v is None:
            raise EvalError(f"unknown name {n.id}")
        return v

    def visit_UnaryOp(self, n):
        v = self.visit(n.operand)
        if isinstance(n.op, ast.USub):
            return -v
        if isinstance(n.op, ast.UAdd):
            return v
        if isinstance(n.op, ast.Invert):
            return (~int(v)) & MASK32
        if isinstance(n.op, ast.Not):
            return int(not v)
        raise EvalError("unary op")

    def visit_BinOp(self, n):
        a, b = self.visit(n.left), self.visit(n.right)
        op = n.op
        isf = isinstance(a, float) or isinstance(b, float)
        if isinstance(op, ast.Add): return a + b
        if isinstance(op, ast.Sub): return a - b
        if isinstance(op, ast.Mult): return a * b
        if isinstance(op, ast.Div):
            if b == 0: raise EvalError("div by zero")
            return a / b if isf else int(a / b) if (a < 0) != (b < 0) else a // b
        if isinstance(op, ast.FloorDiv):  # python '//' never appears in C
            raise EvalError("bad op")
        if isinstance(op, ast.Mod):
            if isf or b == 0: raise EvalError("mod")
            return a % b
        if isf: raise EvalError("bit op on float")
        if isinstance(op, ast.LShift): return (a << b) & MASK32
        if isinstance(op, ast.RShift): return a >> b
        if isinstance(op, ast.BitAnd): return a & b
        if isinstance(op, ast.BitOr): return a | b
        if isinstance(op, ast.BitXor): return a ^ b
        raise EvalError("binary op")

    def visit_BoolOp(self, n):
        vals = [self.visit(v) for v in n.values]
        return int(all(vals)) if isinstance(n.op, ast.And) else int(any(vals))

    def visit_Compare(self, n):
        left = self.visit(n.left)
        for op, c in zip(n.ops, n.comparators):
            r = self.visit(c)
            ok = {ast.Eq: left == r, ast.NotEq: left != r, ast.Lt: left < r, ast.Gt: left > r,
                  ast.LtE: left <= r, ast.GtE: left >= r}.get(type(op))
            if ok is None: raise EvalError("cmp")
            if not ok: return 0
            left = r
        return 1

    def generic_visit(self, n):
        raise EvalError(f"unsupported syntax {type(n).__name__}")


def eval_expr(expr: str, names, depth: int = 0) -> int | float:
    """Evaluate a C integer expression. ``names(id)`` resolves identifiers (or None)."""
    if depth > 8:
        raise EvalError("recursion")
    py = _to_pyexpr(expr.strip())
    py = re.sub(r"\s+", " ", py).strip()
    if "[" in py or "." in py and not re.search(r"\d\.\d|\d\.", py):
        raise EvalError("member/array access")
    try:
        tree = ast.parse(py, mode="eval")
    except SyntaxError as e:
        raise EvalError(f"syntax: {expr!r}") from e
    return _Eval(names).visit(tree)


# ---------------------------------------------------------------- function split
@dataclass
class Function:
    name: str
    header: str
    body: str
    is_isr: bool
    line: int


def split_functions(src: str) -> list[Function]:
    funcs: list[Function] = []
    depth = 0
    seg_start = 0
    i = 0
    n = len(src)
    while i < n:
        c = src[i]
        if c == "{" and depth == 0:
            header = src[seg_start:i]
            j, d = i + 1, 1
            while j < n and d:
                d += (src[j] == "{") - (src[j] == "}")
                j += 1
            body = src[i + 1:j - 1]
            h = re.sub(r"^\s*(?:#[^\n]*\n\s*)*", "", header.strip().split(";")[-1])
            m = re.search(r"([A-Za-z_]\w*)\s*\([^()]*\)\s*$", h)
            if m and "=" not in h and not re.match(r"\s*(struct|union|enum|typedef)\b", h):
                is_isr = bool(re.search(r"interrupt", h))
                funcs.append(Function(m.group(1), h, body, is_isr, src.count("\n", 0, i) + 1))
            seg_start = j
            i = j
            continue
        if c == ";" and depth == 0:
            seg_start = i + 1
        if c == "\n" and depth == 0 and src[seg_start:i].strip().startswith("#"):
            seg_start = i + 1
        i += 1
    return funcs


def split_statements(body: str) -> list[str]:
    stmts: list[str] = []
    cur: list[str] = []
    paren = 0
    for ch in body:
        if ch == "(":
            paren += 1
        elif ch == ")":
            paren -= 1
        if paren == 0 and ch in ";{}":
            s = "".join(cur).strip()
            if s:
                stmts.append(s)
            cur = []
        else:
            cur.append(ch)
    s = "".join(cur).strip()
    if s:
        stmts.append(s)
    return stmts


_CTRL = re.compile(r"^(?:else\s+if|if|for|while|switch)\s*\(")


def _strip_control(stmt: str) -> str:
    stmt = stmt.strip()
    while True:
        s2 = re.sub(r"^(?:else|do)\b\s*", "", stmt)
        m = _CTRL.match(s2)
        if m:
            d, k = 1, m.end()
            while k < len(s2) and d:
                d += (s2[k] == "(") - (s2[k] == ")")
                k += 1
            s2 = s2[k:].strip()
        if s2 == stmt:
            return stmt
        stmt = s2


# ---------------------------------------------------------------- register state
_OSCCONL = re.compile(r"^__builtin_write_OSCCONL\s*\((.*)\)\s*$", re.S)
_ALIAS = re.compile(r"^_(T[123])(IE|IF|IP)$")          # XC16 header aliases e.g. _T1IE
_ASSIGN = re.compile(r"^(?P<lhs>[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)?)\s*(?P<op><<=|>>=|\|=|&=|\^=|\+=|-=|=)(?!=)\s*(?P<rhs>.+)$", re.S)


@dataclass
class Event:
    func: str
    text: str
    target: str     # e.g. "T1CON" or "T1CON.TON"
    value: int
    after: int      # whole-register value after the write
    line: int = 0


@dataclass
class Emulation:
    regs: dict[str, int]
    events: list[Event] = field(default_factory=list)
    defines: dict[str, str] = field(default_factory=dict)
    functions: list[Function] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    unknown_targets: set[str] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)

    def get(self, reg: str, fld: str | None = None) -> int:
        v = self.regs.get(reg, 0)
        if fld is None:
            return v
        f = chipdb.REGISTERS[reg].field(fld)
        return (v & f.mask) >> f.lsb


def initial_regs() -> dict[str, int]:
    regs = {n: r.reset for n, r in chipdb.REGISTERS.items()}
    regs.update(chipdb.DATA_RESET)
    return regs


def _resolve_field(reg: chipdb.Register, fname: str):
    f = reg.field(fname)
    if f:
        return f.lsb, f.width
    m = re.fullmatch(r"([A-Za-z]+\d?[A-Za-z]*?)(\d)", fname)  # TCKPS0, T1IP2 ...
    if m:
        base = reg.field(m.group(1))
        if base and base.width > 1 and int(m.group(2)) < base.width:
            return base.lsb + int(m.group(2)), 1
    return None


def run_statement(emu: Emulation, stmt: str, func: str, line: int = 0, regs: dict | None = None,
                  record: bool = True) -> None:
    regs = emu.regs if regs is None else regs
    stmt = _strip_control(stmt)
    if not stmt or stmt.startswith(("return", "break", "continue", "#")):
        return
    b = _OSCCONL.match(stmt)
    if b:   # XC16 builtin used for the PPS unlock/lock sequence: writes OSCCON<7:0>
        stmt = f"OSCCON = (OSCCON & 0xFF00) | (({b.group(1)}) & 0xFF)"
    m = _ASSIGN.match(stmt)
    if not m:
        if re.match(r"^[A-Za-z_]\w*\s*\(", stmt) is None and "++" not in stmt and "--" not in stmt \
                and not re.match(r"^(?:volatile\s+|static\s+|const\s+)*(?:unsigned|uint\w*|int|char|void|long|short|float|double)\b", stmt):
            emu.ignored.append(stmt[:60])
        return
    lhs = re.sub(r"\s+", "", m.group("lhs"))
    op, rhs = m.group("op"), m.group("rhs").strip()

    # resolve target -> (register, lsb, width)
    reg_name = fld = None
    alias = _ALIAS.match(lhs)
    if alias:
        t, kind = alias.groups()
        n = int(t[1])
        info = {"IE": "iec", "IF": "ifs", "IP": "ipc"}[kind]
        from .timers import TIMERS
        reg_name, fld = TIMERS[n][info]
    elif "." in lhs:
        base, fld = lhs.split(".", 1)
        reg_name = base[:-4] if base.endswith("bits") else base
    else:
        reg_name = lhs
    reg_name = chipdb.REG_ALIASES.get(reg_name, reg_name)
    if reg_name in chipdb.REGISTERS:
        reg = chipdb.REGISTERS[reg_name]
        if fld is None:
            lsb, width = 0, 16
        else:
            rf = _resolve_field(reg, fld)
            if rf is None:
                emu.unknown_targets.add(f"{reg_name}.{fld}")
                return
            lsb, width = rf
    elif reg_name in chipdb.DATA_REGS and fld is None:
        lsb, width = 0, 16
    else:
        emu.unknown_targets.add(lhs)
        return

    def names(ident: str):
        ident = chipdb.REG_ALIASES.get(ident, ident)
        if ident in regs and ident in chipdb.REGISTERS or ident in chipdb.DATA_REGS:
            return regs.get(ident, chipdb.DATA_RESET.get(ident, 0))
        if ident in emu.defines:
            return eval_expr(emu.defines[ident], names, _depth.append(1) or len(_depth))
        if ident in _KNOWN:
            return _KNOWN[ident]
        return None

    _depth: list[int] = []
    try:
        val = eval_expr(rhs, names)
    except EvalError as e:
        emu.warnings.append(f"could not evaluate '{stmt[:70]}' ({e})")
        return
    val = int(val)
    cur = regs.get(reg_name, 0)
    fmask = ((1 << width) - 1) << lsb
    curf = (cur & fmask) >> lsb
    newf = {"=": val, "|=": curf | val, "&=": curf & val, "^=": curf ^ val, "<<=": curf << val,
            ">>=": curf >> val, "+=": curf + val, "-=": curf - val}[op]
    full = op == "=" and fld is None
    if full and (newf & 0xFFFF) != (newf & MASK32) and width == 16 and newf > 0xFFFF:
        emu.warnings.append(f"value 0x{newf:X} truncated to 16 bits in '{stmt[:60]}'")
    newf &= (1 << width) - 1
    regs[reg_name] = (cur & ~fmask & 0xFFFF) | (newf << lsb)
    if record:
        emu.events.append(Event(func, stmt, reg_name if fld is None else f"{reg_name}.{fld}", newf,
                                regs[reg_name], line))


_KNOWN = {"true": 1, "false": 0, "TRUE": 1, "FALSE": 0, "NULL": 0}


def emulate(src: str) -> Emulation:
    clean = strip_comments(src)
    emu = Emulation(regs=initial_regs(), defines=collect_defines(clean))
    emu.functions = split_functions(clean)
    for fn in emu.functions:
        if fn.is_isr or re.fullmatch(r"_T\dInterrupt", fn.name):
            continue
        for st in split_statements(fn.body):
            run_statement(emu, st, fn.name, fn.line)
    return emu


def isr_clears_flag(emu: Emulation, fn: Function, ifs: tuple[str, str]) -> bool:
    """Run the ISR body from a state where the flag is set; True if it ends up cleared."""
    regs = dict(emu.regs)
    reg, f = ifs
    fl = chipdb.REGISTERS[reg].field(f)
    regs[reg] = regs.get(reg, 0) | fl.mask
    scratch = Emulation(regs=regs, defines=emu.defines)
    for st in split_statements(fn.body):
        run_statement(scratch, st, fn.name, record=False, regs=regs)
    return (regs[reg] & fl.mask) == 0
