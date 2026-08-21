# Q1359: ByteArray: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteArray.toLong` in `common/src/main/java/org/tron/common/utils/ByteArray.java` — where the attacker feeds ByteArray.toLong a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that ByteArray.toLong rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteArray.java` -> `ByteArray.toLong`
- Entrypoint: numeric bytes into ByteArray.toLong
- Attacker controls: request/transaction/contract inputs to `ByteArray.toLong` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds ByteArray.toLong a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: ByteArray.toLong rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
