# Q1552: Wallet: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Wallet.create` in `crypto/src/main/java/org/tron/keystore/Wallet.java` — where the attacker passes an off-curve or identity point to Wallet.create causing wrong verification or a crash — to break the invariant that Wallet.create validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Wallet.java` -> `Wallet.create`
- Entrypoint: precompile/verify path to Wallet.create
- Attacker controls: request/transaction/contract inputs to `Wallet.create` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to Wallet.create causing wrong verification or a crash
- Invariant to test: Wallet.create validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
