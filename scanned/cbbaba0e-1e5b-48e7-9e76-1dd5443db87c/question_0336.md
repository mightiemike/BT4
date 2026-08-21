# Q336: VM: storage write miscount

## Question
Can an unprivileged attacker (smart-contract deploy/trigger) abuse `VM.play` in `actuator/src/main/java/org/tron/core/vm/VM.java` — where the attacker triggers VM.play so storage refunds/writes are counted wrong, letting free or negative-cost writes — to break the invariant that storage energy in VM.play equals net slots changed, leading to: Asset/accounting corruption (Critical)?

## Target
- File/function: `actuator/src/main/java/org/tron/core/vm/VM.java` -> `VM.play`
- Entrypoint: contract toggling storage via VM.play
- Attacker controls: request/transaction/contract inputs to `VM.play` (no privileged role, no leaked key, no peer/node control)
- Exploit idea: triggers VM.play so storage refunds/writes are counted wrong, letting free or negative-cost writes
- Invariant to test: storage energy in VM.play equals net slots changed
- Expected Immunefi impact: Asset/accounting corruption (Critical)
- Fast validation: VM test toggling slots and asserting net charge
