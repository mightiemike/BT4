# Q1423: Commons: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `Commons.decode58Check` in `chainbase/src/main/java/org/tron/common/utils/Commons.java` — where the attacker feeds Commons.decode58Check a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that Commons.decode58Check rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/Commons.java` -> `Commons.decode58Check`
- Entrypoint: numeric bytes into Commons.decode58Check
- Attacker controls: request/transaction/contract inputs to `Commons.decode58Check` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds Commons.decode58Check a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: Commons.decode58Check rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
