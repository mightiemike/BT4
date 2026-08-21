# Q3241: PairingCheck: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `PairingCheck.millerLoop` in `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` — where the attacker passes an off-curve or identity point to PairingCheck.millerLoop causing wrong verification or a crash — to break the invariant that PairingCheck.millerLoop validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` -> `PairingCheck.millerLoop`
- Entrypoint: precompile/verify path to PairingCheck.millerLoop
- Attacker controls: request/transaction/contract inputs to `PairingCheck.millerLoop` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to PairingCheck.millerLoop causing wrong verification or a crash
- Invariant to test: PairingCheck.millerLoop validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
