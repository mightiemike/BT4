# Q3042: WalletUtil: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `WalletUtil.checkPermissionOperations` in `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` — where the attacker finds an input to WalletUtil.checkPermissionOperations whose result differs by platform/rounding mode, diverging execution — to break the invariant that WalletUtil.checkPermissionOperations yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` -> `WalletUtil.checkPermissionOperations`
- Entrypoint: value into WalletUtil.checkPermissionOperations
- Attacker controls: request/transaction/contract inputs to `WalletUtil.checkPermissionOperations` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to WalletUtil.checkPermissionOperations whose result differs by platform/rounding mode, diverging execution
- Invariant to test: WalletUtil.checkPermissionOperations yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
