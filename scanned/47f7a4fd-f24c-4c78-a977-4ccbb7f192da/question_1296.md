# Q1296: WalletUtil: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `WalletUtil.checkPermissionOperations` in `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` — where the attacker feeds WalletUtil.checkPermissionOperations a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that WalletUtil.checkPermissionOperations rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` -> `WalletUtil.checkPermissionOperations`
- Entrypoint: numeric bytes into WalletUtil.checkPermissionOperations
- Attacker controls: request/transaction/contract inputs to `WalletUtil.checkPermissionOperations` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds WalletUtil.checkPermissionOperations a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: WalletUtil.checkPermissionOperations rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
