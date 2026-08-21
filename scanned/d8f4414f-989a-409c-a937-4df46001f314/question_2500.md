# Q2500: MerkleRoot: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `MerkleRoot.root` in `common/src/main/java/org/tron/common/utils/MerkleRoot.java` — where the attacker supplies bytes that MerkleRoot.root sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that MerkleRoot.root treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/MerkleRoot.java` -> `MerkleRoot.root`
- Entrypoint: bytes into MerkleRoot.root
- Attacker controls: request/transaction/contract inputs to `MerkleRoot.root` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that MerkleRoot.root sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: MerkleRoot.root treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
