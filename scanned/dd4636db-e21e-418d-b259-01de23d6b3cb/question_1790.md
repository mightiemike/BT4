# Q1790: AccountCapsule: tx hash / dedup collision

## Question
Can an unprivileged attacker (broadcast transaction) abuse `AccountCapsule.createDefaultActivePermission` in `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` — where the attacker crafts two distinct transactions colliding on the id/cache key checked by AccountCapsule.createDefaultActivePermission, evicting or replaying one — to break the invariant that distinct transactions have distinct dedup keys in AccountCapsule.createDefaultActivePermission, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `chainbase/src/main/java/org/tron/core/capsule/AccountCapsule.java` -> `AccountCapsule.createDefaultActivePermission`
- Entrypoint: broadcast colliding txs to AccountCapsule.createDefaultActivePermission
- Attacker controls: request/transaction/contract inputs to `AccountCapsule.createDefaultActivePermission` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts two distinct transactions colliding on the id/cache key checked by AccountCapsule.createDefaultActivePermission, evicting or replaying one
- Invariant to test: distinct transactions have distinct dedup keys in AccountCapsule.createDefaultActivePermission
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit constructing id-collision pair asserting both distinct
