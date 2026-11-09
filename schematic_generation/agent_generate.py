"""
Generate schematics with the tool-using agent (modules/schematic_agent.py) on a local model.

One request:
  python schematic_generation/agent_generate.py --prompt "Add a red LED ..." --name my_led
The benchmark cases (our three prompts plus dataset rows 1481, 1291, 5494):
  python schematic_generation/agent_generate.py
Outputs: <out_dir>/generated_agent_<name>.py, the KiCad project in <out_dir>/build/<name>/,
and one line per run in <out_dir>/agent_log.jsonl. Score with check_nets.py --gen_dir <out_dir>.
Run from the repo root with PROJECT_PATH set.
"""
import argparse, json, os, sys
from pathlib import Path

sys.path.append(os.environ.get("PROJECT_PATH", "."))
import openai
from modules.schematic_agent import SchematicAgent

RAW_CASES = {
    "usb_b": "I would like to add a USB_B connector interface in the schematic, exporting two labels, namely D+ and D-. Make the schematic as simple as possible.",
    "led": "Add a red LED with a current-limiting resistor powered from a +3V3 supply to GND. Make the schematic as simple as possible.",
    "ldo": "Add an AMS1117-3.3 linear regulator that converts +5V to +3V3, with a 10uF input capacitor and a 22uF output capacitor to GND.",
}
DATASET_ROWS = [1481, 1291, 5494]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default=None, help="a single request (default: run the benchmark cases)")
    ap.add_argument("--name", default="request")
    ap.add_argument("--base_url", default="http://127.0.0.1:8080/v1")
    ap.add_argument("--model", default="qwen3.8-flash-next-iq3_xxs")
    ap.add_argument("--extra_body", default='{"chat_template_kwargs": {"enable_thinking": false}}')
    ap.add_argument("--out_dir", default="runs/agent")
    ap.add_argument("--dataset", default="SchGen_dataset.jsonl")
    ap.add_argument("--cases", nargs="*", default=None, help="subset of benchmark case names, e.g. led ds5494")
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    client = openai.OpenAI(base_url=args.base_url, api_key="local", timeout=900)
    agent = SchematicAgent(client, args.model, work_dir=out / "build", extra_body=json.loads(args.extra_body))

    if args.prompt:
        cases = {args.name: args.prompt}
    else:
        rows = [json.loads(l) for l in open(args.dataset, encoding="utf-8")]
        cases = {**RAW_CASES, **{f"ds{i}": rows[i]["messages"][1]["content"] for i in DATASET_ROWS}}
        if args.cases:
            cases = {k: v for k, v in cases.items() if k in args.cases}

    for name, request in cases.items():
        print(f"== {name}", flush=True)
        try:
            res = agent.run(request, name)
        except Exception as e:
            res = {"name": name, "ok": False, "error": f"{type(e).__name__}: {e}"}
        if res.get("code"):
            (out / f"generated_agent_{name}.py").write_text(res["code"], encoding="utf-8")
        with open(out / "agent_log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({k: v for k, v in res.items() if k != "code"}) + "\n")
        print(f"   {'OK' if res.get('ok') else 'FAILED'} in {res.get('secs')} s, {res.get('turns')} turns, "
              f"tools: {dict((t, res.get('tool_calls', []).count(t)) for t in set(res.get('tool_calls', [])))}"
              + (f", error: {res['error']}" if res.get("error") else ""), flush=True)
        print("   " + res.get("report", "").replace("\n", "\n   "), flush=True)


if __name__ == "__main__":
    main()
