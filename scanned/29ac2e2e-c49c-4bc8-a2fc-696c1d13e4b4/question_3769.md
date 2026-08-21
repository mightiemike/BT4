# Q3769: AccountStore: iterator resource leak

## Question
Can an unprivileged attacker (RPC query) abuse `AccountStore.getSun` in `chainbase/src/main/java/org/tron/core/store/AccountStore.java` — where the attacker triggers AccountStore.getSun paths that open iterators/snapshots without closing, leaking handles or memory — to break the invariant that every iterator opened in AccountStore.getSun is closed on all paths, leading to: DoS via RPC-API (Advanced)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountStore.java` -> `AccountStore.getSun`
- Entrypoint: repeated queries via AccountStore.getSun
- Attacker controls: request/transaction/contract inputs to `AccountStore.getSun` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers AccountStore.getSun paths that open iterators/snapshots without closing, leaking handles or memory
- Invariant to test: every iterator opened in AccountStore.getSun is closed on all paths
- Expected Immunefi impact: DoS via RPC-API (Advanced)
- Fast validation: stress AccountStore.getSun and watch handle/heap growth
