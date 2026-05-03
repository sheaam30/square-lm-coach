#!/usr/bin/env python3
"""
Live LLM evals — requires Ollama to be running.

Sends representative shot scenarios to your local Ollama model and scores
each response against quality criteria. Use this after changing the prompt
template or switching models.

Usage:
    python evals/run_evals.py
    python evals/run_evals.py --model llama3.1:8b
    python evals/run_evals.py --ollama http://localhost:11434 --model llama3.2
    python evals/run_evals.py --verbose        # print full responses
"""

import argparse
import json
import os
import sys
import time

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from main import DEFAULT_PROMPT
from tests.test_evals import ALL_SCENARIOS, score_response

# ── Colour helpers ────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def green(s):  return f"{GREEN}{s}{RESET}"
def red(s):    return f"{RED}{s}{RESET}"
def yellow(s): return f"{YELLOW}{s}{RESET}"
def bold(s):   return f"{BOLD}{s}{RESET}"


# ── Ollama call ───────────────────────────────────────────────────────────────

def call_ollama(url: str, model: str, prompt: str) -> str:
    """Call Ollama synchronously and return the full response text."""
    resp = requests.post(
        f"{url}/api/generate",
        json={"model": model, "prompt": prompt, "stream": True},
        stream=True,
        timeout=120,
    )
    resp.raise_for_status()
    tokens = []
    for raw in resp.iter_lines():
        if raw:
            data = json.loads(raw)
            tokens.append(data.get("response", ""))
            if data.get("done"):
                break
    return "".join(tokens)


# ── Eval runner ───────────────────────────────────────────────────────────────

def run_evals(ollama_url: str, model: str, prompt_template: str, verbose: bool) -> bool:
    print(f"\n{bold('Golf Shot Analyzer — LLM Evals')}")
    print(f"Model  : {model}")
    print(f"Ollama : {ollama_url}")
    print(f"Scenarios: {len(ALL_SCENARIOS)}\n")
    print("─" * 60)

    # Verify Ollama is reachable
    try:
        requests.get(f"{ollama_url}/api/tags", timeout=5).raise_for_status()
    except Exception as e:
        print(red(f"\nERROR: Cannot reach Ollama at {ollama_url}"))
        print(f"  {e}")
        print("\nMake sure Ollama is running: ollama serve")
        return False

    total_passed = 0
    total_failed = 0
    all_results  = []

    for scenario_name, shot_data in ALL_SCENARIOS:
        print(f"\n{bold(scenario_name.upper().replace('_', ' '))} "
              f"({shot_data['club_name']}, {shot_data['handed']} handed)")

        prompt = prompt_template.format(**shot_data)

        start = time.time()
        try:
            response = call_ollama(ollama_url, model, prompt)
        except requests.HTTPError as e:
            print(red(f"  HTTP error: {e}"))
            total_failed += len(score_response("", shot_data))
            continue
        except Exception as e:
            print(red(f"  Error calling Ollama: {e}"))
            total_failed += len(score_response("", shot_data))
            continue
        elapsed = time.time() - start

        scores = score_response(response, shot_data)
        passed = sum(v for v in scores.values())
        failed = sum(not v for v in scores.values())
        total_passed += passed
        total_failed += failed

        # Print criteria results
        for criterion, result in scores.items():
            icon = green("✓") if result else red("✗")
            label = criterion.replace("_", " ")
            print(f"  {icon} {label}")

        word_count = len(response.split())
        status = green(f"{passed}/{len(scores)} passed") if failed == 0 \
                 else yellow(f"{passed}/{len(scores)} passed") if failed <= 1 \
                 else red(f"{passed}/{len(scores)} passed")
        print(f"  → {status}  |  {word_count} words  |  {elapsed:.1f}s")

        if verbose:
            print(f"\n  {YELLOW}Response:{RESET}")
            for line in response.strip().splitlines():
                print(f"    {line}")

        all_results.append({
            "scenario": scenario_name,
            "scores": scores,
            "passed": passed,
            "failed": failed,
            "word_count": word_count,
            "elapsed": round(elapsed, 2),
        })

    # ── Summary ───────────────────────────────────────────────────────────────
    total_criteria = total_passed + total_failed
    pct = int(100 * total_passed / total_criteria) if total_criteria else 0

    print("\n" + "─" * 60)
    print(bold("SUMMARY"))
    print(f"  Scenarios : {len(ALL_SCENARIOS)}")
    print(f"  Criteria  : {total_passed}/{total_criteria} passed ({pct}%)")

    failed_scenarios = [r for r in all_results if r["failed"] > 0]
    if failed_scenarios:
        print(f"\n  {yellow('Scenarios with failures:')}")
        for r in failed_scenarios:
            failed_criteria = [k for k, v in r["scores"].items() if not v]
            print(f"    • {r['scenario']}: {', '.join(failed_criteria)}")

    if total_failed == 0:
        print(f"\n  {green('All criteria passed.')} ✓")
    elif pct >= 80:
        print(f"\n  {yellow('Most criteria passed — review failures above.')}")
    else:
        print(f"\n  {red('Significant failures — consider revising the prompt template.')}")

    print()
    return total_failed == 0


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Live LLM evals for Golf Shot Analyzer")
    ap.add_argument("--ollama",  default="http://localhost:11434", help="Ollama server URL")
    ap.add_argument("--model",   default="llama3.2",              help="Ollama model name")
    ap.add_argument("--verbose", action="store_true",              help="Print full LLM responses")
    args = ap.parse_args()

    ok = run_evals(
        ollama_url=args.ollama,
        model=args.model,
        prompt_template=DEFAULT_PROMPT,
        verbose=args.verbose,
    )
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
