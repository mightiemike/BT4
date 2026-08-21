# Q3107: BandwidthProcessor: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `BandwidthProcessor.updateUsageForDelegated` in `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java` — where the attacker repeatedly claims through BandwidthProcessor.updateUsageForDelegated exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in BandwidthProcessor.updateUsageForDelegated, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java` -> `BandwidthProcessor.updateUsageForDelegated`
- Entrypoint: many small claims via BandwidthProcessor.updateUsageForDelegated
- Attacker controls: request/transaction/contract inputs to `BandwidthProcessor.updateUsageForDelegated` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through BandwidthProcessor.updateUsageForDelegated exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in BandwidthProcessor.updateUsageForDelegated
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
