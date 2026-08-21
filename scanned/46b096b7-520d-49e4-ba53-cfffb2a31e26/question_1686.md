# Q1686: WalletUtil: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `WalletUtil.generateContractAddress2` in `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` — where the attacker supplies bytes that WalletUtil.generateContractAddress2 sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that WalletUtil.generateContractAddress2 treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` -> `WalletUtil.generateContractAddress2`
- Entrypoint: bytes into WalletUtil.generateContractAddress2
- Attacker controls: request/transaction/contract inputs to `WalletUtil.generateContractAddress2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that WalletUtil.generateContractAddress2 sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: WalletUtil.generateContractAddress2 treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
