# Q665: ProposalUtil: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `ProposalUtil.validator` in `actuator/src/main/java/org/tron/core/utils/ProposalUtil.java` — where the attacker finds an input to ProposalUtil.validator whose result differs by platform/rounding mode, diverging execution — to break the invariant that ProposalUtil.validator yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/utils/ProposalUtil.java` -> `ProposalUtil.validator`
- Entrypoint: value into ProposalUtil.validator
- Attacker controls: request/transaction/contract inputs to `ProposalUtil.validator` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to ProposalUtil.validator whose result differs by platform/rounding mode, diverging execution
- Invariant to test: ProposalUtil.validator yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
