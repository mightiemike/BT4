# Q3185: cleanup-stuck lifecycle in TxOutputCapsule.getTxOutput

## Question
Can an unprivileged attacker reach /wallet/broadcasthex so framework/src/main/java/org/tron/core/capsule/TxOutputCapsule.java::getTxOutput leaves one cancel, withdraw, claim, spend, or unfreeze lifecycle record behind, making the next legal user action impossible and causing Permanent lock or stale-state corruption?

## Target
- File/function: framework/src/main/java/org/tron/core/capsule/TxOutputCapsule.java::getTxOutput
- Entrypoint: /wallet/broadcasthex
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Target the transitions that move records from active to completed or canceled, especially when multiple stores track the same lifecycle.
- Invariant to test: Lifecycle completion must cleanly retire or transition every linked record that future legal actions depend on.
- Expected Immunefi impact: Permanent lock or stale-state corruption
- Fast validation: Run full create-to-complete flows via /wallet/broadcasthex and assert every active record, index, and balance becomes recoverable and retry-safe afterward.
