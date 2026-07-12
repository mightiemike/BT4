# Q2637: BurnNFT owner binding against IBC voucher backing

## Question
Can NFT owner enter through `msgServer.BurnNFT` by use signer, sender, recipient, and authz grantee fields that diverge but remain syntactically valid while controlling Sender, DenomId, Id, escrow ownership state, focusing on IBC-prefixed denom separation, under the precondition that a valuable denom or NFT exists under the NFT module, then burn an IBC voucher and trigger refund/mint path, causing `IBC voucher backing` to diverge so the invariant `authz and feegrant cannot burn a token outside the granted owner authority` fails and the attacker can destroy IBC backing while packet ack/timeout still expects custody, leading to High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent?

## Target
- File/function: x/nft/keeper/msg_server.go::msgServer.BurnNFT
- Entrypoint: Cosmos SDK MsgBurnNFT transaction
- Attacker controls: Sender, DenomId, Id, escrow ownership state
- Exploit idea: destroy IBC backing while packet ack/timeout still expects custody by testing the owner binding angle against `IBC voucher backing` during `burn an IBC voucher and trigger refund/mint path`, with specific focus on IBC-prefixed denom separation.
- Invariant to test: authz and feegrant cannot burn a token outside the granted owner authority; additionally, signer, denom owner, token owner, and recipient authority cannot diverge.
- Expected Immunefi impact: High signed transaction/authz/feegrant path moving or destroying NFTs contrary to signer intent; mapped to HackenProof Cronos in-scope direct fund/NFT loss, draining, unauthorized withdrawal, or unbacked asset creation.
- Fast validation: keeper test over BurnNFT plus nft-transfer refund and timeout state; assert pre/post balances, owner indexes, shares, escrow, checkpoints, and module accounts match the invariant.
