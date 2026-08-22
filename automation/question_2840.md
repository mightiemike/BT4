# Q2840: WalletUtils: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `WalletUtils.generateWalletFile` in `crypto/src/main/java/org/tron/keystore/WalletUtils.java` — where the attacker submits a non-canonical (high-s) or over-length signature that WalletUtils.generateWalletFile accepts, enabling replay or weight double-count — to break the invariant that WalletUtils.generateWalletFile rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/WalletUtils.java` -> `WalletUtils.generateWalletFile`
- Entrypoint: transaction/precompile path invoking WalletUtils.generateWalletFile
- Attacker controls: request/transaction/contract inputs to `WalletUtils.generateWalletFile` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that WalletUtils.generateWalletFile accepts, enabling replay or weight double-count
- Invariant to test: WalletUtils.generateWalletFile rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
