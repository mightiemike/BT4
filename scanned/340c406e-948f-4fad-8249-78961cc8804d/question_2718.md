# Q2718: Maths: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `Maths.floorDiv` in `common/src/main/java/org/tron/common/math/Maths.java` — where the attacker feeds Maths.floorDiv a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that Maths.floorDiv rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/Maths.java` -> `Maths.floorDiv`
- Entrypoint: numeric bytes into Maths.floorDiv
- Attacker controls: request/transaction/contract inputs to `Maths.floorDiv` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds Maths.floorDiv a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: Maths.floorDiv rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
