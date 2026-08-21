# Q3250: BandwidthProcessor: recover/adjust underflow

## Question
Can an unprivileged attacker (broadcast transaction) abuse `BandwidthProcessor.calculateGlobalNetLimitV2` in `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java` — where the attacker drives BandwidthProcessor.calculateGlobalNetLimitV2 usage recovery so recovered resource exceeds what was consumed, granting free resource — to break the invariant that recovered resource in BandwidthProcessor.calculateGlobalNetLimitV2 never exceeds consumed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/BandwidthProcessor.java` -> `BandwidthProcessor.calculateGlobalNetLimitV2`
- Entrypoint: repeated ops via BandwidthProcessor.calculateGlobalNetLimitV2
- Attacker controls: request/transaction/contract inputs to `BandwidthProcessor.calculateGlobalNetLimitV2` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: drives BandwidthProcessor.calculateGlobalNetLimitV2 usage recovery so recovered resource exceeds what was consumed, granting free resource
- Invariant to test: recovered resource in BandwidthProcessor.calculateGlobalNetLimitV2 never exceeds consumed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit measuring recovery vs consumption
