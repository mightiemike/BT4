# Q1063: AccountCapsule: tx hash / dedup collision

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AccountCapsule.createReadableString` in `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` — where the attacker crafts two distinct transactions colliding on the id/cache key checked by AccountCapsule.createReadableString, evicting or replaying one — to break the invariant that distinct transactions have distinct dedup keys in AccountCapsule.createReadableString, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` -> `AccountCapsule.createReadableString`
- Entrypoint: broadcast colliding txs to AccountCapsule.createReadableString
- Attacker controls: request/transaction/contract inputs to `AccountCapsule.createReadableString` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts two distinct transactions colliding on the id/cache key checked by AccountCapsule.createReadableString, evicting or replaying one
- Invariant to test: distinct transactions have distinct dedup keys in AccountCapsule.createReadableString
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit constructing id-collision pair asserting both distinct
