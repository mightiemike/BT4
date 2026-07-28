# Q3134: EVM call encoding - function choice mode confusion

## Question
When an unprivileged actor cause many public Push-chain outbounds to the same EVM chain to queue concurrently, does `encodeFunctionCall` remain safe if they control `TxType`, asset address emptiness, and payload shape used to choose the vault function name, or can that make it switch between native, ERC20, execute, revert, or rescue semantics without changing the economic intent seen on Push Chain, violate the rule that success, revert, and refund decisions always match the actual EVM chain outcome for that outbound, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:encodeFunctionCall
- Entrypoint: cause many public Push-chain outbounds to the same EVM chain to queue concurrently
- Attacker controls: `TxType`, asset address emptiness, and payload shape used to choose the vault function name
- Exploit idea: switch between native, ERC20, execute, revert, or rescue semantics without changing the economic intent seen on Push Chain
- Invariant to test: success, revert, and refund decisions always match the actual EVM chain outcome for that outbound
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: for one crafted outbound, rebuild the unsigned request and final transaction bytes and prove the hash, nonce, and calldata stay identical across retries
