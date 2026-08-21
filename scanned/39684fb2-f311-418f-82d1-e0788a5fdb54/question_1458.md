# Q1458: MerkleRoot: non-deterministic math

## Question
Can an unprivileged attacker (any request/transaction) abuse `MerkleRoot.createParentLeaves` in `common/src/main/java/org/tron/common/utils/MerkleRoot.java` — where the attacker finds an input to MerkleRoot.createParentLeaves whose result differs by platform/rounding mode, diverging execution — to break the invariant that MerkleRoot.createParentLeaves yields identical output across platforms, leading to: Consensus divergence (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/MerkleRoot.java` -> `MerkleRoot.createParentLeaves`
- Entrypoint: value into MerkleRoot.createParentLeaves
- Attacker controls: request/transaction/contract inputs to `MerkleRoot.createParentLeaves` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: finds an input to MerkleRoot.createParentLeaves whose result differs by platform/rounding mode, diverging execution
- Invariant to test: MerkleRoot.createParentLeaves yields identical output across platforms
- Expected Immunefi impact: Consensus divergence (Critical)
- Fast validation: differential JUnit across rounding modes/JDKs
