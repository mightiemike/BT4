# Q3041: ledger RPC index bounds via `get_sequencer_commitments_on_slot_by_number` (rpc.rs)

## Question
Can an unprivileged attacker who calls a ledger / node RPC method with out-of-range or reversed parameters, controlling the height or index requested, drive `get_sequencer_commitments_on_slot_by_number` in `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` so that the range the ledger RPC iterates and the range the caller requested stop being the same range, breaking the invariant that range queries never read beyond the requested window?

## Target
- File/function: `crates/sovereign-sdk/full-node/db/sov-db/src/ledger_db/rpc.rs` -> `get_sequencer_commitments_on_slot_by_number`
- Entrypoint: unprivileged party calls a ledger / node RPC method with out-of-range or reversed parameters
- Attacker controls: the height or index requested
- Exploit idea: ledger RPC index bounds - reach `get_sequencer_commitments_on_slot_by_number` from that entrypoint and force the divergence where the range the ledger RPC iterates and the range the caller requested stop being the same range; the adjacent symbols in the same file that carry the value are `check_if_l2_block_pruned`, `get_l2_block`, `get_l2_block_by_hash`, `get_l2_block_by_number`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: range queries never read beyond the requested window
- Expected Immunefi impact: High - node serves state contradicting the proved chain to bridges, exchanges and Clementine operators
- Fast validation: call with reversed/oversized ranges and assert bounded output
