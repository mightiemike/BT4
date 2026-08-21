# Q156: BandwidthProcessor: reward rounding drift

## Question
Can an unprivileged attacker (broadcast transaction) abuse `BandwidthProcessor.calculateGlobalNetLimit` in `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java` — where the attacker repeatedly claims through BandwidthProcessor.calculateGlobalNetLimit exploiting rounding/precision to extract more reward than accrued — to break the invariant that reward paid never exceeds accrued reward across rounding in BandwidthProcessor.calculateGlobalNetLimit, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java` -> `BandwidthProcessor.calculateGlobalNetLimit`
- Entrypoint: many small claims via BandwidthProcessor.calculateGlobalNetLimit
- Attacker controls: request/transaction/contract inputs to `BandwidthProcessor.calculateGlobalNetLimit` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: repeatedly claims through BandwidthProcessor.calculateGlobalNetLimit exploiting rounding/precision to extract more reward than accrued
- Invariant to test: reward paid never exceeds accrued reward across rounding in BandwidthProcessor.calculateGlobalNetLimit
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit summing many rounded claims vs single accrual
