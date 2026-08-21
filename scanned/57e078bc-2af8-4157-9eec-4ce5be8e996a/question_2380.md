# Q2380: ByteUtil: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `ByteUtil.intToBytes` in `common/src/main/java/org/tron/common/utils/ByteUtil.java` — where the attacker feeds ByteUtil.intToBytes a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that ByteUtil.intToBytes rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/ByteUtil.java` -> `ByteUtil.intToBytes`
- Entrypoint: numeric bytes into ByteUtil.intToBytes
- Attacker controls: request/transaction/contract inputs to `ByteUtil.intToBytes` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds ByteUtil.intToBytes a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: ByteUtil.intToBytes rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
