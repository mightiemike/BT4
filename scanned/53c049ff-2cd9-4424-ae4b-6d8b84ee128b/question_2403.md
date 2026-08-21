# Q2403: Maths: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `Maths.pow` in `common/src/main/java/org/tron/common/math/Maths.java` — where the attacker feeds Maths.pow a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that Maths.pow rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/Maths.java` -> `Maths.pow`
- Entrypoint: numeric bytes into Maths.pow
- Attacker controls: request/transaction/contract inputs to `Maths.pow` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds Maths.pow a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: Maths.pow rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
