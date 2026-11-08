"""
Run the SchGen comparison cases through frontier models on OpenRouter.

Builds the same prompts generate.py builds (raw prompts: system context +
symbol context chosen by GPT-5.2; dataset rows: Microsoft's own prompt) and
saves each model's code as <out_dir>/generated_<model>_<case>.py.

Usage (repo root, PROJECT_PATH and OPENROUTER_API_KEY set):
  python schematic_generation/compare_frontier.py --models anthropic/claude-opus-5.5 ...
Local server (reuses the saved prompts.json, so no OpenRouter key is needed):
  python schematic_generation/compare_frontier.py --base_url http://127.0.0.1:8080/v1 --models <model id>
Fully local, with symbols picked by the search-tool selector (modules/symbol_search.py):
  python schematic_generation/compare_frontier.py --out_dir runs/local --base_url http://127.0.0.1:8080/v1       --selector_model <model id> --selector_base_url http://127.0.0.1:8080/v1 --models <model id>
"""
import argparse, json, os, re, sys, time
from pathlib import Path

project_path = os.environ["PROJECT_PATH"]
sys.path.append(project_path)

import openai
from config import prepare_context
from modules.symbol_context import load_related_symbols, describe_symbol_info
from modules.symbol_search import select_symbols_with_tools

RAW_CASES = {
    "usb_b": "I would like to add a USB_B connector interface in the schematic, exporting two labels, namely D+ and D-. Make the schematic as simple as possible.",
    "led": "Add a red LED with a current-limiting resistor powered from a +3V3 supply to GND. Make the schematic as simple as possible.",
    "ldo": "Add an AMS1117-3.3 linear regulator that converts +5V to +3V3, with a 10uF input capacitor and a 22uF output capacitor to GND.",
}
DATASET_ROWS = [1481, 1291, 5494]
SELECTOR_MODEL = "openai/gpt-5.2"  # same symbol selector generate.py uses

client = openai.OpenAI(base_url="https://openrouter.ai/api/v1",
                       api_key=os.environ.get("OPENROUTER_API_KEY", "missing"))


class OpenRouterSelector:
    """Minimal stand-in for the repo's LLM wrapper, as load_related_symbols uses it."""
    def get_json_response_retry(self, messages, schema, retries=3):
        for attempt in range(retries):
            try:
                c = client.beta.chat.completions.parse(
                    model=SELECTOR_MODEL, messages=messages, response_format=schema)
                msg = c.choices[0].message
                if msg.parsed:
                    return msg.content, msg.parsed
            except Exception as e:
                print(f"  selector retry {attempt + 1}: {type(e).__name__}")
                time.sleep(3)
        raise RuntimeError("symbol selection failed")


def raw_messages(prompt: str, selector=None):
    # Mirrors run_raw_test() in generate.py. selector(prompt) -> symbols replaces GPT-5.2 selection.
    symbols = (selector(prompt) if selector else
               load_related_symbols(llm=OpenRouterSelector(), sch_request=prompt,
                                    organized_lib_path="./export/organized_lib.json"))
    symbol_context = describe_symbol_info(symbols)
    msgs = prepare_context()
    msgs.append({"role": "user", "content": f"""
                    The user request is: {prompt}
                    ###
                        We have the following symbols and their related context information as listed below: {symbol_context}
                    ###
                        When determining the rotation and mirror of a symbol, REMEMBER to refer to the symbol information and compare it with the actual connections. When determining the connections, remember to refer to the pin location of symbols you have put on the schematic before.
                        Limit your thinking process less than 1000 words.
                    """})
    return msgs


def extract_code(text: str) -> str:
    # Same cleanup as get_final_python(), plus: take the last fenced block if present.
    blocks = re.findall(r"```(?:[Pp]ython)?\s*\n(.*?)\n\s*```", text, re.S)
    return (blocks[-1] if blocks else text).strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--dataset", default="SchGen_dataset.jsonl")
    ap.add_argument("--out_dir", default="runs_2026-11-08/frontier")
    ap.add_argument("--max_tokens", type=int, default=32000)
    ap.add_argument("--base_url", default=None,
                    help="OpenAI-compatible server for the models under test, e.g. "
                         "http://127.0.0.1:8080/v1 (default: OpenRouter). Prompt building "
                         "and symbol selection still use OpenRouter.")
    ap.add_argument("--extra_body", default="{}",
                    help='JSON merged into each request, e.g. \'{"chat_template_kwargs": {"enable_thinking": false}}\'')
    ap.add_argument("--tag", default="", help="suffix for output names, to keep variants apart")
    ap.add_argument("--selector_model", default=None,
                    help="pick symbols with the search-tool selector and this model instead of GPT-5.2 "
                         "(use a new --out_dir, since prompts.json is reused when present)")
    ap.add_argument("--selector_base_url", default=None, help="server for --selector_model (default: OpenRouter)")
    ap.add_argument("--selector_extra_body", default='{"chat_template_kwargs": {"enable_thinking": false}}',
                    help="JSON merged into selector requests")
    args = ap.parse_args()
    extra_body = json.loads(args.extra_body)
    selector = None
    if args.selector_model:
        sel_client = (openai.OpenAI(base_url=args.selector_base_url, api_key="local", timeout=600)
                      if args.selector_base_url else client)
        sel_extra = json.loads(args.selector_extra_body)
        selector = lambda p: select_symbols_with_tools(sel_client, args.selector_model, p, extra_body=sel_extra)
    gen_client = (openai.OpenAI(base_url=args.base_url, api_key="local", timeout=1800)
                  if args.base_url else client)
    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)

    # Build every prompt once so all models get identical input.
    prompts_path = out / "prompts.json"
    if prompts_path.exists():
        cases = json.loads(prompts_path.read_text(encoding="utf-8"))
    else:
        cases = {}
        for name, prompt in RAW_CASES.items():
            print(f"building prompt {name}")
            cases[name] = raw_messages(prompt, selector)
        rows = [json.loads(l) for l in open(args.dataset, encoding="utf-8")]
        for i in DATASET_ROWS:
            cases[f"ds{i}"] = rows[i]["messages"][:2]
        prompts_path.write_text(json.dumps(cases, indent=1), encoding="utf-8")

    usage = []
    for model in args.models:
        tag = model.split("/")[-1] + (f"-{args.tag}" if args.tag else "")
        for name, msgs in cases.items():
            dst = out / f"generated_{tag}_{name}.py"
            if dst.exists():
                print(f"skip {dst.name}"); continue
            t0 = time.time()
            try:
                r = gen_client.chat.completions.create(model=model, messages=msgs,
                                                   max_tokens=args.max_tokens,
                                                   extra_body={"usage": {"include": True}, **extra_body})
            except Exception as e:
                print(f"{tag} {name}: ERROR {type(e).__name__}: {str(e)[:200]}"); continue
            text = r.choices[0].message.content or ""
            dst.write_text(extract_code(text), encoding="utf-8")
            (out / f"raw_{tag}_{name}.txt").write_text(text, encoding="utf-8")
            u = r.usage
            usage.append({"model": tag, "case": name, "secs": round(time.time() - t0),
                          "in": u.prompt_tokens, "out": u.completion_tokens,
                          "cost": getattr(u, "cost", None)})
            print(usage[-1])
    with open(out / "usage.jsonl", "a", encoding="utf-8") as f:
        for u in usage:
            f.write(json.dumps(u) + "\n")


if __name__ == "__main__":
    main()
