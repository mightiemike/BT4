# Q2748: Bech32: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `Bech32.decode` in `common/src/main/java/org/tron/common/utils/Bech32.java` — where the attacker feeds Bech32.decode a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that Bech32.decode rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Bech32.java` -> `Bech32.decode`
- Entrypoint: numeric bytes into Bech32.decode
- Attacker controls: request/transaction/contract inputs to `Bech32.decode` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds Bech32.decode a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: Bech32.decode rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
