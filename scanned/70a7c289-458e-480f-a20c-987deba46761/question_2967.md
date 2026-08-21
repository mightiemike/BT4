# Q2967: SignUtils: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SignUtils.signatureToAddress` in `crypto/src/main/java/org/tron/common/crypto/SignUtils.java` — where the attacker passes an off-curve or identity point to SignUtils.signatureToAddress causing wrong verification or a crash — to break the invariant that SignUtils.signatureToAddress validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/SignUtils.java` -> `SignUtils.signatureToAddress`
- Entrypoint: precompile/verify path to SignUtils.signatureToAddress
- Attacker controls: request/transaction/contract inputs to `SignUtils.signatureToAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to SignUtils.signatureToAddress causing wrong verification or a crash
- Invariant to test: SignUtils.signatureToAddress validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
