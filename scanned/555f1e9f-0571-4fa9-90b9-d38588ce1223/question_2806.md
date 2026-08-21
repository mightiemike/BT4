# Q2806: MerkleRoot: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `MerkleRoot.createLeaf` in `common/src/main/java/org/tron/common/utils/MerkleRoot.java` — where the attacker feeds MerkleRoot.createLeaf a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that MerkleRoot.createLeaf rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/MerkleRoot.java` -> `MerkleRoot.createLeaf`
- Entrypoint: numeric bytes into MerkleRoot.createLeaf
- Attacker controls: request/transaction/contract inputs to `MerkleRoot.createLeaf` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds MerkleRoot.createLeaf a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: MerkleRoot.createLeaf rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
