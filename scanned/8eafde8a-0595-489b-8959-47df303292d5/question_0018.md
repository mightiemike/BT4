# Q0018: commitment partition boundary via `add_partition` (partition.rs)

## Question
Can an unprivileged attacker who sends L2 transactions that force a specific sequencer-commitment partition shape, controlling the per-commitment L2 block counts, drive `add_partition` in `crates/batch-prover/src/partition.rs` so that the commitment range the proof claims and the range the partition actually covered stop being the same range, breaking the invariant that proved ranges equal partitioned ranges?

## Target
- File/function: `crates/batch-prover/src/partition.rs` -> `add_partition`
- Entrypoint: unprivileged party sends L2 transactions that force a specific sequencer-commitment partition shape
- Attacker controls: the per-commitment L2 block counts
- Exploit idea: commitment partition boundary - reach `add_partition` from that entrypoint and force the divergence where the commitment range the proof claims and the range the partition actually covered stop being the same range; the adjacent symbols in the same file that carry the value are `PartitionMode`, `PartitionReason`, `PartitionState`, `Partition`, so evaluate both sides of the equality through them before and after the attacker's action.
- Invariant to test: proved ranges equal partitioned ranges
- Expected Immunefi impact: Critical - a batch/light-client proof accepted for a state transition that never happened (false claim proved)
- Fast validation: partition at an adversarial boundary and diff
