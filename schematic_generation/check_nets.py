"""
Build generated schematic code with init_project.py, export a KiCad netlist, and print the nets.
For dataset rows (case names dsNNNN), also score against Microsoft's reference answer.

Inputs are files named generated_<tag>_<case>.py (compare_frontier.py output), or
generated_<case>.py, where the folder name is then used as the tag.
Reference answers are <ref_dir>/reference_dsNNNN.txt, split at '===== REFERENCE ANSWER ====='.

Usage (repo root, PROJECT_PATH set, sexpdata installed):
  python schematic_generation/check_nets.py --gen_dir runs/frontier runs/local --ref_dir runs/dataset
"""
import argparse, os, re, shutil, subprocess, sys
from collections import Counter
from pathlib import Path

import sexpdata

REPO = Path(__file__).resolve().parents[1]
HEADER = ("# Auto-generated schematic symbols\nimport sys\nimport os\n\n"
          "PROJECT_PATH = os.environ['PROJECT_PATH']\nsys.path.append(PROJECT_PATH)\n"
          "from modules.kicad_sch_interface import *\n\n")
RAW_CASES = ["usb_b", "led", "ldo"]
REF_MARK = "===== REFERENCE ANSWER ====="


def default_cli():
    found = shutil.which("kicad-cli")
    if found:
        return found
    sys.path.append(str(REPO))
    from config import pcbnew_path  # KiCad's bin folder holds kicad-cli too
    return str(Path(pcbnew_path).parent / ("kicad-cli.exe" if os.name == "nt" else "kicad-cli"))


def find_sources(gen_dirs):
    """Yield (tag, case, code) for every generated_*.py in the given folders."""
    for d in map(Path, gen_dirs):
        for f in sorted(d.glob("generated_*.py")):
            stem = f.stem[len("generated_"):]
            m = re.fullmatch(r"(?:(.+)_)?(ds\d+|" + "|".join(RAW_CASES) + ")", stem)
            if not m:
                continue
            yield (m.group(1) or d.name), m.group(2), f.read_text(encoding="utf-8")


def build(work, cli, tag, case, code):
    """Returns (netlist text, None) or (None, error)."""
    name = re.sub(r"[^\w-]", "_", f"{tag}_{case}")
    code = re.sub(r'^\s*set_schematic_filename\(.*\)\s*$', "", code, flags=re.M)
    if "from modules.kicad_sch_interface import *" not in code:
        code = HEADER + code
    py = work / f"{name}.py"
    py.write_text(code, encoding="utf-8")
    r = subprocess.run([sys.executable, str(REPO / "init_project.py"), name, str(py), "--overwrite"],
                       cwd=work, capture_output=True, text=True, encoding="utf-8", errors="replace")
    sch = work / name / f"{name}.kicad_sch"
    if r.returncode != 0 or not sch.exists():
        err = [l for l in (r.stdout + r.stderr).splitlines() if "rror" in l]
        return None, (err[-1] if err else "build failed")[:160]
    net = work / name / f"{name}.net"
    subprocess.run([cli, "sch", "export", "netlist", "--output", str(net), str(sch)],
                   capture_output=True, text=True)
    return (net.read_text(encoding="utf-8") if net.exists() else None), (None if net.exists() else "netlist export failed")


def _kids(node, tag):
    return [x for x in node[1:] if isinstance(x, list) and x and str(x[0]) == tag]


def _val(node, tag):
    k = _kids(node, tag)
    return str(k[0][1]) if k and len(k[0]) > 1 else ""


def parse(netlist):
    """Parts by symbol name, and nets as (sorted 'Symbol.pin' nodes, net name or '' if auto-named)."""
    tree = sexpdata.loads(netlist)
    parts = {}
    for comps in _kids(tree, "components"):
        for c in _kids(comps, "comp"):
            ls = _kids(c, "libsource")
            parts[_val(c, "ref")] = _val(ls[0], "part") if ls else "?"
    nets = []
    for ns in _kids(tree, "nets"):
        for n in _kids(ns, "net"):
            name = _val(n, "name")
            nodes = sorted(f"{parts.get(_val(x, 'ref'), _val(x, 'ref'))}.{_val(x, 'pin')}" for x in _kids(n, "node"))
            auto = name.startswith(("Net-(", "unconnected-"))
            if len(nodes) >= 2 or not auto:
                nets.append((tuple(nodes), "" if auto else name.lstrip("/")))
    return Counter(parts.values()), nets


def score(gen, ref):
    """Matching parts by type, and nets whose pin groups match exactly (names ignored)."""
    gp, gn = gen
    rp, rn = ref
    parts = sum((gp & rp).values())
    nets = Counter(n for n, _ in gn) & Counter(n for n, _ in rn)
    return f"parts {parts}/{sum(rp.values())}  nets {sum(nets.values())}/{len(rn)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen_dir", nargs="+", required=True, help="folders with generated_*.py files")
    ap.add_argument("--ref_dir", default=None, help="folder with reference_dsNNNN.txt files")
    ap.add_argument("--work_dir", default="runs/netcheck", help="where projects are built")
    ap.add_argument("--kicad_cli", default=None)
    args = ap.parse_args()
    cli = args.kicad_cli or default_cli()
    work = Path(args.work_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    # The footprint step reads export/organized_fp.json relative to the working directory.
    if not (work / "export").exists():
        if os.name == "nt":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(work / "export"), str(REPO / "export")],
                           check=True, capture_output=True)
        else:
            (work / "export").symlink_to(REPO / "export", target_is_directory=True)

    results = {}
    if args.ref_dir:
        for f in sorted(Path(args.ref_dir).glob("reference_ds*.txt")):
            case = f.stem[len("reference_"):]
            net, err = build(work, cli, "reference", case, f.read_text(encoding="utf-8").split(REF_MARK)[1])
            results[("reference", case)] = parse(net) if net else err
    for tag, case, code in find_sources(args.gen_dir):
        net, err = build(work, cli, tag, case, code)
        results[(tag, case)] = parse(net) if net else err
        print(f"built {tag:36s} {case:7s} {'ok' if net else 'FAILED: ' + err}", flush=True)

    tags = sorted({t for t, _ in results if t != "reference"})
    cases = sorted({c for _, c in results})
    print("\n=== Dataset rows vs reference (parts by type, nets with identical pin groups) ===")
    for case in (c for c in cases if c.startswith("ds")):
        ref = results.get(("reference", case))
        for tag in tags:
            g = results.get((tag, case))
            if g is None:
                continue
            ok = isinstance(g, tuple) and isinstance(ref, tuple)
            print(f"{case} {tag:36s} " + (score(g, ref) if ok else f"n/a ({ref if g and not isinstance(ref, tuple) else g})"))

    print("\n=== Other cases: nets per run ===")
    for case in (c for c in cases if not c.startswith("ds")):
        for tag in tags:
            g = results.get((tag, case))
            if g is None:
                continue
            if isinstance(g, str):
                print(f"{case} {tag}: build failed: {g}")
                continue
            print(f"{case} {tag}: parts {dict(g[0])}")
            for nodes, name in g[1]:
                print(f"     {name or '(net)':8s} {', '.join(nodes)}")


if __name__ == "__main__":
    main()
