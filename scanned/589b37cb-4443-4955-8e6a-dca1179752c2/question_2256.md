# Q2256: StrictMathWrapper: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `StrictMathWrapper.pow` in `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` — where the attacker feeds StrictMathWrapper.pow a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that StrictMathWrapper.pow rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/math/StrictMathWrapper.java` -> `StrictMathWrapper.pow`
- Entrypoint: numeric bytes into StrictMathWrapper.pow
- Attacker controls: request/transaction/contract inputs to `StrictMathWrapper.pow` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds StrictMathWrapper.pow a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: StrictMathWrapper.pow rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
