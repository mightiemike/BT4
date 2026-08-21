# Q2333: ByteArray: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteArray.fromString` in `common/src/main/java/org/tron/common/utils/ByteArray.java` — where the attacker feeds ByteArray.fromString a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that ByteArray.fromString rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteArray.java` -> `ByteArray.fromString`
- Entrypoint: numeric bytes into ByteArray.fromString
- Attacker controls: request/transaction/contract inputs to `ByteArray.fromString` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds ByteArray.fromString a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: ByteArray.fromString rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
