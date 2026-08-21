# Q2383: SM2Signer: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SM2Signer.verifyHashSignature` in `crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java` — where the attacker passes an off-curve or identity point to SM2Signer.verifyHashSignature causing wrong verification or a crash — to break the invariant that SM2Signer.verifyHashSignature validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java` -> `SM2Signer.verifyHashSignature`
- Entrypoint: precompile/verify path to SM2Signer.verifyHashSignature
- Attacker controls: request/transaction/contract inputs to `SM2Signer.verifyHashSignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to SM2Signer.verifyHashSignature causing wrong verification or a crash
- Invariant to test: SM2Signer.verifyHashSignature validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
