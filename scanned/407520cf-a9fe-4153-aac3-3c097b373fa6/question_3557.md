# Q3557: Wallet: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `Wallet.generateAes128CtrDerivedKey` in `crypto/src/main/java/org/tron/keystore/Wallet.java` — where the attacker passes an off-curve or identity point to Wallet.generateAes128CtrDerivedKey causing wrong verification or a crash — to break the invariant that Wallet.generateAes128CtrDerivedKey validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/Wallet.java` -> `Wallet.generateAes128CtrDerivedKey`
- Entrypoint: precompile/verify path to Wallet.generateAes128CtrDerivedKey
- Attacker controls: request/transaction/contract inputs to `Wallet.generateAes128CtrDerivedKey` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to Wallet.generateAes128CtrDerivedKey causing wrong verification or a crash
- Invariant to test: Wallet.generateAes128CtrDerivedKey validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
