# Q2919: Commons: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `Commons.decodeFromBase58Check` in `chainbase/src/main/java/org/tron/common/utils/Commons.java` — where the attacker feeds Commons.decodeFromBase58Check a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that Commons.decodeFromBase58Check rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/Commons.java` -> `Commons.decodeFromBase58Check`
- Entrypoint: numeric bytes into Commons.decodeFromBase58Check
- Attacker controls: request/transaction/contract inputs to `Commons.decodeFromBase58Check` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds Commons.decodeFromBase58Check a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: Commons.decodeFromBase58Check rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
