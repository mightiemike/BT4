# Q3689: SectionBloomStore: prefix scan amplification

## Question
Can an unprivileged attacker (RPC query) abuse `SectionBloomStore.has` in `chainbase/src/main/java/org/tron/core/store/SectionBloomStore.java` — where the attacker seeds keys so a query iterating SectionBloomStore.has performs an unbounded prefix scan on each request — to break the invariant that iteration in SectionBloomStore.has is bounded independent of attacker-seeded key count, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/SectionBloomStore.java` -> `SectionBloomStore.has`
- Entrypoint: query backed by SectionBloomStore.has after seeding keys
- Attacker controls: request/transaction/contract inputs to `SectionBloomStore.has` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: seeds keys so a query iterating SectionBloomStore.has performs an unbounded prefix scan on each request
- Invariant to test: iteration in SectionBloomStore.has is bounded independent of attacker-seeded key count
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit seeding N keys and measuring SectionBloomStore.has scan growth
