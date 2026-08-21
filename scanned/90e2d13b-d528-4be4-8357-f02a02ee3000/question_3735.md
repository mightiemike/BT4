# Q3735: Base58: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `Base58.encode` in `common/src/main/java/org/tron/common/utils/Base58.java` — where the attacker feeds Base58.encode a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that Base58.encode rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Base58.java` -> `Base58.encode`
- Entrypoint: numeric bytes into Base58.encode
- Attacker controls: request/transaction/contract inputs to `Base58.encode` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds Base58.encode a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: Base58.encode rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
