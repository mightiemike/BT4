# Q2297: BandwidthProcessor: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `BandwidthProcessor.calculateGlobalNetLimitV2` in `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java` — where the attacker uses BandwidthProcessor.calculateGlobalNetLimitV2 to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in BandwidthProcessor.calculateGlobalNetLimitV2 preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java` -> `BandwidthProcessor.calculateGlobalNetLimitV2`
- Entrypoint: broadcast exchange ops via BandwidthProcessor.calculateGlobalNetLimitV2
- Attacker controls: request/transaction/contract inputs to `BandwidthProcessor.calculateGlobalNetLimitV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses BandwidthProcessor.calculateGlobalNetLimitV2 to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in BandwidthProcessor.calculateGlobalNetLimitV2 preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
