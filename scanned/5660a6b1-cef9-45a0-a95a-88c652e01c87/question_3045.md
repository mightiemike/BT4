# Q3045: StrictMathWrapper: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `StrictMathWrapper.min` in `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` — where the attacker feeds StrictMathWrapper.min a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that StrictMathWrapper.min rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` -> `StrictMathWrapper.min`
- Entrypoint: numeric bytes into StrictMathWrapper.min
- Attacker controls: request/transaction/contract inputs to `StrictMathWrapper.min` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds StrictMathWrapper.min a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: StrictMathWrapper.min rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
