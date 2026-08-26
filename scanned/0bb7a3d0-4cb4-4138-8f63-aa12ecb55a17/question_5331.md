# Q5331: fee_distribution::deposit_or_burn_fee - rounding in the burn split repeatedly favours the attacker (landing the block on a slot)

## Question
Can an unprivileged attacker who submits fee-paying transactions whose fees are split between burn and the leader's collector, landing the block on a slot where the collector account is being closed, drive `fee_distribution::deposit_or_burn_fee` to choose fee amounts whose rounding consistently rounds in the collector's favour, so that the invariant that rounding never creates lamports across a block is broken and the outcome is Loss of Funds (lamport inflation / supply corruption)?

## Target
- File/function: `runtime/src/bank/fee_distribution.rs` -> `deposit_or_burn_fee`
- Entrypoint: submits fee-paying transactions whose fees are split between burn and the leader's collector, landing the block on a slot where the collector account is being closed
- Attacker controls: fee amounts through compute budget and signature counts, and the vote/stake accounts it owns
- Exploit idea: Choose fee amounts whose rounding consistently rounds in the collector's favour.
- Invariant to test: Rounding never creates lamports across a block.
- Expected Immunefi impact: Critical - Loss of Funds (lamport inflation / supply corruption)
- Fast validation: bank test running the crafted block and asserting burned plus deposited equals collected fees exactly
