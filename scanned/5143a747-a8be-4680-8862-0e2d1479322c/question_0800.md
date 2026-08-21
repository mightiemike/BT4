# Q800: ProposalUtil: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `ProposalUtil.validator` in `actuator/src/main/java/org/tron/core/utils/ProposalUtil.java` — where the attacker feeds ProposalUtil.validator a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that ProposalUtil.validator rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/utils/ProposalUtil.java` -> `ProposalUtil.validator`
- Entrypoint: numeric bytes into ProposalUtil.validator
- Attacker controls: request/transaction/contract inputs to `ProposalUtil.validator` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds ProposalUtil.validator a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: ProposalUtil.validator rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
