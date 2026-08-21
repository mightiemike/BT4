# Q2660: JLibrustzcash: proof/parameter not fully checked

## Question
Can an unprivileged attacker (shielded transaction) abuse `JLibrustzcash.librustzcashSaplingCheckOutput` in `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java` — where the attacker submits a shielded proof or note to JLibrustzcash.librustzcashSaplingCheckOutput with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend — to break the invariant that JLibrustzcash.librustzcashSaplingCheckOutput binds every proof field to the verified statement, leading to: Asset theft (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/common/zksnark/JLibrustzcash.java` -> `JLibrustzcash.librustzcashSaplingCheckOutput`
- Entrypoint: shielded transaction reaching JLibrustzcash.librustzcashSaplingCheckOutput
- Attacker controls: request/transaction/contract inputs to `JLibrustzcash.librustzcashSaplingCheckOutput` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: submits a shielded proof or note to JLibrustzcash.librustzcashSaplingCheckOutput with a field (anchor, cv, nullifier) not bound, forging a valid-looking spend
- Invariant to test: JLibrustzcash.librustzcashSaplingCheckOutput binds every proof field to the verified statement
- Expected Immunefi impact: Asset theft (Critical)
- Fast validation: JUnit mutating one proof field asserting verify fails
