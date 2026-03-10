#!/usr/bin/env python3
"""
Optimization patch based on performance analysis (2026-02-05):
1. Hour 00:00 UTC blackout (-$187 prevention)
2. Skip 0.80-0.90 entry prices (49.5% WR, -$165)
3. Tighten 0.90+ to absolute minimum
4. Early morning filter (hours 00-02 bad)

Run on VPS: cd /opt/polymarket-bot && python3 patch_optimization.py
"""
import sys


def read_lines(filepath):
    with open(filepath, 'r') as f:
        return f.readlines()


def write_lines(filepath, lines):
    with open(filepath, 'w') as f:
        f.writelines(lines)


def validate_line(lines, idx, expected_fragment, filepath):
    """Validate that line at idx contains expected_fragment. idx is 1-based."""
    actual = lines[idx - 1].strip()
    if expected_fragment not in actual:
        print(f"  VALIDATION FAILED at {filepath}:{idx}")
        print(f"    Expected: {expected_fragment!r}")
        print(f"    Actual:   {actual!r}")
        return False
    return True


def insert_after_line(lines, line_num, new_content):
    """Insert new_content after 1-based line_num."""
    new_lines = new_content.split('\n')
    for i, nl in enumerate(new_lines):
        lines.insert(line_num + i, nl + '\n')
    return lines


# ============================================================
# PATCH 1: run_signal_bot.py — Hour blackout
# ============================================================
def patch_run_signal_bot():
    filepath = '/opt/polymarket-bot/run_signal_bot.py'
    print(f"\nPatching {filepath}...")

    lines = read_lines(filepath)

    # Find the SELL signal block (line with "if direction == "SELL":")
    # Insert hour blackout BEFORE it
    sell_line = None
    for i, line in enumerate(lines):
        if 'if direction == "SELL":' in line and 'return' in lines[i+1]:
            sell_line = i + 1  # 1-based
            break

    if not sell_line:
        print("  ERROR: Could not find SELL signal block")
        return False

    print(f"  Found SELL block at line {sell_line}")

    # Insert hour blackout before the SELL block
    # We insert BEFORE the "# SELL signal: DISABLED" comment (2 lines before sell_line)
    comment_line = sell_line - 3  # The comment is 3 lines before the if
    # Find the exact comment line
    for i in range(max(0, sell_line - 5), sell_line):
        if '# SELL signal: DISABLED' in lines[i]:
            comment_line = i + 1  # 1-based
            break

    blackout_code = (
        '            # Hour blackout: skip signals during bad UTC hours\n'
        '            # Analysis: hour 00 = 0% WR (-$187), hours 01-02 also poor\n'
        '            _signal_hour = datetime.now(timezone.utc).hour\n'
        '            if _signal_hour in (0, 1):\n'
        '                logger.info(f"HOUR BLACKOUT: Skipping signal during UTC hour {_signal_hour}")\n'
        '                return\n'
        '            '
    )

    # Insert before the SELL comment
    for i, cl in enumerate(blackout_code.split('\n')):
        lines.insert(comment_line - 1 + i, cl + '\n')

    write_lines(filepath, lines)
    print(f"  OK: Hour blackout added before line {comment_line}")
    return True


# ============================================================
# PATCH 2: signal_executor.py — Skip 0.80-0.90, minimize 0.90+
# ============================================================
def patch_signal_executor():
    filepath = '/opt/polymarket-bot/signal_executor.py'
    print(f"\nPatching {filepath}...")

    lines = read_lines(filepath)

    # Find the line: if price >= 0.80:
    target_line = None
    for i, line in enumerate(lines):
        if 'if price >= 0.80:' in line:
            target_line = i + 1  # 1-based
            break

    if not target_line:
        print("  ERROR: Could not find 'if price >= 0.80:' line")
        return False

    print(f"  Found 0.80 sizing at line {target_line}")

    # Validate the next line is the old sizing
    if 'max_spend = max(5.0, min(available_capital * 0.025, 15.0))' not in lines[target_line]:  # 0-indexed = target_line
        print(f"  ERROR: Line {target_line+1} doesn't match expected sizing")
        print(f"  Actual: {lines[target_line].strip()}")
        return False

    # Replace the 0.80+ block with skip logic
    # Old (2 lines): if price >= 0.80: / max_spend = ...
    # New: if price >= 0.90: / max_spend = 5.0 (absolute min) / elif price >= 0.80: / return 0 (skip)
    old_line_idx = target_line - 1  # 0-indexed

    lines[old_line_idx] = '        if price >= 0.90:\n'
    lines[old_line_idx + 1] = '            max_spend = 5.00   # 0.90+: coin-flip zone, absolute minimum only\n'

    # Insert new elif for 0.80-0.90 SKIP
    skip_lines = [
        '        elif price >= 0.80:\n',
        '            # 0.80-0.90: 49.5% WR = coin flip, skip entirely\n',
        '            logger.info(f"SKIP: entry price {price:.2f} in 0.80-0.90 dead zone")\n',
        '            return 0\n',
    ]
    for j, sl in enumerate(skip_lines):
        lines.insert(old_line_idx + 2 + j, sl)

    write_lines(filepath, lines)
    print(f"  OK: 0.80-0.90 skip + 0.90+ minimized")
    return True


if __name__ == '__main__':
    print("=" * 50)
    print("Performance Optimization Patch")
    print("=" * 50)

    ok1 = patch_run_signal_bot()
    ok2 = patch_signal_executor()

    if ok1 and ok2:
        print(f"\n{'='*50}")
        print("ALL OPTIMIZATION PATCHES APPLIED")
        print(f"{'='*50}")
        print("\nVerify:")
        print("  python3 -m py_compile /opt/polymarket-bot/run_signal_bot.py")
        print("  python3 -m py_compile /opt/polymarket-bot/signal_executor.py")
        print("\nThen restart:")
        print("  systemctl restart polymarket-bot")
    else:
        print(f"\n{'='*50}")
        print("PATCHES FAILED — DO NOT RESTART")
        print(f"{'='*50}")
        sys.exit(1)
