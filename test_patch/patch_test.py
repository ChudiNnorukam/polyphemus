#!/usr/bin/env python3
"""
Patch script to integrate self_tuner.py into run_signal_bot.py and signal_executor.py.
Uses line-index insertion for robustness (per patching lessons).

Safety: reads files, validates expected content at target lines, then inserts.
Run on VPS: cd /Users/chudinnorukam/Projects/business/test_patch/opt/polymarket-bot && python3 patch_self_tuner.py
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
        print(f"    Expected fragment: {expected_fragment!r}")
        print(f"    Actual line:       {actual!r}")
        return False
    return True


def insert_after_line(lines, line_num, new_content):
    """Insert new_content after 1-based line_num. new_content is a string (may have \\n)."""
    new_lines = new_content.split('\n')
    # Add newlines to each inserted line
    for i, nl in enumerate(new_lines):
        lines.insert(line_num + i, nl + '\n')
    return lines


# ============================================================
# PATCH 1: signal_executor.py
# ============================================================
def patch_signal_executor():
    filepath = '/Users/chudinnorukam/Projects/business/test_patch/opt/polymarket-bot/signal_executor.py'
    print(f"\n{'='*50}")
    print(f"Patching {filepath}")
    print(f"{'='*50}")

    lines = read_lines(filepath)

    # Validate target lines exist as expected
    ok = True
    ok &= validate_line(lines, 72, 'self.total_volume = 0.0', filepath)
    ok &= validate_line(lines, 175, 'max_spend = min(max_spend, available_capital * 0.30)', filepath)

    if not ok:
        print("  ABORT: Line validation failed. File may have changed.")
        return False

    # Patch 1a: Add self.self_tuner = None in __init__ after line 72
    insert_after_line(lines, 72,
        '        self.self_tuner = None  # Set by SignalBot after init')

    # After insert, line numbers shift by 1
    # Original line 175 is now 176

    # Patch 1b: Add tuner multiplier after max_spend cap (now line 176)
    insert_after_line(lines, 176,
        ''
        '        # Self-tuning multiplier (safe: returns 1.0 on any error)\n'
        '        if hasattr(self, \'self_tuner\') and self.self_tuner:\n'
        '            _tuner_mult = self.self_tuner.get_multiplier(price)\n'
        '            if abs(_tuner_mult - 1.0) > 0.001:\n'
        '                _pre_tune = max_spend\n'
        '                max_spend = max_spend * _tuner_mult\n'
        '                logger.info(f"TUNER: ${_pre_tune:.2f} x {_tuner_mult:.3f} = ${max_spend:.2f}")')

    write_lines(filepath, lines)
    print(f"  OK: 2 patches applied")
    return True


# ============================================================
# PATCH 2: run_signal_bot.py
# ============================================================
def patch_run_signal_bot():
    filepath = '/Users/chudinnorukam/Projects/business/test_patch/opt/polymarket-bot/run_signal_bot.py'
    print(f"\n{'='*50}")
    print(f"Patching {filepath}")
    print(f"{'='*50}")

    lines = read_lines(filepath)

    # Validate target lines
    ok = True
    ok &= validate_line(lines, 36, 'from position_redeemer import PositionRedeemer', filepath)
    ok &= validate_line(lines, 141, 'self.position_redeemer = PositionRedeemer()', filepath)
    ok &= validate_line(lines, 991, 'HEALTH: {json.dumps(health)}', filepath)

    if not ok:
        print("  ABORT: Line validation failed. File may have changed.")
        return False

    # Patch 2a: Add import after line 36
    insert_after_line(lines, 36,
        'from self_tuner import SelfTuner')
    # Lines shift by +1

    # Patch 2b: Add init after line 142 (was 141, +1 from import)
    insert_after_line(lines, 142,
        '        \n'
        '        # Self-tuning position sizing (v2 consensus plan)\n'
        '        self.self_tuner = SelfTuner(\n'
        '            db_path="data/performance.db",\n'
        '            state_path="data/tuning_state.json"\n'
        '        )\n'
        '        self.executor.self_tuner = self.self_tuner')
    # Lines shift by +8 (1 blank + 1 comment + 4 init + 1 assign + 1 extra from split)

    # Patch 2c: Add tuner cycle AFTER health dict, BEFORE logger.info
    # Original lines: 989='}', 990=blank, 991=logger.info(HEALTH...)
    # After +1 (import) +7 (init lines) = +8 total shift
    # So: 997='}', 998=blank, 999=logger.info
    # Insert after 998 (blank), so tuner code sits before logger.info
    insert_after_line(lines, 998,
        '                # Self-tuning cycle (tuner has own 15-min cooldown)\n'
        '                try:\n'
        '                    self.self_tuner.run_cycle()\n'
        '                    health[\'tuner_mults\'] = dict(self.self_tuner._cached_multipliers)\n'
        '                    health[\'tuner_kill\'] = self.self_tuner.state.get(\'kill_switch_active\', False)\n'
        '                except Exception as e:\n'
        '                    logger.error(f"Self-tuner cycle error: {e}")\n'
        '                ')

    write_lines(filepath, lines)
    print(f"  OK: 3 patches applied")
    return True


# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    print("=" * 50)
    print("Self-Tuner Integration Patch v2")
    print("=" * 50)

    ok1 = patch_signal_executor()
    ok2 = patch_run_signal_bot()

    if ok1 and ok2:
        print(f"\n{'='*50}")
        print("ALL PATCHES APPLIED SUCCESSFULLY")
        print(f"{'='*50}")
        print("\nVerify with:")
        print("  python3 -m py_compile /Users/chudinnorukam/Projects/business/test_patch/opt/polymarket-bot/self_tuner.py")
        print("  python3 -m py_compile /Users/chudinnorukam/Projects/business/test_patch/opt/polymarket-bot/signal_executor.py")
        print("  python3 -m py_compile /Users/chudinnorukam/Projects/business/test_patch/opt/polymarket-bot/run_signal_bot.py")
        print("\nThen restart:")
        print("  systemctl restart polymarket-bot")
    else:
        print(f"\n{'='*50}")
        print("SOME PATCHES FAILED — DO NOT RESTART")
        print(f"{'='*50}")
        sys.exit(1)
