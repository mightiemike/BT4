# Q2587: RLP: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `RLP.encode` in `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` — where the attacker feeds RLP.encode a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that RLP.encode rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` -> `RLP.encode`
- Entrypoint: numeric bytes into RLP.encode
- Attacker controls: request/transaction/contract inputs to `RLP.encode` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds RLP.encode a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: RLP.encode rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
