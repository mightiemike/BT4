# Q0903: subscription/canonical race via `l2_block_event_handler` (subscription.rs)

## Question
Can an unprivileged attacker who keeps a subscription open across an L1 reorg, controlling the reorg depth it can induce with valid Bitcoin transactions, drive `l2_block_event_handler` in `crates/ethereum-rpc/src/subscription.rs` so that the block a subscriber is notified about and the block that ends up canonical stop being the same block, breaking the invariant that subscribers only observe canonical blocks or explicit reorg notices?

## Target
- File/function: `crates/ethereum-rpc/src/subscription.rs` -> `l2_block_event_handler`
- Entrypoint: unprivileged party keeps a subscription open across an L1 reorg
- Attacker controls: the reorg depth it can induce with valid Bitcoin transactions
- Exploit idea: subscription/canonical race - reach `l2_block_event_handler` from that entrypoint and force the divergence where the block a subscriber is notified about and the block that ends up canonical stop being the same block; the adjacent symbols in the same file that carry the value are `SubscriptionManager`, `register_new_heads_subscription`, `register_new_logs_subscription`, `head_subscriber_task`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: subscribers only observe canonical blocks or explicit reorg notices
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: drive a reorg during an open subscription and assert a correction is emitted
