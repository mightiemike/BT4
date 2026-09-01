# Q0393: subscription replay after reorg via `register_new_logs_subscription` (subscription.rs)

## Question
Can an unprivileged attacker who keeps a subscription open across an L1 reorg, controlling subscription timing, drive `register_new_logs_subscription` in `crates/ethereum-rpc/src/subscription.rs` so that the events a subscriber received and the events the canonical chain contains stop being the same sequence, breaking the invariant that subscribers converge on canonical events?

## Target
- File/function: `crates/ethereum-rpc/src/subscription.rs` -> `register_new_logs_subscription`
- Entrypoint: unprivileged party keeps a subscription open across an L1 reorg
- Attacker controls: subscription timing
- Exploit idea: subscription replay after reorg - reach `register_new_logs_subscription` from that entrypoint and force the divergence where the events a subscriber received and the events the canonical chain contains stop being the same sequence; the adjacent symbols in the same file that carry the value are `SubscriptionManager`, `register_new_heads_subscription`, `head_subscriber_task`, `log_subscriber_task`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: subscribers converge on canonical events
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: reorg and assert corrective events
