# Q585: RLP: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `RLP.encodeByte` in `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` — where the attacker feeds RLP.encodeByte a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that RLP.encodeByte rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` -> `RLP.encodeByte`
- Entrypoint: numeric bytes into RLP.encodeByte
- Attacker controls: request/transaction/contract inputs to `RLP.encodeByte` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds RLP.encodeByte a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: RLP.encodeByte rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
