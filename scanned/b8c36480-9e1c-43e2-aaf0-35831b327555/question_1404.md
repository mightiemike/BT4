# Q1404: Wallet: length-prefix truncation accept

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Wallet.createWalletFile` in `crypto/src/main/java/org/tron/keystore/Wallet.java` — where the attacker submits a signature/pubkey to Wallet.createWalletFile that is short or padded but still parsed, recovering a shifted value — to break the invariant that Wallet.createWalletFile enforces exact expected byte length, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Wallet.java` -> `Wallet.createWalletFile`
- Entrypoint: precompile/verify path to Wallet.createWalletFile
- Attacker controls: request/transaction/contract inputs to `Wallet.createWalletFile` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a signature/pubkey to Wallet.createWalletFile that is short or padded but still parsed, recovering a shifted value
- Invariant to test: Wallet.createWalletFile enforces exact expected byte length
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with short/padded inputs asserting rejection
