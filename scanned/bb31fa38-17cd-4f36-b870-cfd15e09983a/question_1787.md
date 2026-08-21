# Q1787: SectionBloomStore: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `SectionBloomStore.has` in `chainbase/src/main/java/org/tron/core/store/SectionBloomStore.java` — where the attacker triggers SectionBloomStore.has paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in SectionBloomStore.has is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/SectionBloomStore.java` -> `SectionBloomStore.has`
- Entrypoint: repeated queries via SectionBloomStore.has
- Attacker controls: request/transaction/contract inputs to `SectionBloomStore.has` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers SectionBloomStore.has paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in SectionBloomStore.has is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress SectionBloomStore.has and watch handle/heap growth
