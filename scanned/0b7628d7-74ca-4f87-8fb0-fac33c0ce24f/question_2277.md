# Q2277: MerkleRoot: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `MerkleRoot.root` in `common/src/main/java/org/tron/common/utils/MerkleRoot.java` — where the attacker feeds MerkleRoot.root a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that MerkleRoot.root rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/MerkleRoot.java` -> `MerkleRoot.root`
- Entrypoint: numeric bytes into MerkleRoot.root
- Attacker controls: request/transaction/contract inputs to `MerkleRoot.root` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds MerkleRoot.root a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: MerkleRoot.root rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
