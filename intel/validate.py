"""
validate.py — fail loudly if a ledger is malformed or its hash chain is broken.

Run by CI after every scout/agent write and by the routine before it commits:
    python -m intel.validate <ledger_dir>        (exit 1 on any problem)
"""
from __future__ import annotations

import sys
from typing import List, Optional

from intel.ledger import KINDS, read_records, verify_chain
from intel.schema import validate_call, validate_decision, validate_run

_CHECK = {"calls": (validate_call, "call_id"), "decisions": (validate_decision, "decision_id"),
          "runs": (validate_run, "run_id")}


def validate_ledger(ledger_dir: str) -> List[str]:
    errs: List[str] = []
    for kind in KINDS:
        check, id_field = _CHECK[kind]
        for i, rec in enumerate(read_records(ledger_dir, kind)):
            errs += [f"{kind}[{i}] ({rec.get(id_field)}): {e}" for e in check(rec)]
        errs += verify_chain(ledger_dir, kind)
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
