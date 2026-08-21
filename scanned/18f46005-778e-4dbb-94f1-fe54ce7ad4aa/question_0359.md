# Q359: ForkController: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `ForkController.check` in `chainbase/src/main/java/org/tron/common/utils/ForkController.java` — where the attacker supplies bytes that ForkController.check sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that ForkController.check treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/ForkController.java` -> `ForkController.check`
- Entrypoint: bytes into ForkController.check
- Attacker controls: request/transaction/contract inputs to `ForkController.check` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that ForkController.check sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: ForkController.check treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
