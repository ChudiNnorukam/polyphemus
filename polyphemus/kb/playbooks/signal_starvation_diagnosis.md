# Signal Starvation Diagnosis Playbook

Use this when the bot is healthy but there are few or no BTC 5m tradable candidates.

Focus order:
1. epoch coverage completeness
2. shared config era and instance comparability
3. signal generation counts
4. top guard blockers
5. market quiet versus pipeline starvation

Key questions:
- Are we seeing the BTC epochs?
- Are both instances running the same experiment?
- Are signals being generated but filtered?
- Are signals absent because of market conditions or because of pipeline gaps?

Do not:
- call the bot healthy just because the service is up
- infer alpha from a silent pipeline
