# Q1728: EVM gas-used read - function choice nonce collision

## Question
When an unprivileged actor trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data, does `GetGasFeeUsed` remain safe if they control `TxType`, asset address emptiness, and payload shape used to choose the vault function name, or can that make it make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another, violate the rule that success, revert, and refund decisions always match the actual EVM chain outcome for that outbound, and end in permanent freezing or irrecoverable locking of substantial user funds in the bridge flow?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:GetGasFeeUsed
- Entrypoint: trigger a public Push-chain revert outbound toward an EVM chain with attacker-controlled refund recipient and revert data
- Attacker controls: `TxType`, asset address emptiness, and payload shape used to choose the vault function name
- Exploit idea: make distinct user outbounds share a nonce or terminal resolution path so one can consume or replace another
- Invariant to test: success, revert, and refund decisions always match the actual EVM chain outcome for that outbound
- Expected Immunefi impact: permanent freezing or irrecoverable locking of substantial user funds in the bridge flow
- Fast validation: fuzz asset address emptiness, tx type, revert message, and payload shape, then check whether the chosen vault function ever diverges from the intended mode
