/* ======================================
   MAXIA App — Initialization & Startup
   ====================================== */

// Auto-load enterprise data when page is shown
var _origShowPage = showPage;
showPage = function(name, btn) {
  _origShowPage(name, btn);
  if (name === 'enterprise' && !_entLoaded) {
    loadEnterprise();
  }
};

// ======================================
// ESCROW
// ======================================

var _escrowsCache = [];

function escrowStatusBadge(status) {
  var s = (status || '').toLowerCase();
  if (s === 'locked')   return '<span class="badge badge-orange">Locked</span>';
  if (s === 'released') return '<span class="badge badge-green">Released</span>';
  if (s === 'disputed') return '<span class="badge badge-red">Disputed</span>';
  if (s === 'timeout' || s === 'reclaimed') return '<span class="badge" style="background:rgba(245,158,11,.1);border-color:rgba(245,158,11,.2);color:var(--orange)">Timeout</span>';
  return '<span class="badge">' + esc(status || 'unknown') + '</span>';
}

// ======================================
// Escrow Multi-Chain (Solana + Base)
// ======================================

var _escrowChain = 'solana';
var _BASE_ESCROW = '0xBd31bB973183F8476d0C4cF57a92e648b130510C';
var _BASE_USDC = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913';
var _SOL_PROGRAM = '8ADNmAPDxuRvJPBp8dL9rq5jpcGtqAEx4JyZd1rXwBUY';

function setEscrowChain(chain) {
  _escrowChain = chain;
  var solTab = document.getElementById('esc-chain-sol');
  var baseTab = document.getElementById('esc-chain-base');
  var label = document.getElementById('esc-contract-label');
  var link = document.getElementById('esc-contract-link');
  var note = document.getElementById('esc-chain-note');
  var sellerLabel = document.getElementById('esc-seller-label');
  var baseSteps = document.getElementById('esc-base-steps');
  var seller = document.getElementById('esc-seller');

  if (chain === 'solana') {
    solTab.style.background = 'rgba(139,92,246,.15)'; solTab.style.color = 'var(--purple)';
    baseTab.style.background = 'rgba(255,255,255,.03)'; baseTab.style.color = 'var(--muted)';
    label.textContent = 'PROGRAM ID';
    link.href = 'https://solscan.io/account/' + _SOL_PROGRAM;
    link.textContent = _SOL_PROGRAM;
    note.textContent = 'Anchor Smart Contract';
    sellerLabel.textContent = 'Seller Wallet (Solana)';
    seller.placeholder = 'Solana wallet address (base58)...';
    baseSteps.style.display = 'none';
  } else {
    baseTab.style.background = 'rgba(59,130,246,.15)'; baseTab.style.color = 'var(--blue)';
    solTab.style.background = 'rgba(255,255,255,.03)'; solTab.style.color = 'var(--muted)';
    label.textContent = 'CONTRACT';
    link.href = 'https://basescan.org/address/' + _BASE_ESCROW;
    link.textContent = _BASE_ESCROW;
    note.textContent = 'Solidity on Base L2 — gas ~$0.01';
    sellerLabel.textContent = 'Seller Wallet (EVM 0x...)';
    seller.placeholder = '0x... EVM wallet address';
    baseSteps.style.display = 'block';
  }
  updateEscrowPreview();
}

function updateEscrowPreview() {
  var amount = parseFloat(document.getElementById('esc-amount').value) || 100;
  var bps = amount >= 5000 ? 10 : amount >= 500 ? 50 : 150;
  var tierName = bps === 10 ? 'WHALE 0.1%' : bps === 50 ? 'GOLD 0.5%' : 'BRONZE 1.5%';
  var fee = amount * bps / 10000;
  var sellerGets = amount - fee;
  var gas = _escrowChain === 'base' ? '~$0.01' : '~$0.001';
  document.getElementById('esc-prev-fee').textContent = '$' + fee.toFixed(2) + ' (' + tierName + ')';
  document.getElementById('esc-prev-seller').textContent = '$' + sellerGets.toFixed(2);
  document.getElementById('esc-prev-gas').textContent = gas;
}

async function createEscrowBase() {
  if (!window.ethereum) { toast('Install MetaMask to use Base escrow', 'error'); return; }

  var seller = document.getElementById('esc-seller').value.trim();
  var amount = parseFloat(document.getElementById('esc-amount').value);
  var service = document.getElementById('esc-service').value.trim() || 'AI Service';

  if (!seller || !seller.startsWith('0x') || seller.length !== 42) { toast('Enter a valid EVM address (0x...)', 'error'); return; }
  if (!amount || amount <= 0) { toast('Enter a valid USDC amount', 'error'); return; }

  var btn = document.getElementById('btn-create-escrow');
  var result = document.getElementById('esc-create-result');
  btn.disabled = true;

  try {
    var accounts = await window.ethereum.request({method: 'eth_requestAccounts'});
    var buyer = accounts[0];

    // Ensure Base chain
    try {
      await window.ethereum.request({method: 'wallet_switchEthereumChain', params: [{chainId: '0x2105'}]});
    } catch(switchErr) {
      if (switchErr.code === 4902) {
        await window.ethereum.request({method: 'wallet_addEthereumChain', params: [{chainId:'0x2105',chainName:'Base',nativeCurrency:{name:'ETH',symbol:'ETH',decimals:18},rpcUrls:['https://mainnet.base.org'],blockExplorerUrls:['https://basescan.org']}]});
      } else { throw switchErr; }
    }

    var amountRaw = '0x' + (BigInt(Math.round(amount * 1e6))).toString(16);

    // Step 1: Approve USDC
    btn.textContent = 'Step 1/2: Approving USDC...';
    document.getElementById('esc-step1-icon').style.background = 'rgba(59,130,246,.3)';
    var approveData = '0x095ea7b3' +
      _BASE_ESCROW.toLowerCase().replace('0x','').padStart(64,'0') +
      BigInt(Math.round(amount * 1e6)).toString(16).padStart(64,'0');

    var approveTx = await window.ethereum.request({method: 'eth_sendTransaction', params: [{
      from: buyer, to: _BASE_USDC, data: approveData, chainId: '0x2105',
    }]});
    result.innerHTML = '<div style="color:var(--cyan)">Approve TX: ' + approveTx.substring(0,14) + '... Waiting...</div>';

    // Wait for approve confirmation
    await _waitTx(approveTx);
    document.getElementById('esc-step1-icon').style.background = 'rgba(16,185,129,.3)';
    document.getElementById('esc-step1-icon').textContent = '\u2713';

    // Step 2: Lock Escrow
    btn.textContent = 'Step 2/2: Locking USDC...';
    document.getElementById('esc-step2-icon').style.background = 'rgba(59,130,246,.3)';

    // Encode lockEscrow(address seller, uint256 amount, string serviceId)
    // Function selector for lockEscrow(address,uint256,string)
    var lockSig = '0x' + _keccak256('lockEscrow(address,uint256,string)').substring(0,8);
    var sellerPad = seller.toLowerCase().replace('0x','').padStart(64,'0');
    var amountPad = BigInt(Math.round(amount * 1e6)).toString(16).padStart(64,'0');
    // String offset (3 * 32 = 96 = 0x60)
    var strOffset = (96).toString(16).padStart(64,'0');
    // String length
    var strLen = service.length.toString(16).padStart(64,'0');
    // String data (padded to 32 bytes)
    var strHex = '';
    for (var i = 0; i < service.length; i++) strHex += service.charCodeAt(i).toString(16).padStart(2,'0');
    strHex = strHex.padEnd(64,'0');

    var lockData = lockSig + sellerPad + amountPad + strOffset + strLen + strHex;

    var lockTx = await window.ethereum.request({method: 'eth_sendTransaction', params: [{
      from: buyer, to: _BASE_ESCROW, data: lockData, chainId: '0x2105',
    }]});

    await _waitTx(lockTx);
    document.getElementById('esc-step2-icon').style.background = 'rgba(16,185,129,.3)';
    document.getElementById('esc-step2-icon').textContent = '\u2713';

    result.innerHTML = '<div style="color:var(--green)">Escrow created on Base! <a href="https://basescan.org/tx/' + lockTx + '" target="_blank" style="color:var(--cyan)">View on Basescan</a></div>';
    toast('Escrow created: $' + amount.toFixed(2) + ' USDC locked on Base', 'success');
    loadEscrows();

  } catch(e) {
    result.innerHTML = '<div style="color:var(--red)">' + esc(e.message || 'Transaction failed') + '</div>';
  }
  btn.disabled = false;
  btn.textContent = 'Lock USDC in Escrow';
  document.getElementById('esc-step1-icon').textContent = '1';
  document.getElementById('esc-step2-icon').textContent = '2';
  document.getElementById('esc-step1-icon').style.background = 'rgba(255,255,255,.06)';
  document.getElementById('esc-step2-icon').style.background = 'rgba(255,255,255,.06)';
}

async function _waitTx(txHash) {
  for (var i = 0; i < 60; i++) {
    await new Promise(function(r){setTimeout(r, 2000);});
    try {
      var receipt = await window.ethereum.request({method: 'eth_getTransactionReceipt', params: [txHash]});
      if (receipt && receipt.status) return receipt;
    } catch(e) {}
  }
  throw new Error('Transaction timeout');
}

function _keccak256(str) {
  // Pre-computed keccak256 selectors for MaxiaEscrow.sol functions
  var selectors = {
    'lockEscrow(address,uint256,string)': 'd27e71da',
    'confirmDelivery(bytes32)': '74950ffd',
    'autoRefund(bytes32)': '40975025',
    'openDispute(bytes32)': 'f08ef6cb',
    'approve(address,uint256)': '095ea7b3',
  };
  return selectors[str] || '00000000';
}

async function loadEscrows() {
  if (!wallet) {
    document.getElementById('escrow-body').innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">Connect your wallet to view escrows</td></tr>';
    // Reset stats
    document.getElementById('esc-active').textContent = '0';
    document.getElementById('esc-total-vol').textContent = '$0';
    document.getElementById('esc-released').textContent = '0';
    document.getElementById('esc-disputed').textContent = '0';
    return;
  }

  var body = document.getElementById('escrow-body');
  body.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">Loading escrows...</td></tr>';

  var data = await api('/api/escrow/list');
  if (!data) {
    body.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">Could not load escrows</td></tr>';
    return;
  }

  var escrows = data.escrows || data || [];
  _escrowsCache = escrows;

  // Update stats
  var active = 0, totalVol = 0, released = 0, disputed = 0;
  escrows.forEach(function(e) {
    var s = (e.status || '').toLowerCase();
    var amt = parseFloat(e.amount_usdc || e.amount || 0);
    totalVol += amt;
    if (s === 'locked') active++;
    if (s === 'released') released++;
    if (s === 'disputed') disputed++;
  });
  document.getElementById('esc-active').textContent = active;
  document.getElementById('esc-total-vol').textContent = '$' + totalVol.toFixed(2);
  document.getElementById('esc-released').textContent = released;
  document.getElementById('esc-disputed').textContent = disputed;

  if (!Array.isArray(escrows) || escrows.length === 0) {
    body.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">No escrows found</td></tr>';
    return;
  }

  body.innerHTML = escrows.map(function(e) {
    var id = e.escrowId || e.escrow_id || e.id || '—';
    var shortId = String(id).length > 12 ? String(id).slice(0, 8) + '...' : id;
    var seller = e.seller_wallet || e.seller || '—';
    var shortSeller = seller.length > 12 ? seller.slice(0, 6) + '..' + seller.slice(-4) : seller;
    var amount = parseFloat(e.amount_usdc || e.amount || 0).toFixed(2);
    var status = (e.status || 'unknown').toLowerCase();
    var timeout = e.timeout_hours || e.timeout || '—';
    var created = e.created_at || e.created || '—';
    if (created !== '—') {
      try { created = new Date(created).toLocaleDateString(); } catch(ex) {}
    }

    var actions = '';
    if (status === 'locked') {
      actions = '<button class="btn-green btn-sm" onclick="confirmDelivery(\'' + esc(id) + '\')" style="margin-right:4px">Confirm</button>' +
                '<button class="btn-outline" onclick="reclaimTimeout(\'' + esc(id) + '\')" style="font-size:12px;padding:6px 12px">Reclaim</button>';
    }

    return '<tr>' +
      '<td style="font-family:\'JetBrains Mono\',monospace;font-size:13px" title="' + esc(id) + '">' + esc(shortId) + '</td>' +
      '<td style="font-family:\'JetBrains Mono\',monospace;font-size:13px" title="' + esc(seller) + '">' + esc(shortSeller) + '</td>' +
      '<td style="font-family:\'JetBrains Mono\',monospace;color:var(--green)">$' + amount + '</td>' +
      '<td>' + escrowStatusBadge(status) + '</td>' +
      '<td>' + esc(timeout) + 'h</td>' +
      '<td style="font-size:13px;color:var(--muted)">' + esc(created) + '</td>' +
      '<td>' + actions + '</td>' +
    '</tr>';
  }).join('');
}

async function createEscrow() {
  // Route to Base or Solana based on selected chain
  if (_escrowChain === 'base') { return createEscrowBase(); }
  if (!requireWalletFor('lock escrow')) return;

  var seller = document.getElementById('esc-seller').value.trim();
  var amount = parseFloat(document.getElementById('esc-amount').value);
  var service = document.getElementById('esc-service').value.trim();
  var timeout = parseInt(document.getElementById('esc-timeout').value, 10);

  if (!seller || seller.length < 32) { toast('Enter a valid seller wallet address', 'error'); return; }
  if (!amount || amount <= 0) { toast('Enter a valid USDC amount', 'error'); return; }
  if (!service) { toast('Enter a service description', 'error'); return; }
  if (!timeout || timeout < 1 || timeout > 168) { toast('Timeout must be 1-168 hours', 'error'); return; }

  var btn = document.getElementById('btn-create-escrow');
  var result = document.getElementById('esc-create-result');
  btn.disabled = true;

  try {
    // Step 1: Get escrow wallet address from backend
    btn.textContent = 'Step 1/2: Getting escrow address...';
    result.innerHTML = '<div class="swap-status pending"><span class="swap-spinner"></span> Fetching escrow wallet...</div>';
    var info = await api('/api/escrow/info');
    if (!info || !info.solana) throw new Error('Cannot fetch escrow info');
    // Escrow wallet = the program authority wallet that holds USDC
    var escrowWallet = info.solana.escrow_wallet;
    if (!escrowWallet) throw new Error('Escrow wallet not configured on server');

    // Step 2: Send USDC to escrow wallet via Phantom
    btn.textContent = 'Step 2/2: Sign in wallet...';
    result.innerHTML = '<div class="swap-status pending"><span class="swap-spinner"></span> Sign the USDC transfer in your wallet ($' + amount.toFixed(2) + ' to escrow)...</div>';
    var txSignature = await sendSolanaUSDC(escrowWallet, amount);
    if (!txSignature) throw new Error('Transaction cancelled or failed');

    result.innerHTML = '<div class="swap-status pending"><span class="swap-spinner"></span> USDC sent! Waiting for on-chain confirmation (~10s)...</div>';
    btn.textContent = 'Verifying...';

    // Wait for Solana finalization (~6-12s for finalized commitment)
    await new Promise(function(r) { setTimeout(r, 10000); });

    // Step 3: Register escrow on backend (backend verifies tx on-chain)
    var data = await api('/api/escrow/create', {
      method: 'POST',
      body: JSON.stringify({
        seller_wallet: seller,
        amount_usdc: amount,
        service_id: service,
        timeout_hours: timeout,
        tx_signature: txSignature
      })
    });

    if (data && (data.escrowId || data.escrow_id || data.id)) {
      var eid = data.escrowId || data.escrow_id || data.id;
      result.innerHTML = '<div class="swap-status success">Escrow created! $' + amount.toFixed(2) + ' USDC locked.<br><span style="font-family:\'JetBrains Mono\',monospace;font-size:11px">ID: ' + esc(eid) + '</span><br><a href="https://solscan.io/tx/' + esc(txSignature) + '" target="_blank" style="color:var(--cyan);font-size:11px">View on Solscan</a></div>';
      toast('Escrow created: $' + amount.toFixed(2) + ' USDC locked', 'success');
      document.getElementById('esc-seller').value = '';
      document.getElementById('esc-amount').value = '';
      document.getElementById('esc-service').value = '';
      document.getElementById('esc-timeout').value = '24';
      loadEscrows();
    } else {
      var errMsg = (data && data.detail) || (data && data.error) || 'Failed to create escrow';
      result.innerHTML = '<div class="swap-status error">' + esc(errMsg) + '<br><span style="font-size:11px;color:var(--muted)">TX was sent: <a href="https://solscan.io/tx/' + esc(txSignature) + '" target="_blank" style="color:var(--cyan)">' + esc(txSignature.substring(0,20)) + '...</a></span></div>';
      toast(errMsg, 'error');
    }
  } catch(e) {
    result.innerHTML = '<div class="swap-status error">' + esc(e.message || 'Transaction failed') + '</div>';
    toast(e.message || 'Escrow creation failed', 'error');
  }
  btn.disabled = false;
  btn.textContent = 'Lock USDC in Escrow';
}

async function confirmDelivery(escrowId) {
  if (!requireWalletFor('confirm delivery')) return;
  if (!confirm('Confirm delivery? This will release USDC to the seller.')) return;

  toast('Confirming delivery...', 'info');
  var data = await api('/api/escrow/confirm', {
    method: 'POST',
    body: JSON.stringify({ escrow_id: escrowId })
  });

  if (data && !data.error) {
    toast('Delivery confirmed — USDC released to seller', 'success');
    loadEscrows();
  } else {
    var errMsg = (data && data.detail) || (data && data.error) || 'Failed to confirm delivery';
    toast(errMsg, 'error');
  }
}

async function reclaimTimeout(escrowId) {
  if (!requireWalletFor('reclaim escrow')) return;
  if (!confirm('Reclaim funds? This is only possible after the timeout has expired.')) return;

  toast('Reclaiming escrow...', 'info');
  var data = await api('/api/escrow/reclaim', {
    method: 'POST',
    body: JSON.stringify({ escrow_id: escrowId })
  });

  if (data && !data.error) {
    toast('Escrow reclaimed — USDC returned to your wallet', 'success');
    loadEscrows();
  } else {
    var errMsg = (data && data.detail) || (data && data.error) || 'Failed to reclaim escrow';
    toast(errMsg, 'error');
  }
}

// Init
initFromHash();
loadSwapTokens();
// Auto-reconnect Phantom si deja autorise
(async function() {
  try {
    if (window.solana && window.solana.isPhantom) {
      var resp = await window.solana.connect({ onlyIfTrusted: true });
      if (resp && resp.publicKey) {
        wallet = resp.publicKey.toString();
        walletType = 'phantom';
        if (typeof connectedWallets !== 'undefined') connectedWallets.solana = { address: wallet, provider: walletType };
        updateWalletDisplay();
        // Refresh current page data with wallet context
        if (typeof loadEscrows === 'function') loadEscrows();
        if (typeof getSwapQuoteDebounced === 'function') getSwapQuoteDebounced();
      }
    }
  } catch(e) { /* user hasn't approved yet — ignore */ }
})();
(function() {
  var btn = document.getElementById('notif-sound-btn');
  if (btn) btn.textContent = _notifSoundEnabled ? 'ON' : 'OFF';
})();

// === HOME — Live prices + greeting + activity ===
(function(){
  var el=document.getElementById('home-time');
  if(el){var h=new Date().getHours();el.textContent=(h<12?'Good morning':h<18?'Good afternoon':'Good evening')+' — '+new Date().toLocaleString('en-US',{weekday:'long',hour:'2-digit',minute:'2-digit'});}
})();

async function updateHomePrices(){
  try{var r=await fetch('/oracle/price/live/SOL?mode=hft');var d=await r.json();var e=document.getElementById('hp-sol');if(e)e.textContent='$'+d.price.toFixed(2);}catch(e){}
  try{var r=await fetch('/oracle/price/live/ETH?mode=hft');var d=await r.json();var e=document.getElementById('hp-eth');if(e)e.textContent='$'+d.price.toFixed(2);}catch(e){}
  try{var r=await fetch('/oracle/price/live/BTC?mode=hft');var d=await r.json();var e=document.getElementById('hp-btc');if(e)e.textContent='$'+d.price.toFixed(0);}catch(e){}
  try{var r=await fetch('/api/public/stocks');var d=await r.json();(d.stocks||[]).forEach(function(s){var e=document.getElementById('hp-'+s.symbol.toLowerCase());if(e&&s.price_usd>0)e.textContent='$'+s.price_usd.toFixed(2);});}catch(e){}
}
updateHomePrices();setInterval(updateHomePrices,5000);

(function(){
  var el=document.getElementById('home-feed');if(!el)return;
  var msgs=['Platform active — 15 chains, swap on 7','AI chat ready — try the chat button','25 tokenized stocks live','6-source oracle with Pyth + Chainlink','DCA, Grid, Sniper bots — real Jupiter swaps'];
  var i=0;setInterval(function(){el.textContent=msgs[i%msgs.length];i++;},4000);
  fetch('/api/public/marketplace-stats').then(function(r){return r.json()}).then(function(d){
    if(d.registered_agents>1)msgs.push(d.registered_agents+' agents registered');
    if(d.services_listed>0)msgs.push(d.services_listed+' AI services available');
  }).catch(function(){});
})();
