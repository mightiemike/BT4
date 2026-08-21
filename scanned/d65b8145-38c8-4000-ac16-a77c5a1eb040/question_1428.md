# Q1428: Manager: tx hash / dedup collision

## Question
Can an unprivileged attacker (broadcast transaction) abuse `Manager.needToUpdateAsset` in `framework/src/main/java/org/tron/core/db/Manager.java` — where the attacker crafts two distinct transactions colliding on the id/cache key checked by Manager.needToUpdateAsset, evicting or replaying one — to break the invariant that distinct transactions have distinct dedup keys in Manager.needToUpdateAsset, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/db/Manager.java` -> `Manager.needToUpdateAsset`
- Entrypoint: broadcast colliding txs to Manager.needToUpdateAsset
- Attacker controls: request/transaction/contract inputs to `Manager.needToUpdateAsset` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts two distinct transactions colliding on the id/cache key checked by Manager.needToUpdateAsset, evicting or replaying one
- Invariant to test: distinct transactions have distinct dedup keys in Manager.needToUpdateAsset
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: JUnit constructing id-collision pair asserting both distinct
