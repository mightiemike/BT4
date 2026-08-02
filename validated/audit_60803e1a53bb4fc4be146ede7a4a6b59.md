[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/staking_proxy.move (L92-128)
```text
    public entry fun test_set_operator(
        aptos_framework: &signer,
        owner: &signer,
        operator_1: &signer,
        operator_2: &signer,
        new_operator: &signer,
    ) {
        let owner_address = signer::address_of(owner);
        let operator_1_address = signer::address_of(operator_1);
        let operator_2_address = signer::address_of(operator_2);
        let new_operator_address = signer::address_of(new_operator);
        vesting::setup(
            aptos_framework, &vector[owner_address, operator_1_address, operator_2_address, new_operator_address]);
        staking_contract::setup_staking_contract(aptos_framework, owner, operator_1, INITIAL_BALANCE, 0);
        staking_contract::setup_staking_contract(aptos_framework, owner, operator_2, INITIAL_BALANCE, 0);

        let vesting_contract_1 = vesting::setup_vesting_contract(owner, &vector[@11], &vector[INITIAL_BALANCE], owner_address, 0);
        vesting::update_operator(owner, vesting_contract_1, operator_1_address, 0);
        let vesting_contract_2 = vesting::setup_vesting_contract(owner, &vector[@12], &vector[INITIAL_BALANCE], owner_address, 0);
        vesting::update_operator(owner, vesting_contract_2, operator_2_address, 0);

        let (_sk, pk, pop) = stake::generate_identity();
        stake::initialize_test_validator(&pk, &pop, owner, INITIAL_BALANCE, false, false);
        stake::set_operator(owner, operator_1_address);

        set_operator(owner, operator_1_address, new_operator_address);
        // Stake pool's operator has been switched from operator 1 to new operator.
        assert!(stake::get_operator(owner_address) == new_operator_address, 0);
        // Staking contract has been switched from operator 1 to new operator.
        // Staking contract with operator_2 should stay unchanged.
        assert!(staking_contract::staking_contract_exists(owner_address, new_operator_address), 1);
        assert!(!staking_contract::staking_contract_exists(owner_address, operator_1_address), 2);
        assert!(staking_contract::staking_contract_exists(owner_address, operator_2_address), 3);
        // Vesting contract 1 has been switched from operator 1 to new operator while vesting contract 2 stays unchanged
        assert!(vesting::operator(vesting_contract_1) == new_operator_address, 4);
        assert!(vesting::operator(vesting_contract_2) == operator_2_address, 5);
    }
```
