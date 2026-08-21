# Q2218: ProposalUtil: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `ProposalUtil.contain` in `actuator/src/main/java/org/tron/core/utils/ProposalUtil.java` — where the attacker supplies bytes that ProposalUtil.contain sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that ProposalUtil.contain treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/utils/ProposalUtil.java` -> `ProposalUtil.contain`
- Entrypoint: bytes into ProposalUtil.contain
- Attacker controls: request/transaction/contract inputs to `ProposalUtil.contain` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that ProposalUtil.contain sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: ProposalUtil.contain treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
