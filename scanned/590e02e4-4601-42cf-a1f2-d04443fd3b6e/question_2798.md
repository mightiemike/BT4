# Q2798: Lenient source-chain address canonicalization misbinds sender or asset via Two Logically Distinct Inbounds / Failed Inbound Should Still in Keeper.RevertStuckInbound

## Question
Can an unprivileged attacker enter through a user-controlled source-chain gateway event that honest UVs later submit via `MsgVoteInbound` with two logically distinct inbounds that differ only by canonicalization-relevant formatting when a failed inbound should still preserve a safe recovery path, and cause `Keeper.RevertStuckInbound` to drive recovery logic into the wrong recipient, asset, or terminal status, so that it present source-chain fields in a format that maps to the wrong sender or asset once canonicalized, breaking the invariant that canonicalization must not let one user-controlled formatting variant steal another asset or identity, and resulting in Direct theft/loss or wrong-party refund?

## Target
- File/function: x/uexecutor/keeper/admin_revert.go::Keeper.RevertStuckInbound
- Entrypoint: a user-controlled source-chain gateway event that honest UVs later submit via `MsgVoteInbound`
- Attacker controls: two logically distinct inbounds that differ only by canonicalization-relevant formatting
- Exploit idea: Cause `Keeper.RevertStuckInbound` to drive recovery logic into the wrong recipient, asset, or terminal status, so it can present source-chain fields in a format that maps to the wrong sender or asset once canonicalized.
- Invariant to test: canonicalization must not let one user-controlled formatting variant steal another asset or identity
- Expected Immunefi impact: Direct theft/loss or wrong-party refund
- Fast validation: write a keeper integration test that finalizes the crafted inbound(s) through the honest-UV path and inspect the UTX, PC tx, and recovery lifecycle
