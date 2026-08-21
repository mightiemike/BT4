# Q2976: BIUtil: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `BIUtil.addSafely` in `common/src/main/java/org/tron/common/utils/BIUtil.java` — where the attacker feeds BIUtil.addSafely a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that BIUtil.addSafely rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/BIUtil.java` -> `BIUtil.addSafely`
- Entrypoint: numeric bytes into BIUtil.addSafely
- Attacker controls: request/transaction/contract inputs to `BIUtil.addSafely` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds BIUtil.addSafely a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: BIUtil.addSafely rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
