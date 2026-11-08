# Handover: SchGen inference on a rented GPU

Nov 8, 2026 · @Rob

## Goal and context

The user wants to type a plain-English circuit description, have Microsoft's fine-tuned SchGen model turn it into KiCad schematic code, and inspect the resulting schematic themselves. Their machine can't run the model as released, so inference runs on a rented GPU and the schematic is built and viewed locally.

SchGen (arXiv 2605.30345) is a LoRA fine-tune of gpt-oss-20b. It writes Python calls to a small KiCad schematic API, not raw .kicad_sch text. Code and model are public: the GitHub repo microsoft/SchGen, the Hugging Face model microsoft/SchGen, and the dataset microsoft/SchGen_dataset (8,420 rows).

## Decisions and constraints

The agreed approach is to rent a GPU for inference only, then run the schematic-building step on the user's own machine. Training, replicating the paper's benchmark numbers, and teacher-model data generation are out of scope.

- **User hardware:** RTX 5070 Ti (16 GB VRAM) and 64 GB RAM. OS not stated.
- **Why not local:** the repo's training script loads gpt-oss-20b dequantized to bf16, roughly 40 GB of weights by estimate. CPU offload would run but slowly, and a merge-to-GGUF route is untested.
- **Terms:** *inference* = the model turns the description into Python. *Schematic generation* = KiCad executes that Python to produce the .kicad_sch. Do not mix these up.
- **User preferences:** answer succinctly and skip explanatory detail unless asked. **Ask before writing any code**, including glue scripts. If code is approved: scripting and GitHub Actions in pwsh; compiled code in F#, then C#. The SchGen repo itself is Python.

## What is known

Sources are the [repo README](https://github.com/microsoft/SchGen), the [dataset card](https://huggingface.co/datasets/microsoft/SchGen_dataset), the [paper](https://arxiv.org/html/2605.30345v1), and the user's pasted `training/train.py`, all opened this session.

- **Run a prompt:** `schematic_generation/generate.py --test_raw --prompt "<request>"` loads microsoft/SchGen from Hugging Face by default, or a local path via `--model_path`. Output goes to `schematic_generation/generated.py`.
- **Build the schematic:** `init_project.py <project_name> <path to generated.py>` creates a KiCad project and executes the code to produce the .kicad_sch.
- **README example prompt:** "I would like a 3.3V voltage regulator based on AP2112K".
- **Environment per README:** Python 3.10 (Conda suggested), torch 2.8.0 for CUDA 12.8, `requirements.txt`, then trl >= 0.20, peft >= 0.17, transformers >= 4.55, trackio and flash-attn; `PROJECT_PATH` environment variable; KiCad 8.0.9, whose bundled Python is configured in `config.py`.
- **Prompt includes symbol context:** commented test code in `train.py` builds the prompt from the request plus `symbol_context`. The README produces that context by scanning KiCad libraries with `modules/utils/kicad_scan_lib.py`, which writes `export/organized_lib.json` and `export/organized_fp.json`. Whether `generate.py` reads those files is unverified.
- **Model format:** almost certainly a LoRA adapter over `openai/gpt-oss-20b` (a third-party demo loads it that way). The model card itself has not been opened.
- **How it was trained:** base loaded with `Mxfp4Config(dequantize=True)` in bf16; LoRA r=8, alpha=16; max length 13,312 tokens; a modified chat template (`training/modified_chat_template.txt`, reasoning effort set high per a code comment).
- **Authors' hardware:** one 80 GB A100 for training and inference.
- **Dataset:** one train split of 8,420 rows with `messages` (system, user, assistant) and `meta`. No held-out test split is published.
- **Rough quality expectation (paper, in-distribution):** 82.0% valid circuits and 60.5% expert-judged functional correctness. On unseen GitHub designs, 65.59% valid. The README warns outputs may contain wrong connections and need human review.

## Plan

Run inference remotely and everything KiCad-related locally. Each step names where it runs. **Read the three files in the first step before provisioning any GPU, and ask the user before writing code.**

1. **Local: read the code first.** Get `schematic_generation/generate.py`, `config.py` and `modules/utils/llm_interface.py` (clone the repo, or ask the user to paste them; GitHub blocks automated fetching of repo subfolders). Settle the unknowns in the next section.
2. **Local: install KiCad 8.0.9** and clone the repo. Run the symbol scan (`kicad_scan_lib.py`) to produce the `export/*.json` files, and keep them for the remote box.
3. **Remote: choose and start a GPU.** The authors used one 80 GB A100. The bf16 weights are about 40 GB (estimate), so a 48 GB card may be tight with long outputs. Set a spend limit and plan to stop the instance when idle.
4. **Remote: set up the environment** as in the README: Python 3.10, torch 2.8.0 for CUDA 12.8, the requirements, `PROJECT_PATH`. Copy over the `export/*.json` files. Whether the remote box also needs KiCad depends on step 1.
5. **Remote: smoke test.** Run `generate.py --test_raw` with the README example prompt. Record GPU memory used, time per generation, and whether `microsoft/SchGen` loaded without errors.
6. **Transfer:** copy `generated.py` back to the user's machine.
7. **Local: build and inspect.** Run `init_project.py <name> <generated.py>` with KiCad 8.0.9, open the project, run ERC, and review the schematic by eye.
8. **Iterate** with 3 to 5 of the user's own descriptions, both concise and detailed. Then stop the remote instance.

## Unknowns and risks

The biggest unknown is how `generate.py` loads the model, because it decides what GPU is needed. None of the items below were verified.

- **Model loading:** whether `generate.py` loads bf16 (about 40 GB) or a 4-bit format, and whether it applies the modified chat template and high reasoning effort used in training.
- **Remote KiCad:** whether generation needs KiCad on the GPU box, or only the symbol-context JSON files.
- **API key:** the README lists an OpenRouter key in `config.py` under prerequisites. Probably only for dataset construction, not inference. Keep keys out of any file copied to a rented machine unless needed.
- **Model card:** `microsoft/SchGen` has not been opened. Confirm it is an adapter, its size, and any usage notes.
- **flash-attn:** the README installs it. Building it can be slow or fiddly depending on GPU and CUDA version.
- **KiCad version:** the repo targets 8.0.9. Use that version for the build step, even if the user has a newer one installed.
- **Output quality:** training data covers small and medium SparkFun-style modules. The repo says outputs may be wrong and need review, and wiring uses a basic auto-router.
- **Undocumented `evaluation/` folder:** may hold metric scripts, but is not needed for this task.
- **Cost:** no prices were researched. A forgotten running instance is the main risk.

## Success criteria and hand-back

Done means the user has opened at least one model-generated schematic in KiCad and judged it themselves.

- [ ] The README example prompt produces `generated.py` on the remote GPU.
- [ ] `init_project.py` builds a .kicad_sch locally with KiCad 8.0.9 and it opens without errors.
- [ ] ERC has been run and its result noted.
- [ ] 3 to 5 of the user's own descriptions have been tried the same way.

Hand back to the user, briefly: the GPU type, VRAM used and time per generation; the exact commands that worked; any deviations from the README; and the ERC result and any errors for each prompt. Do not start training, quantization or an evaluation harness unless the user asks.

## Sources

Pages opened this session, unless marked otherwise.

- [microsoft/SchGen repo and README](https://github.com/microsoft/SchGen)
- [SchGen paper, arXiv 2605.30345 (full text)](https://arxiv.org/html/2605.30345v1)
- [microsoft/SchGen_dataset](https://huggingface.co/datasets/microsoft/SchGen_dataset)
- [KiCad 8.0.9 and other releases](https://github.com/KiCad/kicad-source-mirror/releases), linked from the README
- [Third-party demo that loads the adapter on gpt-oss-20b](https://mokshmannu-schgen-demo.static.hf.space/index.html), seen in a search result only
- microsoft/SchGen model card on Hugging Face: not opened
