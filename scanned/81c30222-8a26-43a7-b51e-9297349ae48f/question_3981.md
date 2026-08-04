# Q3981: builder-executor mismatch in PaymentAddress.decode

## Question
Can an unprivileged attacker use shielded transaction build -> sign -> /wallet/broadcasttransaction so framework/src/main/java/org/tron/core/zen/address/PaymentAddress.java::decode builds shielded parameters under assumptions that the later executor does not recheck, allowing a crafted request to reach Unauthorized shielded spend or note theft?

## Target
- File/function: framework/src/main/java/org/tron/core/zen/address/PaymentAddress.java::decode
- Entrypoint: shielded transaction build -> sign -> /wallet/broadcasttransaction
- Attacker controls: note commitments, nullifiers, roots or anchors, proofs, viewing keys, transparent addresses, fee, and trigger calldata
- Exploit idea: Compare parameter builders, trigger-input helpers, note scans, and final settlement for missing consistency checks.
- Invariant to test: Any helper that prepares a shielded action must enforce the same object identity, amount, and context rules as final settlement.
- Expected Immunefi impact: Unauthorized shielded spend or note theft
- Fast validation: Build one shielded action through all public helper APIs via shielded transaction build -> sign -> /wallet/broadcasttransaction; assert the executor revalidates every security-critical field.
