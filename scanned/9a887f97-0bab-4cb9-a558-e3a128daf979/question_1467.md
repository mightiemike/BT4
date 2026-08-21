# Q1467: WalletUtil: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `WalletUtil.generateContractAddress2` in `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` — where the attacker finds an input to WalletUtil.generateContractAddress2 whose result differs by platform/rounding mode, diverging execution — to break the invariant that WalletUtil.generateContractAddress2 yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` -> `WalletUtil.generateContractAddress2`
- Entrypoint: value into WalletUtil.generateContractAddress2
- Attacker controls: request/transaction/contract inputs to `WalletUtil.generateContractAddress2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to WalletUtil.generateContractAddress2 whose result differs by platform/rounding mode, diverging execution
- Invariant to test: WalletUtil.generateContractAddress2 yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
