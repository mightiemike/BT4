[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/reconfiguration_with_dkg.move (L60-65)
```text
        dkg::start(
            cur_epoch,
            randomness_config::current(),
            stake::cur_validator_consensus_infos(),
            validator_consensus_infos_from_validator_set(&stake::next_validator_consensus_infos_v2())
        );
```

**File:** aptos-move/framework/aptos-framework/sources/reconfiguration_with_dkg.move (L76-83)
```text
        let dealer_validator_set = stake::cur_validator_consensus_infos();
        let target_validator_set = validator_consensus_infos_from_validator_set(&stake::next_validator_consensus_infos_v2());
        dkg::start(
            cur_epoch,
            randomness_config::current(),
            dealer_validator_set,
            target_validator_set,
        );
```
