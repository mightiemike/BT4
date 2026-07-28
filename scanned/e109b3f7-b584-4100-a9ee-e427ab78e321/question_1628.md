# Q1628: EVM signing hash build - function choice mode confusion

## Question
Can an unprivileged attacker trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data and use control over `TxType`, asset address emptiness, and payload shape used to choose the vault function name so that `GetOutboundSigningRequest` switch between native, ERC20, execute, revert, or rescue semantics without changing the economic intent seen on Push Chain, breaking the invariant that an outbound cannot steal, consume, or inherit another outbound's nonce or terminal state and leading to widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:GetOutboundSigningRequest
- Entrypoint: trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data
- Attacker controls: `TxType`, asset address emptiness, and payload shape used to choose the vault function name
- Exploit idea: switch between native, ERC20, execute, revert, or rescue semantics without changing the economic intent seen on Push Chain
- Invariant to test: an outbound cannot steal, consume, or inherit another outbound's nonce or terminal state
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: for one crafted outbound, rebuild the unsigned request and final transaction bytes and prove the hash, nonce, and calldata stay identical across retries
