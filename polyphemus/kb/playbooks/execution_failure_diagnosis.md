# Execution Failure Diagnosis Playbook

Use this when a candidate passes the signal pipeline but does not become a real trade.

Focus order:
1. placement failure
2. fill timeout
3. retry eligibility
4. price-cap violation
5. too-late-in-epoch rejection after signal generation

Required evidence:
- recent passed BTC candidates
- placement failures
- fill timeouts
- retry skip reasons
- matching engine / error-code context

Do not:
- loosen guards before checking whether execution is the actual bottleneck
- classify all failures as signal problems
