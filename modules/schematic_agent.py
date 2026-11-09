"""
Tool-using schematic generator for any OpenAI-compatible model with tool calling (e.g. a local server).

Instead of one blind generation, the model can:
  - search_symbols:   find real KiCad symbols (modules/symbol_search.py index)
  - get_symbol_info:  read a symbol's pins and size before wiring it
  - check_schematic:  build its draft in KiCad and get back errors, nets and ERC errors
and fix its own mistakes before answering. Builds run with SCHGEN_STRICT_SYMBOLS=1, so an
unknown symbol or pin is an error instead of being replaced by a lookalike.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import sexpdata

from config import prepare_context
from modules.symbol_context import get_symbol_context_from_list
from modules.symbol_search import SymbolIndex, TOOLS as SEARCH_TOOLS
from schematic_generation.check_nets import HEADER, REPO, default_cli, ensure_work_dir, _kids, _val

TOOLS = [
    SEARCH_TOOLS[0],
    {"type": "function", "function": {
        "name": "get_symbol_info",
        "description": "Pins (number, name, position relative to the symbol centre) and size of KiCad symbols.",
        "parameters": {"type": "object", "properties": {
            "symbols": {"type": "array", "items": {"type": "string"},
                        "description": "'Library:Symbol' strings, e.g. ['Device:R', 'Jumper:SolderJumper_2_Open']"}},
            "required": ["symbols"]}}},
    {"type": "function", "function": {
        "name": "check_schematic",
        "description": "Build complete schematic code in KiCad. Returns the build error, or the parts, "
                       "the resulting nets and the electrical rule check (ERC) errors.",
        "parameters": {"type": "object", "properties": {
            "code": {"type": "string", "description": "the complete Python code"}},
            "required": ["code"]}}},
]

AGENT_NOTES = """
###
TOOLS: you can call tools before writing the final code.
- search_symbols: find the exact library and symbol name for every part. Never guess symbol names; use only symbols the search returned.
- get_symbol_info: read the pins and size of the symbols you will use. Use only pin names or numbers it lists.
- check_schematic: build your complete draft code and read the errors, nets and ERC errors. Fix problems and check again.
GUIDANCE:
- Names in the request that are not parts (for example "the ESP21 SDA line" or "the USB251 SDA signal") are usually signals from elsewhere in the design. Add them as labels connected to the right pin; do not add chips for them.
- Build only what the request asks for.
- ERC errors for IC pins that the request does not use are fine.
- Space the parts out so they do not overlap, near the centre of the sheet.
When the check passes and the nets do what the request asks, reply with the final complete code in one ```python block.
"""


def extract_code(text: str) -> str:
    blocks = re.findall(r"```(?:[Pp]ython)?\s*\n(.*?)\n\s*```", text or "", re.S)
    return (blocks[-1] if blocks else "").strip()


class SchematicAgent:
    def __init__(self, client, model: str, work_dir="runs/agent/build", extra_body=None,
                 max_turns: int = 24, max_repairs: int = 2, max_tokens: int = 16000, log=print):
        self.client, self.model = client, model
        self.extra_body = extra_body or {}
        self.max_turns, self.max_repairs, self.max_tokens = max_turns, max_repairs, max_tokens
        self.work = Path(work_dir).resolve()
        ensure_work_dir(self.work)
        self.index = SymbolIndex()
        self.cli = default_cli()
        self.log = log

    # ---- tools ----
    def tool_search(self, query: str) -> str:
        return json.dumps(self.index.search(query) or ["no matches"])

    def tool_symbol_info(self, symbols: list) -> str:
        ok, missing = [], []
        for s in symbols:
            lib, _, name = str(s).split(" - ")[0].partition(":")
            (ok if self.index.exists(lib, name) else missing).append(f"{lib}:{name}")
        out = {}
        if ok:
            out = get_symbol_context_from_list(ok)
        if missing:
            out["not_found"] = f"{missing}: use search_symbols to find the real names"
        return json.dumps(out, default=str)

    def check(self, code: str, name: str):
        """Build code into <work>/<name>/. Returns (ok, report)."""
        code = re.sub(r'^\s*set_schematic_filename\(.*\)\s*$', "", code, flags=re.M)
        if "from modules.kicad_sch_interface import *" not in code:
            code = HEADER + code
        py = self.work / f"{name}.py"
        py.write_text(code, encoding="utf-8")
        env = {**os.environ, "SCHGEN_STRICT_SYMBOLS": "1", "PYTHONIOENCODING": "utf-8"}
        try:
            r = subprocess.run([sys.executable, str(REPO / "init_project.py"), name, str(py), "--overwrite"],
                               cwd=self.work, env=env, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=300)
        except subprocess.TimeoutExpired:
            return False, "BUILD FAILED: timed out after 300 s"
        out = r.stdout + "\n" + r.stderr
        sch = self.work / name / f"{name}.kicad_sch"
        if r.returncode != 0 or not sch.exists():
            return False, "BUILD FAILED:\n" + _error_excerpt(out, py.name)

        net = self.work / name / f"{name}.net"
        subprocess.run([self.cli, "sch", "export", "netlist", "-o", str(net), str(sch)], capture_output=True)
        rpt = self.work / name / f"{name}.erc.rpt"
        subprocess.run([self.cli, "sch", "erc", "-o", str(rpt), str(sch)], capture_output=True)
        parts, nets = _nets_by_ref(net.read_text(encoding="utf-8")) if net.exists() else ({}, [])
        problems = _connection_problems(code, nets)
        lines = ["BUILD OK" if not problems else "BUILD OK, BUT THE WIRING IS WRONG", "Parts: " + ", ".join(f"{k}={v}" for k, v in parts.items()), "Nets:"]
        lines += [f"  {n}: {', '.join(nodes)}" for n, nodes in nets] or ["  (none)"]
        erc = _erc_errors(rpt.read_text(encoding="utf-8")) if rpt.exists() else []
        lines.append(f"ERC errors ({len(erc)}):")
        lines += [f"  {e}" for e in erc[:15]]
        if problems:
            lines.append("Wiring problems (fix these, e.g. move or rotate parts so wires do not run over other pins):")
            lines += [f"  {p}" for p in problems[:15]]
        warns = list(dict.fromkeys(l.strip() for l in out.splitlines() if "Warning" in l))
        if warns:
            lines.append("Builder warnings (layout only, optional to fix):")
            lines += [f"  {w[:200]}" for w in warns[:10]]
        if not problems:
            lines.append("CHECK PASSED. If these nets do what the request asks, reply now with the final code.")
        return not problems, "\n".join(lines)

    # ---- loop ----
    def run(self, request: str, name: str) -> dict:
        msgs = prepare_context()
        msgs[0]["content"] += AGENT_NOTES
        msgs.append({"role": "user", "content": request})
        t0, calls, repairs = time.time(), [], 0
        self.last_checked = None
        self.searches, self.wasted_searches = set(), 0
        self.passes_same_nets, self.last_nets = 0, None
        result = {"name": name, "ok": False, "code": "", "report": ""}
        for turn in range(self.max_turns):
            tools = {"tools": TOOLS}
            if self.wasted_searches >= 3:
                # Stuck searching: take the tools away for one turn so it has to write the code.
                self.log(f"  [{name}] stuck searching, asking for code without tools")
                msgs.append({"role": "user", "content": "Stop searching. You have the symbols you need (the "
                             "'power' library has GND, +3V3, +5V and so on). Write the complete code now "
                             "in one ```python block."})
                tools, self.wasted_searches = {}, 0
            r = self.client.chat.completions.create(model=self.model, messages=msgs, **tools,
                                                    max_tokens=self.max_tokens, extra_body=self.extra_body)
            m = r.choices[0].message
            if m.tool_calls:
                msgs.append({"role": "assistant", "content": m.content or "",
                             "tool_calls": [tc.model_dump() for tc in m.tool_calls]})
                for tc in m.tool_calls:
                    res = self._dispatch(tc, name)
                    calls.append(tc.function.name)
                    msgs.append({"role": "tool", "tool_call_id": tc.id, "content": res})
                if self.passes_same_nets >= 3:
                    # Three passing checks with unchanged nets: the model is only polishing.
                    self.log(f"  [{name}] stopping: three passing checks with the same nets")
                    break
                continue
            code = extract_code(m.content)
            if not code:
                msgs += [{"role": "assistant", "content": m.content or ""},
                         {"role": "user", "content": "Reply with the final complete code in one ```python block."}]
                continue
            ok, report = self.check(code, name)
            result.update(code=code, report=report, ok=ok)
            if ok or repairs >= self.max_repairs:
                break
            repairs += 1
            self.log(f"  [{name}] final code failed, asking for a fix ({repairs}/{self.max_repairs})")
            msgs += [{"role": "assistant", "content": m.content},
                     {"role": "user", "content": f"Your code failed to build:\n{report}\n"
                                                 "Fix it and reply with the complete corrected code."}]
        if not result["code"] and self.last_checked:
            # Out of turns without a final answer: keep the last draft the model checked.
            code, ok, report = self.last_checked
            self.check(code, name)  # leave that draft as the built project
            result.update(code=code, ok=ok, report=report)
        result.update(turns=turn + 1, tool_calls=calls, repairs=repairs, secs=round(time.time() - t0, 1))
        return result

    def _dispatch(self, tc, name: str) -> str:
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            return "Invalid JSON arguments."
        fn = tc.function.name
        if fn == "search_symbols":
            q = str(args.get("query", "")).strip().lower()
            self.log(f"  [{name}] search_symbols({args.get('query')!r})")
            # Small models can get stuck searching; nudge them back to writing code.
            if q in self.searches:
                self.wasted_searches += 1
                res = "You already ran this search. Use the results you have, then write and check the code."
            elif len(self.searches) >= 12:
                self.wasted_searches += 1
                res = "Search limit reached. Use the symbols you have found; write the code and call check_schematic."
            else:
                self.searches.add(q)
                res = self.tool_search(args.get("query", ""))
        elif fn == "get_symbol_info":
            res = self.tool_symbol_info(args.get("symbols", []))
            self.log(f"  [{name}] get_symbol_info({args.get('symbols')})")
        elif fn == "check_schematic":
            ok, res = self.check(args.get("code", ""), name)
            self.last_checked = (args.get("code", ""), ok, res)
            nets = res.split("Nets:")[-1].split("ERC errors")[0] if ok else None
            self.passes_same_nets = self.passes_same_nets + 1 if ok and nets == self.last_nets else int(ok)
            self.last_nets = nets
            self.log(f"  [{name}] check_schematic -> {'OK' if ok else res.splitlines()[-1][:120]}")
        else:
            res = f"Unknown tool {fn}"
        return res


def _error_excerpt(out: str, script_name: str) -> str:
    """The schematic script's own traceback: its frames in the generated file, plus the error line."""
    lines = out.splitlines()
    start = next((i for i, l in enumerate(lines) if l.startswith("Traceback")), None)
    if start is None:
        return "\n".join(l for l in lines if "rror" in l)[-1500:] or out[-1500:]
    keep = []
    for i in range(start + 1, len(lines)):
        l = lines[i]
        if script_name in l:
            keep += [l.strip(), lines[i + 1].strip() if i + 1 < len(lines) else ""]
        if l and not l.startswith(" "):
            keep.append(l.strip())  # the exception message
            break
    return "\n".join(keep)[-1500:]


def _nets_by_ref(netlist: str):
    tree = sexpdata.loads(netlist)
    parts = {}
    for comps in _kids(tree, "components"):
        for c in _kids(comps, "comp"):
            ls = _kids(c, "libsource")
            parts[_val(c, "ref")] = f"{_val(ls[0], 'lib')}:{_val(ls[0], 'part')}" if ls else "?"
    nets = []
    for ns in _kids(tree, "nets"):
        for n in _kids(ns, "net"):
            nodes = []
            for x in _kids(n, "node"):
                fn = _val(x, "pinfunction")
                nodes.append(f"{_val(x, 'ref')}.{_val(x, 'pin')}" + (f"({fn})" if fn else ""))
            nets.append((_val(n, "name").lstrip("/"), nodes))
    return parts, nets


_ARG = r"""\s*(?:\w+\s*=\s*)?["']([^"']+)["']\s*"""


def _kwarg(args: str, key: str):
    m = re.search(key + r"""\s*=\s*["']([^"']+)["']""", args)
    return m.group(1) if m else None


def _connection_problems(code: str, nets) -> list:
    """Compare the connect_pins() calls in the code with the nets KiCad actually built."""
    # Where each pin really ended up; alias both 'D1.1' and 'D1.K' to its net.
    where = {}
    for net_name, nodes in nets:
        for node in nodes:
            m = re.fullmatch(r"(.+?)\.([^.(]+)(?:\((.*)\))?", node)
            if m:
                where[(m.group(1), m.group(2))] = net_name
                if m.group(3):
                    where[(m.group(1), m.group(3))] = net_name
    # What the code meant: union-find over pins, with power symbols and labels joined by name.
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    for m in re.finditer(r"add_schematic_symbol\(([^)]*)\)", code):
        lib, sym, ref = (_kwarg(m.group(1), k) for k in ("symbol_lib", "symbol_name", "reference"))
        if lib == "power" and sym and ref:
            union((ref, "1"), ("NET", sym))
    for m in re.finditer(r"add_label\((.*?)\)\s*$", code, re.M):
        text, ref = _kwarg(m.group(1), "label_text"), _kwarg(m.group(1), "label_ref")
        if text and ref:
            union((ref, "1"), ("NET", text))
    edges = re.findall(r"connect_pins\(" + ",".join([_ARG] * 4) + r"\)", code)
    for a, pa, b, pb in edges:
        union((a, pa), (b, pb))

    problems = []
    for a, pa, b, pb in edges:
        na, nb = where.get((a, pa)), where.get((b, pb))
        if na is not None and nb is not None and na != nb:
            problems.append(f"connect_pins({a}, {pa}, {b}, {pb}) did not connect: {a}.{pa} is on net "
                            f"'{na}' but {b}.{pb} is on net '{nb}'")
    # Pins that share a net although the code never connects them (e.g. a wire crossing a pin).
    by_net = {}
    for (ref, pin), net_name in where.items():
        if (ref, pin) in parent:
            by_net.setdefault(net_name, {}).setdefault(find((ref, pin)), set()).add(f"{ref}.{pin}")
    for net_name, groups in by_net.items():
        if len(groups) > 1:
            problems.append(f"unintended short on net '{net_name}': "
                            + " and ".join("/".join(sorted(g)) for g in groups.values())
                            + " are joined but your code never connects them")
    return problems


def _erc_errors(report: str):
    out = []
    for m in re.finditer(r"^\[(\w+)\]: ([^\n]*)\n\s*; (\w+)\n((?:\s+@\([^\n]*\n?)+)", report, re.M):
        # power_pin_not_driven needs a PWR_FLAG and is normal for circuit fragments; reporting it
        # only makes the model chase it.
        if m.group(3) == "error" and m.group(1) != "power_pin_not_driven":
            items = [re.sub(r"^\s*@\([^)]*\):\s*", "", l) for l in m.group(4).strip().splitlines()]
            out.append(f"{m.group(1)}: {'; '.join(items)}")
    return out
