# Q1574: write-before-finality replay in SafeExchangeProcessor.exchangeToSupply

## Question
Can an unprivileged attacker use /wallet/exchangetransaction -> sign -> /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java::exchangeToSupply records replay-protection or settlement state before final outcome is known, then exploit rollback or retry windows to get Double fill, cancel, or exchange settlement?

## Target
- File/function: chainbase/src/main/java/org/tron/core/capsule/SafeExchangeProcessor.java::exchangeToSupply
- Entrypoint: /wallet/exchangetransaction -> sign -> /wallet/broadcasttransaction
- Attacker controls: owner address, token ids, pair direction, order or cancel ids, quantities, price fields, and signatures
- Exploit idea: Look for writes that happen before the final success/failure decision and can later be retried through another public path.
- Invariant to test: Replay-protection and settlement markers must become durable exactly once and only after the final branch is known.
- Expected Immunefi impact: Double fill, cancel, or exchange settlement
- Fast validation: Inject failures after tentative writes via /wallet/exchangetransaction -> sign -> /wallet/broadcasttransaction; assert retries cannot settle again or bypass replay protection.
