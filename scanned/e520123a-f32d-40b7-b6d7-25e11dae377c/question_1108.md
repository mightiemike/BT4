# Q1108: deposit blob admitted but not executable via `txpool_remove_txs_by_sender` (rpc.rs)

## Question
Can an unprivileged attacker who resubmits a deposit blob that was already included in an earlier L2 block, controlling the entire `Bytes` deposit payload, drive `txpool_remove_txs_by_sender` in `crates/sequencer/src/rpc.rs` so that the deposit that `eth_call` simulated at admission time and the deposit the Bridge system contract actually executes at inclusion height stop being the same call, breaking the invariant that a deposit accepted into `DepositDataMempool` executes with identical semantics at inclusion?

## Target
- File/function: `crates/sequencer/src/rpc.rs` -> `txpool_remove_txs_by_sender`
- Entrypoint: unprivileged party resubmits a deposit blob that was already included in an earlier L2 block
- Attacker controls: the entire `Bytes` deposit payload
- Exploit idea: deposit blob admitted but not executable - reach `txpool_remove_txs_by_sender` from that entrypoint and force the divergence where the deposit that `eth_call` simulated at admission time and the deposit the Bridge system contract actually executes at inclusion height stop being the same call; the adjacent symbols in the same file that carry the value are `RpcContext`, `SequencerRpc`, `SequencerRpcServerImpl`, `create_rpc_context`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: a deposit accepted into `DepositDataMempool` executes with identical semantics at inclusion
- Expected Immunefi impact: Critical - direct theft / unauthorized minting of cBTC (bridge deposit accounting broken)
- Fast validation: unit-test the mempool admission path, then apply the same blob one block later and diff the Bridge call outcome
