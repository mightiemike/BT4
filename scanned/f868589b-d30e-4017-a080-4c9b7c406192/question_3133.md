# Q3133: EVM function select - function choice mode confusion

## Question
If a user cause many public Push-chain outbounds to the same EVM chain to queue concurrently, can `determineFunctionName` be pushed into a path where `TxType`, asset address emptiness, and payload shape used to choose the vault function name causes it to switch between native, ERC20, execute, revert, or rescue semantics without changing the economic intent seen on Push Chain, so that success, revert, and refund decisions always match the actual EVM chain outcome for that outbound no longer holds and the result is direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:determineFunctionName
- Entrypoint: cause many public Push-chain outbounds to the same EVM chain to queue concurrently
- Attacker controls: `TxType`, asset address emptiness, and payload shape used to choose the vault function name
- Exploit idea: switch between native, ERC20, execute, revert, or rescue semantics without changing the economic intent seen on Push Chain
- Invariant to test: success, revert, and refund decisions always match the actual EVM chain outcome for that outbound
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: for one crafted outbound, rebuild the unsigned request and final transaction bytes and prove the hash, nonce, and calldata stay identical across retries
