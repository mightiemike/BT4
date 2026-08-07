# Q1887: common_close_account breaks lamport conservation (lib.rs)

## Question
Can an unprivileged attacker entering through a system/stake/vote instruction sent by an ordinary funded keypair, directly or via CPI reach `common_close_account` in `programs/bpf_loader/src/lib.rs` with an account owned by a program the caller controls, with attacker-chosen data, and make the lamports `common_close_account` removes differ from the lamports it credits, so that the invariant "Sum of lamports before and after the operation is equal, except for the explicit fee/rent burn." breaks and the result is Loss of Funds?

## Target
- File/function: `programs/bpf_loader/src/lib.rs` -> `common_close_account()` (around line 1003)
- Entrypoint: a system/stake/vote instruction sent by an ordinary funded keypair, directly or via CPI
- Attacker controls: an account owned by a program the caller controls, with attacker-chosen data
- Exploit idea: Drive `common_close_account` so the lamports it removes and the lamports it adds differ, minting or destroying value outside the inflation schedule.
- Invariant to test: Sum of lamports before and after the operation is equal, except for the explicit fee/rent burn.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Property test around `common_close_account`: assert `sum_lamports_before == sum_lamports_after + burned` over randomized inputs.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged attacker can craft account writes that make AccountsDB return stale, wrong-slot, or wrong-owner account state to execution, letting a later transaction spend or overwrite value it does not own.
