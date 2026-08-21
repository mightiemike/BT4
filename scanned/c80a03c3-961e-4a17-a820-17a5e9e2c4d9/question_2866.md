# Q2866: Manager: tx hash / dedup collision

## Question
Can an unprivileged attacker (broadcast transaction) abuse `Manager.needToSetBlackholePermission` in `framework/src/main/java/org/tron/core/db/Manager.java` — where the attacker crafts two distinct transactions colliding on the id/cache key checked by Manager.needToSetBlackholePermission, evicting or replaying one — to break the invariant that distinct transactions have distinct dedup keys in Manager.needToSetBlackholePermission, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/db/Manager.java` -> `Manager.needToSetBlackholePermission`
- Entrypoint: broadcast colliding txs to Manager.needToSetBlackholePermission
- Attacker controls: request/transaction/contract inputs to `Manager.needToSetBlackholePermission` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts two distinct transactions colliding on the id/cache key checked by Manager.needToSetBlackholePermission, evicting or replaying one
- Invariant to test: distinct transactions have distinct dedup keys in Manager.needToSetBlackholePermission
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit constructing id-collision pair asserting both distinct
