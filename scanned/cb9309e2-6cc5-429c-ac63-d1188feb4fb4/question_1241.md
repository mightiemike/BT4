# Q1241: ProposalUtil: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `ProposalUtil.contain` in `actuator/src/main/java/org/tron/core/utils/ProposalUtil.java` — where the attacker finds an input to ProposalUtil.contain whose result differs by platform/rounding mode, diverging execution — to break the invariant that ProposalUtil.contain yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/utils/ProposalUtil.java` -> `ProposalUtil.contain`
- Entrypoint: value into ProposalUtil.contain
- Attacker controls: request/transaction/contract inputs to `ProposalUtil.contain` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to ProposalUtil.contain whose result differs by platform/rounding mode, diverging execution
- Invariant to test: ProposalUtil.contain yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
