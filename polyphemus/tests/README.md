# Targeted Hardening Tests

Run the pair-arb hardening checks from the repo parent, not from inside the package directory:

```bash
cd /Users/chudinnorukam/Projects/business
python3 -m pytest polyphemus/test_accumulator.py polyphemus/tests/test_operator_tooling.py -q
```

Notes:
- These targeted tests are isolated from live credentials and do not require a running VPS.
- The broader historical suite may still require optional trading dependencies such as `py_clob_client`.
- Running pytest from inside `polyphemus/` is unsupported because `polyphemus/types.py` shadows the stdlib `types` module on that path.
