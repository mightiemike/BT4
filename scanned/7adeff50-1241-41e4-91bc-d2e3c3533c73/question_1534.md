# Q1534: EVM signing hash build - function choice sign/broadcast mismatch

## Question
When an unprivileged actor trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data, does `GetOutboundSigningRequest` remain safe if they control `TxType`, asset address emptiness, and payload shape used to choose the vault function name, or can that make it produce a signed hash that does not bind the exact destination transaction later broadcast to the EVM chain, violate the rule that each outbound row maps to one and only one EVM execution mode with no silent native/token or execute/revert switch, and end in widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:GetOutboundSigningRequest
- Entrypoint: trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data
- Attacker controls: `TxType`, asset address emptiness, and payload shape used to choose the vault function name
- Exploit idea: produce a signed hash that does not bind the exact destination transaction later broadcast to the EVM chain
- Invariant to test: each outbound row maps to one and only one EVM execution mode with no silent native/token or execute/revert switch
- Expected Immunefi impact: widespread Universal Client overload that prevents new cross-chain transactions from being processed or finalized
- Fast validation: force success, revert, not-found, and mempool-drop cases and verify the resolver never marks the wrong terminal outcome
