"""python -m pitchlens.evals {factcheck,ask,director,report} [--limit N] [--fresh] [--speed X]"""

from __future__ import annotations

import argparse
import asyncio

from . import ask, director, factcheck, report
from .common import ProviderUnavailable


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m pitchlens.evals", description=__doc__)
    parser.add_argument("suite", choices=["factcheck", "ask", "director", "report"])
    parser.add_argument("--limit", type=int, default=None, help="run at most N new cases")
    parser.add_argument("--fresh", action="store_true", help="discard saved results and start over")
    parser.add_argument("--speed", type=float, default=6.0, help="director: replay speed")
    args = parser.parse_args()
    try:
        run_suite(args)
    except ProviderUnavailable as exc:
        print(f"stopped: the model is unavailable ({exc}). Results so far are saved; run again to resume.")
    print(f"wrote {report.write()}")


def run_suite(args: argparse.Namespace) -> None:
    if args.suite == "factcheck":
        results = asyncio.run(factcheck.run(limit=args.limit, fresh=args.fresh))
        print(factcheck.summarise(list(results.done.values())))
    elif args.suite == "ask":
        results = asyncio.run(ask.run(limit=args.limit, fresh=args.fresh))
        print(ask.summarise(list(results.done.values())))
    elif args.suite == "director":
        results = asyncio.run(director.run(speed=args.speed, fresh=args.fresh))
        for row in results.done.values():
            print({k: v for k, v in row.items() if k != "reasons"})


if __name__ == "__main__":
    main()
