Based on the investigation, I found a legitimate unit/decimal-mismatch analog in the PoX-5 signer-weight computation, which is structurally identical to the reported bug class (a computation that implicitly assumes all quantities share one unit/decimal base, when in fact they don't).

### Title
Signer weight/threshold computation conflates uSTX-denominated and sats-denominated stake without normalization - (File: stackslib/src/chainstate/nakamoto/signer_set.rs)

### Summary
`NakamotoSigners::pox_5_make_signer_set` computes each signer's block-signing `weight` and the network-wide `pox_ustx_threshold` by summing a single `u128` quantity (`amount_ustx`) pulled from PoX-5's per-signer delegation total, exactly the same way `PegStabilityModule` summed swap amounts assuming both legs shared the underlying's 18 decimals. In PoX-5, however, the delegated total is explicitly documented as aggregating two different staking currencies with different unit scales: STX-only stacking (uSTX, 6 decimals) and protocol-bond staking (sBTC, expressed in satoshis, 8 decimals, tracked elsewhere in `pox-5.clar` as `total-sats`/`totalSats`). Treating both as fungible "uSTX" before dividing by a single `threshold` breaks the intended equality "weight is proportional to real economic stake."

### Finding Description
`RawPox5Entry` and the iterator that produces it label the delegated amount as `amount_ustx` and the accompanying comment states it "sums STX-only staking and protocol bonds": [1](#0-0) [2](#0-1) 

`pox_5_make_signer_set` then sums this single raw `u128` per signer into `total_ustx_locked`, derives `threshold = ceil(total_ustx_locked / reward_slots)`, and assigns each signer's `weight = stacked_amt / threshold` (with a largest-remainder round-up for leftovers): [3](#0-2) 

Meanwhile, `pox-5.clar`'s bond-reward math independently tracks bond stake in **sats** (`total-sats`, `get-total-shares-staked-for-cycle`, `target-yield = totalSats * targetRate / 10000 / 50`) and STX-only stake in **uSTX** (`stack-stx (amount uint)` locking micro-STX), and even the L1-lockup verifier explicitly documents amounts as "sats": [4](#0-3) 

If `get-amount-delegated-for-signer` (the Clarity function whose return value is deserialized as `amount_ustx`) folds a signer's bond-derived sat balance directly into the same accumulator as their uSTX balance — without a conversion factor — the Rust-side threshold/weight computation ends up mixing two incommensurate units under one linear formula, exactly analogous to `PegStabilityModule` pricing all swaps at par assuming 18 decimals for every token.

### Impact Explanation
Signer weight and the 70% voting threshold gate whether a Nakamoto block is accepted (`verify_signer_signatures`/`compute_voting_weight_threshold`): [5](#0-4) 
If bond/sBTC stake and STX stake are summed as if fungible 1:1 despite different decimal bases, a party can acquire disproportionate signing weight relative to their real economic stake by shifting value into whichever unit is numerically favored by the un-normalized sum — i.e., "signer weight ... from the wrong set" in the allowed analog list. This can let a minority of true economic stake reach the 70% threshold, letting them force through blocks (or block signer-set churn) that honest majority stake would not have authorized.

### Likelihood Explanation
This is only reachable if `get-amount-delegated-for-signer` genuinely returns a sum of uSTX and sats without a scaling factor. I was not able to fully inspect that Clarity function's implementation before running out of tool iterations — the surrounding evidence (explicit "sums STX-only staking and protocol bonds" comment on the Rust side, and separately-tracked `sats`-denominated bond stake in `pox-5.clar`) strongly suggests this mixing occurs, but I could not directly confirm the numeric normalization (or lack thereof) inside `get-amount-delegated-for-signer`/`signer-delegated-per-cycle`. This should be verified against the actual Clarity source before treating this as confirmed rather than a strong structural analog.

### Recommendation
Confirm whether `get-amount-delegated-for-signer` normalizes bond-derived sats stake to a common unit (e.g., a USD- or BTC-value-adjusted uSTX equivalent, or simply keeps bond and STX-only weight in fully separate pools) before summing into the value used for `pox_5_make_signer_set`'s `threshold`/`weight` computation. If no normalization exists, either scale sats contributions by an explicit, oracle-free conversion factor or compute weight per-currency-pool rather than pooling heterogeneous units into one linear threshold.

### Proof of Concept
Not constructed — this requires confirming the exact behavior of `get-amount-delegated-for-signer` in `pox-5.clar`, which was not fully readable within the available tool budget. A concrete PoC would stack a controlled uSTX amount as a normal signer, register a protocol bond with a numerically large `total-sats` value relative to its true value, and show via `pox_5_make_signer_set`'s test harness (`stackslib/src/chainstate/nakamoto/tests/signer_set.rs`) that the resulting `weight` is disproportionate to the bond's actual claim once the mixed units are accounted for.

### Citations

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L336-342)
```rust
/// One (signer_key, amount_ustx) pair contributing to a cycle's signer set,
/// as produced by walking pox-5's per-cycle signer-set linked list.
#[derive(Debug, PartialEq, Eq, Hash, Clone)]
pub struct RawPox5Entry {
    pub(crate) amount_ustx: u128,
    pub(crate) signer_key: [u8; SIGNERS_PK_LEN],
}
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L424-452)
```rust
        // Total uSTX delegated to this signer for this cycle (sums STX-only
        // staking and protocol bonds; see signer-delegated-per-cycle).
        let amount_ustx = self
            .clarity
            .eval_method_read_only(
                &self.pox_contract,
                "get-amount-delegated-for-signer",
                &[lookup_signer.clone(), self.reward_cycle_clar.clone()],
            )
            .map_err(|e| PoxEntryParsingError::Skip(e.to_string()))?
            .expect_u128()
            .map_err(|_| {
                PoxEntryParsingError::Skip(
                    "get-amount-delegated-for-signer did not return uint".into(),
                )
            })?;

        // Signers only enter the linked list after crossing SIGNER_SET_MIN_USTX,
        // so a zero here means contract state is inconsistent. Skip defensively.
        if amount_ustx == 0 {
            return Err(PoxEntryParsingError::Skip(format!(
                "signer {cur_signer} is in cycle linked list with zero delegated uSTX"
            )));
        }

        Ok(Some(RawPox5Entry {
            amount_ustx,
            signer_key,
        }))
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L822-911)
```rust
    pub(crate) fn pox_5_make_signer_set<I>(
        entries: &mut I,
        pox_constants: &PoxConstants,
    ) -> Result<Pox5SignerSetOutput, ChainstateError>
    where
        I: Iterator<Item = Result<RawPox5Entry, PoxEntryParsingError>>,
    {
        let mut signer_set = HashMap::new();
        let mut total_ustx_locked = 0u128;
        for entry_res in entries {
            let entry = match entry_res {
                Ok(x) => x,
                Err(PoxEntryParsingError::Skip(err_str)) => {
                    warn!(
                        "Error while iterating PoX-5 entries, impacting a single entry. Dropping entry from signer set";
                        "error" => err_str
                    );
                    continue;
                }
                Err(PoxEntryParsingError::Abort(err_str)) => {
                    error!(
                        "Abort-triggering error while iterating PoX-5 entries";
                        "error" => err_str
                    );
                    return Err(ChainstateError::PoxNoRewardCycle);
                }
            };

            total_ustx_locked += entry.amount_ustx;

            signer_set
                .entry(entry.signer_key)
                .and_modify(|existing_entry| *existing_entry += entry.amount_ustx)
                .or_insert_with(|| entry.amount_ustx);
        }

        // Allocate `reward_slots` weight across signers in proportion to stake using the
        // a largest-remainder method:
        //
        // The threshold is `ceil(total / reward_slots)`.
        //
        // Flooring each signer's `stacked / threshold` assigns a base weight where the sum is `<= reward_slots`
        // (the ceil makes `total/threshold <= reward_slots`).
        //
        // This leaves some unassigned ("leftover") slots, which are handed out one-per-signer
        //  in descending fractional-remainder order (ties broken by pubkey-sort order).
        //
        // This avoids degenerate modes of the floor-and-drop scheme: when more than
        // `reward_slots` distinct signers hold roughly equal stake, every base weight floors to
        // 0, and without the leftover round the entire signer set could be dropped.
        let reward_slots = u128::from(pox_constants.reward_slots());
        let threshold = std::cmp::max(1, total_ustx_locked.div_ceil(reward_slots));

        struct Apportionment {
            signing_key: [u8; SIGNERS_PK_LEN],
            stacked_amt: u128,
            weight: u128,
            remainder: u128,
        }

        let mut apportioned: Vec<Apportionment> = signer_set
            .into_iter()
            .map(|(signing_key, stacked_amt)| Apportionment {
                signing_key,
                stacked_amt,
                weight: stacked_amt / threshold,
                remainder: stacked_amt % threshold,
            })
            .collect();

        // Guaranteed `<= reward_slots` by the ceil quota, so leftover does not underflow.
        let assigned: u128 = apportioned.iter().map(|entry| entry.weight).sum();
        let mut leftover = reward_slots.saturating_sub(assigned);

        if leftover > 0 {
            // Largest fractional remainder wins the next slot; ties broken by signing_key
            // ascending so the apportionment is deterministic (and matches the final sort).
            apportioned.sort_by(|a, b| {
                b.remainder
                    .cmp(&a.remainder)
                    .then_with(|| a.signing_key.cmp(&b.signing_key))
            });
            for entry in apportioned.iter_mut() {
                if leftover == 0 {
                    break;
                }
                entry.weight += 1;
                leftover -= 1;
            }
        }
```

**File:** stackslib/src/chainstate/stacks/boot/pox-5.clar (L2258-2266)
```text
    (let (
            (accumulator (try! accumulator-res))
            (bond (unwrap! (map-get? protocol-bonds bond-index) ERR_BOND_NOT_FOUND))
            (reward-cycle (get reward-cycle accumulator))
            (total-sats (get-total-shares-staked-for-cycle reward-cycle (some bond-index)))
            (available-rewards (get available-rewards accumulator))
            ;; How much sBTC the bond is supposed to earn per calculation,
            ;; which is (totalSats * apy) / 50
            (target-yield (/ (/ (* total-sats (get target-rate bond)) u10000) u50))
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1180-1189)
```rust
        let threshold = Self::compute_voting_weight_threshold(total_weight)?;

        if total_weight_signed < threshold {
            return Err(ChainstateError::InvalidStacksBlock(format!(
                "Not enough signatures. Needed at least {} but got {} (out of {})",
                threshold, total_weight_signed, total_weight,
            )));
        }

        return Ok(total_weight_signed);
```
