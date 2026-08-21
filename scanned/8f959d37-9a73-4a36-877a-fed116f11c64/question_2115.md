# Q2115: Storage: storage write miscount

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `Storage.put` in `actuator/src/main/java/org/tron/core/vm/program/Storage.java` — where the attacker triggers Storage.put so storage refunds/writes are counted wrong, letting free or negative-cost writes — to break the invariant that storage energy in Storage.put equals net slots changed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/program/Storage.java` -> `Storage.put`
- Entrypoint: contract toggling storage via Storage.put
- Attacker controls: request/transaction/contract inputs to `Storage.put` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers Storage.put so storage refunds/writes are counted wrong, letting free or negative-cost writes
- Invariant to test: storage energy in Storage.put equals net slots changed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test toggling slots and asserting net charge
