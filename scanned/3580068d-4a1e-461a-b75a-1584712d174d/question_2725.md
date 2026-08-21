# Q2725: SectionBloomStore: revoking-store memory blowup

## Question
Can an unprivileged attacker (RPC query) abuse `SectionBloomStore.put` in `chainbase/src/main/java/org/tron/core/store/SectionBloomStore.java` — where the attacker inflates the revoking/undo set through operations touching SectionBloomStore.put, growing memory per block — to break the invariant that undo state in SectionBloomStore.put is bounded per transaction, leading to: DoS via protocol implementation (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/SectionBloomStore.java` -> `SectionBloomStore.put`
- Entrypoint: many state writes via SectionBloomStore.put
- Attacker controls: request/transaction/contract inputs to `SectionBloomStore.put` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: inflates the revoking/undo set through operations touching SectionBloomStore.put, growing memory per block
- Invariant to test: undo state in SectionBloomStore.put is bounded per transaction
- Expected Immunefi impact: DoS via protocol implementation (Advanced)
- Fast validation: JUnit measuring revoking set growth
