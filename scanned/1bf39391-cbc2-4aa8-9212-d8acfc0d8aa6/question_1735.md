# Q1735: invoke_context::get_feature_set - epoch stake queries return node-dependent values (passing the same account twice with)

## Question
Can an unprivileged attacker who invokes its own deployed SBF program, which performs CPI and consumes compute units, passing the same account twice with different signer/writable flags in one instruction, drive `invoke_context::get_feature_set` to call get_epoch_stake or get_epoch_stake_for_vote_account and receive a value that differs between nodes at the same slot, so that the invariant that epoch stake observed by a program is identical on every node at a given slot is broken and the outcome is Consensus/Safety Violation (bank hash divergence or fork)?

## Target
- File/function: `program-runtime/src/invoke_context.rs` -> `get_feature_set`
- Entrypoint: invokes its own deployed SBF program, which performs CPI and consumes compute units, passing the same account twice with different signer/writable flags in one instruction
- Attacker controls: the program bytecode, instruction data, account list and privileges, CPI depth and signer seeds
- Exploit idea: Call get_epoch_stake or get_epoch_stake_for_vote_account and receive a value that differs between nodes at the same slot.
- Invariant to test: Epoch stake observed by a program is identical on every node at a given slot.
- Expected Immunefi impact: Critical - Consensus/Safety Violation (bank hash divergence or fork)
- Fast validation: program-runtime unit test invoking the crafted instruction and asserting privileges, stack height and compute metering hold
