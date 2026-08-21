# Q484: ForkController: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `ForkController.checkForEnergyLimit` in `chainbase/src/main/java/org/tron/common/utils/ForkController.java` — where the attacker feeds ForkController.checkForEnergyLimit a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that ForkController.checkForEnergyLimit rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/ForkController.java` -> `ForkController.checkForEnergyLimit`
- Entrypoint: numeric bytes into ForkController.checkForEnergyLimit
- Attacker controls: request/transaction/contract inputs to `ForkController.checkForEnergyLimit` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds ForkController.checkForEnergyLimit a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: ForkController.checkForEnergyLimit rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
