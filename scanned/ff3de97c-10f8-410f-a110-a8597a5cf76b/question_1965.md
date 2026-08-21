# Q1965: BandwidthProcessor: exchange reserve manipulation

## Question
Can an unprivileged attacker (broadcast transaction) abuse `BandwidthProcessor.updateUsageForDelegated` in `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java` — where the attacker uses BandwidthProcessor.updateUsageForDelegated to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant — to break the invariant that exchange reserve math in BandwidthProcessor.updateUsageForDelegated preserves its stated invariant, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java` -> `BandwidthProcessor.updateUsageForDelegated`
- Entrypoint: broadcast exchange ops via BandwidthProcessor.updateUsageForDelegated
- Attacker controls: request/transaction/contract inputs to `BandwidthProcessor.updateUsageForDelegated` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: uses BandwidthProcessor.updateUsageForDelegated to inject/withdraw/trade so exchange reserves drift from the constant-product/expected invariant
- Invariant to test: exchange reserve math in BandwidthProcessor.updateUsageForDelegated preserves its stated invariant
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit trading across boundaries asserting reserve invariant
