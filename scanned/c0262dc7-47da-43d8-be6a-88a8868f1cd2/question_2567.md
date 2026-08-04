# Q2567: exceptional-halt lock in LogInfo.getAddress

## Question
Can an unprivileged attacker trigger an exceptional halt through /wallet/triggersmartcontract -> sign -> /wallet/broadcasttransaction so common/src/main/java/org/tron/common/runtime/vm/LogInfo.java::getAddress leaves a contract, account, or note in a half-advanced lifecycle state that cannot be legally completed or reversed, resulting in Permanent contract or user-fund lock from broken cleanup?

## Target
- File/function: common/src/main/java/org/tron/common/runtime/vm/LogInfo.java::getAddress
- Entrypoint: /wallet/triggersmartcontract -> sign -> /wallet/broadcasttransaction
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Focus on obligations, escrow-like flows, delegated-resource native opcodes, and lifecycle transitions that span multiple internal structures.
- Invariant to test: Exceptional halts must either leave the lifecycle untouched or leave a recoverable state; they must not strand value.
- Expected Immunefi impact: Permanent contract or user-fund lock from broken cleanup
- Fast validation: Force halts at each stage of the lifecycle via /wallet/triggersmartcontract -> sign -> /wallet/broadcasttransaction and assert users can still fully recover or retry the affected asset/state.
