# Q2474: EVM signing hash build - hash identity nonce collision

## Question
When an unprivileged actor trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data, does `GetOutboundSigningRequest` remain safe if they control `TxID`, `UniversalTxId`, `Nonce`, and `SigningHash` as derived from the outbound row, or can that make it make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another, violate the rule that each outbound row maps to one and only one EVM execution mode with no silent native/token or execute/revert switch, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:GetOutboundSigningRequest
- Entrypoint: trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data
- Attacker controls: `TxID`, `UniversalTxId`, `Nonce`, and `SigningHash` as derived from the outbound row
- Exploit idea: make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another
- Invariant to test: each outbound row maps to one and only one EVM execution mode with no silent native/token or execute/revert switch
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: force success, revert, not-found, and mempool-drop cases and verify the resolver never marks the wrong terminal outcome
