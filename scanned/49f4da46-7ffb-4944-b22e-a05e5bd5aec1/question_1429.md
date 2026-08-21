# Q1429: JLibrustzcash: proof/parameter not fully checked

## Question
Can an unprivileged attacker (shielded transaction) abuse `JLibrustzcash.librustzcashToScalar` in `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java` — where the attacker submits a shielded proof or note to JLibrustzcash.librustzcashToScalar with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend — to break the invariant that JLibrustzcash.librustzcashToScalar binds every proof field to the verified statement, leading to: Asset theft (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java` -> `JLibrustzcash.librustzcashToScalar`
- Entrypoint: shielded transaction reaching JLibrustzcash.librustzcashToScalar
- Attacker controls: request/transaction/contract inputs to `JLibrustzcash.librustzcashToScalar` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a shielded proof or note to JLibrustzcash.librustzcashToScalar with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend
- Invariant to test: JLibrustzcash.librustzcashToScalar binds every proof field to the verified statement
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit mutating one proof field asserting verify fails
