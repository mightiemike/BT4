# Q1274: write-before-finality replay in ChainBaseManager.hasBlocks

## Question
Can an unprivileged attacker use /jsonrpc eth_sendRawTransaction so chainbase/src/main/java/org/tron/core/ChainBaseManager.java::hasBlocks records replay-protection or settlement state before final outcome is known, then exploit rollback or retry windows to get Double application of one logical action?

## Target
- File/function: chainbase/src/main/java/org/tron/core/ChainBaseManager.java::hasBlocks
- Entrypoint: /jsonrpc eth_sendRawTransaction
- Attacker controls: publicly supplied addresses, ids, amounts, signatures, indexes, and encoding flags
- Exploit idea: Look for writes that happen before the final success/failure decision and can later be retried through another public path.
- Invariant to test: Replay-protection and settlement markers must become durable exactly once and only after the final branch is known.
- Expected Immunefi impact: Double application of one logical action
- Fast validation: Inject failures after tentative writes via /jsonrpc eth_sendRawTransaction; assert retries cannot settle again or bypass replay protection.
