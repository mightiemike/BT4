# Q47: Manager: permission parse abuse

## Question
Can an unprivileged attacker (broadcast transaction) abuse `Manager.needToUpdateAsset` in `framework/src/main/java/org/tron/core/db/Manager.java` — where the attacker crafts a permission/contract field that Manager.needToUpdateAsset parses into an over-weight or malformed permission accepted downstream — to break the invariant that Manager.needToUpdateAsset bounds permission count, weight, and structure, leading to: Unauthorized account operations (Critical)?

## Target
- File/function: `framework/src/main/java/org/tron/core/db/Manager.java` -> `Manager.needToUpdateAsset`
- Entrypoint: broadcast a permission tx via Manager.needToUpdateAsset
- Attacker controls: request/transaction/contract inputs to `Manager.needToUpdateAsset` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: crafts a permission/contract field that Manager.needToUpdateAsset parses into an over-weight or malformed permission accepted downstream
- Invariant to test: Manager.needToUpdateAsset bounds permission count, weight, and structure
- Expected Immunefi impact: Unauthorized account operations (Critical)
- Fast validation: JUnit with oversized permission asserting rejection
