# Q611: RLP: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `RLP.decodeIP4Bytes` in `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` — where the attacker feeds RLP.decodeIP4Bytes a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that RLP.decodeIP4Bytes rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/capsule/utils/RLP.java` -> `RLP.decodeIP4Bytes`
- Entrypoint: numeric bytes into RLP.decodeIP4Bytes
- Attacker controls: request/transaction/contract inputs to `RLP.decodeIP4Bytes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds RLP.decodeIP4Bytes a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: RLP.decodeIP4Bytes rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
