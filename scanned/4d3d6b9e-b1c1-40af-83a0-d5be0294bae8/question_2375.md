# Q2375: MerkleRoot: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `MerkleRoot.createLeaf` in `common/src/main/java/org/tron/common/utils/MerkleRoot.java` — where the attacker supplies bytes that MerkleRoot.createLeaf sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that MerkleRoot.createLeaf treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/MerkleRoot.java` -> `MerkleRoot.createLeaf`
- Entrypoint: bytes into MerkleRoot.createLeaf
- Attacker controls: request/transaction/contract inputs to `MerkleRoot.createLeaf` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that MerkleRoot.createLeaf sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: MerkleRoot.createLeaf treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
