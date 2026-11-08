"""
Test the search-tool symbol selector (modules/symbol_search.py) on a local or remote model.

Runs the three raw comparison prompts plus a few vaguer requests, and compares the picks
for the raw prompts with the GPT-5.2 picks saved in <frontier_dir>/prompts.json.

Usage (repo root, PROJECT_PATH set):
  python schematic_generation/compare_selection.py --base_url http://127.0.0.1:8080/v1 \
      --model qwen3.8-flash-next-iq3_xxs --extra_body '{"chat_template_kwargs": {"enable_thinking": false}}'
"""
import argparse, json, os, re, sys, time
from pathlib import Path

sys.path.append(os.environ.get("PROJECT_PATH", "."))
import openai
from modules.symbol_search import SymbolIndex, select_symbols_with_tools
from compare_frontier import RAW_CASES

VAGUE_CASES = {
    "vague_reg": "Drop 5 V down to 3.3 V for a microcontroller, with input and output decoupling capacitors.",
    "vague_usbc": "A USB-C power input with a resettable fuse and a power-on indicator LED.",
    "vague_i2c": "An I2C temperature sensor running from 3.3 V, with pull-up resistors on SDA and SCL.",
}


def gpt52_picks(prompts_path: Path, case: str):
    if not prompts_path.exists():
        return None
    cases = json.loads(prompts_path.read_text(encoding="utf-8"))
    if case not in cases:
        return None
    text = cases[case][-1]["content"]
    return sorted({f"{l}:{n}" for n, l in re.findall(r"'symbol_name': '([^']+)', 'lib_name': '([^']+)'", text)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base_url", default="http://127.0.0.1:8080/v1")
    ap.add_argument("--model", required=True)
    ap.add_argument("--api_key_env", default=None, help="env var holding the API key (local servers need none)")
    ap.add_argument("--extra_body", default="{}")
    ap.add_argument("--frontier_dir", default="runs_2026-11-08/frontier")
    ap.add_argument("--out", default=None, help="JSON results file (default: <frontier_dir>/selection_tools_<model>.json)")
    args = ap.parse_args()

    key = os.environ.get(args.api_key_env, "") if args.api_key_env else "local"
    client = openai.OpenAI(base_url=args.base_url, api_key=key, timeout=600)
    extra = json.loads(args.extra_body)
    index = SymbolIndex()
    frontier = Path(args.frontier_dir)

    results = {}
    for case, prompt in {**RAW_CASES, **VAGUE_CASES}.items():
        t0 = time.time()
        try:
            picks = sorted({f"{s.lib_name}:{s.name}" for s in
                            select_symbols_with_tools(client, args.model, prompt, index=index, extra_body=extra)})
        except Exception as e:
            picks = [f"FAILED: {type(e).__name__}: {str(e)[:150]}"]
        ref = gpt52_picks(frontier / "prompts.json", case)
        results[case] = {"prompt": prompt, "picks": picks, "gpt52": ref, "secs": round(time.time() - t0, 1)}
        print(f"\n== {case} ({results[case]['secs']} s): {prompt}")
        print(f"  picks  : {picks}")
        if ref is not None:
            print(f"  GPT-5.2: {ref}")
            print(f"  missing: {sorted(set(ref) - set(picks))} | extra: {sorted(set(picks) - set(ref))}")

    out = Path(args.out or frontier / f"selection_tools_{args.model.split('/')[-1]}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"\nsaved {out}")


if __name__ == "__main__":
    main()
