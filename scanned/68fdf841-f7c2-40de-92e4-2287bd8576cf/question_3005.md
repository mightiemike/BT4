# Q3005: BandwidthProcessor: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `BandwidthProcessor.calculateGlobalNetLimitV2` in `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java` — where the attacker repeatedly claims through BandwidthProcessor.calculateGlobalNetLimitV2 exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in BandwidthProcessor.calculateGlobalNetLimitV2, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java` -> `BandwidthProcessor.calculateGlobalNetLimitV2`
- Entrypoint: many small claims via BandwidthProcessor.calculateGlobalNetLimitV2
- Attacker controls: request/transaction/contract inputs to `BandwidthProcessor.calculateGlobalNetLimitV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through BandwidthProcessor.calculateGlobalNetLimitV2 exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in BandwidthProcessor.calculateGlobalNetLimitV2
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
