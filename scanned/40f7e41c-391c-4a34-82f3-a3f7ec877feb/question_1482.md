# Q1482: Sha256Hash: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `Sha256Hash.newSM3Digest` in `common/src/main/java/org/tron/common/utils/Sha256Hash.java` — where the attacker finds an input to Sha256Hash.newSM3Digest whose result differs by platform/rounding mode, diverging execution — to break the invariant that Sha256Hash.newSM3Digest yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Sha256Hash.java` -> `Sha256Hash.newSM3Digest`
- Entrypoint: value into Sha256Hash.newSM3Digest
- Attacker controls: request/transaction/contract inputs to `Sha256Hash.newSM3Digest` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to Sha256Hash.newSM3Digest whose result differs by platform/rounding mode, diverging execution
- Invariant to test: Sha256Hash.newSM3Digest yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
