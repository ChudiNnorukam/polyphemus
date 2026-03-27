#!/usr/bin/env python3
"""OpenClaw Orchestrator -- Run all engines in sequence.

Chains CMO -> CTO -> COO -> Memory -> CEO in the correct order.
Each engine logs to DB, auto-reflects, and passes context to the next.

Usage:
    python3 scripts/run_all.py              # Full loop (all 5 engines + CEO)
    python3 scripts/run_all.py --skip-ceo   # Daily engines only, no CEO weekly
    python3 scripts/run_all.py --only cmo   # Run single engine
"""

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


def _load_env():
    for path in [
        os.path.join(SCRIPT_DIR, '..', '.env'),
        '/opt/openclaw/.env',
    ]:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, _, v = line.partition('=')
                        os.environ.setdefault(k.strip(), v.strip())


_load_env()


def run_step(label, fn, *args, **kwargs):
    """Run a step with timing and error handling."""
    print()
    print(f'{"=" * 60}')
    print(f'  {label}')
    print(f'{"=" * 60}')
    t0 = time.time()
    try:
        fn(*args, **kwargs)
        elapsed = time.time() - t0
        return True, elapsed
    except Exception as e:
        elapsed = time.time() - t0
        print(f'\n  ERROR: {e}')
        return False, elapsed


def make_args(**kwargs):
    """Create a simple namespace with defaults for engine cmd functions."""
    ns = argparse.Namespace()
    ns.focus = kwargs.get('focus', None)
    ns.format = kwargs.get('format', 'text')
    return ns


def main():
    parser = argparse.ArgumentParser(description='OpenClaw Orchestrator')
    parser.add_argument('--skip-ceo', action='store_true', help='Skip CEO weekly brief')
    parser.add_argument('--only', choices=['cmo', 'cto', 'coo', 'memory', 'ceo'],
                        help='Run a single engine')
    args = parser.parse_args()

    print()
    print('OPENCLAW ORCHESTRATOR')
    print(f'{datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')

    results = []
    engine_args = make_args()

    steps = []

    if args.only:
        if args.only == 'cmo':
            from cmo_engine import cmd_daily as cmo_daily
            steps = [('CMO Daily', cmo_daily, engine_args)]
        elif args.only == 'cto':
            from cto_engine import cmd_daily as cto_daily
            steps = [('CTO Daily', cto_daily, engine_args)]
        elif args.only == 'coo':
            from coo_engine import cmd_daily as coo_daily
            steps = [('COO Daily', coo_daily, engine_args)]
        elif args.only == 'memory':
            from memory_engine import cmd_scan
            steps = [('Memory Scan', cmd_scan, engine_args)]
        elif args.only == 'ceo':
            from ceo_engine import cmd_weekly as ceo_weekly
            steps = [('CEO Weekly', ceo_weekly, engine_args)]
    else:
        from cmo_engine import cmd_daily as cmo_daily
        from cto_engine import cmd_daily as cto_daily
        from coo_engine import cmd_daily as coo_daily
        from memory_engine import cmd_scan
        from ceo_engine import cmd_weekly as ceo_weekly

        # Phase 1: Run CMO, CTO, COO in parallel (independent engines)
        parallel_steps = [
            ('CMO Daily', cmo_daily, engine_args),
            ('CTO Daily', cto_daily, engine_args),
            ('COO Daily', coo_daily, engine_args),
        ]

        # Phase 2: Sequential steps that depend on Phase 1
        sequential_steps = [
            ('Memory Scan', cmd_scan, engine_args),
        ]
        if not args.skip_ceo:
            sequential_steps.append(('CEO Weekly', ceo_weekly, engine_args))

        steps = parallel_steps + sequential_steps

    if args.only:
        # Single engine: run sequentially
        for label, fn, fn_args in steps:
            ok, elapsed = run_step(label, fn, fn_args)
            results.append((label, ok, elapsed))
    else:
        # Phase 1: run CMO/CTO/COO in parallel
        print(f'\n  Running {len(parallel_steps)} engines in parallel...')
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(run_step, label, fn, fn_args): label
                for label, fn, fn_args in parallel_steps
            }
            for future in as_completed(futures):
                label = futures[future]
                ok, elapsed = future.result()
                results.append((label, ok, elapsed))

        # Phase 2: Memory + CEO sequentially
        for label, fn, fn_args in sequential_steps:
            ok, elapsed = run_step(label, fn, fn_args)
            results.append((label, ok, elapsed))

    # Summary
    print()
    print('=' * 60)
    print('  ORCHESTRATOR SUMMARY')
    print('=' * 60)
    total_time = 0
    for label, ok, elapsed in results:
        status = 'OK' if ok else 'FAIL'
        print(f'  [{status}] {label} ({elapsed:.1f}s)')
        total_time += elapsed
    print(f'\n  Total: {total_time:.1f}s | {len(results)} steps | '
          f'{sum(1 for _, ok, _ in results if ok)} passed, '
          f'{sum(1 for _, ok, _ in results if not ok)} failed')
    print('=' * 60)


if __name__ == '__main__':
    main()
