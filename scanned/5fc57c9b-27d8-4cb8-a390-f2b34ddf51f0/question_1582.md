# Q1582: AccountStore: key collision cross-account

## Question
Can an unprivileged attacker (RPC query) abuse `AccountStore.getBlackholeAddress` in `chainbase/src/main/java/org/tron/core/store/AccountStore.java` — where the attacker crafts a key consumed by AccountStore.getBlackholeAddress that collides with another account's entry, reading/overwriting it — to break the invariant that storage keys in AccountStore.getBlackholeAddress are injective across accounts, leading to: Cross-account state corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/store/AccountStore.java` -> `AccountStore.getBlackholeAddress`
- Entrypoint: write via a path using AccountStore.getBlackholeAddress
- Attacker controls: request/transaction/contract inputs to `AccountStore.getBlackholeAddress` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a key consumed by AccountStore.getBlackholeAddress that collides with another account's entry, reading/overwriting it
- Invariant to test: storage keys in AccountStore.getBlackholeAddress are injective across accounts
- Expected Immunefi impact: Cross-account state corruption (Critical)
- Fast validation: JUnit constructing colliding keys asserting isolation
