#!/usr/bin/env python3
"""Funnel Resolve — Print digital product funnel stats.

Usage:
    python3 scripts/funnel_resolve.py
"""

import os
import sqlite3
import sys

DB_PATH = os.environ.get(
    'LEADS_DB_PATH',
    os.path.join(os.path.dirname(__file__), '..', 'data', 'marketing_leads.db')
)


def _load_env():
    for path in [
        os.path.join(os.path.dirname(__file__), '..', '.env'),
        '/opt/openclaw/.env',
        '/opt/lagbot/lagbot/.env',
    ]:
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, _, v = line.partition('=')
                        os.environ.setdefault(k.strip(), v.strip())


_load_env()


def get_db():
    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}. Run init_db.py + funnel_db_init.py extend first.")
        sys.exit(1)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def pct(num, denom):
    if not denom:
        return 'n/a'
    return f"{num/denom*100:.1f}%"


def main():
    conn = get_db()
    c = conn.cursor()

    if not table_exists(conn, 'funnel_contacts'):
        print("funnel_contacts table not found. Run: python3 scripts/funnel_db_init.py extend")
        conn.close()
        return

    # Purchase counts
    total_purchases = c.execute("SELECT COUNT(*) FROM funnel_contacts").fetchone()[0]
    gumroad_purchases = c.execute(
        "SELECT COUNT(*) FROM funnel_contacts WHERE source='gumroad'"
    ).fetchone()[0]
    stripe_purchases = c.execute(
        "SELECT COUNT(*) FROM funnel_contacts WHERE source='stripe'"
    ).fetchone()[0]

    # Sequence enrollment states
    active_enrollments = c.execute(
        "SELECT COUNT(*) FROM sequence_enrollments WHERE status='active'"
    ).fetchone()[0]
    completed_enrollments = c.execute(
        "SELECT COUNT(*) FROM sequence_enrollments WHERE status='completed'"
    ).fetchone()[0]
    exited_enrollments = c.execute(
        "SELECT COUNT(*) FROM sequence_enrollments WHERE status='exited'"
    ).fetchone()[0]

    # Step progress (% of contacts who reached each step in post-purchase-v1)
    pp_enrollments = c.execute(
        "SELECT COUNT(*) FROM sequence_enrollments WHERE sequence_id='post-purchase-v1'"
    ).fetchone()[0]

    step_counts = {}
    for step in range(1, 6):
        count = c.execute("""
            SELECT COUNT(*) FROM sequence_sends ss
            JOIN sequence_enrollments se ON ss.enrollment_id = se.id
            WHERE se.sequence_id = 'post-purchase-v1'
              AND ss.step = ?
              AND ss.status = 'sent'
        """, (step,)).fetchone()[0]
        step_counts[step] = count

    # Upsell stats
    upsell_triggered = c.execute(
        "SELECT COUNT(*) FROM sequence_enrollments WHERE sequence_id='upsell-v1'"
    ).fetchone()[0]
    upsell_completed = c.execute(
        "SELECT COUNT(*) FROM sequence_enrollments WHERE sequence_id='upsell-v1' AND status='completed'"
    ).fetchone()[0]

    # Email engagement
    total_sent = c.execute(
        "SELECT COUNT(*) FROM sequence_sends WHERE status IN ('sent', 'opened', 'clicked')"
    ).fetchone()[0]
    total_opened = c.execute(
        "SELECT COUNT(*) FROM sequence_sends WHERE opened_at IS NOT NULL"
    ).fetchone()[0]

    # Revenue (if tracked)
    total_revenue_cents = c.execute(
        "SELECT SUM(amount_cents) FROM funnel_contacts"
    ).fetchone()[0] or 0

    # Recent purchases
    recent = c.execute("""
        SELECT email, source, product_id, amount_cents, purchased_at
        FROM funnel_contacts
        ORDER BY purchased_at DESC
        LIMIT 5
    """).fetchall()

    conn.close()

    print()
    print("DIGITAL PRODUCT FUNNEL - RESOLVE")
    print("━" * 46)
    print(f"  Purchases:    {total_purchases} total ({gumroad_purchases} Gumroad / {stripe_purchases} Stripe)")
    if total_revenue_cents:
        print(f"  Revenue:      ${total_revenue_cents/100:,.2f}")
    print()
    print(f"  Sequences:    {active_enrollments} active / {completed_enrollments} completed / {exited_enrollments} exited")
    print()

    if pp_enrollments:
        print("  Step progress (post-purchase-v1):")
        for step in range(1, 6):
            bar = pct(step_counts.get(step, 0), pp_enrollments)
            print(f"    Step {step}: {bar:>6}  ({step_counts.get(step, 0)}/{pp_enrollments})")
    else:
        print("  Step progress: no enrollments yet")

    print()
    print(f"  Upsells:      {upsell_triggered} triggered / {upsell_completed} completed")
    print(f"  Open rate:    {pct(total_opened, total_sent)} ({total_opened}/{total_sent} sent)")

    if recent:
        print()
        print("  Recent purchases:")
        for r in recent:
            purchased = r['purchased_at'][:10] if r['purchased_at'] else '?'
            amount = f"${r['amount_cents']/100:.2f}" if r['amount_cents'] else '$?'
            print(f"    {purchased}  {r['email']:<30} {amount}  [{r['source']}]")

    print("━" * 46)
    print()


if __name__ == '__main__':
    main()
