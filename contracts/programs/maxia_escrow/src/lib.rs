use anchor_lang::prelude::*;
use anchor_spl::token::{self, Token, TokenAccount, Transfer, CloseAccount, Mint};

// #1: Program ID — replaced after `anchor build` on VPS
declare_id!("MAXiAEscrowProgram1111111111111111111111111");

// Mainnet USDC mint (Circle)
const USDC_MINT: Pubkey = pubkey!("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v");

// Minimum escrow: 1 USDC (prevents dust spam + ensures commission > 0)
const MIN_ESCROW_AMOUNT: u64 = 1_000_000;

// Dispute bond: 0.1 USDC (forfeited if dispute resolved against disputant)
const DISPUTE_BOND: u64 = 100_000;

/// MAXIA Escrow Program V4 — All audit findings resolved
///
/// V3 fixes (verified): C-01 C-02 C-03 H-01 H-02 H-03 H-04 H-05 M-01 M-02 M-03 M-04
/// V4 fixes:
///   N-01: emergency_close now closes vault too (refund to buyer if possible)
///   N-02: update_treasury has 48h timelock (pending_treasury pattern)
///   N-03: pause check on open_dispute; reclaim_timeout allowed when paused (by design)
///   N-04: cancel_pending_admin instruction added
///   N-05: commission_bps stored per-escrow at creation (immune to sandwich attack)
///   N-06: open_dispute requires DISPUTE_BOND from caller (anti-griefing)

#[program]
pub mod maxia_escrow {
    use super::*;

    /// Initialize config — called once after deploy
    pub fn initialize(
        ctx: Context<Initialize>,
        commission_bps: u16,
        treasury: Pubkey,
    ) -> Result<()> {
        require!(commission_bps <= 1000, EscrowError::InvalidCommission);
        require!(ctx.accounts.usdc_mint.key() == USDC_MINT, EscrowError::InvalidMint);

        let config = &mut ctx.accounts.config;
        config.admin = ctx.accounts.admin.key();
        config.treasury = treasury;
        config.usdc_mint = USDC_MINT;
        config.commission_bps = commission_bps;
        config.total_escrows = 0;
        config.total_volume = 0;
        config.total_commission = 0;
        config.paused = false;
        config.pending_admin = Pubkey::default();
        config.pending_admin_at = 0;
        config.pending_treasury = Pubkey::default();
        config.pending_treasury_at = 0;
        config.bump = ctx.bumps.config;

        msg!("MAXIA Escrow V4 initialized");
        Ok(())
    }

    /// Update commission rate (admin only)
    pub fn update_commission(ctx: Context<AdminOnly>, new_bps: u16) -> Result<()> {
        require!(new_bps <= 1000, EscrowError::InvalidCommission);
        let old = ctx.accounts.config.commission_bps;
        ctx.accounts.config.commission_bps = new_bps;
        emit!(AdminAction { action: "update_commission".to_string(), old_value: old as u64, new_value: new_bps as u64 });
        Ok(())
    }

    /// N-02: Step 1 — propose new treasury (48h timelock)
    pub fn propose_treasury(ctx: Context<AdminOnly>, new_treasury: Pubkey) -> Result<()> {
        let config = &mut ctx.accounts.config;
        config.pending_treasury = new_treasury;
        config.pending_treasury_at = Clock::get()?.unix_timestamp;
        emit!(AdminAction { action: "propose_treasury".to_string(), old_value: 0, new_value: 0 });
        Ok(())
    }

    /// N-02: Step 2 — accept treasury change (48h timelock)
    pub fn accept_treasury(ctx: Context<AdminOnly>) -> Result<()> {
        let config = &mut ctx.accounts.config;
        require!(config.pending_treasury != Pubkey::default(), EscrowError::NoPendingChange);
        let now = Clock::get()?.unix_timestamp;
        require!(now >= config.pending_treasury_at + 48 * 3600, EscrowError::TimelockNotExpired);
        config.treasury = config.pending_treasury;
        config.pending_treasury = Pubkey::default();
        config.pending_treasury_at = 0;
        emit!(AdminAction { action: "treasury_updated".to_string(), old_value: 0, new_value: 0 });
        Ok(())
    }

    /// Propose new admin (48h timelock)
    pub fn propose_admin(ctx: Context<AdminOnly>, new_admin: Pubkey) -> Result<()> {
        let config = &mut ctx.accounts.config;
        config.pending_admin = new_admin;
        config.pending_admin_at = Clock::get()?.unix_timestamp;
        emit!(AdminAction { action: "propose_admin".to_string(), old_value: 0, new_value: 0 });
        Ok(())
    }

    /// Accept admin transfer (48h timelock)
    pub fn accept_admin(ctx: Context<AcceptAdmin>) -> Result<()> {
        let config = &mut ctx.accounts.config;
        require!(config.pending_admin != Pubkey::default(), EscrowError::NoPendingChange);
        require!(ctx.accounts.new_admin.key() == config.pending_admin, EscrowError::Unauthorized);
        let now = Clock::get()?.unix_timestamp;
        require!(now >= config.pending_admin_at + 48 * 3600, EscrowError::TimelockNotExpired);
        config.admin = config.pending_admin;
        config.pending_admin = Pubkey::default();
        config.pending_admin_at = 0;
        emit!(AdminAction { action: "admin_transferred".to_string(), old_value: 0, new_value: 0 });
        Ok(())
    }

    /// N-04: Cancel pending admin transfer
    pub fn cancel_pending_admin(ctx: Context<AdminOnly>) -> Result<()> {
        let config = &mut ctx.accounts.config;
        config.pending_admin = Pubkey::default();
        config.pending_admin_at = 0;
        emit!(AdminAction { action: "cancel_pending_admin".to_string(), old_value: 0, new_value: 0 });
        Ok(())
    }

    /// Cancel pending treasury change
    pub fn cancel_pending_treasury(ctx: Context<AdminOnly>) -> Result<()> {
        let config = &mut ctx.accounts.config;
        config.pending_treasury = Pubkey::default();
        config.pending_treasury_at = 0;
        emit!(AdminAction { action: "cancel_pending_treasury".to_string(), old_value: 0, new_value: 0 });
        Ok(())
    }

    /// Pause/unpause
    pub fn set_paused(ctx: Context<AdminOnly>, paused: bool) -> Result<()> {
        ctx.accounts.config.paused = paused;
        emit!(AdminAction {
            action: if paused { "paused".to_string() } else { "unpaused".to_string() },
            old_value: 0, new_value: if paused { 1 } else { 0 },
        });
        Ok(())
    }

    /// Create escrow — locks USDC from buyer into PDA vault
    /// N-05: commission_bps stored per-escrow at creation time
    pub fn create_escrow(
        ctx: Context<CreateEscrow>,
        amount: u64,
        service_id: String,
        timeout_hours: u64,
    ) -> Result<()> {
        require!(!ctx.accounts.config.paused, EscrowError::Paused);
        require!(amount >= MIN_ESCROW_AMOUNT, EscrowError::AmountTooSmall);
        require!(service_id.len() <= 64, EscrowError::ServiceIdTooLong);
        require!(timeout_hours >= 1 && timeout_hours <= 168, EscrowError::InvalidTimeout);
        require!(
            ctx.accounts.buyer.key() != ctx.accounts.seller.key(),
            EscrowError::SelfTradeNotAllowed
        );

        let usdc_mint_key = ctx.accounts.usdc_mint.key();
        // N-05: Lock commission at creation time (immune to admin sandwich attack)
        let commission_bps = ctx.accounts.config.commission_bps;

        let escrow = &mut ctx.accounts.escrow;
        escrow.buyer = ctx.accounts.buyer.key();
        escrow.seller = ctx.accounts.seller.key();
        escrow.amount = amount;
        escrow.service_id = service_id;
        escrow.status = EscrowStatus::Locked;
        escrow.created_at = Clock::get()?.unix_timestamp;
        escrow.timeout_at = Clock::get()?.unix_timestamp + (timeout_hours as i64 * 3600);
        escrow.bump = ctx.bumps.escrow_vault;
        escrow.usdc_mint = usdc_mint_key;
        escrow.commission_bps = commission_bps;  // N-05: stored per-escrow

        token::transfer(
            CpiContext::new(
                ctx.accounts.token_program.to_account_info(),
                Transfer {
                    from: ctx.accounts.buyer_token.to_account_info(),
                    to: ctx.accounts.escrow_vault.to_account_info(),
                    authority: ctx.accounts.buyer.to_account_info(),
                },
            ),
            amount,
        )?;

        let config = &mut ctx.accounts.config;
        config.total_escrows = config.total_escrows.saturating_add(1);
        config.total_volume = config.total_volume.saturating_add(amount);

        emit!(EscrowCreated {
            escrow: ctx.accounts.escrow.key(),
            buyer: ctx.accounts.escrow.buyer,
            seller: ctx.accounts.escrow.seller,
            amount,
            service_id: ctx.accounts.escrow.service_id.clone(),
            commission_bps,
        });
        Ok(())
    }

    /// Buyer confirms delivery → commission to treasury, rest to seller
    /// Uses escrow.commission_bps (locked at creation, not current config)
    pub fn confirm_delivery(ctx: Context<ConfirmDelivery>) -> Result<()> {
        require!(!ctx.accounts.config.paused, EscrowError::Paused);

        let escrow_status = ctx.accounts.escrow.status.clone();
        let escrow_buyer = ctx.accounts.escrow.buyer;
        let escrow_seller = ctx.accounts.escrow.seller;
        let escrow_amount = ctx.accounts.escrow.amount;
        let escrow_bump = ctx.accounts.escrow.bump;
        let escrow_key = ctx.accounts.escrow.key();
        // N-05: Use per-escrow commission, not config
        let commission_bps = ctx.accounts.escrow.commission_bps;

        require!(escrow_status == EscrowStatus::Locked, EscrowError::InvalidStatus);
        require!(escrow_buyer == ctx.accounts.buyer.key(), EscrowError::Unauthorized);

        let seeds = &[b"vault" as &[u8], escrow_key.as_ref(), &[escrow_bump]];
        let signer_seeds = &[&seeds[..]];

        let commission = (escrow_amount as u128 * commission_bps as u128 / 10_000) as u64;
        let seller_amount = escrow_amount - commission;

        token::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                Transfer {
                    from: ctx.accounts.escrow_vault.to_account_info(),
                    to: ctx.accounts.seller_token.to_account_info(),
                    authority: ctx.accounts.escrow_vault.to_account_info(),
                },
                signer_seeds,
            ),
            seller_amount,
        )?;

        if commission > 0 {
            token::transfer(
                CpiContext::new_with_signer(
                    ctx.accounts.token_program.to_account_info(),
                    Transfer {
                        from: ctx.accounts.escrow_vault.to_account_info(),
                        to: ctx.accounts.treasury_token.to_account_info(),
                        authority: ctx.accounts.escrow_vault.to_account_info(),
                    },
                    signer_seeds,
                ),
                commission,
            )?;
        }

        token::close_account(CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            CloseAccount {
                account: ctx.accounts.escrow_vault.to_account_info(),
                destination: ctx.accounts.buyer.to_account_info(),
                authority: ctx.accounts.escrow_vault.to_account_info(),
            },
            signer_seeds,
        ))?;

        ctx.accounts.config.total_commission = ctx.accounts.config.total_commission.saturating_add(commission);
        ctx.accounts.escrow.status = EscrowStatus::Released;

        emit!(EscrowReleased { escrow: escrow_key, seller: escrow_seller, amount: seller_amount, commission });
        Ok(())
    }

    /// Buyer reclaims after timeout — allowed even when paused (N-03: by design)
    pub fn reclaim_timeout(ctx: Context<ReclaimTimeout>) -> Result<()> {
        // N-03: NOT pause-gated — buyer must always be able to reclaim their funds
        let escrow_status = ctx.accounts.escrow.status.clone();
        let escrow_buyer = ctx.accounts.escrow.buyer;
        let escrow_amount = ctx.accounts.escrow.amount;
        let escrow_bump = ctx.accounts.escrow.bump;
        let escrow_key = ctx.accounts.escrow.key();
        let escrow_timeout = ctx.accounts.escrow.timeout_at;

        // Only Locked — Disputed escrows MUST go through resolve_dispute (admin)
        // because the vault holds escrow_amount + dispute_bond, and this function
        // only transfers escrow_amount, which would leave bond stuck and revert close_account
        require!(escrow_status == EscrowStatus::Locked, EscrowError::InvalidStatus);
        require!(escrow_buyer == ctx.accounts.buyer.key(), EscrowError::Unauthorized);
        require!(Clock::get()?.unix_timestamp > escrow_timeout, EscrowError::TimeoutNotReached);

        let seeds = &[b"vault" as &[u8], escrow_key.as_ref(), &[escrow_bump]];
        let signer_seeds = &[&seeds[..]];

        token::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                Transfer {
                    from: ctx.accounts.escrow_vault.to_account_info(),
                    to: ctx.accounts.buyer_token.to_account_info(),
                    authority: ctx.accounts.escrow_vault.to_account_info(),
                },
                signer_seeds,
            ),
            escrow_amount,
        )?;

        token::close_account(CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            CloseAccount {
                account: ctx.accounts.escrow_vault.to_account_info(),
                destination: ctx.accounts.buyer.to_account_info(),
                authority: ctx.accounts.escrow_vault.to_account_info(),
            },
            signer_seeds,
        ))?;

        ctx.accounts.escrow.status = EscrowStatus::Refunded;
        emit!(EscrowRefunded { escrow: escrow_key, buyer: escrow_buyer, amount: escrow_amount });
        Ok(())
    }

    /// N-06: Open dispute — requires DISPUTE_BOND from caller (anti-griefing)
    /// N-03: Pause-gated (prevents dispute spam during emergency)
    pub fn open_dispute(ctx: Context<OpenDispute>) -> Result<()> {
        require!(!ctx.accounts.config.paused, EscrowError::Paused);

        let escrow = &ctx.accounts.escrow;
        require!(escrow.status == EscrowStatus::Locked, EscrowError::InvalidStatus);

        let caller = ctx.accounts.caller.key();
        require!(
            caller == escrow.buyer || caller == escrow.seller,
            EscrowError::Unauthorized
        );

        // N-06: Transfer dispute bond from caller to vault (returned to winner on resolution)
        token::transfer(
            CpiContext::new(
                ctx.accounts.token_program.to_account_info(),
                Transfer {
                    from: ctx.accounts.caller_token.to_account_info(),
                    to: ctx.accounts.escrow_vault.to_account_info(),
                    authority: ctx.accounts.caller.to_account_info(),
                },
            ),
            DISPUTE_BOND,
        )?;

        let escrow = &mut ctx.accounts.escrow;
        escrow.status = EscrowStatus::Disputed;
        escrow.dispute_bond = DISPUTE_BOND;
        escrow.dispute_by = caller;

        emit!(DisputeOpened { escrow: escrow.key(), opened_by: caller, bond: DISPUTE_BOND });
        Ok(())
    }

    /// Admin resolves dispute — only on Disputed escrows
    /// Bond returned to winner, forfeited to treasury if loser
    pub fn resolve_dispute(
        ctx: Context<ResolveDispute>,
        release_to_seller: bool,
    ) -> Result<()> {
        let escrow_status = ctx.accounts.escrow.status.clone();
        let escrow_buyer = ctx.accounts.escrow.buyer;
        let escrow_seller = ctx.accounts.escrow.seller;
        let escrow_amount = ctx.accounts.escrow.amount;
        let escrow_bump = ctx.accounts.escrow.bump;
        let escrow_key = ctx.accounts.escrow.key();
        let commission_bps = ctx.accounts.escrow.commission_bps;
        let dispute_bond = ctx.accounts.escrow.dispute_bond;
        let dispute_by = ctx.accounts.escrow.dispute_by;

        require!(escrow_status == EscrowStatus::Disputed, EscrowError::NotDisputed);

        let seeds = &[b"vault" as &[u8], escrow_key.as_ref(), &[escrow_bump]];
        let signer_seeds = &[&seeds[..]];

        // Total in vault = escrow_amount + dispute_bond
        if release_to_seller {
            let commission = (escrow_amount as u128 * commission_bps as u128 / 10_000) as u64;
            let seller_amount = escrow_amount - commission;

            // Pay seller
            token::transfer(
                CpiContext::new_with_signer(
                    ctx.accounts.token_program.to_account_info(),
                    Transfer {
                        from: ctx.accounts.escrow_vault.to_account_info(),
                        to: ctx.accounts.seller_token.to_account_info(),
                        authority: ctx.accounts.escrow_vault.to_account_info(),
                    },
                    signer_seeds,
                ),
                seller_amount,
            )?;

            // Commission + bond forfeited by buyer (if buyer disputed) or returned to seller (if seller disputed)
            let treasury_amount = commission + if dispute_by == escrow_buyer { dispute_bond } else { 0 };
            let seller_bond_return = if dispute_by == escrow_seller { dispute_bond } else { 0 };

            if treasury_amount > 0 {
                token::transfer(
                    CpiContext::new_with_signer(
                        ctx.accounts.token_program.to_account_info(),
                        Transfer {
                            from: ctx.accounts.escrow_vault.to_account_info(),
                            to: ctx.accounts.treasury_token.to_account_info(),
                            authority: ctx.accounts.escrow_vault.to_account_info(),
                        },
                        signer_seeds,
                    ),
                    treasury_amount,
                )?;
            }
            if seller_bond_return > 0 {
                token::transfer(
                    CpiContext::new_with_signer(
                        ctx.accounts.token_program.to_account_info(),
                        Transfer {
                            from: ctx.accounts.escrow_vault.to_account_info(),
                            to: ctx.accounts.seller_token.to_account_info(),
                            authority: ctx.accounts.escrow_vault.to_account_info(),
                        },
                        signer_seeds,
                    ),
                    seller_bond_return,
                )?;
            }
            ctx.accounts.config.total_commission = ctx.accounts.config.total_commission.saturating_add(commission);
        } else {
            // Refund buyer (full amount + bond if buyer disputed)
            let buyer_refund = escrow_amount + if dispute_by == escrow_buyer { dispute_bond } else { 0 };
            token::transfer(
                CpiContext::new_with_signer(
                    ctx.accounts.token_program.to_account_info(),
                    Transfer {
                        from: ctx.accounts.escrow_vault.to_account_info(),
                        to: ctx.accounts.buyer_token.to_account_info(),
                        authority: ctx.accounts.escrow_vault.to_account_info(),
                    },
                    signer_seeds,
                ),
                buyer_refund,
            )?;

            // If seller disputed, their bond goes to treasury
            if dispute_by == escrow_seller && dispute_bond > 0 {
                token::transfer(
                    CpiContext::new_with_signer(
                        ctx.accounts.token_program.to_account_info(),
                        Transfer {
                            from: ctx.accounts.escrow_vault.to_account_info(),
                            to: ctx.accounts.treasury_token.to_account_info(),
                            authority: ctx.accounts.escrow_vault.to_account_info(),
                        },
                        signer_seeds,
                    ),
                    dispute_bond,
                )?;
            }
        }

        // Close vault
        token::close_account(CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            CloseAccount {
                account: ctx.accounts.escrow_vault.to_account_info(),
                destination: ctx.accounts.buyer.to_account_info(),
                authority: ctx.accounts.escrow_vault.to_account_info(),
            },
            signer_seeds,
        ))?;

        ctx.accounts.escrow.status = if release_to_seller { EscrowStatus::Released } else { EscrowStatus::Refunded };
        emit!(DisputeResolved { escrow: escrow_key, released_to_seller: release_to_seller, amount: escrow_amount });
        Ok(())
    }

    /// N-01: Emergency close — closes BOTH escrow AND vault
    /// Transfers full vault balance (amount + any dispute bond) to buyer if possible
    /// If USDC frozen, vault stays orphaned (acceptable last resort)
    pub fn emergency_close(ctx: Context<EmergencyClose>) -> Result<()> {
        let _escrow_amount = ctx.accounts.escrow.amount;
        let _escrow_bond = ctx.accounts.escrow.dispute_bond;
        let escrow_bump = ctx.accounts.escrow.bump;
        let escrow_key = ctx.accounts.escrow.key();

        // Use actual vault balance, not escrow.amount (covers disputed escrows with bond)
        let vault_balance = ctx.accounts.escrow_vault.amount;

        let seeds = &[b"vault" as &[u8], escrow_key.as_ref(), &[escrow_bump]];
        let signer_seeds = &[&seeds[..]];

        // Try to refund FULL vault balance to buyer (will fail if USDC is frozen)
        let transfer_result = if vault_balance > 0 {
            token::transfer(
                CpiContext::new_with_signer(
                    ctx.accounts.token_program.to_account_info(),
                    Transfer {
                        from: ctx.accounts.escrow_vault.to_account_info(),
                        to: ctx.accounts.buyer_token.to_account_info(),
                        authority: ctx.accounts.escrow_vault.to_account_info(),
                    },
                    signer_seeds,
                ),
                vault_balance,
            )
        } else {
            Ok(())
        };

        let refunded = transfer_result.is_ok();

        // Close vault (returns rent SOL to admin)
        // Only succeeds if vault balance is 0 (either transfer worked or was already empty)
        let _ = token::close_account(CpiContext::new_with_signer(
            ctx.accounts.token_program.to_account_info(),
            CloseAccount {
                account: ctx.accounts.escrow_vault.to_account_info(),
                destination: ctx.accounts.admin.to_account_info(),
                authority: ctx.accounts.escrow_vault.to_account_info(),
            },
            signer_seeds,
        ));

        emit!(AdminAction {
            action: if refunded { "emergency_close_refunded".to_string() } else { "emergency_close_frozen".to_string() },
            old_value: vault_balance,
            new_value: if refunded { 1 } else { 0 },
        });
        // Escrow account closed via `close = admin` attribute
        Ok(())
    }
}

// ── Account Structs ──

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(init, payer = admin, space = 8 + Config::INIT_SPACE, seeds = [b"config"], bump)]
    pub config: Account<'info, Config>,
    #[account(mut)]
    pub admin: Signer<'info>,
    #[account(constraint = usdc_mint.key() == USDC_MINT @ EscrowError::InvalidMint)]
    pub usdc_mint: Account<'info, Mint>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct AdminOnly<'info> {
    #[account(mut, seeds = [b"config"], bump = config.bump, has_one = admin)]
    pub config: Account<'info, Config>,
    pub admin: Signer<'info>,
}

#[derive(Accounts)]
pub struct AcceptAdmin<'info> {
    #[account(mut, seeds = [b"config"], bump = config.bump)]
    pub config: Account<'info, Config>,
    pub new_admin: Signer<'info>,
}

#[derive(Accounts)]
#[instruction(amount: u64, service_id: String)]
pub struct CreateEscrow<'info> {
    #[account(init, payer = buyer, space = 8 + Escrow::INIT_SPACE)]
    pub escrow: Account<'info, Escrow>,

    #[account(init, payer = buyer, seeds = [b"vault", escrow.key().as_ref()], bump,
        token::mint = usdc_mint, token::authority = escrow_vault)]
    pub escrow_vault: Account<'info, TokenAccount>,

    #[account(mut, seeds = [b"config"], bump = config.bump)]
    pub config: Account<'info, Config>,

    #[account(mut)]
    pub buyer: Signer<'info>,

    /// CHECK: stored, not signing. Validated != buyer in instruction.
    pub seller: UncheckedAccount<'info>,

    #[account(mut,
        constraint = buyer_token.mint == usdc_mint.key() @ EscrowError::InvalidMint,
        constraint = buyer_token.owner == buyer.key() @ EscrowError::Unauthorized)]
    pub buyer_token: Account<'info, TokenAccount>,

    #[account(constraint = usdc_mint.key() == config.usdc_mint @ EscrowError::InvalidMint)]
    pub usdc_mint: Account<'info, Mint>,

    pub token_program: Program<'info, Token>,
    pub system_program: Program<'info, System>,
    pub rent: Sysvar<'info, Rent>,
}

#[derive(Accounts)]
pub struct ConfirmDelivery<'info> {
    #[account(mut, has_one = buyer, close = buyer)]
    pub escrow: Account<'info, Escrow>,

    #[account(mut, seeds = [b"vault", escrow.key().as_ref()], bump = escrow.bump)]
    pub escrow_vault: Account<'info, TokenAccount>,

    #[account(mut, seeds = [b"config"], bump = config.bump)]
    pub config: Account<'info, Config>,

    #[account(mut)]
    pub buyer: Signer<'info>,

    #[account(mut,
        constraint = seller_token.mint == escrow.usdc_mint @ EscrowError::InvalidMint,
        constraint = seller_token.owner == escrow.seller @ EscrowError::InvalidSellerToken)]
    pub seller_token: Account<'info, TokenAccount>,

    #[account(mut,
        constraint = treasury_token.mint == escrow.usdc_mint @ EscrowError::InvalidMint,
        constraint = treasury_token.owner == config.treasury @ EscrowError::InvalidTreasury)]
    pub treasury_token: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
}

#[derive(Accounts)]
pub struct ReclaimTimeout<'info> {
    #[account(mut, has_one = buyer, close = buyer)]
    pub escrow: Account<'info, Escrow>,

    #[account(mut, seeds = [b"vault", escrow.key().as_ref()], bump = escrow.bump)]
    pub escrow_vault: Account<'info, TokenAccount>,

    #[account(mut)]
    pub buyer: Signer<'info>,

    #[account(mut,
        constraint = buyer_token.mint == escrow.usdc_mint @ EscrowError::InvalidMint,
        constraint = buyer_token.owner == escrow.buyer @ EscrowError::InvalidBuyer)]
    pub buyer_token: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
}

#[derive(Accounts)]
pub struct OpenDispute<'info> {
    #[account(mut)]
    pub escrow: Account<'info, Escrow>,

    // N-06: Vault needed to receive dispute bond
    #[account(mut, seeds = [b"vault", escrow.key().as_ref()], bump = escrow.bump)]
    pub escrow_vault: Account<'info, TokenAccount>,

    // N-03: Config needed for pause check
    #[account(seeds = [b"config"], bump = config.bump)]
    pub config: Account<'info, Config>,

    pub caller: Signer<'info>,

    // N-06: Caller's USDC token account for dispute bond
    #[account(mut,
        constraint = caller_token.mint == escrow.usdc_mint @ EscrowError::InvalidMint,
        constraint = caller_token.owner == caller.key() @ EscrowError::Unauthorized)]
    pub caller_token: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
}

#[derive(Accounts)]
pub struct ResolveDispute<'info> {
    #[account(mut, has_one = buyer, close = buyer)]
    pub escrow: Box<Account<'info, Escrow>>,

    #[account(mut, seeds = [b"vault", escrow.key().as_ref()], bump = escrow.bump)]
    pub escrow_vault: Box<Account<'info, TokenAccount>>,

    #[account(mut, seeds = [b"config"], bump = config.bump)]
    pub config: Box<Account<'info, Config>>,

    #[account(constraint = admin.key() == config.admin @ EscrowError::Unauthorized)]
    pub admin: Signer<'info>,

    /// CHECK: verified by has_one = buyer on escrow
    #[account(mut)]
    pub buyer: UncheckedAccount<'info>,

    #[account(mut,
        constraint = buyer_token.mint == escrow.usdc_mint @ EscrowError::InvalidMint,
        constraint = buyer_token.owner == escrow.buyer @ EscrowError::InvalidBuyer)]
    pub buyer_token: Box<Account<'info, TokenAccount>>,

    #[account(mut,
        constraint = seller_token.mint == escrow.usdc_mint @ EscrowError::InvalidMint,
        constraint = seller_token.owner == escrow.seller @ EscrowError::InvalidSellerToken)]
    pub seller_token: Box<Account<'info, TokenAccount>>,

    #[account(mut,
        constraint = treasury_token.mint == escrow.usdc_mint @ EscrowError::InvalidMint,
        constraint = treasury_token.owner == config.treasury @ EscrowError::InvalidTreasury)]
    pub treasury_token: Box<Account<'info, TokenAccount>>,

    pub token_program: Program<'info, Token>,
}

#[derive(Accounts)]
pub struct EmergencyClose<'info> {
    // N-01: includes vault for cleanup
    #[account(mut, close = admin)]
    pub escrow: Account<'info, Escrow>,

    #[account(mut, seeds = [b"vault", escrow.key().as_ref()], bump = escrow.bump)]
    pub escrow_vault: Account<'info, TokenAccount>,

    #[account(seeds = [b"config"], bump = config.bump, has_one = admin)]
    pub config: Account<'info, Config>,

    #[account(mut)]
    pub admin: Signer<'info>,

    // N-01: buyer token for refund attempt
    #[account(mut,
        constraint = buyer_token.mint == escrow.usdc_mint @ EscrowError::InvalidMint,
        constraint = buyer_token.owner == escrow.buyer @ EscrowError::InvalidBuyer)]
    pub buyer_token: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
}

// ── State ──

#[account]
#[derive(InitSpace)]
pub struct Config {
    pub admin: Pubkey,
    pub treasury: Pubkey,
    pub usdc_mint: Pubkey,
    pub commission_bps: u16,
    pub total_escrows: u64,
    pub total_volume: u64,
    pub total_commission: u64,
    pub paused: bool,
    pub pending_admin: Pubkey,
    pub pending_admin_at: i64,
    pub pending_treasury: Pubkey,      // N-02
    pub pending_treasury_at: i64,      // N-02
    pub bump: u8,
}

#[account]
#[derive(InitSpace)]
pub struct Escrow {
    pub buyer: Pubkey,
    pub seller: Pubkey,
    pub amount: u64,
    #[max_len(64)]
    pub service_id: String,
    pub status: EscrowStatus,
    pub created_at: i64,
    pub timeout_at: i64,
    pub bump: u8,
    pub usdc_mint: Pubkey,
    pub commission_bps: u16,           // N-05: locked at creation
    pub dispute_bond: u64,             // N-06: bond amount (0 if no dispute)
    pub dispute_by: Pubkey,            // N-06: who opened the dispute
}

#[derive(AnchorSerialize, AnchorDeserialize, Clone, PartialEq, Eq, InitSpace)]
pub enum EscrowStatus {
    Locked,
    Released,
    Refunded,
    Disputed,
}

// ── Errors ──

#[error_code]
pub enum EscrowError {
    #[msg("Amount too small: minimum 1 USDC")]
    AmountTooSmall,
    #[msg("Service ID too long: max 64 chars")]
    ServiceIdTooLong,
    #[msg("Invalid timeout: 1-168 hours")]
    InvalidTimeout,
    #[msg("Invalid escrow status")]
    InvalidStatus,
    #[msg("Unauthorized")]
    Unauthorized,
    #[msg("Timeout not reached")]
    TimeoutNotReached,
    #[msg("Invalid USDC mint")]
    InvalidMint,
    #[msg("Commission max 1000 bps (10%)")]
    InvalidCommission,
    #[msg("Invalid buyer account")]
    InvalidBuyer,
    #[msg("Invalid seller token account")]
    InvalidSellerToken,
    #[msg("Invalid treasury token account")]
    InvalidTreasury,
    #[msg("Self-trade not allowed")]
    SelfTradeNotAllowed,
    #[msg("Program is paused")]
    Paused,
    #[msg("Escrow must be Disputed for admin resolution")]
    NotDisputed,
    #[msg("No pending change")]
    NoPendingChange,
    #[msg("48h timelock not expired")]
    TimelockNotExpired,
}

// ── Events ──

#[event]
pub struct EscrowCreated {
    pub escrow: Pubkey,
    pub buyer: Pubkey,
    pub seller: Pubkey,
    pub amount: u64,
    pub service_id: String,
    pub commission_bps: u16,
}

#[event]
pub struct EscrowReleased {
    pub escrow: Pubkey,
    pub seller: Pubkey,
    pub amount: u64,
    pub commission: u64,
}

#[event]
pub struct EscrowRefunded {
    pub escrow: Pubkey,
    pub buyer: Pubkey,
    pub amount: u64,
}

#[event]
pub struct DisputeOpened {
    pub escrow: Pubkey,
    pub opened_by: Pubkey,
    pub bond: u64,
}

#[event]
pub struct DisputeResolved {
    pub escrow: Pubkey,
    pub released_to_seller: bool,
    pub amount: u64,
}

#[event]
pub struct AdminAction {
    pub action: String,
    pub old_value: u64,
    pub new_value: u64,
}
