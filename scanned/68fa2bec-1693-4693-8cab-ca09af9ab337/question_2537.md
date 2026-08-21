# Q2537: Base58: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `Base58.decode` in `common/src/main/java/org/tron/common/utils/Base58.java` — where the attacker feeds Base58.decode a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that Base58.decode rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Base58.java` -> `Base58.decode`
- Entrypoint: numeric bytes into Base58.decode
- Attacker controls: request/transaction/contract inputs to `Base58.decode` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds Base58.decode a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: Base58.decode rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
