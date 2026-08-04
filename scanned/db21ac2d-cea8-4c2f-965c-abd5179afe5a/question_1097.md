# Q1097: internal-transfer mismatch in CallCreate.getData

## Question
Can an unprivileged attacker use /wallet/triggerconstantcontract to make chainbase/src/main/java/org/tron/common/runtime/CallCreate.java::getData commit an internal transfer, refund, or burn in TVM storage, balances, or repository state without the matching receipt, trace, or rollback update in receipts, refunds, internal transfers, or log state, producing Unauthorized internal value movement or a hidden double-settlement path?

## Target
- File/function: chainbase/src/main/java/org/tron/common/runtime/CallCreate.java::getData
- Entrypoint: /wallet/triggerconstantcontract
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Force nested value transfers around reverts, out-of-energy exits, and CREATE/CALL failure modes to see whether accounting and tracing stay aligned.
- Invariant to test: Internal value movement, receipts, and rollback data must stay consistent across all successful and failed execution paths.
- Expected Immunefi impact: Unauthorized internal value movement or a hidden double-settlement path
- Fast validation: Build contracts with nested value movement via /wallet/triggerconstantcontract and assert final balances, internal transactions, receipts, and traces tell one consistent story.
