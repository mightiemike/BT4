# Q1351: EVM already-executed check - replay timing nonce collision

## Question
When an unprivileged actor create a public Push-chain outbound to an EVM destination with attacker-chosen amount, asset, recipient, payload, gas, and revert fields, does `IsAlreadyExecuted` remain safe if they control how the same outbound is retried when mempool drops, nonce consumption, or empty tx hashes occur, or can that make it make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another, violate the rule that success, revert, and refund decisions always match the actual EVM chain outcome for that outbound, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:IsAlreadyExecuted
- Entrypoint: create a public Push-chain outbound to an EVM destination with attacker-chosen amount, asset, recipient, payload, gas, and revert fields
- Attacker controls: how the same outbound is retried when mempool drops, nonce consumption, or empty tx hashes occur
- Exploit idea: make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another
- Invariant to test: success, revert, and refund decisions always match the actual EVM chain outcome for that outbound
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: force success, revert, not-found, and mempool-drop cases and verify the resolver never marks the wrong terminal outcome
