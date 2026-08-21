# Q3995: TronStoreWithRevoking: key collision cross-account

## Question
Can an unprivileged attacker (RPC query) abuse `TronStoreWithRevoking.getUnchecked` in `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` — where the attacker crafts a key consumed by TronStoreWithRevoking.getUnchecked that collides with another account's entry, reading/overwriting it — to break the invariant that storage keys in TronStoreWithRevoking.getUnchecked are injective across accounts, leading to: Cross-account state corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/db/TronStoreWithRevoking.java` -> `TronStoreWithRevoking.getUnchecked`
- Entrypoint: write via a path using TronStoreWithRevoking.getUnchecked
- Attacker controls: request/transaction/contract inputs to `TronStoreWithRevoking.getUnchecked` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a key consumed by TronStoreWithRevoking.getUnchecked that collides with another account's entry, reading/overwriting it
- Invariant to test: storage keys in TronStoreWithRevoking.getUnchecked are injective across accounts
- Expected Immunefi impact: Cross-account state corruption (Critical)
- Fast validation: JUnit constructing colliding keys asserting isolation
