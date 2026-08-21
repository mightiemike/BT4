# Q1691: SM2Signer: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SM2Signer.verifySignature` in `crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java` — where the attacker passes an off-curve or identity point to SM2Signer.verifySignature causing wrong verification or a crash — to break the invariant that SM2Signer.verifySignature validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/sm2/SM2Signer.java` -> `SM2Signer.verifySignature`
- Entrypoint: precompile/verify path to SM2Signer.verifySignature
- Attacker controls: request/transaction/contract inputs to `SM2Signer.verifySignature` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to SM2Signer.verifySignature causing wrong verification or a crash
- Invariant to test: SM2Signer.verifySignature validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
