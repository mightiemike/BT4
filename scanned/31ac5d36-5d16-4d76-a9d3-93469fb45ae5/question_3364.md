# Q3364: SignUtils: point/pubkey not validated

## Question
Can an unprivileged attacker (transaction/precompile) abuse `SignUtils.getGeneratedRandomSign` in `crypto/src/main/java/org/tron/common/crypto/SignUtils.java` — where the attacker passes an off-curve or identity point to SignUtils.getGeneratedRandomSign causing wrong verification or a crash — to break the invariant that SignUtils.getGeneratedRandomSign validates curve membership before use, leading to: Asset theft (Critical)?

## Target
- File/function: `crypto/src/main/java/org/tron/common/crypto/SignUtils.java` -> `SignUtils.getGeneratedRandomSign`
- Entrypoint: precompile/verify path to SignUtils.getGeneratedRandomSign
- Attacker controls: request/transaction/contract inputs to `SignUtils.getGeneratedRandomSign` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: passes an off-curve or identity point to SignUtils.getGeneratedRandomSign causing wrong verification or a crash
- Invariant to test: SignUtils.getGeneratedRandomSign validates curve membership before use
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit with off-curve point asserting rejection
