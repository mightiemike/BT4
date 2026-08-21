# Q53: WalletUtil: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `WalletUtil.generateContractAddress2` in `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` — where the attacker sends a length-prefixed structure to WalletUtil.generateContractAddress2 declaring a huge size, forcing a giant allocation — to break the invariant that WalletUtil.generateContractAddress2 bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` -> `WalletUtil.generateContractAddress2`
- Entrypoint: encoded blob into WalletUtil.generateContractAddress2
- Attacker controls: request/transaction/contract inputs to `WalletUtil.generateContractAddress2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to WalletUtil.generateContractAddress2 declaring a huge size, forcing a giant allocation
- Invariant to test: WalletUtil.generateContractAddress2 bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
