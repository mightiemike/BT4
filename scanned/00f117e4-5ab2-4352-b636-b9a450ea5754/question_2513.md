# Q2513: MessageCall: storage write miscount

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `MessageCall.getOpCode` in `actuator/src/main/java/org/tron/core/vm/MessageCall.java` — where the attacker triggers MessageCall.getOpCode so storage refunds/writes are counted wrong, letting free or negative-cost writes — to break the invariant that storage energy in MessageCall.getOpCode equals net slots changed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/MessageCall.java` -> `MessageCall.getOpCode`
- Entrypoint: contract toggling storage via MessageCall.getOpCode
- Attacker controls: request/transaction/contract inputs to `MessageCall.getOpCode` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers MessageCall.getOpCode so storage refunds/writes are counted wrong, letting free or negative-cost writes
- Invariant to test: storage energy in MessageCall.getOpCode equals net slots changed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test toggling slots and asserting net charge
