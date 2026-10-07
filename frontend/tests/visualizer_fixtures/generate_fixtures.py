"""VISUALIZERS feasibility evidence only; reuse the existing TRACING/ENCODER oracle pipeline."""

import json
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CASES = {
    "scalar": "a=[1,2]\na[1]=3\npass",
    "nested": "inner=[1]\nouter=[inner]\ninner[0]=2\npass",
    "history-a": "a=[1,2]\na[:]=[1,3]\npass",
    "history-b": "a=[9,3]\na[:]=[1,3]\npass",
}


def main():
    assert sys.version_info[:3] == (3, 13, 16)
    oracle = runpy.run_path(str(ROOT / "frontend/tests/trace/generate_fixtures.py"))
    target = Path(__file__).parent / "fixtures"
    target.mkdir(exist_ok=True)
    for name, code in CASES.items():
        trace = oracle["reference_document"](code, 128)
        # Includes contract validation and independent expected-state decoding.
        expected = oracle["expected_steps"](trace)
        line = 4 if name == "nested" else 3
        event = next(
            e for e in trace["events"] if e["kind"] == "LINE" and e["source"]["lineNumber"] == line
        )
        step = event["index"] + 1
        document = {
            "source": "TRACING reference execution + ENCODER encoder/independent decoder",
            "trace": trace,
            "targetStep": step,
            "currentState": expected[step - 1]["state"],
            "predecessorState": expected[step - 2]["state"],
            "expectedPositions": {"o2": [0]}
            if name == "nested"
            else {"o1": [0 if name == "history-b" else 1]},
        }
        (target / (name + ".json")).write_text(json.dumps(document, indent=2) + "\n")
        print(name, "Step", step, "expected", document["expectedPositions"])


if __name__ == "__main__":
    main()
