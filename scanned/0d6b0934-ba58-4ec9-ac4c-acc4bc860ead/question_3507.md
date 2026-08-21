# Q3507: WalletUtil: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `WalletUtil.checkPermissionOperations` in `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` — where the attacker supplies an input where WalletUtil.checkPermissionOperations skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that WalletUtil.checkPermissionOperations rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` -> `WalletUtil.checkPermissionOperations`
- Entrypoint: address string into WalletUtil.checkPermissionOperations
- Attacker controls: request/transaction/contract inputs to `WalletUtil.checkPermissionOperations` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where WalletUtil.checkPermissionOperations skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: WalletUtil.checkPermissionOperations rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
