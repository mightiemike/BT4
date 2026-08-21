# Q2265: MerkleRoot: negative-length / sign extension

## Question
Can an unprivileged attacker (any request/transaction) abuse `MerkleRoot.computeHash` in `common/src/main/java/org/tron/common/utils/MerkleRoot.java` — where the attacker supplies bytes that MerkleRoot.computeHash sign-extends or reads as negative length, causing wrong slicing or huge allocation — to break the invariant that MerkleRoot.computeHash treats lengths as unsigned and bounds them, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/MerkleRoot.java` -> `MerkleRoot.computeHash`
- Entrypoint: bytes into MerkleRoot.computeHash
- Attacker controls: request/transaction/contract inputs to `MerkleRoot.computeHash` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: supplies bytes that MerkleRoot.computeHash sign-extends or reads as negative length, causing wrong slicing or huge allocation
- Invariant to test: MerkleRoot.computeHash treats lengths as unsigned and bounds them
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit with high-bit-set length bytes asserting safe handling
