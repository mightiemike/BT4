# Q3497: WalletUtil: base58/bech32 checksum gap

## Question
Can an unprivileged attacker (any request/transaction) abuse `WalletUtil.generateContractAddress2` in `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` — where the attacker supplies an input where WalletUtil.generateContractAddress2 skips or mis-verifies the checksum, accepting a malformed address — to break the invariant that WalletUtil.generateContractAddress2 rejects any input failing its checksum, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` -> `WalletUtil.generateContractAddress2`
- Entrypoint: address string into WalletUtil.generateContractAddress2
- Attacker controls: request/transaction/contract inputs to `WalletUtil.generateContractAddress2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies an input where WalletUtil.generateContractAddress2 skips or mis-verifies the checksum, accepting a malformed address
- Invariant to test: WalletUtil.generateContractAddress2 rejects any input failing its checksum
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit with bad-checksum strings asserting rejection
