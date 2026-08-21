# Q1694: CommonParameter: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `CommonParameter.calcMaxTimeRatio` in `common/src/main/java/org/tron/common/parameter/CommonParameter.java` — where the attacker feeds CommonParameter.calcMaxTimeRatio a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that CommonParameter.calcMaxTimeRatio rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/parameter/CommonParameter.java` -> `CommonParameter.calcMaxTimeRatio`
- Entrypoint: numeric bytes into CommonParameter.calcMaxTimeRatio
- Attacker controls: request/transaction/contract inputs to `CommonParameter.calcMaxTimeRatio` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds CommonParameter.calcMaxTimeRatio a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: CommonParameter.calcMaxTimeRatio rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
