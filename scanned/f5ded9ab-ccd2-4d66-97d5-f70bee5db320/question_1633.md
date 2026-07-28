# Q1633: EVM already-executed check - function choice mode confusion

## Question
If a user trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data, can `IsAlreadyExecuted` be pushed into a path where `TxType`, asset address emptiness, and payload shape used to choose the vault function name causes it to switch between native, ERC20, execute, revert, or rescue semantics without changing the economic intent seen on Push Chain, so that an outbound cannot steal, consume, or inherit another outbound's nonce or terminal state no longer holds and the result is widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:IsAlreadyExecuted
- Entrypoint: trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data
- Attacker controls: `TxType`, asset address emptiness, and payload shape used to choose the vault function name
- Exploit idea: switch between native, ERC20, execute, revert, or rescue semantics without changing the economic intent seen on Push Chain
- Invariant to test: an outbound cannot steal, consume, or inherit another outbound's nonce or terminal state
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: for one crafted outbound, rebuild the unsigned request and final transaction bytes and prove the hash, nonce, and calldata stay identical across retries
