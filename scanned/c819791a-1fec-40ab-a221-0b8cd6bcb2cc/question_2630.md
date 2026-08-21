# Q2630: ForkController: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `ForkController.passOld` in `chainbase/src/main/java/org/tron/common/utils/ForkController.java` — where the attacker feeds ForkController.passOld a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that ForkController.passOld rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/ForkController.java` -> `ForkController.passOld`
- Entrypoint: numeric bytes into ForkController.passOld
- Attacker controls: request/transaction/contract inputs to `ForkController.passOld` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds ForkController.passOld a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: ForkController.passOld rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
