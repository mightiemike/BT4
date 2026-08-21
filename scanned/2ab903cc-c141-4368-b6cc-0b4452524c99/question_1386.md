# Q1386: CompactEncoder: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `CompactEncoder.binToNibbles` in `common/src/main/java/org/tron/common/utils/CompactEncoder.java` — where the attacker finds an input to CompactEncoder.binToNibbles whose result differs by platform/rounding mode, diverging execution — to break the invariant that CompactEncoder.binToNibbles yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/CompactEncoder.java` -> `CompactEncoder.binToNibbles`
- Entrypoint: value into CompactEncoder.binToNibbles
- Attacker controls: request/transaction/contract inputs to `CompactEncoder.binToNibbles` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to CompactEncoder.binToNibbles whose result differs by platform/rounding mode, diverging execution
- Invariant to test: CompactEncoder.binToNibbles yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
