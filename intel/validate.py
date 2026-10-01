"""
validate.py — fail loudly if a ledger is malformed or its hash chain is broken.

Run by CI after every scout/agent write and by the routine before it commits:
    python -m intel.validate <ledger_dir>        (exit 1 on any problem)
"""
from __future__ import annotations

import sys
from typing import List, Optional

from intel.ledger import read_records, verify_chain
from intel.schema import validate_call, validate_run


def validate_ledger(ledger_dir: str) -> List[str]:
    errs: List[str] = []
    for i, rec in enumerate(read_records(ledger_dir, "calls")):
        errs += [f"calls[{i}] ({rec.get('call_id')}): {e}" for e in validate_call(rec)]
    for i, rec in enumerate(read_records(ledger_dir, "runs")):
        errs += [f"runs[{i}] ({rec.get('run_id')}): {e}" for e in validate_run(rec)]
    errs += verify_chain(ledger_dir, "calls")
    errs += verify_chain(ledger_dir, "runs")
    return errs


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 1:
        print("usage: python -m intel.validate <ledger_dir>")
        return 2
    errs = validate_ledger(argv[0])
    for e in errs:
        print("INVALID:", e)
    print("ledger valid" if not errs else f"{len(errs)} problem(s) found")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
