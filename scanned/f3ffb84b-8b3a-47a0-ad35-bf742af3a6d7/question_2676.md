# Q2676: WalletUtils: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `WalletUtils.writeWalletFile` in `crypto/src/main/java/org/tron/keystore/WalletUtils.java` — where the attacker passes an off-curve or identity point to WalletUtils.writeWalletFile causing wrong verification or a crash — to break the invariant that WalletUtils.writeWalletFile validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/WalletUtils.java` -> `WalletUtils.writeWalletFile`
- Entrypoint: precompile/verify path to WalletUtils.writeWalletFile
- Attacker controls: request/transaction/contract inputs to `WalletUtils.writeWalletFile` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to WalletUtils.writeWalletFile causing wrong verification or a crash
- Invariant to test: WalletUtils.writeWalletFile validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
