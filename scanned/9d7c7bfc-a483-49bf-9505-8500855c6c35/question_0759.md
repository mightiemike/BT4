# Q759: WalletUtils: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `WalletUtils.passwordValid` in `crypto/src/main/java/org/tron/keystore/WalletUtils.java` — where the attacker passes an off-curve or identity point to WalletUtils.passwordValid causing wrong verification or a crash — to break the invariant that WalletUtils.passwordValid validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/keystore/WalletUtils.java` -> `WalletUtils.passwordValid`
- Entrypoint: precompile/verify path to WalletUtils.passwordValid
- Attacker controls: request/transaction/contract inputs to `WalletUtils.passwordValid` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to WalletUtils.passwordValid causing wrong verification or a crash
- Invariant to test: WalletUtils.passwordValid validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
