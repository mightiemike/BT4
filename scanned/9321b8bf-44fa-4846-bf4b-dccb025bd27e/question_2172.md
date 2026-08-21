# Q2172: Wallet: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Wallet.createLight` in `crypto/src/main/java/org/tron/keystore/Wallet.java` — where the attacker passes an off-curve or identity point to Wallet.createLight causing wrong verification or a crash — to break the invariant that Wallet.createLight validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Wallet.java` -> `Wallet.createLight`
- Entrypoint: precompile/verify path to Wallet.createLight
- Attacker controls: request/transaction/contract inputs to `Wallet.createLight` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to Wallet.createLight causing wrong verification or a crash
- Invariant to test: Wallet.createLight validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
