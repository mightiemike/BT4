# Q1104: log-trace side effect in CallCreate.getData

## Question
Can an unprivileged attacker use /wallet/triggersmartcontract -> sign -> /wallet/broadcasttransaction so chainbase/src/main/java/org/tron/common/runtime/CallCreate.java::getData emits logs, traces, or bloom updates that survive a failed branch or disagree with the committed TVM storage, balances, or repository state/receipts, refunds, internal transfers, or log state, enabling later settlement or monitoring logic to act on false execution state and causing Repeatable invalid settlement, false spent-state decisions, or node resource abuse from inconsistent execution artifacts?

## Target
- File/function: chainbase/src/main/java/org/tron/common/runtime/CallCreate.java::getData
- Entrypoint: /wallet/triggersmartcontract -> sign -> /wallet/broadcasttransaction
- Attacker controls: contract bytecode, calldata, call value, fee limit, energy, nested-call structure, and storage keys
- Exploit idea: Trigger logging before late failures, nested revert patterns, and edge cases where trace collection is decoupled from state commits.
- Invariant to test: Published execution artifacts must correspond exactly to the committed execution branch and final TVM storage, balances, or repository state/receipts, refunds, internal transfers, or log state.
- Expected Immunefi impact: Repeatable invalid settlement, false spent-state decisions, or node resource abuse from inconsistent execution artifacts
- Fast validation: Construct late-reverting contracts via /wallet/triggersmartcontract -> sign -> /wallet/broadcasttransaction; assert committed logs, traces, and blooms match only the surviving branch.
