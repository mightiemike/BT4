# Q3504: Sha256Hash: integer parse overflow

## Question
Can an unprivileged attacker (any request/transaction) abuse `Sha256Hash.newSM3Digest` in `common/src/main/java/org/tron/common/utils/Sha256Hash.java` — where the attacker feeds Sha256Hash.newSM3Digest a value that overflows when parsed to numeric, propagating a wrong amount into accounting — to break the invariant that Sha256Hash.newSM3Digest rejects or saturates out-of-range numeric input, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `common/src/main/java/org/tron/common/utils/Sha256Hash.java` -> `Sha256Hash.newSM3Digest`
- Entrypoint: numeric bytes into Sha256Hash.newSM3Digest
- Attacker controls: request/transaction/contract inputs to `Sha256Hash.newSM3Digest` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: feeds Sha256Hash.newSM3Digest a value that overflows when parsed to numeric, propagating a wrong amount into accounting
- Invariant to test: Sha256Hash.newSM3Digest rejects or saturates out-of-range numeric input
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit at MIN/MAX asserting no silent wrap
