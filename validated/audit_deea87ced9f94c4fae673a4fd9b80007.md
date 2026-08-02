No vulnerability found for this question.

**Analysis:**

The `starting_index` field used by `get_player_starting_index()` in `WeightedConfig::new` is computed deterministically as a strict prefix sum over the `weights` vector — `starting_index[0] = 0` and `starting_index[i] = starting_index[i-1] + weights[i-1]` for consecutive players — which by construction tiles `[0, W)` with no gaps or overlaps. [1](#0-0) 

There is no alternate constructor for `WeightedConfig`/`WeightedConfigBlstrs` that would let an attacker supply arbitrary, non-contiguous `starting_index` values; the field is private and only ever set via this cumulative sum in `new()`. [2](#0-1) 

Additionally, `sc` (the `WeightedConfigBlstrs`) is not part of the unprivileged, attacker-supplied `Transcript` at all — it is passed as a separate parameter to `Transcript::verify` in `crates/aptos-dkg/src/pvss/das/weighted_protocol.rs`, and reflects the DKG session's validator weight configuration (derived from on-chain stake/validator set), not something an attacker can craft alongside the transcript. [3](#0-2) 

Since `get_player_weight` and `get_player_starting_index` both read from the same immutable, internally-consistent `weights`/`starting_index` vectors that are only constructed together via the invariant-preserving `new()`, an attacker cannot make player ranges overlap or leave gaps in `[0, W)` while keeping `get_player_weight` succeeding for all players — the premise of the question (a constructible `sc` with overlapping starting indices) is not realizable given the current implementation. The attacker only controls the `Transcript` contents (`V`, `V_hat`, `R`, `R_hat`, `C`, `soks`), not the `sc` structure, so this does not provide a path to corrupt the per-player share-to-index binding via unprivileged transcript submission.

### Citations

**File:** crates/aptos-crypto/src/weighted_config.rs (L38-53)
```rust
pub struct WeightedConfig<TC: ThresholdConfig> {
    /// A weighted config is a $w$-out-of-$W$ threshold config, where $w$ is the minimum weight
    /// needed to reconstruct the secret and $W$ is the total weight.
    tc: TC,
    /// The total number of players in the protocol.
    num_players: usize,
    /// Each player's weight
    weights: Vec<usize>,
    /// Player's starting index `a` in a vector of all `W` shares, such that this player owns shares
    /// `W[a, a + weight[player])`. Useful during weighted secret reconstruction.
    starting_index: Vec<usize>,
    /// The maximum weight of any player.
    max_weight: usize,
    /// The minimum weight of any player.
    min_weight: usize,
}
```

**File:** crates/aptos-crypto/src/weighted_config.rs (L82-103)
```rust
        // e.g., Suppose the weights for players 0, 1 and 2 are [2, 4, 3]
        // Then, our PVSS transcript implementation will store a vector of 2 + 4 + 3 = 9 shares,
        // such that:
        //  - Player 0 will own the shares at indices [0..2), i.e.,starting index 0
        //  - Player 1 will own the shares at indices [2..2 + 4) = [2..6), i.e.,starting index 2
        //  - Player 2 will own the shares at indices [6, 6 + 3) = [6..9), i.e., starting index 6
        let mut starting_index = Vec::with_capacity(weights.len());
        starting_index.push(0);

        for w in weights.iter().take(n - 1) {
            starting_index.push(starting_index.last().unwrap() + w);
        }

        let tc = TC::new(threshold_weight, W)?;
        Ok(WeightedConfig {
            tc,
            num_players: n,
            weights,
            starting_index,
            max_weight,
            min_weight,
        })
```

**File:** crates/aptos-dkg/src/pvss/das/weighted_protocol.rs (L288-360)
```rust
    #[allow(non_snake_case)]
    fn verify<A: Serialize + Clone>(
        &self,
        sc: &<Self as traits::TranscriptCore>::SecretSharingConfig,
        pp: &Self::PublicParameters,
        spks: &[Self::SigningPubKey],
        eks: &[Self::EncryptPubKey],
        auxs: &[A],
    ) -> anyhow::Result<()> {
        self.check_sizes(sc)?;
        let n = sc.get_total_num_players();
        if eks.len() != n {
            bail!("Expected {} encryption keys, but got {}", n, eks.len());
        }
        let W = sc.get_total_weight();

        // Deriving challenges by flipping coins: less complex to implement & less likely to get wrong. Creates bad RNG risks but we deem that acceptable.
        let mut rng = rand::thread_rng();
        let extra = random_scalars(2 + W * 3, &mut rng);

        let sok_vrfy_challenge = &extra[W * 3 + 1];
        let g_2 = pp.get_commitment_base();
        let g_1 = pp.get_encryption_public_params().pubkey_base();
        batch_verify_soks::<G1Projective, A>(
            self.soks.as_slice(),
            g_1,
            &self.V[W],
            spks,
            auxs,
            sok_vrfy_challenge,
        )?;

        let ldt = LowDegreeTest::random(
            &mut rng,
            sc.get_threshold_weight(),
            W + 1,
            true,
            sc.get_batch_evaluation_domain(),
        );
        ldt.low_degree_test_on_g1(&self.V)?;

        //
        // Correctness of encryptions check
        //

        let alphas_betas_and_gammas = &extra[0..W * 3 + 1];
        let (alphas_and_betas, gammas) = alphas_betas_and_gammas.split_at(2 * W + 1);
        let (alphas, betas) = alphas_and_betas.split_at(W + 1);
        assert_eq!(alphas.len(), W + 1);
        assert_eq!(betas.len(), W);
        assert_eq!(gammas.len(), W);

        let lc_VR_hat = G2Projective::multi_exp_iter(
            self.V_hat.iter().chain(self.R_hat.iter()),
            alphas_and_betas.iter(),
        );
        let lc_VRC = G1Projective::multi_exp_iter(
            self.V.iter().chain(self.R.iter()).chain(self.C.iter()),
            alphas_betas_and_gammas.iter(),
        );
        let lc_V_hat = G2Projective::multi_exp_iter(self.V_hat.iter().take(W), gammas.iter());
        let mut lc_R_hat = Vec::with_capacity(n);

        for i in 0..n {
            let p = sc.get_player(i);
            let weight = sc.get_player_weight(&p)?;
            let s_i = sc.get_player_starting_index(&p);

            lc_R_hat.push(g2_multi_exp(
                &self.R_hat[s_i..s_i + weight],
                &gammas[s_i..s_i + weight],
            ));
        }
```
