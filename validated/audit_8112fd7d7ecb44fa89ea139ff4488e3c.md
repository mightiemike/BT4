[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/aptos-crypto/src/blstrs/scalar_secret_key.rs (L6-30)
```rust
use crate::{
    arkworks::shamir::{Reconstructable, ShamirShare},
    blstrs::{lagrange::lagrange_coefficients, threshold_config::ThresholdConfigBlstrs},
    traits::{TSecretSharingConfig as _, ThresholdConfig as _},
};
use blstrs::Scalar;
use ff::Field;
use more_asserts::{assert_ge, assert_le};

impl Reconstructable<ThresholdConfigBlstrs> for Scalar {
    type ShareValue = Scalar;

    fn reconstruct(
        sc: &ThresholdConfigBlstrs,
        shares: &[ShamirShare<Self::ShareValue>],
    ) -> anyhow::Result<Self> {
        assert_ge!(shares.len(), sc.get_threshold());
        assert_le!(shares.len(), sc.get_total_num_players());

        let ids = shares.iter().map(|(p, _)| p.id).collect::<Vec<usize>>();
        let lagr = lagrange_coefficients(
            sc.get_batch_evaluation_domain(),
            ids.as_slice(),
            &Scalar::ZERO,
        );
```

**File:** crates/aptos-dkg/src/pvss/dealt_secret_key.rs (L12-20)
```rust
        use aptos_crypto::blstrs::{$GT_PROJ_NUM_BYTES, $gt_proj_from_bytes};
        use crate::{
            algebra::lagrange::lagrange_coefficients,
            pvss::{
                dealt_secret_key_share::$gt::DealtSecretKeyShare,
                threshold_config::ThresholdConfigBlstrs,
            },
            utils::{$gt_multi_exp},
        };
```

**File:** crates/aptos-crypto/src/blstrs/lagrange.rs (L325-363)
```rust
    #[test]
    fn test_lagrange() {
        let mut rng = thread_rng();

        for n in 1..=FFT_THRESH * 2 {
            for t in 1..=n {
                // println!("t = {t}, n = {n}");
                let deg = t - 1; // the degree of the polynomial

                // pick a random $f(X)$
                let f = random_scalars(deg + 1, &mut rng);

                // give shares to all the $n$ players: i.e., evals[i] = f(\omega^i)
                let batch_dom = BatchEvaluationDomain::new(n);
                let mut evals = f.clone();
                fft_assign(&mut evals, &batch_dom.get_subdomain(n));

                // try to reconstruct $f(0)$ from a random subset of t shares
                let mut players: Vec<usize> = (0..n)
                    .choose_multiple(&mut rng, t)
                    .into_iter()
                    .collect::<Vec<usize>>();

                players.sort();

                let lagr = lagrange_coefficients(&batch_dom, players.as_slice(), &Scalar::ZERO);
                // println!("lagr: {:?}", lagr);

                let mut s = Scalar::ZERO;
                for i in 0..t {
                    s += lagr[i].mul(evals[players[i]]);
                }

                // println!("s   : {s}");
                // println!("f[0]: {}", f[0]);

                assert_eq!(s, f[0]);
            }
        }
```
