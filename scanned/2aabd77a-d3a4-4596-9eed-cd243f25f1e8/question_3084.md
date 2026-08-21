# Q3084: WalletUtil: address decode ambiguity

## Question
Can an unprivileged attacker (any request/transaction) abuse `WalletUtil.generateContractAddress2` in `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` — where the attacker exploits WalletUtil.generateContractAddress2 to decode two byte forms to the same/different address, confusing owner resolution — to break the invariant that WalletUtil.generateContractAddress2 maps each input to exactly one canonical address or rejects, leading to: Unauthorized account operations (Intermediate)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/utils/WalletUtil.java` -> `WalletUtil.generateContractAddress2`
- Entrypoint: input flowing into WalletUtil.generateContractAddress2
- Attacker controls: request/transaction/contract inputs to `WalletUtil.generateContractAddress2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: exploits WalletUtil.generateContractAddress2 to decode two byte forms to the same/different address, confusing owner resolution
- Invariant to test: WalletUtil.generateContractAddress2 maps each input to exactly one canonical address or rejects
- Expected Immunefi impact: Unauthorized account operations (Intermediate)
- Fast validation: JUnit differential on padded/short address bytes
