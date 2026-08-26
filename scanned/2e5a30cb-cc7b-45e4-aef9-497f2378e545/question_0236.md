# Q236: sigverify::count_valid_packets - zero-signature packet passes verification (sending one packet whose signature array)

## Question
Can an unprivileged attacker who sends raw transaction packets to a validator's TPU/QUIC ingress, sending one packet whose signature array length disagrees with its header signer count, drive `sigverify::count_valid_packets` to have a packet with num_signatures == 0 or a truncated signature array reported as verified, so that the invariant that a packet is only marked verified when every required signature was checked is broken and the outcome is Loss of Funds (theft of funds without the owner's signature)?

## Target
- File/function: `perf/src/sigverify.rs` -> `count_valid_packets`
- Entrypoint: sends raw transaction packets to a validator's TPU/QUIC ingress, sending one packet whose signature array length disagrees with its header signer count
- Attacker controls: the complete packet bytes, packet batch sizes and the declared signature/pubkey offsets
- Exploit idea: Have a packet with num_signatures == 0 or a truncated signature array reported as verified.
- Invariant to test: A packet is only marked verified when every required signature was checked.
- Expected Immunefi impact: Critical - Loss of Funds (theft of funds without the owner's signature)
- Fast validation: unit-test verify_packet on the crafted packet and assert it is marked discard and never reaches the bank
