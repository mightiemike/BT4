# Q2138: PairingCheck: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `PairingCheck.finalExponentiation` in `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` — where the attacker passes an off-curve or identity point to PairingCheck.finalExponentiation causing wrong verification or a crash — to break the invariant that PairingCheck.finalExponentiation validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/zksnark/PairingCheck.java` -> `PairingCheck.finalExponentiation`
- Entrypoint: precompile/verify path to PairingCheck.finalExponentiation
- Attacker controls: request/transaction/contract inputs to `PairingCheck.finalExponentiation` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to PairingCheck.finalExponentiation causing wrong verification or a crash
- Invariant to test: PairingCheck.finalExponentiation validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
