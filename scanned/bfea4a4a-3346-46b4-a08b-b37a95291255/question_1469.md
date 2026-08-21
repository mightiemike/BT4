# Q1469: ForkController: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `ForkController.passOld` in `chainbase/src/main/java/org/tron/common/utils/ForkController.java` — where the attacker sends a length-prefixed structure to ForkController.passOld declaring a huge size, forcing a giant allocation — to break the invariant that ForkController.passOld bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/ForkController.java` -> `ForkController.passOld`
- Entrypoint: encoded blob into ForkController.passOld
- Attacker controls: request/transaction/contract inputs to `ForkController.passOld` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to ForkController.passOld declaring a huge size, forcing a giant allocation
- Invariant to test: ForkController.passOld bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
