# Q0993: subscription replay after reorg via `l2_block_event_handler` (subscription.rs)

## Question
Can an unprivileged attacker who keeps a subscription open across an L1 reorg, controlling subscription timing, drive `l2_block_event_handler` in `crates/ethereum-rpc/src/subscription.rs` so that the events a subscriber received and the events the canonical chain contains stop being the same sequence, breaking the invariant that subscribers converge on canonical events?

## Target
- File/function: `crates/ethereum-rpc/src/subscription.rs` -> `l2_block_event_handler`
- Entrypoint: unprivileged party keeps a subscription open across an L1 reorg
- Attacker controls: subscription timing
- Exploit idea: subscription replay after reorg - reach `l2_block_event_handler` from that entrypoint and force the divergence where the events a subscriber received and the events the canonical chain contains stop being the same sequence; the adjacent symbols in the same file that carry the value are `SubscriptionManager`, `register_new_heads_subscription`, `register_new_logs_subscription`, `head_subscriber_task`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: subscribers converge on canonical events
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: reorg and assert corrective events
