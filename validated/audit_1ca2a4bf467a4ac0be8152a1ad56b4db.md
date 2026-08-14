### Title
Missing DB-level unique constraint allows race condition to create duplicate active OCR1 jobs for the same contract - ([File: core/services/job/orm.go])

### Summary
`orm.CreateJob` for `OffchainReporting` (OCR1) jobs relies purely on an application-level SELECT-then-INSERT check (`existingSpec` lookup at lines 230-242) to enforce uniqueness of `(contract_address, evm_chain_id)`. Migration `0146_unique_contract_address_per_chain.sql` explicitly removed the DB-level unique index that previously enforced this invariant, stating that re-adding a proper constraint "requires CREATE OPERATOR (admin) privilege," and no replacement constraint was added. This leaves a genuine TOCTOU race window between the SELECT and the subsequent `insertOCROracleSpec`/`InsertJob` calls.

### Finding Description
In `core/services/job/orm.go`, the `CreateJob` function handles the `OffchainReporting` case by running, inside a DB transaction, a `SELECT * FROM ocr_oracle_specs WHERE contract_address = $1 and (evm_chain_id = $2 or evm_chain_id IS NULL) LIMIT 1` query [1](#0-0) . If no row is found (`sql.ErrNoRows`), it proceeds to call `tx.insertOCROracleSpec` and commit the job. This is a classic check-then-act race: two concurrent `CreateJob` calls for the same `ContractAddress`/`EVMChainID` can both execute their `SELECT` before either has committed its `INSERT`, so both see `sql.ErrNoRows` and both proceed to insert.

Historically, the `ocr_oracle_specs` table had unique indexes (`unique_contract_addr`, `unique_contract_addr_per_chain`) enforcing this at the database level [2](#0-1) . However, migration `0146_unique_contract_address_per_chain.sql` removed these indexes on `Up` (only recreating them on `Down`), with the migration's own comment acknowledging that re-adding a proper DB constraint "requires CREATE OPERATOR (admin) privilege" — i.e., the constraint was dropped and never properly reinstated for the forward migration path [3](#0-2) . This confirms there is no DB-level uniqueness guard currently backing the ORM's application-level check for OCR1 jobs, unlike OCR2 which retains `offchainreporting2_oracle_specs_unique_contract_addr` (later scoped to `(contract_id, feed_id)`) [4](#0-3) .

Because the SELECT and INSERT are only wrapped in `o.transact` without evidence of `SERIALIZABLE` isolation or a row-level advisory lock, standard Postgres `READ COMMITTED`/`REPEATABLE READ` transactions do not prevent phantom-read races here — a unique constraint (which would abort one of the two transactions with a constraint violation) is exactly the mechanism intentionally removed.

### Impact Explanation
If exploited, two active `OffchainReporting` jobs with identical `ContractAddress`/`EVMChainID` could both run and independently transmit OCR reports to the same on-chain contract, each following its own local round/config-tracking state. This can cause conflicting or duplicated price report transmissions from the node for the same feed, which is a data-integrity/misreporting concern for price-oracle correctness — matching a "misreporting/data tampering" class of impact in Chainlink's bounty program, scoped to node-operator-caused inconsistency rather than protocol-level fund loss.

### Likelihood Explanation
Exploitation requires job-creation access (a node operator/API-key holder with permission to call `CreateJob`), which is a real precondition but consistent with the rules here (job-creation access, not admin/node-operator-privilege escalation beyond what's already granted for creating jobs). The race window is narrow — it depends on winning a timing race between two concurrent transactions' SELECT statements before either commits — so it is feasible but not trivially/always reproducible; it would typically require deliberate concurrent firing of near-simultaneous requests, and success is probabilistic rather than guaranteed on every attempt.

### Recommendation
Add a DB-level unique index/constraint on `ocr_oracle_specs (contract_address, evm_chain_id)` (with a partial index handling `evm_chain_id IS NULL`, mirroring the pattern used in migration `0073`), and have `insertOCROracleSpec`/`InsertJob` handle the resulting unique-violation error gracefully (translating it into the same "a job with contract address ... already exists" error). This closes the TOCTOU window because the database itself will reject the second concurrent insert, regardless of transaction isolation level used for the SELECT.

### Proof of Concept
Integration test plan:
1. Set up two goroutines that both call `jobORM.CreateJob(ctx, &jb)` with `OCROracleSpec` objects sharing the same `ContractAddress` and `EVMChainID`, synchronized via a barrier/channel so both execute their SELECT before either commits (e.g., by injecting a delay/hook after the SELECT and before the INSERT, or by using `pg_sleep` within a wrapping transaction to widen the window).
2. Run both `CreateJob` calls concurrently.
3. Assert (currently failing under the described root cause): `cltest.AssertCount(t, db, "ocr_oracle_specs", 1)` — expect only 1 row, but without a DB constraint both inserts can succeed, yielding 2 rows.
4. After adding the recommended unique index, assert that exactly one `CreateJob` call succeeds and the other returns an error (unique violation translated to the standard duplicate-contract error), and `ocr_oracle_specs`/`jobs` count remains 1.

### Citations

**File:** core/services/job/orm.go (L230-242)
```go
			newChainID := jb.OCROracleSpec.EVMChainID
			existingSpec := new(OCROracleSpec)
			err := tx.ds.GetContext(ctx, existingSpec, `SELECT * FROM ocr_oracle_specs WHERE contract_address = $1 and (evm_chain_id = $2 or evm_chain_id IS NULL) LIMIT 1;`,
				jb.OCROracleSpec.ContractAddress, newChainID,
			)

			if !errors.Is(err, sql.ErrNoRows) {
				if err != nil {
					return errors.Wrap(err, "failed to validate OffchainreportingOracleSpec on creation")
				}

				return errors.Errorf("a job with contract address %s already exists for chain ID %s", jb.OCROracleSpec.ContractAddress, newChainID)
			}
```

**File:** core/store/migrate/migrations/0073_ocr_duplicate_contract_addresses_allowed_across_chains.sql (L1-11)
```sql
-- +goose Up

ALTER TABLE offchainreporting_oracle_specs DROP CONSTRAINT unique_contract_addr;
CREATE UNIQUE INDEX unique_contract_addr_per_chain ON offchainreporting_oracle_specs (contract_address, evm_chain_id) WHERE evm_chain_id IS NOT NULL;
CREATE UNIQUE INDEX unique_contract_addr ON offchainreporting_oracle_specs (contract_address) WHERE evm_chain_id IS NULL;

-- +goose Down

DROP INDEX unique_contract_addr;
DROP INDEX unique_contract_addr_per_chain;
ALTER TABLE offchainreporting_oracle_specs ADD CONSTRAINT unique_contract_addr UNIQUE (contract_address);
```

**File:** core/store/migrate/migrations/0146_unique_contract_address_per_chain.sql (L1-10)
```sql
-- +goose Up
--- Remove all but most recently added contract_address for each chain. We will no longer allow duplicates, but enforcing that with a db constraint requires CREATE OPERATOR (admin) privilege
DELETE FROM ocr_oracle_specs WHERE id IN (SELECT id FROM (SELECT id, MAX(id) OVER(PARTITION BY evm_chain_id, contract_address ORDER BY id) AS max FROM ocr_oracle_specs) x WHERE id != max);

-- +goose Down
DROP INDEX IF EXISTS ocr_oracle_specs_unique_contract_addr;
DROP OPERATOR CLASS IF EXISTS wildcard_cmp USING BTREE CASCADE;
DROP FUNCTION IF EXISTS wildcard_cmp(INTEGER, INTEGER) CASCADE;
CREATE UNIQUE INDEX IF NOT EXISTS unique_contract_addr ON ocr_oracle_specs (contract_address) WHERE evm_chain_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS unique_contract_addr_per_chain ON ocr_oracle_specs (contract_address, evm_chain_id) WHERE evm_chain_id IS NOT NULL;
```

**File:** core/store/migrate/migrations/0163_mercury_jobs_multiple_per_contract.sql (L1-11)
```sql
-- +goose Up
ALTER TABLE ocr2_oracle_specs
    -- NOTE: The cleanest way to do this would be to allow NULL feed_id and use
    -- postgres 15's NULLS NOT DISTINCT feature on the index.
    -- However, it isn't reasonable to expect all users to upgrade to pg 15 at
    -- this time, so we require all specs to have a feed ID and use the zero
    -- value to indicate a missing feed ID.
    ADD COLUMN feed_id bytea CHECK (octet_length(feed_id) = 32) NOT NULL DEFAULT '\x0000000000000000000000000000000000000000000000000000000000000000', 
    DROP CONSTRAINT offchainreporting2_oracle_specs_unique_contract_addr;
;
CREATE UNIQUE INDEX offchainreporting2_oracle_specs_unique_contract_addr ON ocr2_oracle_specs (contract_id, feed_id);
```
