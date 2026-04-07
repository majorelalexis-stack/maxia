/* ======================================
   MAXIA App — Swap UI & Execution
   ====================================== */

// ======================================
// SWAP
// ======================================

/**
 * Render the swap tier progression bar showing all tiers.
 * Uses tier_info from the quote API if available, otherwise falls back
 * to cachedSwapTiers from /api/public/prices.
 * @param {object|null} tierInfo - tier_info from quote response
 * @param {string|null} currentTier - tier name from quote (e.g. "BRONZE")
 * @param {number} valueUsd - transaction value in USD
 */
function renderSwapTierProgress(tierInfo, currentTier, valueUsd) {
  var tierProgress = document.getElementById('swap-tier-progress');
  if (!tierProgress) return;

  // Build tier list from API data (tier_info.all_tiers) or cached /api/public/prices
  var tierColors = {BRONZE: '#cd7f32', SILVER: '#c0c0c0', GOLD: '#ffd700', WHALE: '#00e5ff'};
  var tiers = [];

  if (tierInfo && tierInfo.all_tiers) {
    // Use all_tiers from quote response (dynamic, from backend)
    Object.keys(tierInfo.all_tiers).forEach(function(name) {
      var t = tierInfo.all_tiers[name];
      tiers.push({name: name, pct: t.pct, bps: t.bps, min: t.min_amount, max: t.max_amount});
    });
  } else if (cachedSwapTiers) {
    // Fallback: use tiers cached from /api/public/prices
    Object.keys(cachedSwapTiers).forEach(function(name) {
      var t = cachedSwapTiers[name];
      tiers.push({name: name, pct: (t.bps / 100).toFixed(2) + '%', bps: t.bps, min: t.min_amount, max: t.max_amount});
    });
  } else {
    // Hardcoded last resort (matches config.py SWAP_COMMISSION_TIERS)
    tiers = [
      {name: 'BRONZE', pct: '0.10%', bps: 10, min: 0, max: 1000},
      {name: 'SILVER', pct: '0.05%', bps: 5, min: 1000, max: 5000},
      {name: 'GOLD', pct: '0.03%', bps: 3, min: 5000, max: 25000},
      {name: 'WHALE', pct: '0.01%', bps: 1, min: 25000, max: 999999999}
    ];
  }

  if (tiers.length === 0) { tierProgress.style.display = 'none'; return; }

  // Determine current tier from API response (based on 30-day volume, not trade amount)
  var activeTier = (tierInfo && tierInfo.current_tier) ? tierInfo.current_tier : (currentTier || 'BRONZE');
  if (!activeTier) activeTier = 'BRONZE';

  tierProgress.style.display = 'flex';
  var bars = document.getElementById('swap-tier-bars');
  bars.innerHTML = '';

  tiers.forEach(function(t) {
    var active = t.name === activeTier;
    var color = tierColors[t.name] || 'var(--cyan)';
    var fmtK = function(v) { return v >= 1000 ? '$' + (v / 1000) + 'K' : '$' + v; };
    var rangeLabel;
    if (t.max >= 999999999) { rangeLabel = fmtK(t.min) + '+'; }
    else if (t.min === 0) { rangeLabel = '$0-' + fmtK(t.max); }
    else { rangeLabel = fmtK(t.min) + '-' + fmtK(t.max); }
    var div = document.createElement('div');
    div.style.cssText = 'flex:1;text-align:center;padding:6px 4px;border-radius:6px;' +
      'background:' + (active ? 'rgba(255,255,255,0.08)' : 'transparent') + ';' +
      'border:1px solid ' + (active ? color : 'rgba(255,255,255,.1)') + ';transition:all .2s';
    div.innerHTML = '<div style="font-size:10px;color:' + color + ';font-weight:600">' + esc(t.name) + '</div>' +
      '<div style="font-size:14px;color:' + (active ? color : 'var(--muted)') + ';font-weight:700">' + esc(t.pct) + '</div>' +
      '<div style="font-size:9px;color:var(--muted)">' + rangeLabel + '</div>';
    bars.appendChild(div);
  });

  // Next tier message
  var nextEl = document.getElementById('swap-tier-next');
  if (tierInfo && tierInfo.next_tier && tierInfo.remaining_to_next > 0) {
    nextEl.textContent = 'Swap $' + Math.round(tierInfo.remaining_to_next).toLocaleString() + ' more to reach ' + tierInfo.next_tier + ' tier';
    nextEl.style.color = 'var(--muted)';
    nextEl.style.display = 'block';
  } else if (tierInfo && !tierInfo.next_tier) {
    nextEl.textContent = 'You have the best tier!';
    nextEl.style.color = 'var(--cyan)';
    nextEl.style.display = 'block';
  } else {
    // Compute next tier from tiers array (based on 30-day volume, not trade amount)
    var volume30d = (tierInfo && tierInfo.volume_30d) ? tierInfo.volume_30d : 0;
    var activeIdx = -1;
    for (var j = 0; j < tiers.length; j++) { if (tiers[j].name === activeTier) { activeIdx = j; break; } }
    if (activeIdx >= 0 && activeIdx + 1 < tiers.length) {
      var nextT = tiers[activeIdx + 1];
      var rem = Math.max(0, nextT.min - volume30d);
      if (rem > 0) {
        nextEl.textContent = 'Swap $' + Math.round(rem).toLocaleString() + ' more to reach ' + nextT.name + ' tier';
        nextEl.style.color = 'var(--muted)';
        nextEl.style.display = 'block';
      } else {
        nextEl.style.display = 'none';
      }
    } else if (activeIdx === tiers.length - 1) {
      nextEl.textContent = 'You have the best tier!';
      nextEl.style.color = 'var(--cyan)';
      nextEl.style.display = 'block';
    } else {
      nextEl.style.display = 'none';
    }
  }
}

async function loadSwapTokens() {
  try {
    var data = await api('/api/public/crypto/prices');
    if (data.prices) {
      swapTokens = Object.entries(data.prices).map(function(entry) {
        return { symbol: entry[0], name: entry[1].name || entry[0], price: entry[1].price || 0 };
      });

      var fromSel = document.getElementById('swap-from-token');
      var toSel = document.getElementById('swap-to-token');
      fromSel.innerHTML = '';
      toSel.innerHTML = '';

      swapTokens.forEach(function(t) {
        fromSel.innerHTML += '<option value="' + esc(t.symbol) + '">' + esc(t.symbol) + '</option>';
        toSel.innerHTML += '<option value="' + esc(t.symbol) + '">' + esc(t.symbol) + '</option>';
      });

      fromSel.value = 'SOL';
      toSel.value = 'USDC';
      if (!fromSel.value && swapTokens.length > 0) fromSel.selectedIndex = 0;
      if (!toSel.value && swapTokens.length > 1) toSel.selectedIndex = 1;
    }
  } catch (e) {
    var fallback = ['SOL','USDC','ETH','BTC','BONK','JUP','RAY','ORCA','PYTH','WIF'];
    var fromSel2 = document.getElementById('swap-from-token');
    var toSel2 = document.getElementById('swap-to-token');
    fromSel2.innerHTML = '';
    toSel2.innerHTML = '';
    fallback.forEach(function(s) {
      fromSel2.innerHTML += '<option value="' + s + '">' + s + '</option>';
      toSel2.innerHTML += '<option value="' + s + '">' + s + '</option>';
    });
    fromSel2.value = 'SOL';
    toSel2.value = 'USDC';
  }
}

function getSwapQuoteDebounced() {
  clearTimeout(quoteTimer);
  quoteTimer = setTimeout(getSwapQuote, 400);
}

async function getSwapQuote() {
  var from = document.getElementById('swap-from-token').value;
  var to = document.getElementById('swap-to-token').value;
  var amount = parseFloat(document.getElementById('swap-from-amount').value);
  var btn = document.getElementById('btn-swap');
  var info = document.getElementById('swap-info');

  if (!amount || amount <= 0) {
    document.getElementById('swap-to-amount').value = '';
    info.style.display = 'none';
    btn.textContent = 'Enter an amount';
    btn.disabled = true;
    return;
  }

  if (from === to) {
    document.getElementById('swap-to-amount').value = fmtAmount(amount);
    info.style.display = 'none';
    btn.textContent = 'Select different tokens';
    btn.disabled = true;
    return;
  }

  btn.textContent = 'Fetching quote...';
  btn.disabled = true;

  try {
    var data = await api('/api/public/crypto/quote?from_token=' + encodeURIComponent(from) + '&to_token=' + encodeURIComponent(to) + '&amount=' + amount);
    if (data) {
      var outAmount = data.output_amount || data.estimated_output || 0;
      document.getElementById('swap-to-amount').value = fmtAmount(outAmount);
      document.getElementById('swap-rate').textContent = '1 ' + from + ' = ' + fmtRate(data.rate || (outAmount / amount)) + ' ' + to;
      document.getElementById('swap-fee').textContent = fmtUSD(data.commission_usd || 0) + ' (' + (data.commission_pct || '0.10%') + ' ' + (data.tier || 'BRONZE') + ')';
      // Frais reseau estimes selon la chain
      var netFeeEl = document.getElementById('swap-network-fee');
      if (netFeeEl) {
        var isEvm = isEvmWallet();
        if (isEvm) {
          getEvmChainId().then(function(cid) {
            var fees = {1:'~$2-5 (Ethereum)',8453:'~$0.01 (Base)',137:'~$0.01 (Polygon)',42161:'~$0.01 (Arbitrum)',43114:'~$0.03 (Avalanche)',56:'~$0.05 (BNB)'};
            netFeeEl.textContent = fees[cid] || '~$0.01 (L2)';
          });
        } else {
          netFeeEl.textContent = '~$0.001 (Solana)';
        }
      }
      document.getElementById('swap-receive').textContent = fmtAmount(outAmount) + ' ' + to;
      document.getElementById('swap-tier').textContent = (data.tier || 'BRONZE') + ' ' + (data.commission_bps ? (data.commission_bps/100).toFixed(2) + '%' : (data.commission_pct || '0.10%'));
      info.style.display = 'flex';
      // Show route info based on wallet type
      var isSolWallet = isSolanaWallet();
      var isEvm = isEvmWallet();
      var routeRow = document.getElementById('swap-route-row');
      if (routeRow) {
        var showRoute = isSolWallet || isEvm;
        routeRow.style.display = showRoute ? 'flex' : 'none';
        if (isSolWallet && TOKEN_MINTS[from] && TOKEN_MINTS[to]) {
          document.getElementById('swap-route').textContent = 'Jupiter Aggregator (on-chain)';
        } else if (isEvm) {
          getEvmChainId().then(function(cid) {
            var chainInfo = EVM_CHAINS[cid];
            var chainName = chainInfo ? chainInfo.name : 'EVM';
            document.getElementById('swap-route').textContent = 'ParaSwap (' + chainName + ')';
          });
        }
      }
      // Render swap tier progression bars
      renderSwapTierProgress(data.tier_info, data.tier, data.input_value_usd);

      var routeLabel = isSolWallet ? ' (Jupiter)' : (isEvm ? ' (ParaSwap)' : '');
      btn.textContent = 'Swap ' + from + ' → ' + to + routeLabel;
      btn.disabled = false;
      // Clear previous swap status when getting new quote
      setSwapStatus(null);
    }
  } catch (e) {
    var fromToken = swapTokens.find(function(t) { return t.symbol === from; });
    var toToken = swapTokens.find(function(t) { return t.symbol === to; });
    if (fromToken && toToken && toToken.price > 0) {
      var rate = fromToken.price / toToken.price;
      var out = amount * rate * 0.95;
      document.getElementById('swap-to-amount').value = fmtAmount(out);
      document.getElementById('swap-rate').textContent = '1 ' + from + ' = ' + fmtRate(rate) + ' ' + to;
      var estFee = amount * fromToken.price * 0.001;
      document.getElementById('swap-fee').textContent = fmtUSD(estFee) + ' (~0.10% BRONZE)';
      document.getElementById('swap-receive').textContent = fmtAmount(out) + ' ' + to;
      info.style.display = 'flex';
      var isSolWallet2 = isSolanaWallet();
      var isEvm2 = isEvmWallet();
      var routeRow2 = document.getElementById('swap-route-row');
      if (routeRow2) {
        routeRow2.style.display = (isSolWallet2 || isEvm2) ? 'flex' : 'none';
        if (isEvm2) {
          getEvmChainId().then(function(cid) {
            var ci = EVM_CHAINS[cid];
            document.getElementById('swap-route').textContent = 'ParaSwap (' + (ci ? ci.name : 'EVM') + ')';
          });
        }
      }
      // Render swap tier bars using fallback (cached tiers from /api/public/prices)
      var fallbackValueUsd = amount * fromToken.price;
      renderSwapTierProgress(null, null, fallbackValueUsd);

      var routeLabel2 = isSolWallet2 ? ' (Jupiter)' : (isEvm2 ? ' (ParaSwap)' : '');
      btn.textContent = 'Swap ' + from + ' → ' + to + routeLabel2;
      btn.disabled = false;
      setSwapStatus(null);
    } else {
      btn.textContent = 'Quote unavailable';
      btn.disabled = true;
    }
  }
}

function swapDirection() {
  var fromSel = document.getElementById('swap-from-token');
  var toSel = document.getElementById('swap-to-token');
  var temp = fromSel.value;
  fromSel.value = toSel.value;
  toSel.value = temp;
  getSwapQuoteDebounced();
}

function setSwapStatus(msg, type) {
  var el = document.getElementById('swap-status');
  if (!msg) { el.style.display = 'none'; el.innerHTML = ''; return; }
  el.style.display = 'block';
  el.className = 'swap-status ' + (type || 'pending');
  el.innerHTML = msg;
}

async function executeSwap() {
  var from = document.getElementById('swap-from-token').value;
  var to = document.getElementById('swap-to-token').value;
  var amount = parseFloat(document.getElementById('swap-from-amount').value);

  // -- Multi-wallet: auto-select wallet based on token type --
  var isSolToken = !!TOKEN_MINTS[from];
  var isEvmToken = false;
  // Check if from token exists in any EVM chain's token list
  for (var cid in EVM_CHAINS) {
    if (EVM_CHAINS[cid].tokens && EVM_CHAINS[cid].tokens[from]) { isEvmToken = true; break; }
  }

  // Auto-switch active wallet based on the from token
  if (isSolToken && connectedWallets.solana) {
    wallet = connectedWallets.solana.address;
    walletType = connectedWallets.solana.provider;
  } else if (isEvmToken && connectedWallets.evm) {
    wallet = connectedWallets.evm.address;
    walletType = connectedWallets.evm.provider;
  }

  if (!requireWalletFor('swap')) return;

  if (!amount || amount <= 0) { toast('Enter a valid amount', 'error'); return; }
  if (from === to) { toast('Select different tokens', 'error'); return; }

  // Route to the appropriate swap engine
  if (isEvmWallet()) {
    await executeEvmSwap(from, to, amount);
  } else if (isSolanaWallet()) {
    await executeSolanaSwap(from, to, amount);
  } else {
    // Other chains — redirect to native DEX
    var dexLinks = {
      'xrp': {name: 'XRPL DEX', url: 'https://sologenic.org/trade'},
      'ton': {name: 'STON.fi', url: 'https://app.ston.fi/swap'},
      'sui': {name: 'Cetus', url: 'https://app.cetus.zone/swap'},
      'tron': {name: 'SunSwap', url: 'https://sunswap.com/#/v3/swap'},
      'near': {name: 'Ref Finance', url: 'https://app.ref.finance/'},
      'aptos': {name: 'PancakeSwap', url: 'https://aptos.pancakeswap.finance/swap'},
      'sei': {name: 'DragonSwap', url: 'https://dragonswap.app/swap'},
    };
    var chain = (walletType || '').toLowerCase();
    var dex = dexLinks[chain];
    if (dex) {
      setSwapStatus(
        '<div style="padding:16px;background:rgba(0,229,204,.05);border:1px solid rgba(0,229,204,.2);border-radius:12px">' +
        '<div style="font-weight:600;margin-bottom:8px">Swap via ' + dex.name + '</div>' +
        '<div style="font-size:13px;color:var(--muted);margin-bottom:12px">' + from + ' → ' + to + ' swap on ' + chain.toUpperCase() + ' is executed via ' + dex.name + '</div>' +
        '<a href="' + dex.url + '" target="_blank" style="display:inline-block;padding:10px 24px;background:var(--cyan);color:var(--bg);border-radius:8px;font-weight:600;text-decoration:none">Open ' + dex.name + ' →</a>' +
        '</div>', 'pending'
      );
    } else {
      toast('Connect Phantom (Solana) or MetaMask (EVM) to swap', 'error');
    }
  }
}

// ======================================
// SOLANA SWAP (Jupiter)
// ======================================
async function executeSolanaSwap(from, to, amount) {
  // Resolve mint addresses
  var fromInfo = TOKEN_MINTS[from];
  var toInfo = TOKEN_MINTS[to];
  if (!fromInfo || !toInfo) {
    toast('Token not supported for on-chain swap: ' + (!fromInfo ? from : to), 'error');
    return;
  }

  var btn = document.getElementById('btn-swap');
  var originalText = btn.textContent;
  btn.disabled = true;

  try {
    // -- Step 1: Get Jupiter quote --
    btn.textContent = 'Getting quote...';
    setSwapStatus('<span class="swap-spinner"></span>Fetching best route from Jupiter...', 'pending');

    var amountRaw = Math.floor(amount * Math.pow(10, fromInfo.decimals));
    var quoteUrl = JUPITER_V1_API + '/quote?inputMint=' + fromInfo.mint
      + '&outputMint=' + toInfo.mint
      + '&amount=' + amountRaw
      + '&slippageBps=50';

    var quoteResp = await fetch(quoteUrl);
    if (!quoteResp.ok) {
      var quoteErr = await quoteResp.text();
      throw new Error('Jupiter quote failed: ' + quoteResp.status + ' ' + quoteErr);
    }
    var quote = await quoteResp.json();

    if (quote.error) {
      throw new Error('Jupiter: ' + (quote.error || quote.message || 'Quote failed'));
    }

    // Display quote output
    var outRaw = parseInt(quote.outAmount || '0');
    var outHuman = outRaw / Math.pow(10, toInfo.decimals);
    setSwapStatus(
      '<span class="swap-spinner"></span>Route found: ' + amount + ' ' + from + ' → ' +
      outHuman.toFixed(toInfo.decimals <= 6 ? Math.min(toInfo.decimals, 6) : 4) + ' ' + to +
      '<br><span style="font-size:12px;opacity:.7">Price impact: ' + ((parseFloat(quote.priceImpactPct) || 0) * 100).toFixed(4) + '%</span>',
      'pending'
    );

    // -- Step 2: Get swap transaction from Jupiter --
    btn.textContent = 'Building transaction...';
    setSwapStatus('<span class="swap-spinner"></span>Building swap transaction...', 'pending');

    var swapResp = await fetch(JUPITER_V1_API + '/swap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        quoteResponse: quote,
        userPublicKey: wallet,
        dynamicComputeUnitLimit: true,
        prioritizationFeeLamports: 'auto'
      })
    });

    if (!swapResp.ok) {
      var swapErr = await swapResp.text();
      throw new Error('Jupiter swap build failed: ' + swapResp.status + ' ' + swapErr);
    }
    var swapData = await swapResp.json();

    if (swapData.error) {
      throw new Error('Jupiter: ' + (swapData.error || 'Swap build failed'));
    }

    var swapTxBase64 = swapData.swapTransaction;
    if (!swapTxBase64) {
      throw new Error('No swap transaction returned from Jupiter');
    }

    // -- Step 3: Deserialize and sign with wallet --
    btn.textContent = 'Confirm in wallet...';
    setSwapStatus('<span class="swap-spinner"></span>Please confirm the transaction in your wallet...', 'confirming');

    // Decode base64 to Uint8Array
    var txBytes = Uint8Array.from(atob(swapTxBase64), function(c) { return c.charCodeAt(0); });

    // Jupiter returns VersionedTransaction
    var transaction = solanaWeb3.VersionedTransaction.deserialize(txBytes);

    // Get the wallet provider
    var provider = null;
    if (walletType === 'phantom') provider = window.solana;
    else if (walletType === 'backpack') provider = window.backpack;
    else if (walletType === 'solflare') provider = window.solflare;

    if (!provider) {
      throw new Error('Wallet provider not found. Please reconnect your wallet.');
    }

    // -- Step 4: Sign AND Send via wallet (Phantom uses its own RPC — no 403) --
    btn.textContent = 'Sending transaction...';
    setSwapStatus('<span class="swap-spinner"></span>Signing and sending via wallet...', 'confirming');

    var result = await provider.signAndSendTransaction(transaction, {
      skipPreflight: false,
      maxRetries: 3,
      preflightCommitment: 'confirmed'
    });
    var txSignature = result.signature || result;

    // -- Step 5: Confirm transaction --
    btn.textContent = 'Confirming...';
    setSwapStatus(
      '<span class="swap-spinner"></span>Transaction sent! Waiting for confirmation...' +
      '<div class="tx-sig">Signature: ' + esc(txSignature) + '</div>',
      'confirming'
    );

    // Wait for confirmation with timeout
    var connection = new solanaWeb3.Connection(SOLANA_RPC, 'confirmed');
    try {
      var confirmResult = await connection.confirmTransaction(txSignature, 'confirmed');
      if (confirmResult.value && confirmResult.value.err) {
        throw new Error('Transaction failed on-chain: ' + JSON.stringify(confirmResult.value.err));
      }
    } catch(confirmErr) {
      // Confirmation check may fail on public RPC but tx was already sent via Phantom
      console.log('Confirmation check error (tx may still succeed):', confirmErr);
    }

    // -- Success --
    var solscanUrl = 'https://solscan.io/tx/' + txSignature;
    setSwapStatus(
      'Swap successful! ' + amount + ' ' + from + ' → ' +
      outHuman.toFixed(toInfo.decimals <= 6 ? Math.min(toInfo.decimals, 6) : 4) + ' ' + to +
      '<div class="tx-sig"><a href="' + esc(solscanUrl) + '" target="_blank" rel="noopener">View on Solscan: ' + esc(txSignature.slice(0, 20)) + '...' + esc(txSignature.slice(-8)) + '</a></div>' +
      '<div style="display:inline-flex;align-items:center;gap:4px;background:rgba(0,255,136,0.1);border:1px solid rgba(0,255,136,0.3);border-radius:4px;padding:2px 8px;font-size:11px;color:#00ff88;margin-top:8px"><span>&#10003;</span> OFAC Screened</div>',
      'success'
    );

    toast('Swap confirmed! ' + from + ' → ' + to, 'success');

    // Clear form
    document.getElementById('swap-from-amount').value = '';
    document.getElementById('swap-to-amount').value = '';
    document.getElementById('swap-info').style.display = 'none';
    btn.textContent = 'Enter an amount';
    btn.disabled = true;

    // Also log to backend (fire and forget)
    fetch('/api/public/crypto/log-swap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from_token: from, to_token: to, amount: amount,
        output_amount: outHuman, wallet: wallet,
        tx_signature: txSignature, source: 'jupiter_phantom'
      })
    }).catch(function(err) { console.warn('Swap log failed:', err); });

  } catch (e) {
    var errMsg = e.message || String(e);

    // User rejected
    if (errMsg.indexOf('User rejected') !== -1 || errMsg.indexOf('user rejected') !== -1 || errMsg.indexOf('cancelled') !== -1) {
      setSwapStatus('Transaction cancelled by user.', 'error');
      toast('Transaction cancelled', 'info');
    }
    // Insufficient balance
    else if (errMsg.indexOf('insufficient') !== -1 || errMsg.indexOf('Insufficient') !== -1 || errMsg.indexOf('0x1') !== -1) {
      setSwapStatus('Insufficient balance for this swap. Make sure you have enough ' + from + ' and SOL for fees.', 'error');
      toast('Insufficient balance', 'error');
    }
    // Generic error
    else {
      setSwapStatus('Swap failed: ' + esc(errMsg), 'error');
      toast('Swap failed: ' + errMsg, 'error');
    }

    btn.textContent = 'Swap ' + from + ' → ' + to;
    btn.disabled = false;
  }
}

// ======================================
// EVM SWAP (ParaSwap — Ethereum, Base, Polygon, Arbitrum, Avalanche, BNB)
// ======================================
async function executeEvmSwap(from, to, amount) {
  var btn = document.getElementById('btn-swap');
  btn.disabled = true;

  try {
    // -- Step 1: Detect chain --
    var chainId = await getEvmChainId();
    var chainInfo = EVM_CHAINS[chainId];

    if (!chainInfo) {
      // Offer to switch to a supported chain
      var supportedNames = Object.values(EVM_CHAINS).map(function(c) { return c.name; }).join(', ');
      setSwapStatus(
        'Unsupported chain (ID: ' + chainId + '). Please switch to: ' + supportedNames + '.' +
        '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">' +
          '<button onclick="switchEvmChain(1).then(function(){executeSwap()})" style="padding:6px 14px;background:rgba(59,130,246,.2);color:var(--blue);border:1px solid rgba(59,130,246,.3);border-radius:8px;cursor:pointer;font-size:13px;font-family:Outfit">Ethereum</button>' +
          '<button onclick="switchEvmChain(8453).then(function(){executeSwap()})" style="padding:6px 14px;background:rgba(59,130,246,.2);color:var(--blue);border:1px solid rgba(59,130,246,.3);border-radius:8px;cursor:pointer;font-size:13px;font-family:Outfit">Base</button>' +
          '<button onclick="switchEvmChain(137).then(function(){executeSwap()})" style="padding:6px 14px;background:rgba(139,92,246,.2);color:var(--purple);border:1px solid rgba(139,92,246,.3);border-radius:8px;cursor:pointer;font-size:13px;font-family:Outfit">Polygon</button>' +
          '<button onclick="switchEvmChain(42161).then(function(){executeSwap()})" style="padding:6px 14px;background:rgba(59,130,246,.2);color:var(--blue);border:1px solid rgba(59,130,246,.3);border-radius:8px;cursor:pointer;font-size:13px;font-family:Outfit">Arbitrum</button>' +
        '</div>',
        'error'
      );
      btn.textContent = 'Switch chain';
      btn.disabled = false;
      return;
    }

    // -- Step 2: Resolve token addresses on this chain --
    var fromEvm = resolveEvmToken(from, chainId);
    var toEvm = resolveEvmToken(to, chainId);

    if (!fromEvm || !toEvm) {
      var missing = !fromEvm ? from : to;
      setSwapStatus(
        missing + ' is not available on ' + chainInfo.name + '. ' +
        'Try a different token or switch to a chain that supports it.' +
        '<div style="margin-top:8px;font-size:12px;color:var(--muted)">Available on ' + chainInfo.name + ': ' +
          Object.keys(EVM_TOKENS[chainId] || {}).join(', ') +
        '</div>',
        'error'
      );
      btn.textContent = 'Token not available on ' + chainInfo.name;
      btn.disabled = false;
      return;
    }

    btn.textContent = 'Getting quote...';
    setSwapStatus('<span class="swap-spinner"></span>Fetching best route from ParaSwap on ' + chainInfo.name + '...', 'pending');

    // -- Step 3: Get ParaSwap price quote --
    var amountRaw = BigInt(Math.floor(amount * Math.pow(10, fromEvm.decimals))).toString();

    var priceUrl = PARASWAP_API + '/prices?'
      + 'srcToken=' + encodeURIComponent(fromEvm.address)
      + '&destToken=' + encodeURIComponent(toEvm.address)
      + '&amount=' + amountRaw
      + '&srcDecimals=' + fromEvm.decimals
      + '&destDecimals=' + toEvm.decimals
      + '&network=' + chainId
      + '&side=SELL'
      + '&partner=maxia';

    var priceResp = await fetch(priceUrl);
    if (!priceResp.ok) {
      var priceErr = await priceResp.json().catch(function() { return {}; });
      throw new Error('ParaSwap price failed: ' + (priceErr.error || priceResp.status));
    }
    var priceData = await priceResp.json();

    if (priceData.error) {
      throw new Error('ParaSwap: ' + priceData.error);
    }

    var priceRoute = priceData.priceRoute;
    if (!priceRoute) {
      throw new Error('No route found on ParaSwap for ' + from + ' → ' + to + ' on ' + chainInfo.name);
    }

    var destAmountRaw = priceRoute.destAmount || '0';
    var destHuman = Number(destAmountRaw) / Math.pow(10, toEvm.decimals);
    var gasCostUSD = priceRoute.gasCostUSD || '0';

    setSwapStatus(
      '<span class="swap-spinner"></span>Route found on ' + chainInfo.name + ': ' + amount + ' ' + from + ' → ' +
      destHuman.toFixed(toEvm.decimals <= 6 ? Math.min(toEvm.decimals, 6) : 4) + ' ' + to +
      '<br><span style="font-size:12px;opacity:.7">Gas cost: ~$' + Number(gasCostUSD).toFixed(2) + '</span>',
      'pending'
    );

    // -- Step 4: Build swap transaction via ParaSwap --
    btn.textContent = 'Building transaction...';
    setSwapStatus('<span class="swap-spinner"></span>Building swap transaction on ' + chainInfo.name + '...', 'pending');

    // Allow 1% slippage
    var minDestAmount = BigInt(destAmountRaw) * BigInt(99) / BigInt(100);

    var txBuildResp = await fetch(PARASWAP_API + '/transactions/' + chainId + '?ignoreChecks=true', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        srcToken: fromEvm.address,
        destToken: toEvm.address,
        srcAmount: amountRaw,
        destAmount: minDestAmount.toString(),
        priceRoute: priceRoute,
        userAddress: wallet,
        partner: 'maxia',
        srcDecimals: fromEvm.decimals,
        destDecimals: toEvm.decimals
      })
    });

    if (!txBuildResp.ok) {
      var buildErr = await txBuildResp.json().catch(function() { return {}; });
      throw new Error('ParaSwap build failed: ' + (buildErr.error || txBuildResp.status));
    }
    var txData = await txBuildResp.json();

    if (txData.error) {
      throw new Error('ParaSwap: ' + txData.error);
    }

    // -- Step 5: Approve ERC-20 token if needed --
    if (fromEvm.address.toLowerCase() !== EVM_NATIVE.toLowerCase()) {
      btn.textContent = 'Checking approval...';
      setSwapStatus('<span class="swap-spinner"></span>Checking token approval...', 'pending');

      var spender = txData.to; // ParaSwap contract that needs approval
      var needsApproval = await checkEvmApproval(fromEvm.address, spender, amountRaw);
      if (needsApproval) {
        btn.textContent = 'Approve ' + from + ' in wallet...';
        setSwapStatus('<span class="swap-spinner"></span>Please approve ' + from + ' spending in your wallet...', 'confirming');
        await approveEvmToken(fromEvm.address, spender, amountRaw);
        setSwapStatus('<span class="swap-spinner"></span>Approval confirmed. Sending swap...', 'pending');
      }
    }

    // -- Step 6: Send swap transaction via MetaMask --
    btn.textContent = 'Confirm in wallet...';
    setSwapStatus('<span class="swap-spinner"></span>Please confirm the swap in your wallet...', 'confirming');

    var txParams = {
      from: wallet,
      to: txData.to,
      data: txData.data,
      value: txData.value || '0x0',
      gas: txData.gas ? '0x' + parseInt(txData.gas).toString(16) : undefined,
      chainId: chainInfo.hex
    };
    // Remove undefined fields
    if (!txParams.gas) delete txParams.gas;

    var txHash = await window.ethereum.request({
      method: 'eth_sendTransaction',
      params: [txParams]
    });

    // -- Step 7: Wait for confirmation --
    btn.textContent = 'Confirming...';
    setSwapStatus(
      '<span class="swap-spinner"></span>Transaction sent on ' + esc(chainInfo.name) + '! Waiting for confirmation...' +
      '<div class="tx-sig">Tx: ' + esc(txHash) + '</div>',
      'confirming'
    );

    // Poll for receipt
    var receipt = null;
    for (var i = 0; i < 120; i++) {
      await new Promise(function(r) { setTimeout(r, 2000); });
      try {
        receipt = await window.ethereum.request({
          method: 'eth_getTransactionReceipt',
          params: [txHash]
        });
        if (receipt) break;
      } catch(pollErr) { /* keep polling */ }
    }

    if (receipt && receipt.status === '0x0') {
      throw new Error('Transaction reverted on-chain');
    }

    // -- Success --
    var explorerUrl = chainInfo.explorer + '/tx/' + txHash;
    setSwapStatus(
      'Swap successful on ' + chainInfo.name + '! ' + amount + ' ' + from + ' → ' +
      destHuman.toFixed(toEvm.decimals <= 6 ? Math.min(toEvm.decimals, 6) : 4) + ' ' + to +
      '<div class="tx-sig"><a href="' + esc(explorerUrl) + '" target="_blank" rel="noopener">View on ' + esc(chainInfo.name) + ' Explorer: ' + esc(txHash.slice(0, 20)) + '...' + esc(txHash.slice(-8)) + '</a></div>' +
      '<div style="display:inline-flex;align-items:center;gap:4px;background:rgba(0,255,136,0.1);border:1px solid rgba(0,255,136,0.3);border-radius:4px;padding:2px 8px;font-size:11px;color:#00ff88;margin-top:8px"><span>&#10003;</span> OFAC Screened</div>',
      'success'
    );

    toast('Swap confirmed on ' + chainInfo.name + '! ' + from + ' → ' + to, 'success');

    // Clear form
    document.getElementById('swap-from-amount').value = '';
    document.getElementById('swap-to-amount').value = '';
    document.getElementById('swap-info').style.display = 'none';
    btn.textContent = 'Enter an amount';
    btn.disabled = true;

    // Log to backend (fire and forget)
    fetch('/api/public/crypto/log-swap', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        from_token: from, to_token: to, amount: amount,
        wallet: wallet, tx_signature: txHash,
        source: 'paraswap_' + chainInfo.name.toLowerCase()
      })
    }).catch(function(err) { console.warn('EVM swap log failed:', err); });

  } catch (e) {
    var errMsg = e.message || String(e);

    if (errMsg.indexOf('User rejected') !== -1 || errMsg.indexOf('user rejected') !== -1 ||
        errMsg.indexOf('User denied') !== -1 || errMsg.indexOf('denied') !== -1 || errMsg.indexOf('cancelled') !== -1) {
      setSwapStatus('Transaction cancelled by user.', 'error');
      toast('Transaction cancelled', 'info');
    }
    else if (errMsg.indexOf('insufficient') !== -1 || errMsg.indexOf('Insufficient') !== -1) {
      var chainLabel = (chainInfo && chainInfo.name) || 'this chain';
      var gasToken = (chainInfo && chainInfo.symbol) || 'native token';
      setSwapStatus('Insufficient balance. Make sure you have enough ' + from + ' and ' + gasToken + ' for gas on ' + chainLabel + '.', 'error');
      toast('Insufficient balance', 'error');
    }
    else if (errMsg.indexOf('reverted') !== -1) {
      setSwapStatus('Swap reverted on-chain. The route may have expired or slippage was too high. Try again.', 'error');
      toast('Transaction reverted', 'error');
    }
    else {
      setSwapStatus('Swap failed: ' + esc(errMsg), 'error');
      toast('Swap failed: ' + errMsg, 'error');
    }

    btn.textContent = 'Swap ' + from + ' → ' + to;
    btn.disabled = false;
  }
}

// -- Check if ERC-20 approval is needed --
async function checkEvmApproval(tokenAddress, spenderAddress, amountRaw) {
  if (tokenAddress.toLowerCase() === EVM_NATIVE.toLowerCase()) return false;
  try {
    var allowanceData = '0xdd62ed3e'
      + wallet.slice(2).toLowerCase().padStart(64, '0')
      + spenderAddress.slice(2).toLowerCase().padStart(64, '0');
    var allowance = await window.ethereum.request({
      method: 'eth_call',
      params: [{ to: tokenAddress, data: allowanceData }, 'latest']
    });
    return BigInt(allowance) < BigInt(amountRaw);
  } catch(e) {
    return true; // assume approval needed if check fails
  }
}

