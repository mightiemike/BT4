# Q1337: Wallet: signature malleability accepted

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Wallet.createWalletFile` in `crypto/src/main/java/org/tron/keystore/Wallet.java` — where the attacker submits a non-canonical (high-s) or over-length signature that Wallet.createWalletFile accepts, enabling replay or weight double-count — to break the invariant that Wallet.createWalletFile rejects non-canonical and non-65-byte signatures, leading to: Asset theft / unauthorized ops (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Wallet.java` -> `Wallet.createWalletFile`
- Entrypoint: transaction/precompile path invoking Wallet.createWalletFile
- Attacker controls: request/transaction/contract inputs to `Wallet.createWalletFile` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a non-canonical (high-s) or over-length signature that Wallet.createWalletFile accepts, enabling replay or weight double-count
- Invariant to test: Wallet.createWalletFile rejects non-canonical and non-65-byte signatures
- Expected Immunefi impact: Asset theft / unauthorized ops (Critical)
- Fast validation: JUnit feeding high-s and 66-byte sigs asserting rejection
