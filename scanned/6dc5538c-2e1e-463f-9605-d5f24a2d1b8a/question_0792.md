# Q0792: Expire/finalize index cleanup leaves a ballot processable twice via Vote-Bearing Messages If Signer / Variant Handling Is Only in Keeper.MarkBallotExpired

## Question
Can an unprivileged attacker enter through an attacker-created observation that honest UVs later vote through the generic ballot engine with vote-bearing messages if signer restrictions can be bypassed by an unprivileged account when variant handling is the only guard against semantic collisions, and cause `Keeper.MarkBallotExpired` to trigger an unsafe state-transition edge case, so that it strand ids across active, expired, and finalized sets so later logic acts on them again, breaking the invariant that one ballot must have exactly one terminal lifecycle across all indexes, and resulting in Duplicate or blocked finalization leading to fund loss or freeze?

## Target
- File/function: x/uvalidator/keeper/ballot.go::Keeper.MarkBallotExpired
- Entrypoint: an attacker-created observation that honest UVs later vote through the generic ballot engine
- Attacker controls: vote-bearing messages if signer restrictions can be bypassed by an unprivileged account
- Exploit idea: Cause `Keeper.MarkBallotExpired` to trigger an unsafe state-transition edge case, so it can strand ids across active, expired, and finalized sets so later logic acts on them again.
- Invariant to test: one ballot must have exactly one terminal lifecycle across all indexes
- Expected Immunefi impact: Duplicate or blocked finalization leading to fund loss or freeze
- Fast validation: write a keeper test that feeds the crafted observation variants into the ballot engine and inspect ids, vote counts, and terminal hooks
