# Q1873: MerkleRoot: RLP/decode allocation bomb

## Question
Can an unprivileged attacker (any request/transaction) abuse `MerkleRoot.createParentLeaves` in `common/src/main/java/org/tron/common/utils/MerkleRoot.java` — where the attacker sends a length-prefixed structure to MerkleRoot.createParentLeaves declaring a huge size, forcing a giant allocation — to break the invariant that MerkleRoot.createParentLeaves bounds declared sizes against actual input length, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/MerkleRoot.java` -> `MerkleRoot.createParentLeaves`
- Entrypoint: encoded blob into MerkleRoot.createParentLeaves
- Attacker controls: request/transaction/contract inputs to `MerkleRoot.createParentLeaves` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: sends a length-prefixed structure to MerkleRoot.createParentLeaves declaring a huge size, forcing a giant allocation
- Invariant to test: MerkleRoot.createParentLeaves bounds declared sizes against actual input length
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with oversized length prefix asserting rejection
