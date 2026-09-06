### No vulnerability found for this question.

**Reasoning:** The recipient field in `TransactionPayload::Coinbase` is an intentional "pay-to-alt-recipient" feature introduced in Epoch 2.1, not an authentication bypass. `NakamotoChainState::make_scheduled_miner_reward` in `stackslib/src/chainstate/nakamoto/tenure.rs` and its epoch-2.x counterpart `StacksChainState::make_scheduled_miner_reward` in `stackslib/src/chainstate/stacks/db/blocks.rs` deliberately honor `recipient_opt` from the coinbase payload, falling back to the origin's address only when no recipient is specified [1](#0-0) . The coinbase transaction itself must be signed by the winning miner's own key (`coinbase_tx.get_origin()`), so `reward.address` (the winner, verified against the sortition) is unaffected — only `reward.recipient` (the payout destination) is redirectable, exactly as intended by design [2](#0-1) .

There is no requirement that the recipient principal "authorize" or "control the auth for" receiving funds — this mirrors ordinary STX transfers, where any principal can be a recipient without signing anything, since receiving funds requires no permission in this account model. `StacksBlock::validate_transaction_static_epoch` explicitly gates this feature by epoch (`recipient_opt.is_some()` requires `epoch_id >= StacksEpochId::Epoch21`) and it is exercised by first-party test helpers such as `make_coinbase_to_contract` and `make_nakamoto_coinbase`, confirming it is a supported, tested code path rather than a bypass [3](#0-2) [4](#0-3) .

Because the miner who wins the sortition is the one broadcasting and signing the coinbase, and the "reward.recipient == sortition-winner's authorized recipient" equality the question posits is not an invariant enforced or claimed anywhere in the code — the winner's *chosen* payout address (which can legitimately be a contract or a third party they control, e.g., a mining pool payout address) is by design the "authorized recipient." No unprivileged attacker can redirect *another* miner's coinbase reward; a miner can only redirect their own reward, which is not reward theft — it's equivalent to choosing a payout address, an intended and documented feature, not a vulnerability.

### Citations

**File:** stackslib/src/chainstate/nakamoto/tenure.rs (L297-311)
```rust
        let miner_auth = coinbase_tx.get_origin();
        let miner_addr = miner_auth.get_address(mainnet);

        let recipient = if epoch_id >= StacksEpochId::Epoch21 {
            // pay to tx-designated recipient, or if there is none, pay to the origin
            match coinbase_tx.try_as_coinbase() {
                Some((_, recipient_opt, _)) => recipient_opt
                    .cloned()
                    .unwrap_or(miner_addr.to_account_principal()),
                None => miner_addr.to_account_principal(),
            }
        } else {
            // pre-2.1, always pay to the origin
            miner_addr.to_account_principal()
        };
```

**File:** stackslib/src/chainstate/stacks/db/blocks.rs (L3563-3577)
```rust
        let miner_auth = coinbase_tx.get_origin();
        let miner_addr = miner_auth.get_address(mainnet);

        let recipient = if epoch_id >= StacksEpochId::Epoch21 {
            // pay to tx-designated recipient, or if there is none, pay to the origin
            match coinbase_tx.try_as_coinbase() {
                Some((_, recipient_opt, _)) => recipient_opt
                    .cloned()
                    .unwrap_or(miner_addr.to_account_principal()),
                None => miner_addr.to_account_principal(),
            }
        } else {
            // pre-2.1, always pay to the origin
            miner_addr.to_account_principal()
        };
```

**File:** stackslib/src/chainstate/stacks/block.rs (L583-598)
```rust
        if let TransactionPayload::Coinbase(_, ref recipient_opt, ref proof_opt) = &tx.payload {
            if proof_opt.is_some() && epoch_id < StacksEpochId::Epoch30 {
                // not supported
                error!("Coinbase with VRF proof not supported before Stacks 3.0"; "txid" => %tx.txid());
                return false;
            }
            if proof_opt.is_none() && epoch_id >= StacksEpochId::Epoch30 {
                // not supported
                error!("Coinbase with VRF proof is required in Stacks 3.0 and later"; "txid" => %tx.txid());
                return false;
            }
            if recipient_opt.is_some() && epoch_id < StacksEpochId::Epoch21 {
                // not supported
                error!("Coinbase pay-to-alt-recipient not supported before Stacks 2.1"; "txid" => %tx.txid());
                return false;
            }
```

**File:** stackslib/src/chainstate/stacks/tests/mod.rs (L971-982)
```rust
pub fn make_coinbase_to_contract(
    miner: &mut TestMiner,
    burnchain_height: usize,
    contract: QualifiedContractIdentifier,
) -> StacksTransaction {
    make_coinbase_with_nonce(
        miner,
        burnchain_height,
        miner.get_nonce(),
        Some(PrincipalData::Contract(contract)),
    )
}
```
