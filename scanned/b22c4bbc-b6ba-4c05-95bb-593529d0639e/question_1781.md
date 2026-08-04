# Q1781: cleanup-stuck lifecycle in CommonStore.delete

## Question
Can an unprivileged attacker reach /jsonrpc eth_sendRawTransaction so chainbase/src/main/java/org/tron/core/db/CommonStore.java::delete leaves one cancel, withdraw, claim, spend, or unfreeze lifecycle record behind, making the next legal user action impossible and causing Permanent lock or stale-state corruption?

## Target
- File/function: chainbase/src/main/java/org/tron/core/db/CommonStore.java::delete
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Target the transitions that move records from active to completed or canceled, especially when multiple stores track the same lifecycle.
- Invariant to test: Lifecycle completion must cleanly retire or transition every linked record that future legal actions depend on.
- Expected Immunefi impact: Permanent lock or stale-state corruption
- Fast validation: Run full create-to-complete flows via /jsonrpc eth_sendRawTransaction and assert every active record, index, and balance becomes recoverable and retry-safe afterward.
