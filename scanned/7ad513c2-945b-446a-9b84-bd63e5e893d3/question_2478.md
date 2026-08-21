# Q2478: ForkController: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `ForkController.check` in `chainbase/src/main/java/org/tron/common/utils/ForkController.java` — where the attacker feeds ForkController.check a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that ForkController.check rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/ForkController.java` -> `ForkController.check`
- Entrypoint: numeric bytes into ForkController.check
- Attacker controls: request/transaction/contract inputs to `ForkController.check` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds ForkController.check a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: ForkController.check rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
