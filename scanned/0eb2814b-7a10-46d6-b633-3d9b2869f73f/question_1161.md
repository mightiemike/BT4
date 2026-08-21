# Q1161: SectionBloomStore: count/size query full-scan

## Question
Can an unprivileged attacker (RPC query) abuse `SectionBloomStore.has` in `chainbase/src/main/java/org/tron/core/store/SectionBloomStore.java` — where the attacker calls a count/size path backed by SectionBloomStore.has that iterates the whole store per request — to break the invariant that SectionBloomStore.has answers count/size without full iteration, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/SectionBloomStore.java` -> `SectionBloomStore.has`
- Entrypoint: query backed by SectionBloomStore.has
- Attacker controls: request/transaction/contract inputs to `SectionBloomStore.has` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: calls a count/size path backed by SectionBloomStore.has that iterates the whole store per request
- Invariant to test: SectionBloomStore.has answers count/size without full iteration
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: JUnit measuring SectionBloomStore.has cost vs store size
