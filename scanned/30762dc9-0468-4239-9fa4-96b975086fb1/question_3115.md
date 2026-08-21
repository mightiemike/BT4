# Q3115: MerkleRoot: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `MerkleRoot.createLeaf` in `common/src/main/java/org/tron/common/utils/MerkleRoot.java` — where the attacker finds an input to MerkleRoot.createLeaf whose result differs by platform/rounding mode, diverging execution — to break the invariant that MerkleRoot.createLeaf yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/MerkleRoot.java` -> `MerkleRoot.createLeaf`
- Entrypoint: value into MerkleRoot.createLeaf
- Attacker controls: request/transaction/contract inputs to `MerkleRoot.createLeaf` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to MerkleRoot.createLeaf whose result differs by platform/rounding mode, diverging execution
- Invariant to test: MerkleRoot.createLeaf yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
