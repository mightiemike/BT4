# Q279: ProposalUtil: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `ProposalUtil.validator` in `actuator/src/main/java/org/tron/core/utils/ProposalUtil.java` — where the attacker supplies bytes that ProposalUtil.validator sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that ProposalUtil.validator treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/utils/ProposalUtil.java` -> `ProposalUtil.validator`
- Entrypoint: bytes into ProposalUtil.validator
- Attacker controls: request/transaction/contract inputs to `ProposalUtil.validator` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that ProposalUtil.validator sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: ProposalUtil.validator treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
