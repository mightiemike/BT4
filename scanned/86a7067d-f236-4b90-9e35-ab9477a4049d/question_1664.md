# Q1664: Wallet: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Wallet.createStandard` in `crypto/src/main/java/org/tron/keystore/Wallet.java` — where the attacker passes an off-curve or identity point to Wallet.createStandard causing wrong verification or a crash — to break the invariant that Wallet.createStandard validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Wallet.java` -> `Wallet.createStandard`
- Entrypoint: precompile/verify path to Wallet.createStandard
- Attacker controls: request/transaction/contract inputs to `Wallet.createStandard` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to Wallet.createStandard causing wrong verification or a crash
- Invariant to test: Wallet.createStandard validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
