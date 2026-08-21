# Q1719: MerkleRoot: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `MerkleRoot.createLeaves` in `common/src/main/java/org/tron/common/utils/MerkleRoot.java` — where the attacker supplies bytes that MerkleRoot.createLeaves sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that MerkleRoot.createLeaves treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/MerkleRoot.java` -> `MerkleRoot.createLeaves`
- Entrypoint: bytes into MerkleRoot.createLeaves
- Attacker controls: request/transaction/contract inputs to `MerkleRoot.createLeaves` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that MerkleRoot.createLeaves sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: MerkleRoot.createLeaves treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
