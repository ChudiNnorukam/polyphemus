# Audit Mismatch Triage Playbook

Use this when `emmanuel` reports a CLOB to DB trade audit failure or the audit status is unknown.

Checklist:
1. Refresh caches.
2. Retrieve the latest audit line and classify it as `pass`, `fail`, or `unknown`.
3. Compare recent CLOB order ids with both DB entry hashes and DB exit hashes.
4. Separate SSH/auth failure from true accounting mismatch.
5. Do not recommend any live promotion until the audit is clean.

Primary output:
- the latest evidence line
- the probable cause
- the next debugging step

Do not:
- treat SSH failure as a clean audit
- recommend live changes while bookkeeping trust is unresolved
