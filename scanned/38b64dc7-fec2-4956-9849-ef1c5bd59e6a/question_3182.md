# Q3182: write-before-finality replay in TxOutputCapsule.getTxOutput

## Question
Can an unprivileged attacker use /wallet/broadcasthex so framework/src/main/java/org/tron/core/capsule/TxOutputCapsule.java::getTxOutput records replay-protection or settlement state before final outcome is known, then exploit rollback or retry windows to get Double application of one logical action?

## Target
- File/function: framework/src/main/java/org/tron/core/capsule/TxOutputCapsule.java::getTxOutput
- Entrypoint: /wallet/broadcasthex
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Look for writes that happen before the final success/failure decision and can later be retried through another public path.
- Invariant to test: Replay-protection and settlement markers must become durable exactly once and only after the final branch is known.
- Expected Immunefi impact: Double application of one logical action
- Fast validation: Inject failures after tentative writes via /wallet/broadcasthex; assert retries cannot settle again or bypass replay protection.
