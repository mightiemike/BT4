# Q1924: StrictMathWrapper: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `StrictMathWrapper.addExact` in `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` — where the attacker feeds StrictMathWrapper.addExact a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that StrictMathWrapper.addExact rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` -> `StrictMathWrapper.addExact`
- Entrypoint: numeric bytes into StrictMathWrapper.addExact
- Attacker controls: request/transaction/contract inputs to `StrictMathWrapper.addExact` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds StrictMathWrapper.addExact a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: StrictMathWrapper.addExact rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
