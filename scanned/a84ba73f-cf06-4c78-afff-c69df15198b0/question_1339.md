# Q1339: PairingCheck: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `PairingCheck.flippedMillerLoopDoubling` in `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` — where the attacker passes an off-curve or identity point to PairingCheck.flippedMillerLoopDoubling causing wrong verification or a crash — to break the invariant that PairingCheck.flippedMillerLoopDoubling validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` -> `PairingCheck.flippedMillerLoopDoubling`
- Entrypoint: precompile/verify path to PairingCheck.flippedMillerLoopDoubling
- Attacker controls: request/transaction/contract inputs to `PairingCheck.flippedMillerLoopDoubling` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to PairingCheck.flippedMillerLoopDoubling causing wrong verification or a crash
- Invariant to test: PairingCheck.flippedMillerLoopDoubling validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
