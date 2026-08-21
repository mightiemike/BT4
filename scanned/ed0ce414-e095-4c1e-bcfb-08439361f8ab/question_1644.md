# Q1644: BandwidthProcessor: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `BandwidthProcessor.updateUsageForDelegated` in `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java` — where the attacker drives BandwidthProcessor.updateUsageForDelegated usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in BandwidthProcessor.updateUsageForDelegated never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java` -> `BandwidthProcessor.updateUsageForDelegated`
- Entrypoint: repeated ops via BandwidthProcessor.updateUsageForDelegated
- Attacker controls: request/transaction/contract inputs to `BandwidthProcessor.updateUsageForDelegated` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives BandwidthProcessor.updateUsageForDelegated usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in BandwidthProcessor.updateUsageForDelegated never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
