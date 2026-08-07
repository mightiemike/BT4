# Q1160: try_into_config settles one authorization twice (transaction_meta.rs)

## Question
Can an unprivileged attacker entering through a transaction broadcast to a public TPU/QUIC endpoint by an ordinary funded keypair reach `try_into_config` in `runtime-transaction/src/transaction_meta.rs` with arguments that drive the path into its error branch after side effects were applied, and have `try_into_config` apply the same authorized effect a second time, so that the invariant "One signed authorization produces exactly one state effect." breaks and the result is Loss of Funds?

## Target
- File/function: `runtime-transaction/src/transaction_meta.rs` -> `try_into_config()` (around line 139)
- Entrypoint: a transaction broadcast to a public TPU/QUIC endpoint by an ordinary funded keypair
- Attacker controls: arguments that drive the path into its error branch after side effects were applied
- Exploit idea: Get `try_into_config` to apply the same logical effect twice from a single user authorization by re-entering it or replaying the surrounding flow.
- Invariant to test: One signed authorization produces exactly one state effect.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Integration test: submit the flow twice (and once re-entrantly) and assert the second application is rejected and balances moved once.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged attacker can cause account state to be silently lost, duplicated, or resurrected across cache flush, shrink, ancient-append-vec packing, clean, or purge, changing user balances without a transaction.
