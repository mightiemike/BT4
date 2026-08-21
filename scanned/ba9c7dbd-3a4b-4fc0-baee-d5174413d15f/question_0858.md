# Q858: ForkController: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `ForkController.passOld` in `chainbase/src/main/java/org/tron/common/utils/ForkController.java` — where the attacker supplies bytes that ForkController.passOld sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that ForkController.passOld treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/ForkController.java` -> `ForkController.passOld`
- Entrypoint: bytes into ForkController.passOld
- Attacker controls: request/transaction/contract inputs to `ForkController.passOld` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that ForkController.passOld sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: ForkController.passOld treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
