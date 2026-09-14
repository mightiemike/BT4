## Analysis

I found a valid analog. It maps directly onto the reported bug class: **a party that subsidizes another account's gas costs can be drained by an attacker who controls the execution logic on the receiving end**, because the subsidized amount is computed from a metered/burnt-gas quantity that the attacker's own contract code can inflate, and part of that metered gas is paid out to the attacker as a reward.

### Title
Meta-transaction relayer subsidy is drainable via the `burnt_gas_reward` payout to attacker-controlled contracts - (File: `runtime/runtime/src/lib.rs`)

### Summary
NEAR's meta-transaction feature (NEP-366, `Action::Delegate`) lets a relayer submit and fully pay (gas + attached deposits) for actions authored by another account, exactly the "FeePayer subsidizing" pattern in the external report [1](#0-0) . When the relayer's sponsored transaction results in a `FunctionCall` receipt, the runtime automatically credits `burnt_gas_reward` (30% by default in the fee config, i.e. `3/10`) of `gas_burnt_for_function_call` back to the *receiving* contract account, funded out of the tokens the relayer already paid for gas [2](#0-1) . Because the receiving contract's code fully controls how much gas it burns per call (up to the attached/prepaid gas limit), an attacker who deploys the target contract and gets a relayer to sponsor calls into it can maximize `gas_burnt_for_function_call` and pocket 30% of the relayer's gas spend on every call, i.e. the same "sponsor pays gasUsed(+overhead), attacker's contract logic profits from the subsidized portion" pattern described in the external report for Brahma/Gelato.

### Finding Description
- The relayer/sponsor pays for gas and deposits of a meta-transaction's inner actions, exactly analogous to `FeePayer`/Gelato subsidizing a user's automation transaction: "The relayer wraps it in a transaction, of which the relayer is the signer and therefore pays the gas costs" [3](#0-2) .
- During `apply_action_receipt`, after gas burning and refund accounting, the runtime computes `receiver_gas_reward = gas_burnt_for_function_call * burnt_gas_reward.numer / burnt_gas_reward.denom` and credits it directly to the receiving contract's account, subtracting it from `tx_burnt_amount` [4](#0-3) .
- `gas_burnt_for_function_call` is the gas actually consumed executing the receiving contract's WASM code — fully under the attacker's control if they own the contract; they can loop cheap WASM operations to burn as much of the attached/prepaid gas as desired, up to `max_total_prepaid_gas` (300 Tgas) per call [5](#0-4) .
- This is explicitly flagged in nearcore's own documentation as a known risk category: *"when implementing a free meta-transaction relayer one has to be careful not to be susceptible to faucet-draining attacks where an attacker extracts funds from the relayer by making calls to a contract they own"* [6](#0-5) .
- This mirrors the root cause of the external report precisely: the sponsor's payment (gas cost paid by relayer) is used as the basis for a payout (`burnt_gas_reward`) that flows to an address the attacker controls, and the attacker's own code (contract logic burning gas) directly inflates the payout, just like the attacker's "gas-minting adapter logic" inflated `gasUsed` in the original Brahma/Gelato report.

### Impact Explanation
Any relayer that sponsors meta-transactions for third-party contracts (a common intended use case for NEP-366) can have up to `burnt_gas_reward` (30% under the current default parameter, present at protocol version 86 per the runtime config snapshots) of every gas-burnt unit it pays for redirected to an attacker-controlled contract account, with no cap tied to actual useful work performed. Repeated calls let the attacker systematically drain the relayer's NEAR balance. This is concrete unauthorized value movement from the sponsoring party to the attacker, reachable purely by an unprivileged transaction signer (the attacker) who convinces/gets a relayer to sponsor calls to their contract.

### Likelihood Explanation
This requires a relayer to be willing to sponsor calls to an account the attacker controls — a scenario nearcore's own docs single out as a real risk for "free"/"general-purpose" relayers, i.e. it's a foreseeable operational configuration, not a contrived edge case. Mitigation is left entirely to relayer implementations (e.g., contract allow-listing), and there is no protocol-level cap that ties `burnt_gas_reward` payout to work actually of value to the relayer/sponsor.

### Recommendation
As with the external report's mitigation (only subsidize the portion of cost not controllable by the untrusted party), consider decoupling the `burnt_gas_reward` payout from attacker-controlled `gas_burnt_for_function_call` when the payer is a relayer distinct from the receiver, or provide protocol/tooling support (e.g., a per-relayer allowance/cap, or an explicit "no reward" mode for delegate-action receipts) so sponsors are not exposed to unbounded reward-draining via gas-burning loops in contracts they don't control. At minimum, this risk should be prominently surfaced to relayer implementers (it is already partially documented) since it is not something protocol-level defenses currently prevent.

### Proof of Concept
1. Attacker deploys a contract `Evil` whose method `drain()` performs a WASM loop that burns close to the full attached/prepaid gas doing cheap computation (no state writes needed).
2. Attacker (as `sender_id`) signs a `DelegateAction` targeting `Evil::drain()` and gets a relayer to submit it, paying gas per the meta-transaction flow [7](#0-6) .
3. On execution, `apply_action_receipt` computes `gas_burnt_for_function_call` ≈ full attached gas, and credits `Evil`'s account with `burnt_gas_reward` (30%) of the gas-price value of that burnt gas [2](#0-1) , exactly as validated by existing test helpers computing `gas_burnt_to_reward` for meta-tx scenarios [8](#0-7) .
4. Attacker repeats step 2 at will (each is a fresh, valid transaction/receipt), draining 30% of every gas unit the relayer purchases for these calls, with no upper bound besides the relayer's willingness to keep paying.

### Citations

**File:** docs/architecture/how/meta-tx.md (L40-45)
```markdown
With meta transactions, Alice can create a `DelegateAction`, which is very
similar to a transaction. It also contains a list of actions to execute and a
single receiver for those actions. She signs the `DelegateAction` and forwards
it (off-chain) to a relayer. The relayer wraps it in a transaction, of which the
relayer is the signer and therefore pays the gas costs. If the inner actions
have an attached token balance, this is also paid for by the relayer.
```

**File:** docs/architecture/how/meta-tx.md (L47-52)
```markdown
On chain, the `SignedDelegateAction` inside the transaction is converted to an
action receipt with the same `SignedDelegateAction` on the relayer's shard. The
receipt is forwarded to the account from `Alice`, which will unpacked the
`SignedDelegateAction` and verify that it is signed by Alice with a valid Nonce
etc. If all checks are successful, a new action receipt with the inner actions
as body is sent to `FT`. There, the `ft_transfer` call finally executes.
```

**File:** runtime/runtime/src/lib.rs (L1107-1138)
```rust
        // Adding burnt gas reward for function calls if the account exists.
        let receiver_gas_reward = result
            .gas_burnt_for_function_call
            .checked_mul(*apply_state.config.fees.burnt_gas_reward.numer() as u64)
            .unwrap()
            .checked_div(*apply_state.config.fees.burnt_gas_reward.denom() as u64)
            .unwrap();
        // The balance that the current account should receive as a reward for function call
        // execution.
        let receiver_reward =
            if ProtocolFeature::AccountCostIncrease.enabled(apply_state.current_protocol_version) {
                safe_gas_to_balance(gas_burn_price, receiver_gas_reward)?
            } else {
                // Post NEP-536/pre AccountCostIncrease: We are not refunding gas price differences, we just use the receipt
                // gas price and call it the correct price.
                // No deficits to try and recover. Use receipt gas price for reward calculation
                safe_gas_to_balance(gas_purchase_price, receiver_gas_reward)?
            };

        if receiver_reward > Balance::ZERO {
            let mut account = get_account(state_update, account_id)?;
            if let Some(ref mut account) = account {
                // Validators receive the remaining execution reward that was not given to the
                // account holder. If the account doesn't exist by the end of the execution, the
                // validators receive the full reward.
                tx_burnt_amount = tx_burnt_amount.checked_sub(receiver_reward).unwrap();
                account.set_amount(safe_add_balance(account.amount(), receiver_reward)?);
                set_account(state_update, account_id.clone(), account);
                state_update.commit(StateChangeCause::ActionReceiptGasReward {
                    receipt_hash: receipt.get_hash(),
                });
            }
```

**File:** docs/architecture/how/gas.md (L123-133)
```markdown
The gas attached to a function can be at most `max_total_prepaid_gas`, which is
300 Tgas since the mainnet launch. Note that this limit is per
`SignedTransaction`, not per function call. In other words, batched function
calls share this limit.

There is also a limit to how much single call can burn, `max_gas_burnt`, which
used to be 200 Tgas but has been increased to 300 Tgas in protocol version 52.
(Note: When attaching gas to an outgoing function call, this is not counted as
gas burnt.) However, given a call can never burn more than was attached anyway,
this second limit is obsolete with the current configuration where the two limits
are equal.
```

**File:** docs/architecture/how/gas.md (L153-157)
```markdown
The idea is that it gives contract developers a source of income and hence an
incentive to create useful contracts that are commonly used. But there are also
downsides, such as when implementing a free meta-transaction relayer one has to
be careful not to be susceptible to faucet-draining attacks where an attacker
extracts funds from the relayer by making calls to a contract they own.
```

**File:** integration-tests/src/tests/features/delegate_action.rs (L262-266)
```rust
    // calculate contract rewards as reward("gas burnt in fn call receipt" - "static exec costs")
    let gas_burnt_for_function_call =
        tx_result.receipts_outcome[1].outcome.gas_burnt.checked_sub(static_exec_gas).unwrap();
    let dyn_cost = fee_helper.gas_to_balance(gas_burnt_for_function_call);
    let contract_reward = fee_helper.gas_burnt_to_reward(gas_burnt_for_function_call);
```
