# Q1913: PairingCheck: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `PairingCheck.flippedMillerLoopMixedAddition` in `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` — where the attacker passes an off-curve or identity point to PairingCheck.flippedMillerLoopMixedAddition causing wrong verification or a crash — to break the invariant that PairingCheck.flippedMillerLoopMixedAddition validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` -> `PairingCheck.flippedMillerLoopMixedAddition`
- Entrypoint: precompile/verify path to PairingCheck.flippedMillerLoopMixedAddition
- Attacker controls: request/transaction/contract inputs to `PairingCheck.flippedMillerLoopMixedAddition` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to PairingCheck.flippedMillerLoopMixedAddition causing wrong verification or a crash
- Invariant to test: PairingCheck.flippedMillerLoopMixedAddition validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
