No vulnerability found for this question.

**Analysis:**

A self-loop `Branch(i)` at offset `i` (target == cur_instr) does satisfy `is_back_edge` (`target_instr <= cur_instr`), so `instruction_labels` marks offset `i` as `Label::Loop { last_continue: i }` [1](#0-0) . This is not a misclassification bypassing break/continue validation — the instruction is still pushed onto `loop_stack` and both `check_continues` and `check_breaks` run against it normally [2](#0-1) . `check_continues` confirms the back-jump target equals the current loop head (`i == i`), which trivially passes [3](#0-2) ; there is no forward branch here so `check_breaks` doesn't apply. This is correct handling of a degenerate one-instruction infinite loop (`loop {}`), not an exemption from validation.

Even granting the loop is accepted (which it should be — `while(true) {}`-style infinite loops are legal Move programs), this does not create any committed-state divergence:

- Non-terminating loops are a normal, expected part of Move's execution model; termination is enforced by the deterministic gas metering system, not by the bytecode verifier's control-flow checks. The control-flow verifier's job (per its own comments) is only to ensure structured jumps — no loop-body splitting, valid break/continue targets, and bounded nesting depth [4](#0-3) .
- Gas costs and gas schedules are deterministic, on-chain configuration values applied identically by every validator executing the same bytecode with the same inputs. Every honest validator re-executing this transaction will spin through the identical `Branch` instruction the identical number of times and hit `OUT_OF_GAS` (or otherwise abort) identically — there is no path by which one honest validator would derive a different write set than another for the same transaction.
- The abstract-interpretation back-edge logic used for type/stack safety analysis (`AbstractInterpreter::analyze_function` in `absint.rs`) relies on a separate `ControlFlowGraph::is_back_edge` computed from the CFG's block graph, not on `control_flow_v5::is_back_edge` [5](#0-4) , so there's no cross-contamination between this loop-structure check and the type-safety fixed-point analysis that could produce unsound bytecode acceptance.

Since execution remains fully deterministic across all validators regardless of whether the loop terminates or exhausts gas, there is no committed-state corruption, no accumulator/proof divergence, and no hard-fork-only divergence introduced by this code path.

### Citations

**File:** third_party/move/move-bytecode-verifier/src/control_flow_v5.rs (L87-104)
```rust
fn instruction_labels(context: &ControlFlowVerifier) -> Vec<Label> {
    let mut labels: Vec<Label> = (0..context.code.len()).map(|_| Label::Code).collect();
    let mut loop_continue = |loop_idx: CodeOffset, last_continue: CodeOffset| {
        labels[loop_idx as usize] = Label::Loop { last_continue }
    };
    for (i, instr) in context.code() {
        match instr {
            // Back jump/"continue"
            Bytecode::Branch(prev) | Bytecode::BrTrue(prev) | Bytecode::BrFalse(prev)
                if is_back_edge(i, *prev) =>
            {
                loop_continue(*prev, i)
            },
            _ => (),
        }
    }
    labels
}
```

**File:** third_party/move/move-bytecode-verifier/src/control_flow_v5.rs (L106-127)
```rust
// Ensures the invariant:
//   - All forward jumps do not enter into the middle of a loop
//   - All "breaks" go to the "end" of the loop
//   - All back jumps are only to the current loop
//   - Nested loops do not exceed a given depth
fn check_jumps(
    verifier_config: &VerifierConfig,
    context: &ControlFlowVerifier,
    labels: Vec<Label>,
) -> PartialVMResult<()> {
    // All back jumps are only to the current loop
    check_continues(context, &labels)?;
    // All "breaks" go to the "end" of the loop
    check_breaks(context, &labels)?;

    let loop_depth = count_loop_depth(&labels);

    // All forward jumps do not enter into the middle of a loop
    check_no_loop_splits(context, &labels, &loop_depth)?;
    // Nested loops do not exceed a given depth
    check_loop_depth(verifier_config, context, &labels, &loop_depth)
}
```

**File:** third_party/move/move-bytecode-verifier/src/control_flow_v5.rs (L129-160)
```rust
fn check_code<
    F: FnMut(&Vec<(CodeOffset, CodeOffset)>, CodeOffset, &Bytecode) -> PartialVMResult<()>,
>(
    context: &ControlFlowVerifier,
    labels: &[Label],
    mut check: F,
) -> PartialVMResult<()> {
    let mut loop_stack: Vec<(CodeOffset, CodeOffset)> = vec![];
    for (cur_instr, instr, label) in context.labeled_code(labels) {
        // Add loop to stack
        if let Label::Loop { last_continue } = label {
            loop_stack.push((cur_instr, *last_continue));
        }

        check(&loop_stack, cur_instr, instr)?;

        // Pop if last continue
        match instr {
            // Back jump/"continue"
            Bytecode::Branch(target) | Bytecode::BrTrue(target) | Bytecode::BrFalse(target)
                if is_back_edge(cur_instr, *target) =>
            {
                let (_cur_loop_head, last_continue) = safe_unwrap!(loop_stack.last());
                if cur_instr == *last_continue {
                    loop_stack.pop();
                }
            },
            _ => (),
        }
    }
    Ok(())
}
```

**File:** third_party/move/move-bytecode-verifier/src/control_flow_v5.rs (L167-185)
```rust
fn check_continues(context: &ControlFlowVerifier, labels: &[Label]) -> PartialVMResult<()> {
    check_code(context, labels, |loop_stack, cur_instr, instr| {
        match instr {
            // Back jump/"continue"
            Bytecode::Branch(target) | Bytecode::BrTrue(target) | Bytecode::BrFalse(target)
                if is_back_edge(cur_instr, *target) =>
            {
                let (cur_loop_head, _last_continue) = safe_unwrap!(loop_stack.last());
                if target != cur_loop_head {
                    // Invalid back jump. Cannot back jump outside of the current loop
                    Err(context.error(StatusCode::INVALID_LOOP_CONTINUE, cur_instr))
                } else {
                    Ok(())
                }
            },
            _ => Ok(()),
        }
    })
}
```

**File:** third_party/move/move-bytecode-verifier/src/absint.rs (L108-118)
```rust
                            },
                            JoinResult::Changed => {
                                // If the cur->successor is a back edge, jump back to the beginning
                                // of the loop, instead of the normal next block
                                if function_view
                                    .cfg()
                                    .is_back_edge(block_id, *successor_block_id)
                                {
                                    next_block_candidates.push(*successor_block_id);
                                }
                            },
```
