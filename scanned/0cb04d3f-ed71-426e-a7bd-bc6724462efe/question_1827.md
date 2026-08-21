# Q1827: MerkleRoot: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `MerkleRoot.root` in `common/src/main/java/org/tron/common/utils/MerkleRoot.java` — where the attacker finds an input to MerkleRoot.root whose result differs by platform/rounding mode, diverging execution — to break the invariant that MerkleRoot.root yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/MerkleRoot.java` -> `MerkleRoot.root`
- Entrypoint: value into MerkleRoot.root
- Attacker controls: request/transaction/contract inputs to `MerkleRoot.root` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to MerkleRoot.root whose result differs by platform/rounding mode, diverging execution
- Invariant to test: MerkleRoot.root yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
