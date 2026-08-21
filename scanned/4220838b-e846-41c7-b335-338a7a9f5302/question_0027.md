# Q27: StrictMathWrapper: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `StrictMathWrapper.subtractExact` in `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` — where the attacker feeds StrictMathWrapper.subtractExact a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that StrictMathWrapper.subtractExact rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` -> `StrictMathWrapper.subtractExact`
- Entrypoint: numeric bytes into StrictMathWrapper.subtractExact
- Attacker controls: request/transaction/contract inputs to `StrictMathWrapper.subtractExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds StrictMathWrapper.subtractExact a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: StrictMathWrapper.subtractExact rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
