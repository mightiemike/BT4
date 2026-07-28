# Q3885: EVM function select - hash identity mode confusion

## Question
When an unprivileged actor cause many public Push-chain outbounds to the same EVM chain to queue concurrently, does `determineFunctionName` remain safe if they control `TxID`, `UniversalTxId`, `Nonce`, and `SigningHash` as derived from the outbound row, or can that make it switch between native, ERC20, execute, revert, or rescue semantics without changing the economic intent seen on Push Chain, violate the rule that each outbound row maps to one and only one EVM execution mode with no silent native/token or execute/revert switch, and end in direct theft or loss of substantial user funds through an unauthorized cross-chain execution?

## Target
- File/function: universalClient/chains/evm/tx_builder.go:determineFunctionName
- Entrypoint: cause many public Push-chain outbounds to the same EVM chain to queue concurrently
- Attacker controls: `TxID`, `UniversalTxId`, `Nonce`, and `SigningHash` as derived from the outbound row
- Exploit idea: switch between native, ERC20, execute, revert, or rescue semantics without changing the economic intent seen on Push Chain
- Invariant to test: each outbound row maps to one and only one EVM execution mode with no silent native/token or execute/revert switch
- Expected Immunefi impact: direct theft or loss of substantial user funds through an unauthorized cross-chain execution
- Fast validation: queue several outbounds to the same chain, drop or replace one EVM tx, and see whether another outbound incorrectly inherits its nonce state
