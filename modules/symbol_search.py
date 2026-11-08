"""
Keyword search over KiCad symbols, and a symbol selector that gives the search to a model as a tool.

The index is SQLite FTS5 over library, symbol name and description, built once from
export/organized_lib.json (about 12 s) into export/symbol_index.db (about 5 MB).
A search takes about 10 ms.

The selector is an alternative to load_related_symbols(): instead of sending the model
whole library listings, the model searches for what it needs and submits its picks.
It works with any OpenAI-compatible server that supports tool calling.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from modules.sch_module_def import SymbolInfo

DEFAULT_LIB = "./export/organized_lib.json"
DEFAULT_DB = "./export/symbol_index.db"


def build_index(organized_lib_path: str = DEFAULT_LIB, db_path: str = DEFAULT_DB) -> int:
    """(Re)build the FTS5 index. Returns the number of symbols indexed."""
    with open(organized_lib_path, encoding="utf-8") as f:
        data = json.load(f)
    db = Path(db_path)
    db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    con.execute("CREATE VIRTUAL TABLE sym USING fts5(lib, name, description)")
    # organized_lib.json keeps the KiCad quotes around names and descriptions.
    con.executemany("INSERT INTO sym VALUES (?,?,?)",
                    [(lib, e["name"].strip('"'), e.get("description", "").strip('"'))
                     for lib, entries in data.items() for e in entries])
    con.commit()
    n = con.execute("SELECT count(*) FROM sym").fetchone()[0]
    con.close()
    return n


class SymbolIndex:
    def __init__(self, db_path: str = DEFAULT_DB, organized_lib_path: str = DEFAULT_LIB):
        db = Path(db_path)
        lib = Path(organized_lib_path)
        if not db.exists() or (lib.exists() and lib.stat().st_mtime > db.stat().st_mtime):
            build_index(organized_lib_path, db_path)
        self.con = sqlite3.connect(db)

    def search(self, query: str, limit: int = 10) -> list[str]:
        """Best matches as 'Library:Symbol - description' lines."""
        q = query.strip()
        # Exact name matches first (e.g. 'R', 'LED', 'USB_B', '+3V3').
        rows = self.con.execute("SELECT lib, name, description FROM sym WHERE name = ? COLLATE NOCASE",
                                (q,)).fetchall()
        terms = ['"' + w.replace('"', "") + '"' + ("*" if len(w) > 2 else "")
                 for w in re.split(r"[^\w.]+", q) if w]
        if terms:
            # All words first; if that finds little, any word.
            for match in (" ".join(terms), " OR ".join(terms)):
                # bm25 column weights: lib, name, description. Names count most.
                rows += self.con.execute(
                    "SELECT lib, name, description FROM sym WHERE sym MATCH ? "
                    "ORDER BY bm25(sym, 2.0, 10.0, 1.0) LIMIT ?", (match, limit * 2)).fetchall()
                if len(rows) >= 3 or len(terms) == 1:
                    break
        seen, out = set(), []
        for lib, name, desc in rows:
            if (lib, name) not in seen:
                seen.add((lib, name))
                out.append(f"{lib}:{name} - {desc[:100]}")
        return out[:limit]

    def exists(self, lib: str, name: str) -> bool:
        return self.con.execute("SELECT 1 FROM sym WHERE lib = ? AND name = ?", (lib, name)).fetchone() is not None


TOOLS = [
    {"type": "function", "function": {
        "name": "search_symbols",
        "description": "Keyword search over KiCad symbol names and descriptions. "
                       "Returns 'Library:Symbol - description' lines, best match first.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "a part name like 'USB_B' or 'AMS1117-3.3', "
                                                       "a power net like '+3V3' or 'GND', "
                                                       "or a few description words like 'resistor'"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "submit_symbols",
        "description": "Submit the final list of symbols for the schematic. Call once, at the end.",
        "parameters": {"type": "object", "properties": {
            "symbols": {"type": "array", "items": {"type": "string"},
                        "description": "'Library:Symbol' strings exactly as search_symbols returned them"}},
            "required": ["symbols"]}}},
]

SYSTEM_PROMPT = """You choose KiCad schematic symbols for a circuit request.
Use search_symbols to find each part, then call submit_symbols once with every symbol the schematic needs.
- Include passives (resistors, capacitors) and the power symbols for every supply and ground net
  (the 'power' library, e.g. +3V3, +5V, VBUS, GND).
- Prefer the plain generic symbol (Device:R, Device:C, Device:LED) unless the request names something specific.
- Only submit symbols that search_symbols returned."""


def select_symbols_with_tools(client, model: str, sch_request: str, index: SymbolIndex | None = None,
                              extra_body: dict | None = None, max_turns: int = 12,
                              max_tokens: int = 4000, logger=None) -> list[SymbolInfo]:
    """Let the model search the index and return its picks, in the shape load_related_symbols() returns."""
    index = index or SymbolIndex()
    msgs = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Circuit request: {sch_request}"}]
    for _ in range(max_turns):
        r = client.chat.completions.create(model=model, messages=msgs, tools=TOOLS,
                                           max_tokens=max_tokens, extra_body=extra_body or {})
        m = r.choices[0].message
        if not m.tool_calls:
            # The model answered in text instead of submitting: take any Library:Symbol it wrote.
            picks = _valid(index, re.findall(r"([\w.+-]+):([\w.+-]+)", m.content or ""))
            if picks:
                return picks
            msgs += [{"role": "assistant", "content": m.content or ""},
                     {"role": "user", "content": "Call submit_symbols with your final list."}]
            continue
        msgs.append({"role": "assistant", "content": m.content or "",
                     "tool_calls": [tc.model_dump() for tc in m.tool_calls]})
        for tc in m.tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if tc.function.name == "submit_symbols":
                pairs = [tuple(s.split(" - ")[0].split(":", 1)) for s in args.get("symbols", []) if ":" in s]
                bad = [f"{l}:{n}" for l, n in pairs if not index.exists(l, n)]
                if pairs and not bad:
                    if logger:
                        logger.info(f"Selected symbols: {pairs}")
                    return [SymbolInfo(lib_name=l, name=n) for l, n in dict.fromkeys(pairs)]
                result = (f"Not found: {bad}. Search for them and submit again." if bad
                          else "No symbols given. Submit a list of 'Library:Symbol' strings.")
            elif tc.function.name == "search_symbols":
                hits = index.search(args.get("query", ""))
                if logger:
                    logger.info(f"search_symbols({args.get('query')!r}) -> {len(hits)} hits")
                result = hits or ["no matches"]
            else:
                result = f"Unknown tool {tc.function.name}"
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
    raise RuntimeError(f"symbol selection did not finish in {max_turns} turns")


def _valid(index: SymbolIndex, pairs) -> list[SymbolInfo]:
    keep = dict.fromkeys((l, n) for l, n in pairs if index.exists(l, n))
    return [SymbolInfo(lib_name=l, name=n) for l, n in keep]
