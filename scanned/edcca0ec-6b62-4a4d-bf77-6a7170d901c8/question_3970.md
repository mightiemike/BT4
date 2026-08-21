# Q3970: MessageCall: storage write miscount

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MessageCall.getEndowment` in `actuator/src/main/java/org/tron/core/vm/MessageCall.java` — where the attacker triggers MessageCall.getEndowment so storage refunds/writes are counted wrong, letting free or negative-cost writes — to break the invariant that storage energy in MessageCall.getEndowment equals net slots changed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/MessageCall.java` -> `MessageCall.getEndowment`
- Entrypoint: contract toggling storage via MessageCall.getEndowment
- Attacker controls: request/transaction/contract inputs to `MessageCall.getEndowment` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers MessageCall.getEndowment so storage refunds/writes are counted wrong, letting free or negative-cost writes
- Invariant to test: storage energy in MessageCall.getEndowment equals net slots changed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test toggling slots and asserting net charge
