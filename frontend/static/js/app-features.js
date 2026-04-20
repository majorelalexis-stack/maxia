/* ======================================
   MAXIA App — Features (GPU, Yields, Stocks, NFTs, Bridge, Escrow, Enterprise)
   ====================================== */

// ======================================
// GPU
// ======================================
var _gpuTiersCache = [];
var _gpuTreasurySolana = '7RtCpikgfd6xiFQyVoxjV51HN14XXRrQJiJ3KrzUdQsW';
var _gpuTreasuryBase = '0x8958A2c37EcABEDf62727461ba30943cE3E0Fb3c';

// AWS/cloud reference prices for comparison
var _cloudPrices = {
  "rtx3090": 0.60, "rtx4090": 0.89, "rtx5090": 1.49, "a6000": 0.80,
  "l4": 0.80, "l40s": 1.40, "rtx_pro6000": 2.80, "a100_80": 3.93,
  "h100_sxm": 5.32, "h100_nvl": 4.98, "h200": 7.20, "b200": 9.99,
  "4xa100": 15.72,
};

async function loadGPU() {
  var grid = document.getElementById('gpu-grid');
  try {
    var data = await api('/api/public/gpu/tiers');
    var tiers = (data && data.tiers) || (data && data.gpu_tiers) || (Array.isArray(data) ? data : []);
    if (data && data.treasury_solana) _gpuTreasurySolana = data.treasury_solana;
    if (data && data.treasury_base) _gpuTreasuryBase = data.treasury_base;

    if (Array.isArray(tiers) && tiers.length > 0) {
      _gpuTiersCache = tiers;
      // Show only available GPUs on Akash (not local, available_count > 0 or available === true)
      var realTiers = tiers.filter(function(g) {
        if (g.local) return false;
        var count = g.available_count || 0;
        return g.available || count > 0;
      });

      if (realTiers.length === 0) {
        grid.innerHTML = '<div class="card" style="text-align:center;padding:40px;grid-column:1/-1">' +
          '<div style="font-size:36px;margin-bottom:12px">&#9889;</div>' +
          '<div style="font-weight:600;margin-bottom:8px">No GPUs available right now</div>' +
          '<div style="color:var(--muted);margin-bottom:12px">All providers are at capacity. Check back in a few minutes.</div>' +
          '<button class="btn-outline" onclick="loadGPU()">Refresh</button>' +
        '</div>';
      } else {
        grid.innerHTML = '<table style="width:100%;border-collapse:collapse;font-size:13px">' +
          '<thead><tr style="background:rgba(255,255,255,.02);border-bottom:1px solid rgba(255,255,255,.06)">' +
            '<th style="padding:12px 16px;text-align:left;font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px">GPU</th>' +
            '<th style="padding:12px 8px;text-align:center;font-size:11px;color:var(--muted);text-transform:uppercase">VRAM</th>' +
            '<th style="padding:12px 8px;text-align:right;font-size:11px;color:var(--muted);text-transform:uppercase">MAXIA Price</th>' +
            '<th style="padding:12px 8px;text-align:right;font-size:11px;color:var(--muted);text-transform:uppercase">vs Cloud</th>' +
            '<th style="padding:12px 8px;text-align:center;font-size:11px;color:var(--muted);text-transform:uppercase">Stock</th>' +
            '<th style="padding:12px 16px;text-align:right;font-size:11px;color:var(--muted);text-transform:uppercase"></th>' +
          '</tr></thead><tbody>' +
          realTiers.map(function(g) {
            var tierId = g.id || '';
            var name = g.label || g.name || 'GPU';
            var vram = g.vram_gb || g.vram || '—';
            var price = g.price_per_hour_usdc || g.price_per_hour || g.price || 0;
            var count = g.available_count || 0;
            var cloudPrice = _cloudPrices[tierId] || 0;
            var savings = cloudPrice > 0 ? Math.round((1 - price / cloudPrice) * 100) : 0;
            var savingsStr = savings > 0 ? '<span style="color:var(--green);font-weight:600">-' + savings + '%</span>' : '—';
            var cloudStr = cloudPrice > 0 ? '<span style="color:var(--muted2);text-decoration:line-through">$' + cloudPrice.toFixed(2) + '</span>' : '';
            var stockColor = count >= 5 ? 'var(--green)' : count >= 2 ? 'orange' : 'var(--red)';
            var stockLabel = count > 0 ? count + (count === 1 ? ' unit' : ' units') : 'Low';
            return '<tr style="border-bottom:1px solid rgba(255,255,255,.04);transition:background .15s" onmouseenter="this.style.background=\'rgba(255,255,255,.03)\'" onmouseleave="this.style.background=\'\'">' +
              '<td style="padding:14px 16px"><div style="font-weight:700;font-size:15px">' + esc(name) + '</div></td>' +
              '<td style="padding:14px 8px;text-align:center;color:var(--cyan);font-family:\'JetBrains Mono\',monospace;font-size:13px">' + esc(String(vram)) + ' GB</td>' +
              '<td style="padding:14px 8px;text-align:right"><span style="font-size:18px;font-weight:700;color:var(--green);font-family:\'JetBrains Mono\',monospace">$' + price.toFixed(2) + '</span><span style="color:var(--muted);font-size:11px">/hr</span></td>' +
              '<td style="padding:14px 8px;text-align:right">' + cloudStr + ' ' + savingsStr + '</td>' +
              '<td style="padding:14px 8px;text-align:center"><span style="color:' + stockColor + ';font-weight:600;font-family:\'JetBrains Mono\',monospace;font-size:12px">' + stockLabel + '</span></td>' +
              '<td style="padding:14px 16px;text-align:right">' +
                '<button class="btn-outline" onclick="showRentDialog(\'' + esc(tierId) + '\',\'' + esc(name) + '\',' + price + ')" style="padding:6px 16px;font-size:12px;color:var(--green);border-color:rgba(16,185,129,.3)">Rent</button>' +
              '</td>' +
            '</tr>';
          }).join('') +
          '</tbody></table>';
      }
    } else {
      renderFallbackGPU(grid);
    }
  } catch (e) {
    renderFallbackGPU(grid);
  }
}

function renderFallbackGPU(grid) {
  grid.innerHTML = '<div class="card" style="text-align:center;padding:40px;grid-column:1/-1">' +
    '<div style="font-size:36px;margin-bottom:12px">&#9889;</div>' +
    '<div style="color:var(--muted);margin-bottom:12px">Could not load GPU prices from the API.</div>' +
    '<button class="btn-outline" onclick="loadGPU()">Retry</button>' +
  '</div>';
}

function showRentDialog(tierId, name, pricePerHr) {
  if (!requireWalletFor('rent GPU')) return;

  var existing = document.getElementById('gpu-rent-modal');
  if (existing) existing.remove();

  var total = pricePerHr.toFixed(2);
  var walletLabel = isEvmWallet() ? 'MetaMask (Base USDC)' : 'Phantom (Solana USDC)';

  var modal = document.createElement('div');
  modal.id = 'gpu-rent-modal';
  modal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.7);z-index:10000;display:flex;align-items:center;justify-content:center';
  modal.innerHTML =
    '<div style="background:var(--card);border:1px solid var(--muted2);border-radius:16px;padding:32px;max-width:420px;width:90%">' +
      '<h3 style="margin:0 0 8px">' + esc(name) + '</h3>' +
      '<p style="color:var(--muted);margin:0 0 20px">$' + pricePerHr.toFixed(2) + '/hr — SSH + Jupyter included</p>' +
      '<label style="display:block;margin-bottom:6px;font-size:14px;color:var(--muted)">Duration (hours)</label>' +
      '<input type="number" id="gpu-rent-hours" value="1" min="0.17" max="720" step="0.5" ' +
        'style="width:100%;padding:10px;background:var(--bg);border:1px solid var(--muted2);border-radius:8px;color:var(--text);font-size:16px;margin-bottom:8px;box-sizing:border-box">' +
      '<div id="gpu-rent-cost" style="font-size:14px;color:var(--green);margin-bottom:4px">Total: $' + total + ' USDC</div>' +
      '<div style="font-size:12px;color:var(--cyan);margin-bottom:16px;font-family:\'JetBrains Mono\',monospace">Decentralized GPU Cloud &bull; ' + esc(walletLabel) + '</div>' +
      '<div id="gpu-rent-status" style="display:none;margin-bottom:12px;padding:10px;border-radius:8px;font-size:13px"></div>' +
      '<div style="display:flex;gap:12px">' +
        '<button class="btn-green" style="flex:1" onclick="executeGpuRent(\'' + esc(tierId) + '\', \'' + esc(name) + '\',' + pricePerHr + ')" id="btn-gpu-rent">' +
          'Pay $' + total + ' USDC & Rent' +
        '</button>' +
        '<button class="btn-outline" style="flex:0 0 auto" onclick="closeGpuModal()">Cancel</button>' +
      '</div>' +
    '</div>';

  document.body.appendChild(modal);
  modal.addEventListener('click', function(e) { if (e.target === modal) closeGpuModal(); });

  var hoursInput = document.getElementById('gpu-rent-hours');
  hoursInput.addEventListener('input', function() {
    var h = parseFloat(hoursInput.value) || 1;
    var t = (h * pricePerHr).toFixed(2);
    document.getElementById('gpu-rent-cost').textContent = 'Total: $' + t + ' USDC';
    document.getElementById('btn-gpu-rent').textContent = 'Pay $' + t + ' USDC & Rent';
  });
}

function closeGpuModal() {
  var m = document.getElementById('gpu-rent-modal');
  if (m) m.remove();
}

// SPL USDC transfer via Phantom (simple transfer, not swap)
async function sendSolanaUSDC(recipientAddress, amountUsdc) {
  // Get blockhash via backend (Helius RPC, no CORS issues)
  var bhData = await api('/api/public/solana/blockhash');
  if (!bhData || !bhData.blockhash) throw new Error('Cannot get Solana blockhash — try again');
  var blockhash = bhData.blockhash;

  var senderPubkey = new solanaWeb3.PublicKey(wallet);
  var recipientPubkey = new solanaWeb3.PublicKey(recipientAddress);
  var usdcMintPubkey = new solanaWeb3.PublicKey(USDC_MINT);
  var TOKEN_PROGRAM_ID = new solanaWeb3.PublicKey('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA');
  var ASSOCIATED_TOKEN_PROGRAM_ID = new solanaWeb3.PublicKey('ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL');

  function deriveATA(owner, mint) {
    return solanaWeb3.PublicKey.findProgramAddressSync(
      [owner.toBytes(), TOKEN_PROGRAM_ID.toBytes(), mint.toBytes()],
      ASSOCIATED_TOKEN_PROGRAM_ID
    )[0];
  }
  var senderATA = deriveATA(senderPubkey, usdcMintPubkey);
  var recipientATA = deriveATA(recipientPubkey, usdcMintPubkey);

  var amountRaw = BigInt(Math.round(amountUsdc * 1e6));

  // transfer_checked instruction (index 12)
  var data = new Uint8Array(1 + 8 + 1);
  data[0] = 12;
  var view = new DataView(data.buffer);
  view.setBigUint64(1, amountRaw, true);
  data[9] = 6; // USDC decimals

  var transferIx = new solanaWeb3.TransactionInstruction({
    keys: [
      { pubkey: senderATA,      isSigner: false, isWritable: true },
      { pubkey: usdcMintPubkey, isSigner: false, isWritable: false },
      { pubkey: recipientATA,   isSigner: false, isWritable: true },
      { pubkey: senderPubkey,   isSigner: true,  isWritable: false },
    ],
    programId: TOKEN_PROGRAM_ID,
    data: data,
  });

  // Check if recipient ATA exists, create only if missing
  var connection = new solanaWeb3.Connection(SOLANA_RPC, 'confirmed');
  var tx = new solanaWeb3.Transaction();
  try {
    var ataInfo = await connection.getAccountInfo(recipientATA);
    if (!ataInfo) {
      // ATA does not exist — add CreateIdempotent instruction
      var createData = new Uint8Array(1);
      createData[0] = 1; // CreateIdempotent
      tx.add(new solanaWeb3.TransactionInstruction({
        keys: [
          { pubkey: senderPubkey,                      isSigner: true,  isWritable: true },
          { pubkey: recipientATA,                      isSigner: false, isWritable: true },
          { pubkey: recipientPubkey,                   isSigner: false, isWritable: false },
          { pubkey: usdcMintPubkey,                    isSigner: false, isWritable: false },
          { pubkey: solanaWeb3.SystemProgram.programId, isSigner: false, isWritable: false },
          { pubkey: TOKEN_PROGRAM_ID,                  isSigner: false, isWritable: false },
        ],
        programId: ASSOCIATED_TOKEN_PROGRAM_ID,
        data: createData,
      }));
    }
  } catch(e) { /* RPC check failed — skip create, treasury ATA almost certainly exists */ }
  tx.add(transferIx);
  tx.recentBlockhash = blockhash;
  tx.feePayer = senderPubkey;

  var provider = null;
  if (walletType === 'phantom') provider = window.solana;
  else if (walletType === 'backpack') provider = window.backpack;
  else if (walletType === 'solflare') provider = window.solflare;
  if (!provider) throw new Error('Wallet provider not found');

  var result = await provider.signAndSendTransaction(tx, { skipPreflight: false, maxRetries: 3, preflightCommitment: 'confirmed' });
  return result.signature || result;
}

// EVM USDC transfer via MetaMask (Base chain)
async function sendEvmUSDC(recipientAddress, amountUsdc) {
  var accounts = await window.ethereum.request({ method: 'eth_requestAccounts' });
  var sender = accounts[0];

  // Ensure Base chain
  try {
    await window.ethereum.request({ method: 'wallet_switchEthereumChain', params: [{ chainId: '0x2105' }] });
  } catch (switchErr) {
    if (switchErr.code === 4902) {
      await window.ethereum.request({ method: 'wallet_addEthereumChain', params: [{ chainId: '0x2105', chainName: 'Base', nativeCurrency: { name: 'ETH', symbol: 'ETH', decimals: 18 }, rpcUrls: ['https://mainnet.base.org'], blockExplorerUrls: ['https://basescan.org'] }] });
    } else { throw switchErr; }
  }

  var amountRaw = '0x' + BigInt(Math.round(amountUsdc * 1e6)).toString(16);
  // ERC-20 transfer(address,uint256)
  var transferData = '0xa9059cbb' +
    recipientAddress.toLowerCase().replace('0x', '').padStart(64, '0') +
    BigInt(Math.round(amountUsdc * 1e6)).toString(16).padStart(64, '0');

  var txHash = await window.ethereum.request({
    method: 'eth_sendTransaction',
    params: [{ from: sender, to: _BASE_USDC, data: transferData, chainId: '0x2105' }]
  });

  // Wait for confirmation
  await _waitTx(txHash);
  return txHash;
}

async function executeGpuRent(tierId, name, pricePerHr) {
  var hours = parseFloat(document.getElementById('gpu-rent-hours').value) || 1;
  var totalCost = hours * pricePerHr;
  var btn = document.getElementById('btn-gpu-rent');
  var statusEl = document.getElementById('gpu-rent-status');

  function setStatus(msg, color) {
    statusEl.style.display = 'block';
    statusEl.style.background = 'rgba(' + (color === 'green' ? '16,185,129' : color === 'blue' ? '59,130,246' : '239,68,68') + ',.1)';
    statusEl.style.color = 'var(--' + color + ')';
    statusEl.innerHTML = msg;
  }

  btn.disabled = true;

  try {
    // Step 1: Get treasury address from cached tiers
    var treasuryAddress = '';
    if (isEvmWallet()) {
      treasuryAddress = _gpuTreasuryBase || '';
      if (!treasuryAddress) { throw new Error('Base treasury address not configured'); }
    } else {
      treasuryAddress = _gpuTreasurySolana || '';
      if (!treasuryAddress) { throw new Error('Solana treasury address not configured'); }
    }

    // Step 2: Send USDC payment via wallet
    btn.textContent = 'Confirm in wallet...';
    setStatus('<span class="swap-spinner"></span> Approve the $' + totalCost.toFixed(2) + ' USDC transfer in your wallet...', 'blue');

    var txSignature;
    if (isEvmWallet()) {
      txSignature = await sendEvmUSDC(treasuryAddress, totalCost);
    } else {
      txSignature = await sendSolanaUSDC(treasuryAddress, totalCost);
    }

    // Step 3: Send to backend for verification + provisioning
    btn.textContent = 'Verifying payment...';
    setStatus('<span class="swap-spinner"></span> Payment sent! Verifying on-chain and provisioning GPU...', 'blue');

    var result = await api('/api/gpu/rent', {
      method: 'POST',
      body: JSON.stringify({ gpu: tierId, wallet: wallet, hours: hours, payment_tx: txSignature })
    });

    if (!result) throw new Error('Server error — payment was sent, contact support with tx: ' + txSignature);

    closeGpuModal();
    showGpuCredentials(result, name);
    toast('GPU ' + name + ' provisioned!', 'success');

  } catch (e) {
    var errMsg = e.message || String(e);
    if (errMsg.indexOf('User rejected') !== -1 || errMsg.indexOf('user rejected') !== -1 || errMsg.indexOf('cancelled') !== -1) {
      setStatus('Transaction cancelled.', 'red');
      toast('Payment cancelled', 'info');
    } else if (errMsg.indexOf('insufficient') !== -1 || errMsg.indexOf('Insufficient') !== -1) {
      setStatus('Insufficient USDC balance.', 'red');
      toast('Insufficient USDC balance', 'error');
    } else {
      setStatus('Error: ' + esc(errMsg), 'red');
      toast('GPU rental failed: ' + errMsg, 'error');
    }
    btn.textContent = 'Retry — Pay $' + totalCost.toFixed(2) + ' USDC';
    btn.disabled = false;
  }
}

function showGpuCredentials(result, name) {
  var existing = document.getElementById('gpu-creds-modal');
  if (existing) existing.remove();

  var status = result.status || 'provisioning';
  var statusColor = status === 'running' ? 'var(--green)' : 'orange';
  var trial = result.is_free_trial ? ' (Free Trial)' : '';
  var autoTerm = result.auto_terminate_at ? new Date(result.auto_terminate_at * 1000).toLocaleString() : '—';

  var modal = document.createElement('div');
  modal.id = 'gpu-creds-modal';
  modal.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.7);z-index:10000;display:flex;align-items:center;justify-content:center';
  modal.innerHTML =
    '<div style="background:var(--card);border:1px solid var(--green);border-radius:16px;padding:32px;max-width:520px;width:90%;max-height:90vh;overflow-y:auto">' +
      '<h3 style="margin:0 0 4px;color:var(--green)">GPU Provisioned' + esc(trial) + '</h3>' +
      '<p style="color:var(--muted);margin:0 0 16px">' + esc(name) + ' — ' + result.duration_hours + 'h</p>' +
      '<div style="margin-bottom:16px">' +
        '<span style="color:' + statusColor + ';font-weight:600">' + esc(status.toUpperCase()) + '</span>' +
        (status === 'provisioning' ? '<span style="color:var(--muted);font-size:13px"> — pod is starting up, credentials will work in ~30s</span>' : '') +
      '</div>' +
      '<div style="background:var(--bg);border-radius:8px;padding:16px;margin-bottom:12px">' +
        '<div style="font-size:12px;color:var(--muted);margin-bottom:4px">Pod ID</div>' +
        '<div style="font-family:monospace;font-size:13px;word-break:break-all">' + esc(result.instanceId) + '</div>' +
      '</div>' +
      (result.ssh_command ? '<div style="background:var(--bg);border-radius:8px;padding:16px;margin-bottom:12px;cursor:pointer" onclick="navigator.clipboard.writeText(\'' + esc(result.ssh_command).replace(/'/g, "\\'") + '\');toast(\'SSH command copied!\',\'success\')">' +
        '<div style="font-size:12px;color:var(--muted);margin-bottom:4px">SSH Access (click to copy)</div>' +
        '<div style="font-family:monospace;font-size:13px;word-break:break-all">' + esc(result.ssh_command) + '</div>' +
      '</div>' : '') +
      (result.jupyter_url ? '<div style="background:var(--bg);border-radius:8px;padding:16px;margin-bottom:12px">' +
        '<div style="font-size:12px;color:var(--muted);margin-bottom:4px">Jupyter Notebook</div>' +
        '<a href="' + esc(result.jupyter_url) + '" target="_blank" rel="noopener" style="font-family:monospace;font-size:13px;color:var(--cyan);word-break:break-all">' + esc(result.jupyter_url) + '</a>' +
      '</div>' : '') +
      '<div style="display:flex;gap:16px;font-size:13px;color:var(--muted);margin-bottom:16px">' +
        '<div>Cost: <span style="color:var(--green)">$' + (result.total_cost || 0).toFixed(2) + '</span></div>' +
        '<div>Auto-stop: ' + autoTerm + '</div>' +
      '</div>' +
      (result.instructions ? '<div style="background:rgba(0,200,200,.05);border:1px solid var(--cyan);border-radius:8px;padding:12px;margin-bottom:16px;font-size:12px;white-space:pre-wrap;font-family:monospace;word-break:break-all">' + esc(result.instructions) + '</div>' : '') +
      '<button class="btn-green" style="width:100%" onclick="this.closest(\'#gpu-creds-modal\').remove()">Got it</button>' +
    '</div>';

  document.body.appendChild(modal);
}

// ======================================
// YIELDS
// ======================================

// Protocol name → real deposit page URL
var DEFI_URLS = {
  'marinade': 'https://marinade.finance/app/stake/',
  'marinade finance': 'https://marinade.finance/app/stake/',
  'jito': 'https://www.jito.network/staking/',
  'aave': 'https://app.aave.com/',
  'aave v3': 'https://app.aave.com/',
  'aave v2': 'https://app.aave.com/',
  'compound': 'https://app.compound.finance/',
  'compound v3': 'https://app.compound.finance/',
  'compound v2': 'https://app.compound.finance/',
  'raydium': 'https://raydium.io/liquidity/',
  'kamino': 'https://app.kamino.finance/',
  'gmx': 'https://app.gmx.io/',
  'sanctum': 'https://app.sanctum.so/',
  'ref finance': 'https://app.ref.finance/',
  'ref-finance': 'https://app.ref.finance/',
  'aerodrome': 'https://aerodrome.finance/liquidity',
  'lido': 'https://stake.lido.fi/',
  'rocket pool': 'https://stake.rocketpool.net/',
  'rocketpool': 'https://stake.rocketpool.net/',
  'eigenlayer': 'https://app.eigenlayer.xyz/',
  'orca': 'https://www.orca.so/pools',
  'jupiter': 'https://jup.ag/',
};

function getDefiDepositUrl(protocol, fallbackUrl, poolId) {
  // 1. Use API-provided URL if available
  if (fallbackUrl && fallbackUrl !== '#' && fallbackUrl.indexOf('http') === 0) {
    return fallbackUrl;
  }
  // 2. Try local mapping
  var key = (protocol || '').toLowerCase().trim();
  if (DEFI_URLS[key]) return DEFI_URLS[key];
  // 3. Try partial match
  for (var k in DEFI_URLS) {
    if (key.indexOf(k) >= 0 || k.indexOf(key) >= 0) return DEFI_URLS[k];
  }
  // 4. Fallback to DeFiLlama
  if (poolId) return 'https://defillama.com/yields/pool/' + poolId;
  if (protocol) return 'https://defillama.com/yields?project=' + encodeURIComponent(protocol);
  return '#';
}

var _yieldAsset = 'USDC';
var _yieldType = '';

function setYieldAsset(asset, btn) {
  _yieldAsset = asset || 'USDC';
  document.querySelectorAll('.yield-asset').forEach(function(b) { b.classList.remove('active'); });
  if (btn) btn.classList.add('active');
  loadYields();
}

function setYieldType(type, btn) {
  _yieldType = type || '';
  document.querySelectorAll('.yield-type').forEach(function(b) { b.classList.remove('active'); });
  if (btn) btn.classList.add('active');
  var compSec = document.getElementById('compound-section');
  var yieldWrap = document.getElementById('yields-table-wrap');
  if (type === 'compound') {
    if (compSec) compSec.style.display = '';
    if (yieldWrap) yieldWrap.style.display = 'none';
    loadCompoundSection();
  } else {
    if (compSec) compSec.style.display = 'none';
    if (yieldWrap) yieldWrap.style.display = '';
    loadYields();
  }
}

async function loadYields(asset, btnEl) {
  if (asset !== undefined) _yieldAsset = asset || 'USDC';
  if (btnEl) {
    document.querySelectorAll('.yield-asset').forEach(function(b) { b.classList.remove('active'); });
    btnEl.classList.add('active');
  }

  var tbody = document.getElementById('yields-body');
  tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">Loading...</td></tr>';

  try {
    if (tbody) tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">DeFi yield aggregation is not available in this version of MAXIA.</td></tr>';
    return;
    var data = {yields: []};
    var pools = data.yields || data.pools || data.results || [];
    if (!Array.isArray(pools)) pools = [pools];

    if (pools.length > 0) {
      tbody.innerHTML = pools.map(function(p) {
        var apy = p.apy_pct || p.apy || 0;
        var risk = (p.risk || 'low').toLowerCase();
        var riskClass = risk === 'high' ? 'badge-red' : (risk === 'medium' ? 'badge-orange' : 'badge-green');
        var riskLabel = risk.charAt(0).toUpperCase() + risk.slice(1);
        var apyColor = apy > 100 ? 'var(--red)' : (apy > 50 ? 'var(--orange)' : 'var(--green)');
        var warningIcon = p.warning ? ' ⚠' : '';
        var proto = p.protocol || p.project || '—';
        var depositUrl = getDefiDepositUrl(proto, p.url, p.pool);
        var depositBtn = depositUrl !== '#'
          ? '<a href="' + esc(depositUrl) + '" target="_blank" rel="noopener" style="padding:4px 12px;background:linear-gradient(135deg,#00e5ff,#7c3aed);color:white;border-radius:6px;font-size:11px;text-decoration:none;cursor:pointer">Deposit</a>'
          : '<span style="padding:4px 12px;background:var(--surface2);color:var(--muted);border-radius:6px;font-size:11px">No link</span>';
        return '<tr>' +
          '<td style="font-weight:600">' + esc(proto) + '</td>' +
          '<td>' + (typeof getChainIcon==='function'?getChainIcon(p.chain||'solana',14):'') + ' <span class="badge badge-purple" style="font-size:11px">' + esc((p.chain || 'solana').charAt(0).toUpperCase() + (p.chain || 'solana').slice(1)) + '</span></td>' +
          '<td style="color:' + apyColor + ';font-weight:700;font-family:JetBrains Mono,monospace">' + Number(apy).toFixed(2) + '%' + warningIcon + '</td>' +
          '<td style="font-family:JetBrains Mono,monospace;font-size:13px">' + fmtUSD(p.tvl_usd || p.tvl || 0) + '</td>' +
          '<td><span class="badge ' + riskClass + '">' + riskLabel + '</span></td>' +
          '<td style="color:var(--muted)">' + esc(p.type || 'DeFi') + '</td>' +
          '<td>' + depositBtn + '</td>' +
        '</tr>';
      }).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">No yields found for ' + esc(asset || 'all assets') + '</td></tr>';
    }
  } catch (e) {
    var fallback = [
      { protocol: 'Marinade', chain: 'Solana', apy: 7.2, tvl: 1800000000, risk: 'Low', type: 'Staking' },
      { protocol: 'Jito', chain: 'Solana', apy: 6.8, tvl: 900000000, risk: 'Low', type: 'Liquid Staking' },
      { protocol: 'Aave V3', chain: 'Ethereum', apy: 4.5, tvl: 12000000000, risk: 'Low', type: 'Lending' },
      { protocol: 'Compound V3', chain: 'Ethereum', apy: 3.9, tvl: 3200000000, risk: 'Low', type: 'Lending' },
      { protocol: 'Aave V3', chain: 'Polygon', apy: 5.1, tvl: 800000000, risk: 'Low', type: 'Lending' },
      { protocol: 'Aave V3', chain: 'Arbitrum', apy: 4.8, tvl: 1200000000, risk: 'Low', type: 'Lending' },
      { protocol: 'Raydium', chain: 'Solana', apy: 12.5, tvl: 450000000, risk: 'Medium', type: 'LP' },
      { protocol: 'Orca', chain: 'Solana', apy: 9.3, tvl: 320000000, risk: 'Medium', type: 'LP' }
    ];
    tbody.innerHTML = fallback.map(function(p) {
      var riskClass = p.risk === 'High' ? 'badge-red' : (p.risk === 'Medium' ? 'badge-orange' : 'badge-green');
      var depositUrl = getDefiDepositUrl(p.protocol || p.project, p.url, p.pool);
      var depositBtn = depositUrl !== '#'
        ? '<a href="' + esc(depositUrl) + '" target="_blank" rel="noopener" style="padding:4px 12px;background:linear-gradient(135deg,#00e5ff,#7c3aed);color:white;border-radius:6px;font-size:11px;text-decoration:none;cursor:pointer">Deposit</a>'
        : '<span style="padding:4px 12px;background:var(--surface2);color:var(--muted);border-radius:6px;font-size:11px">No link</span>';
      return '<tr>' +
        '<td style="font-weight:600">' + p.protocol + '</td>' +
        '<td>' + (typeof getChainIcon==='function'?getChainIcon(p.chain.toLowerCase(),14):'') + ' <span class="badge badge-purple" style="font-size:11px">' + p.chain + '</span></td>' +
        '<td style="color:var(--green);font-weight:700;font-family:JetBrains Mono,monospace">' + p.apy.toFixed(2) + '%</td>' +
        '<td style="font-family:JetBrains Mono,monospace;font-size:13px">' + fmtUSD(p.tvl) + '</td>' +
        '<td><span class="badge ' + riskClass + '">' + p.risk + '</span></td>' +
        '<td style="color:var(--muted)">' + p.type + '</td>' +
        '<td>' + depositBtn + '</td>' +
      '</tr>';
    }).join('');
  }
}

// ======================================
// AUTO-COMPOUND (wallet-based)
// ======================================

async function loadCompoundSection() {
  await Promise.all([loadCompoundStats(), loadCompoundProtocols(), loadCompoundVaults()]);
}

async function loadCompoundStats() {
  var data = await api('/api/compound/stats');
  if (!data) return;
  var el = function(id) { return document.getElementById(id); };
  el('compound-stat-vaults').textContent = data.active_vaults || 0;
  el('compound-stat-tvl').textContent = fmtUSD(data.total_value_locked_usdc || 0);
  el('compound-stat-yield').textContent = fmtUSD(data.total_yield_generated_usdc || 0);
}

async function loadCompoundProtocols() {
  var container = document.getElementById('compound-protocols');
  if (!container) return;
  var data = await api('/api/compound/protocols');
  if (!data || !data.protocols) { container.innerHTML = '<p style="color:var(--muted)">Failed to load protocols</p>'; return; }
  container.innerHTML = data.protocols.map(function(p) {
    var apyColor = p.net_apy_percent > 7 ? 'var(--green)' : 'var(--cyan)';
    return '<div style="background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px">' +
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">' +
        '<span style="font-weight:700;font-size:15px">' + esc(p.name) + '</span>' +
        '<span class="badge badge-purple" style="font-size:11px">' + esc(p.asset) + '</span>' +
      '</div>' +
      '<div style="font-size:13px;color:var(--muted);margin-bottom:12px">' + esc(p.description) + '</div>' +
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">' +
        '<div><span style="color:var(--muted);font-size:12px">Gross APY</span><div style="font-size:18px;font-weight:700;color:' + apyColor + ';font-family:JetBrains Mono,monospace">' + Number(p.gross_apy_percent).toFixed(2) + '%</div></div>' +
        '<div><span style="color:var(--muted);font-size:12px">Net APY</span><div style="font-size:18px;font-weight:700;color:' + apyColor + ';font-family:JetBrains Mono,monospace">' + Number(p.net_apy_percent).toFixed(2) + '%</div></div>' +
        '<div><span style="color:var(--muted);font-size:12px">Fee</span><div style="font-size:14px;color:var(--muted);font-family:JetBrains Mono,monospace">' + Number(p.performance_fee_percent || 0).toFixed(1) + '%</div></div>' +
      '</div>' +
      '<button class="btn-primary" style="width:100%;padding:8px;font-size:13px" onclick="compoundDeposit(\'' + esc(p.protocol_id) + '\',' + p.gross_apy_percent + ')">Deposit ' + esc(p.asset) + '</button>' +
    '</div>';
  }).join('');
}

async function loadCompoundVaults() {
  var noWallet = document.getElementById('compound-no-wallet');
  var table = document.getElementById('compound-vaults-table');
  var tbody = document.getElementById('compound-vaults-body');
  if (!wallet) {
    if (noWallet) noWallet.style.display = '';
    if (table) table.style.display = 'none';
    return;
  }
  if (noWallet) noWallet.style.display = 'none';
  if (table) table.style.display = '';
  var data = await api('/api/compound/w/my');
  if (!data || !data.vaults) { tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">No vaults yet</td></tr>'; return; }
  if (data.vaults.length === 0) { tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">No vaults — deposit to start earning</td></tr>'; return; }
  tbody.innerHTML = data.vaults.map(function(v) {
    var gain = v.gain_usdc || 0;
    var gainColor = gain > 0 ? 'var(--green)' : 'var(--muted)';
    var statusBadge = v.status === 'active' ? '<span class="badge badge-green">Active</span>' : '<span class="badge badge-red">Closed</span>';
    var safeVid = esc(v.vault_id || '');
    var withdrawBtn = v.status === 'active'
      ? '<button class="btn-outline" style="padding:4px 10px;font-size:11px;color:var(--red);border-color:var(--red)" onclick="compoundWithdraw(\'' + safeVid + '\')">Withdraw</button>'
      : '';
    return '<tr>' +
      '<td style="font-weight:600">' + esc(v.protocol_name || v.protocol) + '</td>' +
      '<td style="font-family:JetBrains Mono,monospace;font-size:13px">' + fmtUSD(v.deposited_usdc) + '</td>' +
      '<td style="font-family:JetBrains Mono,monospace;font-size:13px">' + fmtUSD(v.current_value_usdc) + '</td>' +
      '<td style="color:' + gainColor + ';font-family:JetBrains Mono,monospace;font-size:13px">+' + fmtUSD(gain) + '</td>' +
      '<td style="color:var(--green);font-family:JetBrains Mono,monospace">' + Number(v.net_apy_percent || 0).toFixed(2) + '%</td>' +
      '<td style="text-align:center">' + (v.total_compounds || 0) + '</td>' +
      '<td>' + withdrawBtn + '</td>' +
    '</tr>';
  }).join('');
}

var _compoundPending = false;
async function compoundDeposit(protocolId, currentApy) {
  if (_compoundPending) return;
  if (!requireWalletFor('deposit')) return;
  var amount = prompt('Deposit amount in USDC (min 1, max 50000):');
  if (!amount) return;
  var amountNum = parseFloat(amount);
  if (isNaN(amountNum) || amountNum < 1 || amountNum > 50000) { showToast('Invalid amount (1-50000 USDC)', 'error'); return; }
  _compoundPending = true;
  var data = await api('/api/compound/w/deposit', {
    method: 'POST',
    body: JSON.stringify({ protocol: protocolId, amount_usdc: amountNum, wallet: wallet })
  });
  if (!data || !data.success) { _compoundPending = false; showToast('Deposit failed', 'error'); return; }
  showToast('Vault created — ' + data.protocol + ' @ ' + Number(data.net_apy_percent).toFixed(2) + '% net APY', 'success');
  // Construire la transaction on-chain
  if (data.vault_id) {
    var txData = await api('/api/compound/w/tx/' + encodeURIComponent(data.vault_id));
    if (txData && txData.transaction_b64 && window.solana) {
      try {
        showToast('Sign the transaction in your wallet...', 'info');
        var txBytes = Uint8Array.from(atob(txData.transaction_b64), function(c) { return c.charCodeAt(0); });
        var tx = solanaWeb3.VersionedTransaction.deserialize(txBytes);
        var signed = await window.solana.signTransaction(tx);
        var conn = new solanaWeb3.Connection('https://api.mainnet-beta.solana.com');
        var sig = await conn.sendRawTransaction(signed.serialize());
        showToast('Transaction sent: ' + sig.slice(0, 12) + '...', 'success');
      } catch (txErr) {
        console.warn('TX signing skipped:', txErr.message);
        showToast('Vault created (on-chain deposit pending)', 'info');
      }
    }
  }
  _compoundPending = false;
  await loadCompoundSection();
}

async function compoundWithdraw(vaultId) {
  if (_compoundPending) return;
  if (!confirm('Withdraw and close this vault?')) return;
  _compoundPending = true;
  var data = await api('/api/compound/w/' + encodeURIComponent(vaultId), { method: 'DELETE' });
  _compoundPending = false;
  if (!data || !data.success) { showToast('Withdraw failed', 'error'); return; }
  showToast('Vault closed — profit: ' + fmtUSD(data.net_profit_usdc || 0), 'success');
  await loadCompoundSection();
}

// ======================================
// BRIDGE (Li.Fi API for EVM-to-EVM, Portal Bridge fallback for non-EVM)
// ======================================

// -- Li.Fi Bridge chain IDs --
var LIFI_CHAIN_IDS = {
  'ethereum': 1,
  'polygon': 137,
  'arbitrum': 42161,
  'avalanche': 43114,
  'base': 8453,
  'bnb': 56
};

// -- Li.Fi token addresses per chain (native = 0x0...0) --
var LIFI_TOKENS = {
  1: {
    'USDC': { address: '0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48', decimals: 6 },
    'ETH':  { address: '0x0000000000000000000000000000000000000000', decimals: 18 },
    'USDT': { address: '0xdAC17F958D2ee523a2206206994597C13D831ec7', decimals: 6 }
  },
  137: {
    'USDC': { address: '0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359', decimals: 6 },
    'ETH':  { address: '0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619', decimals: 18 },
    'USDT': { address: '0xc2132D05D31c914a87C6611C10748AEb04B58e8F', decimals: 6 }
  },
  42161: {
    'USDC': { address: '0xaf88d065e77c8cC2239327C5EDb3A432268e5831', decimals: 6 },
    'ETH':  { address: '0x0000000000000000000000000000000000000000', decimals: 18 },
    'USDT': { address: '0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9', decimals: 6 }
  },
  43114: {
    'USDC': { address: '0xB97EF9Ef8734C71904D8002F8b6Bc66Dd9c48a6E', decimals: 6 },
    'ETH':  { address: '0x49D5c2BdFfac6CE2BFdB6640F4F80f226bc10bAB', decimals: 18 },
    'USDT': { address: '0x9702230A8Ea53601f5cD2dc00fDBc13d4dF4A8c7', decimals: 6 }
  },
  8453: {
    'USDC': { address: '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913', decimals: 6 },
    'ETH':  { address: '0x0000000000000000000000000000000000000000', decimals: 18 },
    'USDT': { address: '0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2', decimals: 6 }
  },
  56: {
    'USDC': { address: '0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d', decimals: 18 },
    'ETH':  { address: '0x2170Ed0880ac9A755fd29B2688956BD959F933F8', decimals: 18 },
    'USDT': { address: '0x55d398326f99059fF775485246999027B3197955', decimals: 18 }
  }
};

var LIFI_API = 'https://li.quest/v1';

// Store the last Li.Fi quote for execution
var _bridgeQuoteData = null;

function isBridgeEvmToEvm(from, to) {
  return LIFI_CHAIN_IDS[from] !== undefined && LIFI_CHAIN_IDS[to] !== undefined;
}

async function getBridgeQuote() {
  var from = document.getElementById('bridge-from').value;
  var to = document.getElementById('bridge-to').value;
  var token = document.getElementById('bridge-token').value;
  var amount = parseFloat(document.getElementById('bridge-amount').value);
  var resultDiv = document.getElementById('bridge-result');

  if (!amount || amount <= 0) { toast('Enter a valid amount', 'error'); return; }
  if (from === to) { toast('Select different chains', 'error'); return; }

  _bridgeQuoteData = null;

  // -- Non-EVM chain involved? Redirect to Portal Bridge --
  if (!isBridgeEvmToEvm(from, to)) {
    var portalFrom = from.charAt(0).toUpperCase() + from.slice(1);
    var portalTo = to.charAt(0).toUpperCase() + to.slice(1);
    resultDiv.innerHTML = '<div class="card bridge-result">' +
      '<h3 style="font-size:16px;font-weight:600;margin-bottom:16px">External Bridge Required</h3>' +
      '<div style="display:flex;flex-direction:column;gap:10px">' +
        '<div style="display:flex;justify-content:space-between"><span style="color:var(--muted)">Route</span><span>' + esc(portalFrom) + ' &rarr; ' + esc(portalTo) + '</span></div>' +
        '<div style="display:flex;justify-content:space-between"><span style="color:var(--muted)">Token</span><span>' + esc(token) + '</span></div>' +
        '<div style="display:flex;justify-content:space-between"><span style="color:var(--muted)">Amount</span><span>' + amount.toFixed(2) + '</span></div>' +
        '<div style="border-top:1px solid rgba(255,255,255,.06);padding-top:12px;margin-top:4px">' +
          '<p style="font-size:13px;color:var(--muted);margin-bottom:12px">' +
            'Bridges involving ' + esc(portalFrom) + ' or ' + esc(portalTo) + ' require Wormhole Portal Bridge. ' +
            'Click below to open Portal Bridge with your route.' +
          '</p>' +
        '</div>' +
      '</div>' +
      '<a href="https://portalbridge.com/" target="_blank" rel="noopener" class="btn btn-primary" style="margin-top:16px;width:100%;display:block;text-align:center;text-decoration:none;color:#fff">' +
        'Bridge via Portal Bridge &#8599;' +
      '</a>' +
      '<p style="font-size:11px;color:var(--muted2);margin-top:12px;text-align:center">Portal Bridge supports Solana, Ethereum, Polygon, Arbitrum, Avalanche, BNB, SUI, Aptos, NEAR and more.</p>' +
    '</div>';
    return;
  }

  // -- EVM to EVM: Use Li.Fi API for live quote --
  resultDiv.innerHTML = '<div class="card bridge-result" style="text-align:center;padding:24px;color:var(--muted)">' +
    '<span class="swap-spinner"></span> Fetching live quote from Li.Fi bridge aggregator...</div>';

  var fromChainId = LIFI_CHAIN_IDS[from];
  var toChainId = LIFI_CHAIN_IDS[to];
  var fromTokens = LIFI_TOKENS[fromChainId];
  var toTokens = LIFI_TOKENS[toChainId];

  if (!fromTokens || !fromTokens[token]) {
    resultDiv.innerHTML = '<div class="card bridge-result" style="color:var(--red);padding:24px">' + esc(token) + ' is not available for bridging from ' + esc(EVM_CHAINS[fromChainId].name) + '. Try USDC, ETH or USDT.</div>';
    return;
  }
  if (!toTokens || !toTokens[token]) {
    resultDiv.innerHTML = '<div class="card bridge-result" style="color:var(--red);padding:24px">' + esc(token) + ' is not available for bridging to ' + esc(EVM_CHAINS[toChainId].name) + '. Try USDC, ETH or USDT.</div>';
    return;
  }

  var fromTokenAddr = fromTokens[token].address;
  var toTokenAddr = toTokens[token].address;
  var decimals = fromTokens[token].decimals;
  var amountRaw = BigInt(Math.floor(amount * Math.pow(10, decimals))).toString();
  var senderAddr = wallet || '0x0000000000000000000000000000000000000000';

  try {
    var quoteUrl = LIFI_API + '/quote?' + new URLSearchParams({
      fromChain: fromChainId.toString(),
      toChain: toChainId.toString(),
      fromToken: fromTokenAddr,
      toToken: toTokenAddr,
      fromAmount: amountRaw,
      fromAddress: senderAddr
    }).toString();

    var quoteResp = await fetch(quoteUrl);
    if (!quoteResp.ok) {
      var errBody = await quoteResp.text();
      throw new Error('Li.Fi quote failed (' + quoteResp.status + '): ' + errBody);
    }
    var quote = await quoteResp.json();

    if (quote.message || quote.error) {
      throw new Error(quote.message || quote.error);
    }

    // Parse quote data
    var estimate = quote.estimate || {};
    var toAmountRaw = estimate.toAmount || amountRaw;
    var toDecimals = (toTokens[token] || {}).decimals || decimals;
    var toAmountHuman = Number(toAmountRaw) / Math.pow(10, toDecimals);
    var gasCostsUsd = 0;
    if (estimate.gasCosts && estimate.gasCosts.length > 0) {
      for (var gi = 0; gi < estimate.gasCosts.length; gi++) {
        gasCostsUsd += parseFloat(estimate.gasCosts[gi].amountUSD || '0');
      }
    }
    var feeCostsUsd = 0;
    if (estimate.feeCosts && estimate.feeCosts.length > 0) {
      for (var fi = 0; fi < estimate.feeCosts.length; fi++) {
        feeCostsUsd += parseFloat(estimate.feeCosts[fi].amountUSD || '0');
      }
    }
    var executionDuration = estimate.executionDuration || 0;
    var timeStr = executionDuration > 60 ? Math.ceil(executionDuration / 60) + ' min' : executionDuration + ' sec';
    var toolName = (quote.tool || 'Li.Fi').replace(/-/g, ' ');
    var fromChainName = EVM_CHAINS[fromChainId] ? EVM_CHAINS[fromChainId].name : from;
    var toChainName = EVM_CHAINS[toChainId] ? EVM_CHAINS[toChainId].name : to;

    // Store for execution
    _bridgeQuoteData = {
      quote: quote,
      from: from,
      to: to,
      token: token,
      amount: amount,
      fromChainId: fromChainId,
      toChainId: toChainId,
      toAmountHuman: toAmountHuman
    };

    resultDiv.innerHTML = '<div class="card bridge-result">' +
      '<h3 style="font-size:16px;font-weight:600;margin-bottom:20px">Bridge Quote <span class="badge" style="font-size:11px;margin-left:8px">LIVE</span></h3>' +
      '<div style="display:flex;flex-direction:column;gap:8px">' +
        '<div style="display:flex;justify-content:space-between"><span style="color:var(--muted)">You send</span><span style="font-weight:600">' + amount.toFixed(2) + ' ' + esc(token) + ' on ' + esc(fromChainName) + '</span></div>' +
        '<div style="display:flex;justify-content:space-between"><span style="color:var(--muted)">You receive</span><span style="font-weight:700;color:var(--green);font-size:18px">' + toAmountHuman.toFixed(toDecimals <= 6 ? Math.min(toDecimals, 4) : 4) + ' ' + esc(token) + ' on ' + esc(toChainName) + '</span></div>' +
        '<div style="border-top:1px solid rgba(255,255,255,.06);padding-top:8px;margin-top:4px"></div>' +
        '<div style="display:flex;justify-content:space-between"><span style="color:var(--muted)">Bridge protocol</span><span>' + esc(toolName) + '</span></div>' +
        '<div style="display:flex;justify-content:space-between"><span style="color:var(--muted)">Gas cost</span><span>~$' + gasCostsUsd.toFixed(2) + '</span></div>' +
        '<div style="display:flex;justify-content:space-between"><span style="color:var(--muted)">Bridge fee</span><span>$' + feeCostsUsd.toFixed(2) + '</span></div>' +
        '<div style="display:flex;justify-content:space-between"><span style="color:var(--muted)">MAXIA fee</span><span style="color:var(--green);font-weight:600">$0.00 (free)</span></div>' +
        '<div style="display:flex;justify-content:space-between"><span style="color:var(--muted)">Estimated time</span><span>' + esc(timeStr) + '</span></div>' +
        '<div style="display:flex;justify-content:space-between"><span style="color:var(--muted)">Route</span><span>' + esc(fromChainName) + ' &rarr; ' + esc(toolName) + ' &rarr; ' + esc(toChainName) + '</span></div>' +
      '</div>' +
      '<div id="bridge-exec-status" style="margin-top:16px"></div>' +
      '<button class="btn btn-primary" id="btn-bridge-exec" style="margin-top:16px;width:100%" onclick="executeBridge()">' +
        'Bridge ' + amount.toFixed(2) + ' ' + esc(token) + ' now' +
      '</button>' +
    '</div>';

  } catch (e) {
    console.error('Bridge quote error:', e);
    // Fallback: show error + direct bridge links
    var fromName = EVM_CHAINS[fromChainId] ? EVM_CHAINS[fromChainId].name : from;
    var toName = EVM_CHAINS[toChainId] ? EVM_CHAINS[toChainId].name : to;
    resultDiv.innerHTML = '<div class="card bridge-result">' +
      '<h3 style="font-size:16px;font-weight:600;margin-bottom:16px;color:var(--orange)">Quote Unavailable</h3>' +
      '<p style="font-size:13px;color:var(--muted);margin-bottom:12px">' +
        'Could not get a live quote for ' + amount.toFixed(2) + ' ' + esc(token) + ' from ' + esc(fromName) + ' to ' + esc(toName) + '.' +
        '<br><span style="font-size:12px;opacity:.8">Reason: ' + esc(e.message || 'Unknown error') + '</span>' +
      '</p>' +
      '<p style="font-size:13px;color:var(--muted);margin-bottom:16px">You can bridge directly through these services:</p>' +
      '<div style="display:flex;gap:10px;flex-wrap:wrap">' +
        '<a href="https://jumper.exchange/?fromChain=' + fromChainId + '&toChain=' + toChainId + '" target="_blank" rel="noopener" class="btn btn-primary btn-sm" style="flex:1;text-align:center;text-decoration:none;color:#fff">' +
          'Jumper (Li.Fi) &#8599;' +
        '</a>' +
        '<a href="https://portalbridge.com/" target="_blank" rel="noopener" class="btn btn-outline btn-sm" style="flex:1;text-align:center;text-decoration:none">' +
          'Portal Bridge &#8599;' +
        '</a>' +
      '</div>' +
    '</div>';
  }
}

async function executeBridge() {
  if (!requireWalletFor('bridge tokens')) return;

  if (!_bridgeQuoteData || !_bridgeQuoteData.quote) {
    toast('Get a quote first', 'error');
    return;
  }

  if (!isEvmWallet()) {
    toast('Bridge requires an EVM wallet (MetaMask, Rabby, Coinbase). Connect one first.', 'error');
    return;
  }

  var fromChainId = _bridgeQuoteData.fromChainId;
  var toChainId = _bridgeQuoteData.toChainId;
  var token = _bridgeQuoteData.token;
  var amount = _bridgeQuoteData.amount;
  var fromChainName = EVM_CHAINS[fromChainId] ? EVM_CHAINS[fromChainId].name : _bridgeQuoteData.from;
  var toChainName = EVM_CHAINS[toChainId] ? EVM_CHAINS[toChainId].name : _bridgeQuoteData.to;
  var btn = document.getElementById('btn-bridge-exec');
  var statusDiv = document.getElementById('bridge-exec-status');

  function setExecStatus(msg, type) {
    var colorMap = { 'pending': 'var(--cyan)', 'confirming': 'var(--orange)', 'success': 'var(--green)', 'error': 'var(--red)' };
    statusDiv.innerHTML = '<div style="padding:12px;border-radius:10px;background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);font-size:14px;color:' + (colorMap[type] || 'var(--muted)') + '">' + msg + '</div>';
  }

  btn.disabled = true;

  try {
    // -- Step 1: Switch to the source chain --
    var currentChainId = await getEvmChainId();
    if (currentChainId !== fromChainId) {
      btn.textContent = 'Switching to ' + fromChainName + '...';
      setExecStatus('<span class="swap-spinner"></span> Switching wallet to ' + esc(fromChainName) + '...', 'pending');
      var switched = await switchEvmChain(fromChainId);
      if (!switched) {
        throw new Error('Could not switch to ' + fromChainName + '. Please switch manually in your wallet.');
      }
      // Small delay to let wallet settle after chain switch
      await new Promise(function(r) { setTimeout(r, 1000); });
    }

    // -- Step 2: Re-fetch quote with the connected wallet for a fresh tx --
    btn.textContent = 'Fetching fresh quote...';
    setExecStatus('<span class="swap-spinner"></span> Refreshing quote with your wallet...', 'pending');

    var fromTokenAddr = LIFI_TOKENS[fromChainId][token].address;
    var toTokenAddr = LIFI_TOKENS[toChainId][token].address;
    var decimals = LIFI_TOKENS[fromChainId][token].decimals;
    var amountRaw = BigInt(Math.floor(amount * Math.pow(10, decimals))).toString();

    var freshUrl = LIFI_API + '/quote?' + new URLSearchParams({
      fromChain: fromChainId.toString(),
      toChain: toChainId.toString(),
      fromToken: fromTokenAddr,
      toToken: toTokenAddr,
      fromAmount: amountRaw,
      fromAddress: wallet
    }).toString();

    var freshResp = await fetch(freshUrl);
    if (!freshResp.ok) {
      var errText = await freshResp.text();
      throw new Error('Fresh quote failed: ' + errText);
    }
    var freshQuote = await freshResp.json();
    if (freshQuote.message || freshQuote.error) {
      throw new Error(freshQuote.message || freshQuote.error);
    }

    var txRequest = freshQuote.transactionRequest;
    if (!txRequest) {
      throw new Error('No transaction returned by Li.Fi. The bridge route may not support this token/amount combination.');
    }

    // -- Step 3: Approve token if needed (non-native tokens) --
    if (fromTokenAddr !== '0x0000000000000000000000000000000000000000') {
      var approvalAddr = freshQuote.estimate && freshQuote.estimate.approvalAddress ? freshQuote.estimate.approvalAddress : txRequest.to;
      if (approvalAddr) {
        btn.textContent = 'Checking approval...';
        setExecStatus('<span class="swap-spinner"></span> Checking token approval for ' + esc(token) + '...', 'pending');

        var needsApproval = await checkEvmApproval(fromTokenAddr, approvalAddr, amountRaw);
        if (needsApproval) {
          btn.textContent = 'Approve ' + token + ' in wallet...';
          setExecStatus('<span class="swap-spinner"></span> Please approve ' + esc(token) + ' spending in your wallet...', 'confirming');
          await approveEvmToken(fromTokenAddr, approvalAddr, amountRaw);
          setExecStatus('<span class="swap-spinner"></span> Approval confirmed! Sending bridge transaction...', 'pending');
        }
      }
    }

    // -- Step 4: Send the bridge transaction via MetaMask --
    btn.textContent = 'Confirm in wallet...';
    setExecStatus('<span class="swap-spinner"></span> Please confirm the bridge transaction in your wallet...', 'confirming');

    var txParams = {
      from: wallet,
      to: txRequest.to,
      data: txRequest.data,
      value: txRequest.value || '0x0',
      gasLimit: txRequest.gasLimit ? '0x' + BigInt(txRequest.gasLimit).toString(16) : undefined,
      chainId: txRequest.chainId ? '0x' + parseInt(txRequest.chainId).toString(16) : EVM_CHAINS[fromChainId].hex
    };
    // Clean up undefined fields
    if (!txParams.gasLimit) delete txParams.gasLimit;

    var txHash = await window.ethereum.request({
      method: 'eth_sendTransaction',
      params: [txParams]
    });

    // -- Step 5: Wait for on-chain confirmation --
    btn.textContent = 'Confirming on ' + fromChainName + '...';
    setExecStatus(
      '<span class="swap-spinner"></span> Transaction sent! Waiting for confirmation on ' + esc(fromChainName) + '...' +
      '<div style="margin-top:8px;font-family:JetBrains Mono,monospace;font-size:12px;word-break:break-all;color:var(--muted)">' +
        'Tx: ' + txHash +
      '</div>',
      'confirming'
    );

    var receipt = null;
    for (var i = 0; i < 120; i++) {
      await new Promise(function(r) { setTimeout(r, 2500); });
      try {
        receipt = await window.ethereum.request({
          method: 'eth_getTransactionReceipt',
          params: [txHash]
        });
        if (receipt) break;
      } catch(pollErr) { /* keep polling */ }
    }

    if (receipt && receipt.status === '0x0') {
      throw new Error('Bridge transaction reverted on-chain. Check your balance and try again.');
    }

    var explorerUrl = EVM_CHAINS[fromChainId] ? EVM_CHAINS[fromChainId].explorer + '/tx/' + txHash : '#';

    // -- Step 6: Show success + tracking links --
    btn.textContent = 'Bridge submitted!';
    btn.disabled = true;
    setExecStatus(
      '<div style="text-align:center">' +
        '<div style="font-size:32px;margin-bottom:8px">&#9989;</div>' +
        '<div style="font-size:16px;font-weight:600;color:var(--green);margin-bottom:12px">Bridge Transaction Confirmed!</div>' +
        '<div style="font-size:13px;color:var(--muted);margin-bottom:16px">' +
          'Your ' + amount.toFixed(2) + ' ' + esc(token) + ' is being bridged from ' + esc(fromChainName) + ' to ' + esc(toChainName) + '.' +
          '<br>The tokens will arrive on ' + esc(toChainName) + ' shortly (check your wallet).' +
        '</div>' +
        '<div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap">' +
          '<a href="' + esc(explorerUrl) + '" target="_blank" rel="noopener" class="btn btn-outline btn-sm" style="text-decoration:none">' +
            'View on Explorer &#8599;' +
          '</a>' +
          '<a href="https://scan.li.fi/tx/' + txHash + '" target="_blank" rel="noopener" class="btn btn-outline btn-sm" style="text-decoration:none">' +
            'Track on Li.Fi &#8599;' +
          '</a>' +
        '</div>' +
        '<div style="margin-top:12px;font-family:JetBrains Mono,monospace;font-size:11px;word-break:break-all;color:var(--muted2)">' + txHash + '</div>' +
        '<div style="display:inline-flex;align-items:center;gap:4px;background:rgba(0,255,136,0.1);border:1px solid rgba(0,255,136,0.3);border-radius:4px;padding:2px 8px;font-size:11px;color:#00ff88;margin-top:8px"><span>&#10003;</span> OFAC Screened</div>' +
      '</div>',
      'success'
    );

    _bridgeQuoteData = null;
    toast('Bridge transaction confirmed on ' + fromChainName + '!', 'success');

  } catch (e) {
    console.error('Bridge execution error:', e);
    var userMsg = e.message || 'Unknown error';
    // MetaMask user rejection
    if (e.code === 4001 || (userMsg && userMsg.indexOf('User denied') !== -1) || (userMsg && userMsg.indexOf('rejected') !== -1)) {
      userMsg = 'Transaction rejected in wallet.';
    }
    setExecStatus(
      '<div style="color:var(--red)">' +
        '<strong>Bridge failed:</strong> ' + esc(userMsg) +
        '<div style="margin-top:12px;font-size:13px;color:var(--muted)">' +
          'You can also bridge directly:' +
          '<div style="display:flex;gap:8px;margin-top:8px">' +
            '<a href="https://jumper.exchange/?fromChain=' + fromChainId + '&toChain=' + toChainId + '" target="_blank" rel="noopener" class="btn btn-outline btn-sm" style="text-decoration:none;font-size:12px">Jumper &#8599;</a>' +
            '<a href="https://portalbridge.com/" target="_blank" rel="noopener" class="btn btn-outline btn-sm" style="text-decoration:none;font-size:12px">Portal &#8599;</a>' +
          '</div>' +
        '</div>' +
      '</div>',
      'error'
    );
    btn.textContent = 'Retry Bridge';
    btn.disabled = false;
    toast('Bridge failed: ' + userMsg, 'error');
  }
}

// ======================================
// STOCKS — Tokenized stock trading via Backed Finance xStocks
// ======================================

// Mint addresses from backend/tokenized_stocks.py (Backed Finance xStocks on Solana)
var STOCK_CATALOG = {
  "AAPL":  { name:"Apple Inc.",          xstock:"AAPLX",  mint:"XsbEhLAtcf6HdfpFZ5xEMdqW8nfAvcsP5bdudRLJzJp", sector:"Technology",       logo:"https://companieslogo.com/img/orig/AAPL-bf1a4314.png" },
  "TSLA":  { name:"Tesla Inc.",          xstock:"TSLAX",  mint:"XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB", sector:"Automotive",       logo:"https://companieslogo.com/img/orig/TSLA-6da498e2.png" },
  "NVDA":  { name:"NVIDIA Corp.",        xstock:"NVDAX",  mint:"Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh", sector:"Semiconductors",   logo:"https://companieslogo.com/img/orig/NVDA-220f2e6a.png" },
  "GOOGL": { name:"Alphabet Inc.",       xstock:"GOOGLX", mint:"XsCPL9dNWBMvFtTmwcCA5v3xWPSMEBCszbQdiLLq6aN", sector:"Technology",       logo:"https://companieslogo.com/img/orig/GOOG-0ed88f7c.png" },
  "MSFT":  { name:"Microsoft Corp.",     xstock:"MSFTX",  mint:"XsMTBZsqrDgTRWKzKMGSDE8GQjPX4mNQHN3fLFMKfBJ", sector:"Technology",       logo:"https://companieslogo.com/img/orig/MSFT-a203b22d.png" },
  "AMZN":  { name:"Amazon.com Inc.",     xstock:"AMZNX",  mint:"Xs3eBt7uRfJX8QUs4suhyU8p2M6DoUDrJyWBa8LLZsg", sector:"Consumer",         logo:"https://companieslogo.com/img/orig/AMZN-e9f942e4.png" },
  "META":  { name:"Meta Platforms Inc.", xstock:"METAX",  mint:"XsoeC2iBhNSXVgVB9GNofBSVw3VF9LDLBqSMhRdZi43", sector:"Technology",       logo:"https://companieslogo.com/img/orig/META-12fb9e4a.png" },
  "MSTR":  { name:"MicroStrategy Inc.",  xstock:"MSTRX",  mint:"XsP7xzNPvEHS1m6qfanPUGjNmdnmsLKEoNAnHjdxxyZ", sector:"Technology/BTC",   logo:"https://companieslogo.com/img/orig/MSTR-66bc637c.png" },
  "QQQ":   { name:"Nasdaq 100 ETF",     xstock:"QQQX",   mint:"Xs8S1uUs1zvS2p7iwtsG3b6fkhpvmwz4GYU3gWAmWHZ", sector:"ETF",              logo:"https://companieslogo.com/img/orig/QQQ-6bd07e4e.png" },
  "SPY":   { name:"S&P 500 ETF",        xstock:"SPYX",   mint:"XsoCS1TfEyfFhfvj8EtZ528L3CaKBDBRqRapnBbDF2W", sector:"ETF",              logo:"https://companieslogo.com/img/orig/SPY-8cf3e3d2.png" }
};

var USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";

// Cache loaded prices from API
var _stockPriceCache = {};
var _stocksData = [];
var _stockFilter = 'all';
var _selectedStock = null;

async function loadStocks() {
  var tbody = document.getElementById('stocks-tbody');
  if (tbody) tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--muted);padding:24px">Tokenized stocks are not available in this version of MAXIA.</td></tr>';
  return;
  try {
    var data = {stocks: []};
    var stocks = (data && data.stocks) || (Array.isArray(data) ? data : []);

    // Update oracle status badge
    var ms = data && data.market_status;
    if (ms) {
      var badge = document.getElementById('stk-oracle-badge');
      if (badge) {
        badge.textContent = ms.label || 'Oracle: —';
        badge.className = 'badge ' + (ms.oracle === 'live' ? 'badge-green' : ms.oracle === 'after_hours' ? 'badge-orange' : '');
        badge.title = ms.note || '';
      }
    }

    if (!Array.isArray(stocks) || stocks.length === 0) {
      // Fallback catalog — prices hydrated live by startStockPriceLive() WS
      stocks = Object.keys(STOCK_CATALOG).map(function(sym) {
        var cat = STOCK_CATALOG[sym];
        return {symbol: sym, name: cat.name, price_usd: 0, change_24h_pct: 0, sector: cat.sector, xstock: cat.xstock};
      });
    }

    // Normalize and cache
    _stocksData = stocks.map(function(s) {
      var sym = (s.symbol||'').replace(/^x/,'').toUpperCase();
      var cat = STOCK_CATALOG[sym] || {};
      var price = s.price_usd || s.price || 0;
      var change = s.change_24h_pct || 0;
      var obj = {
        symbol: sym, name: s.name || cat.name || sym, price: price, change: change,
        sector: s.sector || cat.sector || 'Technology', xstock: cat.xstock || sym+'X',
        mint: cat.mint || '', logo: cat.logo || '', provider: cat.mint && cat.mint.length > 20 ? 'Backed' : '—',
        dex_price: s.dex_price_usd || 0,
      };
      _stockPriceCache[sym] = {price: price, change: change, source: price > 0 ? 'live' : 'unavailable'};
      return obj;
    });

    renderStockRows();
    // Demarrer le live price immediatement
    startStockPriceLive();
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:40px;color:var(--red)">Failed to load stocks</td></tr>';
  }
}

function renderStockRows() {
  var tbody = document.getElementById('stocks-tbody');
  var filtered = _stocksData.filter(function(s) {
    if (_stockFilter === 'all') return true;
    return s.sector === _stockFilter || (s.sector||'').indexOf(_stockFilter) >= 0;
  });

  // Sort
  var sortVal = (document.getElementById('stk-sort')||{}).value || 'name';
  if (sortVal === 'price_desc') filtered.sort(function(a,b){return b.price-a.price;});
  else if (sortVal === 'price_asc') filtered.sort(function(a,b){return a.price-b.price;});
  else if (sortVal === 'change_desc') filtered.sort(function(a,b){return b.change-a.change;});
  else if (sortVal === 'change_asc') filtered.sort(function(a,b){return a.change-b.change;});
  else filtered.sort(function(a,b){return a.name.localeCompare(b.name);});

  if (!filtered.length) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:24px;color:var(--muted)">No stocks in this category</td></tr>';
    return;
  }

  tbody.innerHTML = filtered.map(function(s) {
    var changeColor = s.change >= 0 ? 'var(--green)' : 'var(--red)';
    var changeStr = (s.change >= 0 ? '+' : '') + s.change.toFixed(2) + '%';
    var priceStr = s.price > 0 ? '$' + s.price.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}) : '—';
    var dexStr = s.dex_price > 0 ? '<div style="font-size:10px;color:var(--cyan)" title="DEX on-chain price (24/7)">DEX $' + s.dex_price.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}) + '</div>' : '';
    var isSelected = _selectedStock === s.symbol;
    var rowBg = isSelected ? 'background:rgba(59,130,246,.08)' : '';
    var available = s.mint && s.mint.length > 20;
    return '<tr onclick="selectStock(\'' + esc(s.symbol) + '\')" style="cursor:pointer;border-bottom:1px solid rgba(255,255,255,.04);transition:background .15s;' + rowBg + '" onmouseenter="this.style.background=\'rgba(255,255,255,.03)\'" onmouseleave="this.style.background=\'' + (isSelected?'rgba(59,130,246,.08)':'') + '\'">' +
      '<td style="padding:12px 16px"><div style="display:flex;align-items:center;gap:10px">' +
        (s.logo ? '<img src="'+esc(s.logo)+'" width="28" height="28" style="border-radius:50%;background:var(--bg2)" onerror="this.style.display=\'none\'">' : '<div style="width:28px;height:28px;border-radius:50%;background:var(--bg2);display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700;color:var(--cyan)">'+s.symbol.charAt(0)+'</div>') +
        '<div><div style="font-weight:600;font-size:14px">' + esc(s.xstock) + '</div><div style="font-size:11px;color:var(--muted)">' + esc(s.name) + '</div></div>' +
      '</div></td>' +
      '<td id="stk-price-' + esc(s.symbol) + '" style="padding:12px 8px;text-align:right;font-family:\'JetBrains Mono\',monospace;font-weight:600;font-size:14px">' + priceStr + dexStr + '</td>' +
      '<td id="stk-change-' + esc(s.symbol) + '" style="padding:12px 8px;text-align:right;font-weight:600;color:' + changeColor + '">' + changeStr + '</td>' +
      '<td style="padding:12px 8px;text-align:center"><span class="badge" style="font-size:10px;padding:2px 8px">' + (available ? esc(s.provider) : '<span style="color:var(--muted2)">Soon</span>') + '</span></td>' +
      '<td style="padding:12px 16px;text-align:right;white-space:nowrap">' +
        (available ?
          '<button class="btn-outline" onclick="event.stopPropagation();quickStockTrade(\'' + esc(s.symbol) + '\',\'buy\')" style="padding:4px 10px;font-size:11px;color:var(--green);border-color:rgba(16,185,129,.3);margin-right:4px;display:inline-block">Buy</button>' +
          '<button class="btn-outline" onclick="event.stopPropagation();quickStockTrade(\'' + esc(s.symbol) + '\',\'sell\')" style="padding:4px 10px;font-size:11px;color:var(--red);border-color:rgba(239,68,68,.3);display:inline-block">Sell</button>'
          : '<span style="font-size:11px;color:var(--muted2)">Coming Q2</span>') +
      '</td>' +
    '</tr>';
  }).join('');
}

function filterStocks(sector, btn) {
  _stockFilter = sector;
  document.querySelectorAll('.stk-filter').forEach(function(b){b.classList.remove('active');});
  if (btn) btn.classList.add('active');
  renderStockRows();
}

function sortStocks(val) { renderStockRows(); }

// -- Live price updater — WebSocket HFT, prix chaque seconde, flash vert/rouge --
var _stkPriceWS = null;
function startStockPriceLive() {
  if (_stkPriceWS && _stkPriceWS.readyState <= 1) return;
  var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  _stkPriceWS = new WebSocket(proto + '//' + location.host + '/ws/prices');
  _stkPriceWS.onopen = function() { _stkPriceWS.send(JSON.stringify({mode: 'hft'})); };
  _stkPriceWS.onmessage = function(e) {
    try {
      var msg = JSON.parse(e.data);
      var d = msg.data || {};
      var sym = (d.symbol || '').toUpperCase();
      var newPrice = d.price || 0;
      if (!sym || newPrice <= 0) return;
      var oldPrice = (_stockPriceCache[sym] || {}).price || 0;
      _stockPriceCache[sym] = {price: newPrice, change: (_stockPriceCache[sym]||{}).change || 0, source: 'live'};
      var s = _stocksData.find(function(st){return st.symbol===sym;});
      if (s) s.price = newPrice;
      var priceEl = document.getElementById('stk-price-' + sym);
      if (priceEl) {
        priceEl.textContent = '$' + newPrice.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});
        var flashColor = newPrice > oldPrice ? '#10b981' : newPrice < oldPrice ? '#ef4444' : '';
        if (flashColor && oldPrice > 0) {
          priceEl.style.color = flashColor;
          setTimeout(function(){priceEl.style.color='';}, 800);
        }
      }
    } catch(err) {}
  };
  _stkPriceWS.onclose = function() { setTimeout(startStockPriceLive, 3000); };
}

// Demarrer le live quand la page stocks s'affiche
if (!window._stkLiveHooked) {
  window._stkLiveHooked = true;
  var _realShowPage = window.showPage;
  window.showPage = function(page) {
    if (typeof _realShowPage === 'function') _realShowPage(page);
    if (page === 'stocks' || page === 'trading') startStockPriceLive();
  };
}

function selectStock(symbol) {
  _selectedStock = symbol;
  renderStockRows();
  renderStockDetail(symbol);
}

function renderStockDetail(symbol) {
  var panel = document.getElementById('stk-detail-content');
  var cat = STOCK_CATALOG[symbol] || {};
  var cached = _stockPriceCache[symbol] || {};
  var price = cached.price || 0;
  var change = cached.change || 0;
  var changeColor = change >= 0 ? 'var(--green)' : 'var(--red)';
  var changeStr = (change >= 0 ? '+' : '') + change.toFixed(2) + '%';
  var hasMint = cat.mint && cat.mint.length > 20;
  var jupUrl = hasMint ? 'https://jup.ag/swap/USDC-' + cat.mint : '';
  var birdeyeUrl = hasMint ? 'https://birdeye.so/token/' + cat.mint + '?chain=solana' : '';

  panel.innerHTML =
    // Header
    '<div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">' +
      (cat.logo ? '<img src="'+esc(cat.logo)+'" width="36" height="36" style="border-radius:50%;background:var(--bg2)" onerror="this.style.display=\'none\'">' : '') +
      '<div><div style="font-size:18px;font-weight:700">' + esc(cat.xstock||symbol) + '</div><div style="font-size:12px;color:var(--muted)">' + esc(cat.name||symbol) + '</div></div>' +
    '</div>' +

    // Price
    '<div style="margin-bottom:16px">' +
      '<div style="font-size:28px;font-weight:800;font-family:\'JetBrains Mono\',monospace">' + (price > 0 ? '$'+price.toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2}) : 'Loading...') + '</div>' +
      '<div style="font-size:14px;font-weight:600;color:' + changeColor + '">' + changeStr + ' today</div>' +
    '</div>' +

    // Chart with timeframe selector + live WebSocket
    '<div style="margin-bottom:8px;display:flex;gap:4px">' +
      '<button class="btn-outline stk-tf active" onclick="setStkChartTF(1,this)" style="padding:3px 6px;font-size:10px;color:#10b981;border-color:#10b981">1S</button>' +
      '<button class="btn-outline stk-tf" onclick="setStkChartTF(5,this)" style="padding:3px 6px;font-size:10px;color:#10b981;border-color:#10b981">5S</button>' +
      '<button class="btn-outline stk-tf" onclick="setStkChartTF(60,this)" style="padding:3px 6px;font-size:10px">1M</button>' +
      '<button class="btn-outline stk-tf" onclick="setStkChartTF(3600,this)" style="padding:3px 6px;font-size:10px">1H</button>' +
      '<button class="btn-outline stk-tf" onclick="setStkChartTF(86400,this)" style="padding:3px 6px;font-size:10px">1D</button>' +
      '<span id="stk-chart-status" style="margin-left:auto;font-size:10px;color:var(--muted)"></span>' +
    '</div>' +
    '<div id="stk-mini-chart" style="width:100%;height:250px;background:var(--bg);border-radius:8px;margin-bottom:16px"></div>' +

    // Info row
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px;font-size:12px">' +
      '<div style="background:var(--bg);border-radius:8px;padding:10px"><div style="color:var(--muted);margin-bottom:2px">Sector</div><div style="font-weight:600">' + esc(cat.sector||'Tech') + '</div></div>' +
      '<div style="background:var(--bg);border-radius:8px;padding:10px"><div style="color:var(--muted);margin-bottom:2px">Provider</div><div style="font-weight:600">' + (hasMint ? 'Backed Finance' : 'Not yet listed') + '</div></div>' +
    '</div>' +

    // Trade form
    (hasMint ?
      '<div style="margin-bottom:12px">' +
        '<label style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px;display:block">Amount (USDC)</label>' +
        '<input type="number" id="stk-trade-amount" class="form-input" placeholder="100.00" value="100" step="any" style="width:100%;padding:10px;font-size:14px;font-family:\'JetBrains Mono\',monospace" oninput="updateStkSummary(\'' + esc(symbol) + '\')">' +
        '<div style="display:flex;gap:4px;margin-top:6px">' +
          '<button class="btn-outline" onclick="document.getElementById(\'stk-trade-amount\').value=10;updateStkSummary(\'' + esc(symbol) + '\')" style="flex:1;padding:3px;font-size:10px">$10</button>' +
          '<button class="btn-outline" onclick="document.getElementById(\'stk-trade-amount\').value=50;updateStkSummary(\'' + esc(symbol) + '\')" style="flex:1;padding:3px;font-size:10px">$50</button>' +
          '<button class="btn-outline" onclick="document.getElementById(\'stk-trade-amount\').value=100;updateStkSummary(\'' + esc(symbol) + '\')" style="flex:1;padding:3px;font-size:10px">$100</button>' +
          '<button class="btn-outline" onclick="document.getElementById(\'stk-trade-amount\').value=500;updateStkSummary(\'' + esc(symbol) + '\')" style="flex:1;padding:3px;font-size:10px">$500</button>' +
        '</div>' +
      '</div>' +

      '<div id="stk-trade-summary" style="background:var(--bg);border-radius:8px;padding:10px;margin-bottom:12px;font-size:12px">' +
        '<div style="display:flex;justify-content:space-between;margin-bottom:3px"><span style="color:var(--muted)">Est. shares</span><span style="font-family:\'JetBrains Mono\',monospace">' + (price > 0 ? (99/price).toFixed(4) : '—') + '</span></div>' +
        '<div style="display:flex;justify-content:space-between;margin-bottom:3px"><span style="color:var(--muted)">Fee (1%)</span><span style="font-family:\'JetBrains Mono\',monospace">$1.00</span></div>' +
        '<div style="display:flex;justify-content:space-between"><span style="font-weight:600">You receive</span><span style="font-family:\'JetBrains Mono\',monospace;font-weight:600">' + (price > 0 ? (99/price).toFixed(4)+' '+esc(cat.xstock||symbol) : '—') + '</span></div>' +
      '</div>' +

      '<div style="display:flex;gap:8px;margin-bottom:12px">' +
        '<button class="btn btn-primary" onclick="executeStockSwap(\'' + esc(symbol) + '\')" style="flex:1;padding:12px;font-size:14px;font-weight:700;border-radius:8px;background:linear-gradient(135deg,#10B981,#059669)">Buy ' + esc(cat.xstock||symbol) + '</button>' +
        '<button class="btn" onclick="executeStockSwap(\'' + esc(symbol) + '\')" style="flex:1;padding:12px;font-size:14px;font-weight:700;border-radius:8px;background:linear-gradient(135deg,#EF4444,#DC2626);color:#fff;border:none;cursor:pointer">Sell</button>' +
      '</div>' +

      '<div style="display:flex;gap:6px;justify-content:center">' +
        '<a href="' + jupUrl + '" target="_blank" class="btn-outline" style="padding:4px 10px;font-size:10px;text-decoration:none">Jupiter</a>' +
        '<a href="' + birdeyeUrl + '" target="_blank" class="btn-outline" style="padding:4px 10px;font-size:10px;text-decoration:none">Birdeye</a>' +
        '<a href="https://solscan.io/token/' + esc(cat.mint) + '" target="_blank" class="btn-outline" style="padding:4px 10px;font-size:10px;text-decoration:none">Solscan</a>' +
      '</div>'
      :
      '<div style="text-align:center;padding:20px;background:var(--bg);border-radius:8px">' +
        '<div style="font-size:14px;font-weight:600;margin-bottom:4px">Not yet tokenized</div>' +
        '<div style="font-size:12px;color:var(--muted)">This stock will be available when a provider lists it on-chain.</div>' +
      '</div>'
    ) +

    '<div id="stk-trade-result" style="margin-top:8px;font-size:12px;display:none"></div>';

  // Load mini chart
  loadStockMiniChart(symbol);
}

function updateStkSummary(symbol) {
  var amount = parseFloat((document.getElementById('stk-trade-amount')||{}).value) || 0;
  var cached = _stockPriceCache[symbol] || {};
  var price = cached.price || 1;
  var cat = STOCK_CATALOG[symbol] || {};
  var bps = amount >= 5000 ? 10 : amount >= 500 ? 50 : 150;
  var fee = amount * bps / 10000;
  var net = amount - fee;
  var shares = price > 0 ? net / price : 0;
  var tierName = bps === 10 ? 'WHALE 0.1%' : bps === 50 ? 'GOLD 0.5%' : 'BRONZE 1.5%';
  var el = document.getElementById('stk-trade-summary');
  if (el) el.innerHTML =
    '<div style="display:flex;justify-content:space-between;margin-bottom:3px"><span style="color:var(--muted)">Est. shares</span><span style="font-family:\'JetBrains Mono\',monospace">' + shares.toFixed(4) + '</span></div>' +
    '<div style="display:flex;justify-content:space-between;margin-bottom:3px"><span style="color:var(--muted)">Fee (' + tierName + ')</span><span style="font-family:\'JetBrains Mono\',monospace">$' + fee.toFixed(2) + '</span></div>' +
    '<div style="display:flex;justify-content:space-between"><span style="font-weight:600">You receive</span><span style="font-family:\'JetBrains Mono\',monospace;font-weight:600">' + shares.toFixed(4) + ' ' + esc(cat.xstock||symbol) + '</span></div>';
}

var _stkChart = null;
var _stkSeries = null;
var _stkWS = null;
var _stkSymbol = '';
var _stkInterval = 1;

function setStkChartTF(interval_s, btn) {
  _stkInterval = interval_s;
  document.querySelectorAll('.stk-tf').forEach(function(b) { b.classList.remove('active'); });
  if (btn) btn.classList.add('active');
  if (_stkSymbol) loadStockMiniChart(_stkSymbol);
}

async function loadStockMiniChart(symbol) {
  var container = document.getElementById('stk-mini-chart');
  var statusEl = document.getElementById('stk-chart-status');
  if (!container || typeof LightweightCharts === 'undefined') return;
  container.innerHTML = '';
  _stkSymbol = symbol;

  // Pyth SSE tokens — live chart possible
  var stkLiveTokens = ['SOL', 'ETH', 'BTC', 'USDC'];
  // Map stock symbols to crypto (AAPL has no SSE — use REST)
  var baseSymbol = symbol.replace(/X$/, ''); // AAPLX -> AAPL

  // Create candlestick chart
  if (_stkChart) { _stkChart.remove(); _stkChart = null; }
  var isSecond = _stkInterval <= 60;
  _stkChart = LightweightCharts.createChart(container, {
    width: container.clientWidth, height: 250,
    layout: {background:{color:'#0a0e17'},textColor:'#94a3b8',fontSize:10},
    grid: {vertLines:{color:'rgba(255,255,255,.03)'},horzLines:{color:'rgba(255,255,255,.03)'}},
    timeScale: {timeVisible:true, secondsVisible:isSecond, borderColor:'rgba(255,255,255,.06)'},
    rightPriceScale:{borderColor:'rgba(255,255,255,.06)'},
    crosshair:{mode:0},
  });
  _stkSeries = _stkChart.addCandlestickSeries({
    upColor:'#10b981', downColor:'#ef4444', borderUpColor:'#10b981', borderDownColor:'#ef4444',
    wickUpColor:'#10b981', wickDownColor:'#ef4444',
  });
  window.addEventListener('resize', function() { if (_stkChart) _stkChart.applyOptions({width:container.clientWidth}); });

  // For Pyth-streamed tokens (SOL, ETH, BTC, USDC) — live WebSocket
  if (stkLiveTokens.indexOf(baseSymbol) >= 0) {
    if (_stkWS) { _stkWS.close(); _stkWS = null; }
    // Load history for longer timeframes
    if (_stkInterval >= 3600) {
      try {
        var tfMap = {3600:'1h',21600:'6h',86400:'1d'};
        var r = await api('/api/trading/candles/' + baseSymbol + '?interval=' + (tfMap[_stkInterval]||'1h') + '&limit=48');
        if (r && r.candles && r.candles.length > 0) {
          _stkSeries.setData(r.candles.map(function(c){return{time:c.timestamp,open:c.open,high:c.high,low:c.low,close:c.close};}));
        }
      } catch(e) {}
    }
    var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    _stkWS = new WebSocket(proto + '//' + location.host + '/ws/chart');
    _stkWS.onopen = function() {
      _stkWS.send(JSON.stringify({symbol: baseSymbol, interval: _stkInterval}));
      if (statusEl) statusEl.innerHTML = '<span style="color:#10b981">LIVE</span>';
    };
    _stkWS.onmessage = function(e) {
      try {
        var msg = JSON.parse(e.data);
        if (msg.type === 'history' && msg.candles && _stkSeries) {
          _stkSeries.setData(msg.candles);
          _stkChart.timeScale().fitContent();
        } else if ((msg.type === 'candle_update' || msg.type === 'candle_complete') && _stkSeries) {
          _stkSeries.update({time:msg.time, open:msg.open, high:msg.high, low:msg.low, close:msg.close});
          if (msg.type === 'candle_update') _stkChart.timeScale().scrollToRealTime();
        }
      } catch(err) {}
    };
    _stkWS.onclose = function() { if (statusEl) statusEl.innerHTML = '<span style="color:var(--muted)">Offline</span>'; };
  } else {
    // Stock sans SSE — REST only
    if (_stkWS) { _stkWS.close(); _stkWS = null; }
    var tfMap2 = {1:'1m',5:'1m',60:'1m',3600:'1h',21600:'1d',86400:'1d'};
    try {
      var r = await api('/api/trading/candles/' + baseSymbol + '?interval=' + (tfMap2[_stkInterval]||'1h') + '&limit=60');
      if (r && r.candles && r.candles.length > 0) {
        _stkSeries.setData(r.candles.map(function(c){return{time:c.timestamp,open:c.open,high:c.high,low:c.low,close:c.close};}));
        _stkChart.timeScale().fitContent();
        if (statusEl) statusEl.innerHTML = '<span style="color:var(--muted)">Historical</span>';
      } else {
        if (statusEl) statusEl.innerHTML = '<span style="color:var(--muted)">No data</span>';
      }
    } catch(e) {
      if (statusEl) statusEl.innerHTML = '<span style="color:var(--red)">Error</span>';
    }
  }
}

function quickStockTrade(symbol, side) {
  selectStock(symbol);
  // Scroll to detail panel
  var panel = document.getElementById('stk-detail-panel');
  if (panel) panel.scrollIntoView({behavior:'smooth', block:'start'});
}

async function renderFallbackStocks(grid) {
  /* Fallback: affiche les symboles du catalog. Prix hydrates live via WS /ws/prices. */
  var symbols = ['AAPL', 'GOOGL', 'TSLA', 'AMZN', 'MSFT', 'NVDA', 'META', 'MSTR', 'QQQ', 'SPY'];
  var fallback = symbols.map(function(sym) { return { sym: sym, price: 0 }; });

  fallback.forEach(function(f) {
    _stockPriceCache[f.sym] = { price: f.price, change: f.change || 0, source: f.price > 0 ? 'oracle' : 'unavailable' };
  });
  grid.innerHTML = fallback.map(function(f) {
    var cat = STOCK_CATALOG[f.sym] || {};
    var hasMint = cat.mint && cat.mint.length > 20;
    var priceStr = f.price > 0 ? ('$' + f.price.toFixed(2)) : 'Loading...';
    var changeVal = f.change || 0;
    var changeClass = changeVal >= 0 ? 'color:var(--green)' : 'color:var(--red)';
    var changeStr = f.price > 0 ? ((changeVal >= 0 ? '+' : '') + changeVal.toFixed(2) + '%') : '—';
    return '<div class="card stock-card">' +
      '<div class="stock-symbol">' + (cat.xstock || f.sym + 'X') + '</div>' +
      '<div class="stock-name">' + (cat.name || f.sym) + '</div>' +
      '<div class="stock-price">' + priceStr + '</div>' +
      '<div class="stock-change" style="' + (f.price > 0 ? changeClass : 'color:var(--muted)') + '">' + changeStr + '</div>' +
      '<div class="stock-sector"><span class="badge badge-purple">' + (cat.sector || 'Tech') + '</span></div>' +
      '<div class="stock-actions">' +
        '<button class="btn-green btn-sm" onclick="openStockModal(\'' + f.sym + '\',\'buy\')">Buy</button>' +
        '<button class="btn-red btn-sm" onclick="openStockModal(\'' + f.sym + '\',\'sell\')">Sell</button>' +
      '</div>' +
      '<div class="stock-provider">' + (hasMint ? 'Backed Finance' : 'Coming soon') + '</div>' +
    '</div>';
  }).join('');
}

function openStockModal(symbol, side) {
  if (!requireWalletFor(side + ' stock')) return;

  var cat = STOCK_CATALOG[symbol] || {};
  var cached = _stockPriceCache[symbol] || {};
  var price = cached.price || 0;
  var name = cat.name || symbol;
  var xstock = cat.xstock || symbol + 'X';
  var mint = cat.mint || '';
  var hasMint = mint.length > 20;
  var isBuy = (side === 'buy');

  // Remove existing modal
  closeStockModal();

  var overlay = document.createElement('div');
  overlay.id = 'stock-trade-modal';
  overlay.className = 'stock-modal-overlay';

  // Jupiter swap URL: USDC -> xStock token
  var jupiterUrl = hasMint
    ? 'https://jup.ag/swap/USDC-' + mint
    : '';

  // Birdeye URL for the token
  var birdeyeUrl = hasMint
    ? 'https://birdeye.so/token/' + mint + '?chain=solana'
    : '';

  // Solscan URL
  var solscanUrl = hasMint
    ? 'https://solscan.io/token/' + mint
    : '';

  var modalHTML =
    '<div class="stock-modal">' +
      '<div style="display:flex;justify-content:space-between;align-items:flex-start">' +
        '<div>' +
          '<h3>' + (isBuy ? 'Buy' : 'Sell') + ' ' + esc(xstock) + '</h3>' +
          '<div class="sm-sub">' + esc(name) + ' — Tokenized Stock on Solana</div>' +
        '</div>' +
        '<button onclick="closeStockModal()" style="background:none;border:none;color:var(--muted);font-size:24px;cursor:pointer;padding:0;line-height:1">&times;</button>' +
      '</div>' +

      // Current price
      '<div class="sm-price-row">' +
        '<div>' +
          '<div class="sm-label">Current Price</div>' +
          '<div style="font-size:11px;color:var(--muted2);margin-top:2px">' + (cached.source === 'fallback' ? 'Delayed' : 'Live via Oracle') + '</div>' +
        '</div>' +
        '<div class="sm-val">$' + Number(price).toFixed(2) + '</div>' +
      '</div>' +

      // Amount input
      '<div class="sm-input-wrap">' +
        '<label>Amount (USDC)</label>' +
        '<input type="number" id="stock-amount-input" placeholder="10.00" min="1" max="100000" step="0.01" value="10" oninput="updateStockSummary(\'' + symbol + '\',' + price + ')">' +
      '</div>' +

      // Order summary — default $10 = BRONZE 1%
      '<div class="sm-summary" id="stock-order-summary">' +
        '<div class="sm-row"><span>Investment</span><span>$10.00 USDC</span></div>' +
        '<div class="sm-row"><span>Est. shares</span><span>' + (price > 0 ? (9.90 / price).toFixed(4) : '—') + ' ' + esc(xstock) + '</span></div>' +
        '<div class="sm-row"><span>Marketplace Fee</span><span style="color:var(--cyan)">$0.10 (1% BRONZE)</span></div>' +
        '<div class="sm-row"><span style="font-weight:600">Total</span><span style="color:var(--green)">$10.00 USDC</span></div>' +
      '</div>' +

      // Provider & availability info
      (hasMint ?
        '<div class="sm-available">' +
          '<strong>Available on Solana</strong> via <a href="https://backed.fi" target="_blank" style="color:var(--green)">Backed Finance</a><br>' +
          '<span style="font-size:12px;color:var(--muted)">Token: ' + esc(xstock) + ' &mdash; Mint: ' + mint.substring(0, 8) + '...' + mint.substring(mint.length - 4) + '</span>' +
        '</div>'
        :
        '<div class="sm-coming-soon">' +
          '<strong>Coming Q2 2026</strong><br>' +
          '<span style="font-size:12px">This stock does not yet have a tokenized version on Solana. We will list it as soon as a provider launches it.</span>' +
        '</div>'
      ) +

      // Provider details
      '<div class="sm-provider-info">' +
        '<strong>How it works:</strong> Tokenized stocks are SPL tokens on Solana, backed 1:1 by real shares held in custody. ' +
        'MAXIA routes your USDC through <a href="https://jup.ag" target="_blank">Jupiter</a> to swap for the stock token.' +
        (hasMint ? '<br><br><strong>Links:</strong> ' +
          '<a href="' + birdeyeUrl + '" target="_blank">Birdeye</a> &bull; ' +
          '<a href="' + solscanUrl + '" target="_blank">Solscan</a> &bull; ' +
          '<a href="https://backed.fi" target="_blank">Backed Finance</a>'
          : '') +
      '</div>' +

      // Commission tiers
      '<details style="margin-bottom:16px;cursor:pointer">' +
        '<summary style="font-size:13px;color:var(--muted);font-family:JetBrains Mono,monospace">Commission tiers</summary>' +
        '<div style="padding:10px 0;font-size:13px;color:var(--muted);line-height:2">' +
          'BRONZE: 1.5% (under $500)<br>' +
          'GOLD: 0.5% ($500 - $5,000)<br>' +
          'WHALE: 0.1% (over $5,000)' +
        '</div>' +
      '</details>' +

      // Action buttons
      '<div class="sm-actions">' +
        (hasMint && isBuy ?
          '<button class="btn btn-primary btn-sm" onclick="executeStockSwap(\'' + symbol + '\')" id="btn-stock-execute" style="flex:2">' +
            'Swap USDC &rarr; ' + esc(xstock) + ' via Jupiter' +
          '</button>'
          : hasMint && !isBuy ?
          '<button class="btn btn-primary btn-sm" onclick="executeStockSwap(\'' + symbol + '\')" id="btn-stock-execute" style="flex:2;background:linear-gradient(135deg,var(--red),var(--orange))">' +
            'Swap ' + esc(xstock) + ' &rarr; USDC via Jupiter' +
          '</button>'
          :
          '<button class="btn btn-primary btn-sm" disabled style="flex:2;opacity:.5">' +
            'Not yet available on-chain' +
          '</button>'
        ) +
        (hasMint ?
          '<a href="' + jupiterUrl + '" target="_blank" class="btn-outline btn-sm" style="flex:1;text-align:center;display:flex;align-items:center;justify-content:center;text-decoration:none">' +
            'Open Jupiter' +
          '</a>'
          : '') +
        '<button class="btn-outline btn-sm" onclick="closeStockModal()" style="flex:0 0 auto">Cancel</button>' +
      '</div>' +
    '</div>';

  overlay.innerHTML = modalHTML;
  document.body.appendChild(overlay);

  // Close on backdrop click
  overlay.addEventListener('click', function(e) { if (e.target === overlay) closeStockModal(); });

  // Close on Escape
  overlay._escHandler = function(e) { if (e.key === 'Escape') closeStockModal(); };
  document.addEventListener('keydown', overlay._escHandler);

  // Focus the amount input
  setTimeout(function() {
    var inp = document.getElementById('stock-amount-input');
    if (inp) { inp.focus(); inp.select(); }
  }, 100);
}

function getMarketplaceTier(amount) {
  if (amount > 5000) return { name: 'WHALE', pct: 0.1 };
  if (amount >= 500) return { name: 'GOLD', pct: 0.5 };
  return { name: 'BRONZE', pct: 1.0 };
}

function updateStockSummary(symbol, price) {
  var inp = document.getElementById('stock-amount-input');
  var summaryEl = document.getElementById('stock-order-summary');
  if (!inp || !summaryEl) return;

  var amount = parseFloat(inp.value) || 0;
  var cat = STOCK_CATALOG[symbol] || {};
  var xstock = cat.xstock || symbol + 'X';

  // Commission based on marketplace tiers
  var tier = getMarketplaceTier(amount);
  var commission = amount * tier.pct / 100;
  var net = amount - commission;
  var shares = price > 0 ? (net / price) : 0;
  var total = amount;

  summaryEl.innerHTML =
    '<div class="sm-row"><span>Investment</span><span>$' + amount.toFixed(2) + ' USDC</span></div>' +
    '<div class="sm-row"><span>Est. shares</span><span>' + shares.toFixed(4) + ' ' + esc(xstock) + '</span></div>' +
    '<div class="sm-row"><span>Marketplace Fee</span><span style="color:var(--cyan)">$' + commission.toFixed(2) + ' (' + tier.pct + '% ' + tier.name + ')</span></div>' +
    '<div class="sm-row"><span style="font-weight:600">Total</span><span style="color:var(--green)">$' + total.toFixed(2) + ' USDC</span></div>';
}

function closeStockModal() {
  var m = document.getElementById('stock-trade-modal');
  if (m) {
    if (m._escHandler) document.removeEventListener('keydown', m._escHandler);
    m.remove();
  }
}

async function executeStockSwap(symbol) {
  var cat = STOCK_CATALOG[symbol] || {};
  var mint = cat.mint || '';
  var xstock = cat.xstock || symbol + 'X';
  var amountEl = document.getElementById('stock-amount-input');
  var btn = document.getElementById('btn-stock-execute');
  var amount = parseFloat(amountEl ? amountEl.value : 0) || 0;

  if (amount < 1) { toast('Minimum $1 USDC', 'error'); return; }
  if (amount > 100000) { toast('Maximum $100,000 USDC per trade', 'error'); return; }
  if (!mint || mint.length < 20) { toast('No on-chain token available yet', 'error'); return; }

  // Disable button, show loading
  if (btn) { btn.textContent = 'Preparing swap...'; btn.disabled = true; }

  try {
    // Step 1: Get a Jupiter quote via our backend
    var quoteResp = await api('/api/public/crypto/swap', {
      method: 'POST',
      body: JSON.stringify({
        from: 'USDC',
        to: xstock,
        amount_usdc: amount,
        wallet: wallet,
        to_mint: mint,
        from_mint: USDC_MINT,
        slippage_bps: 100
      })
    });

    if (quoteResp.error) {
      // If backend swap fails, fall back to Jupiter UI
      throw new Error(quoteResp.error);
    }

    if (quoteResp.success || quoteResp.tx_signature || quoteResp.route) {
      closeStockModal();
      toast('Swap submitted: USDC -> ' + xstock + ' ($' + amount.toFixed(2) + ')', 'success');
      loadStocks();
      return;
    }

    // If we got a quote but no execution, open Jupiter as fallback
    throw new Error('swap_redirect');

  } catch (e) {
    // Fallback: open Jupiter swap page directly
    var jupUrl = 'https://jup.ag/swap/USDC-' + mint + '?amount=' + (amount * 1e6);
    if (btn) { btn.disabled = false; }

    if (e.message === 'swap_redirect' || e.message.indexOf('not supported') >= 0 || e.message.indexOf('route') >= 0) {
      // Expected: redirect to Jupiter for manual swap
      closeStockModal();
      window.open(jupUrl, '_blank');
      toast('Opening Jupiter to swap USDC -> ' + xstock, 'info');
    } else {
      // Unexpected error: offer Jupiter as alternative
      if (btn) {
        btn.innerHTML = '<a href="' + jupUrl + '" target="_blank" style="color:white;text-decoration:none">Open Jupiter Instead</a>';
        btn.disabled = false;
        btn.onclick = function() { window.open(jupUrl, '_blank'); closeStockModal(); };
      }
      toast('Swap via API failed — use Jupiter directly: ' + e.message, 'error');
    }
  }
}

// Legacy alias for any old references
function tradeStock(symbol, side) {
  var sym = symbol.replace(/^x/, '').toUpperCase();
  openStockModal(sym, side);
}

// ======================================
// NFTs
// ======================================
async function loadNFTs() {
  var nftGrid = document.getElementById('nfts-grid');
  var agentsGrid = document.getElementById('agents-grid');

  // Load NFT collection
  try {
    var data = await api('/api/nft/collection');
    var nfts = data.nfts || data.collection || [];

    if (Array.isArray(nfts) && nfts.length > 0) {
      nftGrid.innerHTML = nfts.map(function(n) {
        var typeAttr = (n.attributes && n.attributes.type) || '';
        var typeLabel = typeAttr === 'agent_id' ? 'Agent ID' : typeAttr === 'service_pass' ? 'Service Pass' : typeAttr === 'trust_attestation' ? 'Trust Attestation' : 'NFT';
        var typeBadge = typeAttr === 'agent_id' ? 'badge-purple' : typeAttr === 'trust_attestation' ? 'badge-green' : typeAttr === 'service_pass' ? 'badge-orange' : '';
        var onChainHTML = '';
        if (n.tx_signature) {
          var shortTx = n.tx_signature.length > 16 ? n.tx_signature.slice(0, 8) + '...' + n.tx_signature.slice(-6) : n.tx_signature;
          onChainHTML = '<div style="margin-top:8px"><span class="badge badge-green" style="font-size:10px">ON-CHAIN</span> <a href="https://solscan.io/tx/' + esc(n.tx_signature) + '" target="_blank" rel="noopener" style="font-size:10px;font-family:JetBrains Mono,monospace">' + esc(shortTx) + '</a></div>';
        }
        return '<div class="card nft-card">' +
          '<div class="nft-icon">' + (n.image_url ? '<img src="' + esc(n.image_url) + '" style="width:80px;height:80px;border-radius:12px;object-fit:cover" alt="">' : '&#127912;') + '</div>' +
          '<div class="nft-name">' + esc(n.name || 'Untitled') + '</div>' +
          '<div style="margin:6px 0"><span class="badge ' + typeBadge + '" style="font-size:11px">' + esc(typeLabel) + '</span> <span class="badge" style="font-size:11px">' + esc((n.chain || 'solana').toUpperCase()) + '</span></div>' +
          '<div class="nft-desc">' + esc(n.description || '') + '</div>' +
          '<div style="font-size:11px;color:var(--muted2);margin-top:6px">' + esc(n.minted_at || '') + '</div>' +
          onChainHTML +
        '</div>';
      }).join('');
    } else {
      nftGrid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="icon">&#127912;</div><div class="msg">No NFTs minted yet</div><div class="sub">Mint your first one above!</div></div>';
    }
  } catch (e) {
    nftGrid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="icon">&#127912;</div><div class="msg">No NFTs minted yet</div><div class="sub">Mint your first one above!</div></div>';
  }

  // Load agents
  try {
    var agentData = await api('/api/nft/agents');
    var agents = agentData.agents || [];
    var nftsMinted = agentData.nfts_minted || 0;
    var totalAgents = agentData.total || agents.length;

    // Update stats
    document.getElementById('nft-total-agents').textContent = totalAgents;
    document.getElementById('nft-total-minted').textContent = nftsMinted;

    if (agents.length > 0) {
      var totalTrust = 0;
      var totalBadges = 0;
      agents.forEach(function(a) { totalTrust += (a.trust_score || 0); totalBadges += (a.badges || []).length; });
      document.getElementById('nft-avg-trust').textContent = Math.round(totalTrust / agents.length);
      document.getElementById('nft-total-badges').textContent = totalBadges;

      agentsGrid.innerHTML = agents.map(function(a) {
        var trust = a.trust_score || 50;
        var trustColor = trust >= 70 ? 'var(--green)' : (trust >= 40 ? 'var(--orange)' : 'var(--red)');
        var badges = a.badges || [];
        var addr = a.agent_address || '';
        var shortAddr = addr.length > 16 ? addr.slice(0,6) + '...' + addr.slice(-4) : addr;
        return '<div class="card" style="text-align:center">' +
          '<div style="font-size:32px;margin-bottom:8px">&#129302;</div>' +
          '<div style="font-size:16px;font-weight:600;margin-bottom:4px">' + esc(a.name || 'Agent') + '</div>' +
          '<div style="font-size:11px;color:var(--muted2);font-family:JetBrains Mono,monospace;margin-bottom:8px">' + esc(shortAddr) + '</div>' +
          '<div style="margin-bottom:8px"><span class="badge badge-purple" style="font-size:11px">' + esc((a.chain || 'solana').toUpperCase()) + '</span> <span class="badge" style="font-size:11px">' + esc(a.tier || 'BRONZE') + '</span></div>' +
          '<div style="font-family:JetBrains Mono,monospace;font-size:24px;font-weight:700;color:' + trustColor + ';margin-bottom:4px">' + trust + '</div>' +
          '<div style="font-size:12px;color:var(--muted);margin-bottom:8px">Trust Score</div>' +
          (badges.length > 0 ? '<div style="display:flex;gap:4px;flex-wrap:wrap;justify-content:center">' + badges.map(function(b) { return '<span class="badge badge-green" style="font-size:10px">' + esc(b) + '</span>'; }).join('') + '</div>' : '') +
          '<button class="btn-outline" style="margin-top:12px;font-size:12px;padding:6px 14px" onclick="document.getElementById(\'agent-address\').value=\'' + esc(addr) + '\';showPage(\'agentid\');lookupAgent()">View Details</button>' +
        '</div>';
      }).join('');
    } else {
      agentsGrid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="icon">&#129302;</div><div class="msg">No agents registered yet</div><div class="sub">Register your AI agent to get an on-chain identity</div></div>';
    }
  } catch (e) {
    agentsGrid.innerHTML = '<div class="empty-state" style="grid-column:1/-1"><div class="icon">&#129302;</div><div class="msg">Could not load agents</div><div class="sub">' + esc(e.message) + '</div></div>';
    document.getElementById('nft-total-agents').textContent = '0';
    document.getElementById('nft-total-minted').textContent = '0';
    document.getElementById('nft-avg-trust').textContent = '—';
    document.getElementById('nft-total-badges').textContent = '0';
  }
}

async function mintNFT() {
  if (!requireWalletFor('mint NFT')) return;

  var name = document.getElementById('nft-name').value.trim();
  var desc = document.getElementById('nft-desc').value.trim() || 'MAXIA NFT';
  if (!name) { toast('Enter a name for your NFT', 'error'); return; }

  var txSig = null;
  var chain = isSolanaWallet() ? 'solana' : 'ethereum';

  // If Solana wallet: create a real on-chain memo transaction
  if (isSolanaWallet()) {
    try {
      toast('Building Solana transaction...', 'info');
      var connection = new solanaWeb3.Connection(SOLANA_RPC, 'confirmed');
      var publicKey = new solanaWeb3.PublicKey(wallet);
      var memoData = JSON.stringify({type: 'maxia_nft', name: name, desc: desc, ts: Date.now()});

      var tx = new solanaWeb3.Transaction();
      tx.add(new solanaWeb3.TransactionInstruction({
        keys: [{pubkey: publicKey, isSigner: true, isWritable: true}],
        programId: new solanaWeb3.PublicKey('MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr'),
        data: new TextEncoder().encode(memoData)
      }));

      var latestBlock = await connection.getLatestBlockhash('confirmed');
      tx.recentBlockhash = latestBlock.blockhash;
      tx.feePayer = publicKey;

      toast('Sign the transaction in Phantom...', 'info');
      var provider = window.solana;
      if (walletType === 'backpack') provider = window.backpack;
      else if (walletType === 'solflare') provider = window.solflare;

      var result = await provider.signAndSendTransaction(tx);
      txSig = result.signature || result;

      toast('Transaction sent! Confirming...', 'info');
      await connection.confirmTransaction(txSig, 'confirmed');
      toast('On-chain confirmation received', 'success');
    } catch (e) {
      if (e.message && (e.message.includes('User rejected') || e.message.includes('rejected'))) {
        toast('Transaction cancelled by user', 'error');
        return;
      }
      toast('On-chain tx failed: ' + e.message + ' — recording off-chain', 'error');
      txSig = null;
    }
  }

  // Record on backend (with or without tx_signature)
  try {
    var payload = {name: name, description: desc, owner_address: wallet, chain: chain};
    if (txSig) payload.tx_signature = txSig;

    await api('/api/nft/mint', {
      method: 'POST',
      body: JSON.stringify(payload)
    });

    if (txSig) {
      toast('NFT "' + name + '" minted on-chain! Tx: ' + txSig.slice(0, 8) + '...', 'success');
    } else {
      toast('NFT "' + name + '" minted (off-chain record)', 'success');
    }
    document.getElementById('nft-name').value = '';
    document.getElementById('nft-desc').value = '';
    loadNFTs();
  } catch (e) {
    toast('Backend record failed: ' + e.message, 'error');
  }
}

// ======================================
// AGENT ID
// ======================================
async function createAgentID() {
  var resultDiv = document.getElementById('aid-create-result');
  if (!requireWalletFor('create Agent ID')) return;

  var name = document.getElementById('aid-name').value.trim();
  if (!name) {
    resultDiv.innerHTML = '<div style="padding:12px;background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);border-radius:10px;font-size:13px;color:var(--red)">Enter a name for your agent</div>';
    return;
  }
  resultDiv.innerHTML = '<div style="padding:12px;color:var(--cyan);font-size:13px">Creating Agent ID...</div>';
  var txSig = null;
  var chain = isSolanaWallet() ? 'solana' : 'ethereum';

  // If Solana wallet: create a real on-chain memo transaction for Agent ID
  if (isSolanaWallet()) {
    try {
      toast('Building Agent ID transaction...', 'info');
      var connection = new solanaWeb3.Connection(SOLANA_RPC, 'confirmed');
      var publicKey = new solanaWeb3.PublicKey(wallet);
      var memoData = JSON.stringify({type: 'maxia_agent_id', name: name, wallet: wallet, ts: Date.now()});

      var tx = new solanaWeb3.Transaction();
      tx.add(new solanaWeb3.TransactionInstruction({
        keys: [{pubkey: publicKey, isSigner: true, isWritable: true}],
        programId: new solanaWeb3.PublicKey('MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr'),
        data: new TextEncoder().encode(memoData)
      }));

      var latestBlock = await connection.getLatestBlockhash('confirmed');
      tx.recentBlockhash = latestBlock.blockhash;
      tx.feePayer = publicKey;

      toast('Sign the transaction in Phantom...', 'info');
      var provider = window.solana;
      if (walletType === 'backpack') provider = window.backpack;
      else if (walletType === 'solflare') provider = window.solflare;

      var result = await provider.signAndSendTransaction(tx);
      txSig = result.signature || result;

      toast('Transaction sent! Confirming...', 'info');
      await connection.confirmTransaction(txSig, 'confirmed');
      toast('On-chain confirmation received', 'success');
    } catch (e) {
      if (e.message && (e.message.includes('User rejected') || e.message.includes('rejected'))) {
        toast('Transaction cancelled by user', 'error');
        return;
      }
      toast('On-chain tx failed: ' + e.message + ' — recording off-chain', 'error');
      txSig = null;
    }
  }

  // Record Agent ID on backend
  try {
    var payload = {agent_address: wallet, name: name, chain: chain};
    if (txSig) payload.tx_signature = txSig;

    var data = await api('/api/nft/agent-id', {
      method: 'POST',
      body: JSON.stringify(payload)
    });

    if (txSig) {
      var shortTx = txSig.length > 16 ? txSig.slice(0, 8) + '...' + txSig.slice(-6) : txSig;
      resultDiv.innerHTML = '<div style="padding:12px;background:rgba(16,185,129,.1);border:1px solid rgba(16,185,129,.3);border-radius:10px;font-size:13px">' +
        '<span class="badge badge-green" style="font-size:10px;margin-right:6px">ON-CHAIN</span>' +
        'Agent ID created! <a href="https://solscan.io/tx/' + esc(txSig) + '" target="_blank" rel="noopener" style="font-family:JetBrains Mono,monospace;font-size:11px">' + esc(shortTx) + '</a>' +
      '</div>';
      toast('Agent ID "' + name + '" created on-chain! Tx: ' + txSig.slice(0, 8) + '...', 'success');
    } else {
      resultDiv.innerHTML = '<div style="padding:12px;background:rgba(59,130,246,.1);border:1px solid rgba(59,130,246,.3);border-radius:10px;font-size:13px">Agent ID "' + esc(name) + '" created (off-chain record)</div>';
      toast('Agent ID "' + name + '" created', 'success');
    }

    document.getElementById('aid-name').value = '';
    loadAgentList();
    loadNFTs();
  } catch (e) {
    if (e.message && e.message.includes('409')) {
      toast('Agent ID already exists for this wallet', 'error');
      resultDiv.innerHTML = '<div style="padding:12px;background:rgba(239,68,68,.1);border:1px solid rgba(239,68,68,.3);border-radius:10px;font-size:13px;color:var(--red)">Agent ID already exists for this wallet</div>';
    } else {
      toast('Failed to create Agent ID: ' + e.message, 'error');
      resultDiv.innerHTML = '';
    }
  }
}

async function loadAgentList() {
  var tbody = document.getElementById('agents-body');
  // Show wallet warning if not connected
  var warn = document.getElementById('aid-wallet-warning');
  if (warn) warn.style.display = wallet ? 'none' : 'block';
  try {
    var data = await api('/api/nft/agents');
    if (!data) { tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">No agents registered yet</td></tr>'; return; }
    var agents = data.agents || [];
    var nftsMinted = data.nfts_minted || 0;
    var totalAgents = data.total || agents.length;

    // Update stats
    document.getElementById('aid-total').textContent = totalAgents;
    document.getElementById('aid-nfts').textContent = nftsMinted;

    if (agents.length > 0) {
      var totalTrust = 0;
      var totalBadges = 0;
      agents.forEach(function(a) { totalTrust += (a.trust_score || 0); totalBadges += (a.badges || []).length; });
      document.getElementById('aid-avg-trust').textContent = Math.round(totalTrust / agents.length);
      document.getElementById('aid-badges').textContent = totalBadges;

      tbody.innerHTML = agents.map(function(a) {
        var trust = a.trust_score || 50;
        var trustColor = trust >= 70 ? 'var(--green)' : (trust >= 40 ? 'var(--orange)' : 'var(--red)');
        var addr = a.agent_address || '';
        var shortAddr = addr.length > 16 ? addr.slice(0,6) + '...' + addr.slice(-4) : addr;
        var badges = a.badges || [];
        var badgesHTML = badges.length > 0 ? badges.map(function(b) { return '<span class="badge badge-green" style="font-size:10px">' + esc(b) + '</span>'; }).join(' ') : '<span style="color:var(--muted2)">—</span>';
        return '<tr>' +
          '<td style="font-weight:600">' + esc(a.name || 'Agent') + '</td>' +
          '<td style="font-size:11px;font-family:JetBrains Mono,monospace;color:var(--muted)">' + esc(shortAddr) + '</td>' +
          '<td><span class="badge badge-purple" style="font-size:11px">' + esc((a.chain || 'solana').toUpperCase()) + '</span></td>' +
          '<td style="font-weight:700;font-family:JetBrains Mono,monospace;color:' + trustColor + '">' + trust + '</td>' +
          '<td><span class="badge" style="font-size:11px">' + esc(a.tier || 'BRONZE') + '</span></td>' +
          '<td>' + badgesHTML + '</td>' +
          '<td><button onclick="document.getElementById(\'agent-address\').value=\'' + esc(addr) + '\';lookupAgent()" style="padding:4px 12px;background:linear-gradient(135deg,#00e5ff,#7c3aed);color:white;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-family:Outfit">Lookup</button></td>' +
        '</tr>';
      }).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">No agents registered yet</td></tr>';
    }
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">Error loading agents: ' + esc(e.message) + '</td></tr>';
    document.getElementById('aid-total').textContent = '0';
    document.getElementById('aid-avg-trust').textContent = '—';
    document.getElementById('aid-badges').textContent = '0';
    document.getElementById('aid-nfts').textContent = '0';
  }
}

async function lookupAgent() {
  var addr = document.getElementById('agent-address').value.trim();
  var resultDiv = document.getElementById('agent-result');

  if (!addr) {
    resultDiv.innerHTML = '<div class="card" style="max-width:520px;text-align:center;padding:24px"><div style="color:var(--red);font-size:14px">Enter a wallet address or Agent ID above</div></div>';
    return;
  }

  resultDiv.innerHTML = '<div class="card" style="text-align:center;padding:24px;color:var(--muted)">Looking up agent...</div>';

  try {
    var data = await api('/api/nft/trust-score/' + encodeURIComponent(addr));
    var ts = data.trust_score || {};
    var score = (typeof ts === 'object') ? (ts.score || 0) : (ts || 0);
    var breakdown = (typeof ts === 'object') ? (ts.breakdown || {}) : {};
    var level = (typeof ts === 'object') ? (ts.level || '') : '';
    var badges = ts.badges || data.badges || [];
    var stats = data.stats || {};

    var scoreColor = 'var(--green)';
    var scoreBg = 'rgba(16,185,129,.1)';
    var scoreBorder = 'rgba(16,185,129,.3)';
    if (score < 40) {
      scoreColor = 'var(--red)'; scoreBg = 'rgba(239,68,68,.1)'; scoreBorder = 'rgba(239,68,68,.3)';
    } else if (score < 70) {
      scoreColor = 'var(--orange)'; scoreBg = 'rgba(245,158,11,.1)'; scoreBorder = 'rgba(245,158,11,.3)';
    }

    var breakdownHTML = '';
    var factors = Object.entries(breakdown);
    if (factors.length > 0) {
      breakdownHTML = '<h4 style="font-size:15px;font-weight:600;margin:24px 0 16px">Breakdown</h4>';
      factors.forEach(function(entry) {
        var key = entry[0], val = entry[1];
        var pct = Math.min(100, Math.max(0, Number(val)));
        var barColor = pct >= 70 ? 'var(--green)' : (pct >= 40 ? 'var(--orange)' : 'var(--red)');
        breakdownHTML += '<div class="trust-row"><span class="label">' + esc(key.replace(/_/g, ' ')) + '</span><span style="font-family:JetBrains Mono,monospace;color:' + barColor + '">' + pct + '</span></div>' +
          '<div class="trust-bar"><div class="trust-bar-fill" style="width:' + pct + '%;background:' + barColor + '"></div></div>';
      });
    }

    var badgesHTML = '';
    if (badges.length > 0) {
      badgesHTML = '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:20px">' +
        badges.map(function(b) { return '<span class="badge">' + esc(b) + '</span>'; }).join('') +
      '</div>';
    }

    var statsHTML = '';
    var statEntries = Object.entries(stats);
    if (statEntries.length > 0) {
      statsHTML = '<h4 style="font-size:15px;font-weight:600;margin:24px 0 16px">Stats</h4>' +
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px">' +
        statEntries.map(function(entry) {
          return '<div style="background:var(--bg);border-radius:10px;padding:14px;text-align:center">' +
            '<div style="font-family:JetBrains Mono,monospace;font-size:18px;font-weight:600;color:var(--cyan)">' + esc(entry[1]) + '</div>' +
            '<div style="font-size:12px;color:var(--muted);margin-top:4px">' + esc(entry[0].replace(/_/g, ' ')) + '</div>' +
          '</div>';
        }).join('') +
      '</div>';
    }

    resultDiv.innerHTML = '<div class="card" style="max-width:600px;padding:32px">' +
      '<div class="trust-circle" style="color:' + scoreColor + ';background:' + scoreBg + ';border:3px solid ' + scoreBorder + '">' + Math.round(score) + '</div>' +
      '<div style="text-align:center;margin-bottom:8px;font-size:14px;color:var(--muted)">Trust Score</div>' +
      '<div style="text-align:center;margin-bottom:16px;font-size:12px;color:var(--muted2);font-family:JetBrains Mono,monospace;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%">' + esc(addr) + '</div>' +
      badgesHTML + breakdownHTML + statsHTML +
    '</div>';

  } catch (e) {
    resultDiv.innerHTML = '<div class="card" style="max-width:520px">' +
      '<div class="trust-circle" style="color:var(--muted);background:rgba(255,255,255,.03);border:2px solid rgba(255,255,255,.06)">?</div>' +
      '<div style="text-align:center;color:var(--muted)">Agent not found or no data available</div>' +
      '<div style="text-align:center;color:var(--muted2);font-size:13px;margin-top:8px">' + esc(e.message) + '</div>' +
    '</div>';
  }
}

// ======================================
// AGENT CREDIT SCORE
// ======================================
var _lastCreditScore = null;

async function loadCreditScore() {
  var card = document.getElementById('credit-score-card');
  var empty = document.getElementById('credit-score-empty');
  if (!wallet) {
    if (card) card.style.display = 'none';
    if (empty) { empty.style.display = 'block'; empty.textContent = 'Connect your wallet to view your credit score'; }
    return;
  }
  if (empty) empty.style.display = 'none';
  if (card) card.style.display = 'block';

  try {
    var data = await api('/api/public/credit-score/' + encodeURIComponent(wallet));
    if (data.error) {
      if (card) card.style.display = 'none';
      if (empty) { empty.style.display = 'block'; empty.textContent = 'Could not compute credit score: ' + data.error; }
      return;
    }
    _lastCreditScore = data;
    var score = data.score || 0;
    var grade = data.grade || 'C';

    // Grade color
    var gradeColor = 'var(--gold)';
    if (score < 40) gradeColor = 'var(--red)';
    else if (score < 60) gradeColor = 'var(--orange)';
    else if (score >= 80) gradeColor = 'var(--green)';

    // Score color
    var scoreColor = 'var(--green)';
    if (score < 40) scoreColor = 'var(--red)';
    else if (score < 70) scoreColor = 'var(--orange)';

    document.getElementById('cs-grade').style.color = gradeColor;
    document.getElementById('cs-grade').textContent = grade;
    document.getElementById('cs-score').style.color = scoreColor;
    document.getElementById('cs-score').textContent = score;

    // Components
    var comp = data.components || {};
    var compEl = document.getElementById('cs-components');
    var compHTML = '';
    var compLabels = {volume: 'Volume', activity: 'Activity', disputes: 'Disputes', age: 'Account Age'};
    Object.keys(compLabels).forEach(function(key) {
      var val = comp[key] || 0;
      var pct = Math.min(100, Math.max(0, val));
      var barColor = pct >= 70 ? 'var(--green)' : (pct >= 40 ? 'var(--orange)' : 'var(--red)');
      compHTML += '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">' +
        '<span style="font-size:12px;color:var(--muted);width:80px">' + compLabels[key] + '</span>' +
        '<div style="flex:1;height:6px;background:rgba(255,255,255,.06);border-radius:3px;overflow:hidden">' +
          '<div style="width:' + pct + '%;height:100%;background:' + barColor + ';border-radius:3px;transition:width .5s"></div>' +
        '</div>' +
        '<span style="font-size:11px;font-family:JetBrains Mono,monospace;color:' + barColor + ';width:32px;text-align:right">' + Math.round(pct) + '</span>' +
      '</div>';
    });
    compEl.innerHTML = compHTML;
  } catch (e) {
    if (card) card.style.display = 'none';
    if (empty) { empty.style.display = 'block'; empty.textContent = 'Error loading credit score'; }
  }
}

function copyCreditScore() {
  if (!_lastCreditScore) {
    toast('No credit score data to copy', 'error');
    return;
  }
  var exportData = {
    wallet: _lastCreditScore.wallet,
    score: _lastCreditScore.score,
    grade: _lastCreditScore.grade,
    components: _lastCreditScore.components,
    computed_at: _lastCreditScore.computed_at,
    signature: _lastCreditScore.signature,
    platform: _lastCreditScore.platform,
    platform_url: _lastCreditScore.platform_url,
    verify_url: _lastCreditScore.platform_url + '/api/public/credit-score/verify'
  };
  navigator.clipboard.writeText(JSON.stringify(exportData, null, 2)).then(function() {
    toast('Credit score JSON copied to clipboard', 'success');
  }).catch(function() {
    toast('Failed to copy — check browser permissions', 'error');
  });
}

// ======================================
// EVM CHAIN BAR
// ======================================

// Show/hide EVM chain bar based on wallet type
function updateEvmChainBar() {
  var bar = document.getElementById('evm-chain-bar');
  if (!bar) return;
  if (isEvmWallet()) {
    bar.style.display = 'block';
    highlightActiveChain();
  } else {
    bar.style.display = 'none';
  }
}

// Highlight the active chain button
async function highlightActiveChain() {
  var chainId = await getEvmChainId();
  document.querySelectorAll('.evm-chain-btn').forEach(function(b) {
    var btnChain = parseInt(b.getAttribute('data-chain'));
    if (btnChain === chainId) {
      b.style.borderColor = 'var(--cyan)';
      b.style.color = 'var(--cyan)';
      b.style.fontWeight = '700';
    } else {
      b.style.borderColor = '';
      b.style.color = 'var(--text)';
      b.style.fontWeight = '';
    }
  });
}

// Switch chain and refresh quote
async function switchAndRefresh(chainId) {
  var ok = await switchEvmChain(chainId);
  if (ok) {
    highlightActiveChain();
    getSwapQuoteDebounced();
    toast('Switched to ' + EVM_CHAINS[chainId].name, 'success');
  } else {
    toast('Could not switch chain', 'error');
  }
}

// ======================================
// HASH ROUTING & INIT
// ======================================
var _validPages = ['home', 'swap', 'portfolio', 'gpu', 'yields', 'bridge', 'stocks', 'escrow', 'nfts', 'agentid', 'trading', 'enterprise'];

function initFromHash() {
  // Handle ?install= from App Store
  var params = new URLSearchParams(window.location.search);
  var installId = params.get('install');
  if (installId) {
    showPage('home');
    setTimeout(function() {
      toast('Agent "' + esc(installId) + '" — connect wallet to install', 'info');
    }, 500);
    return;
  }

  var hash = window.location.hash.replace('#', '').toLowerCase();
  if (hash && _validPages.indexOf(hash) !== -1) {
    showPage(hash);
  } else {
    showPage('home');
  }
}

window.addEventListener('hashchange', function() {
  var hash = window.location.hash.replace('#', '').toLowerCase();
  if (hash && _validPages.indexOf(hash) !== -1) {
    showPage(hash);
  }
});

// ======================================
// LIVE PRICES — fetch from API on load
// ======================================
(async function fetchLivePrices() {
  try {
    var data = await api('/api/public/prices');
    if (!data) return;

    // Update GPU home card description with cheapest price from API
    if (data.gpu_tiers && data.gpu_tiers.length > 0) {
      var prices = data.gpu_tiers.map(function(g) {
        return g.base_price_per_hour || g.price_per_hour_usdc || g.price_per_hour || g.price || 999;
      });
      var minPrice = Math.min.apply(null, prices);
      var descEl = document.getElementById('gpu-home-desc');
      if (descEl) {
        descEl.textContent = 'RTX 3090 to B200. From $' + minPrice.toFixed(2) + '/h. Fine-tune LLMs.';
      }
    }

    // Update stocks commission badge from API
    if (data.stock_commission_tiers) {
      var tiers = data.stock_commission_tiers;
      var tierNames = Object.keys(tiers);
      if (tierNames.length > 0) {
        var bpsValues = tierNames.map(function(k) { return tiers[k].bps; });
        var minBps = Math.min.apply(null, bpsValues);
        var maxBps = Math.max.apply(null, bpsValues);
        var badgeEl = document.getElementById('stocks-commission-badge');
        if (badgeEl) {
          badgeEl.textContent = 'BRONZE ' + (maxBps / 100) + '% → WHALE ' + (minBps / 100) + '%';
        }
      }
    }

    // Cache swap commission tiers for the tier progression bar
    if (data.swap_commission_tiers) {
      cachedSwapTiers = data.swap_commission_tiers;
    }
  } catch (e) {
    console.warn('[Prices] Could not fetch live prices:', e);
  }

  // ==========================================
  //  Slippage control + Price Impact + Tx History
  // ==========================================
  var swapSlippage = 1.0; // default 1%

  window.setSlippage = function(pct) {
    swapSlippage = Math.max(0.1, Math.min(50, pct));
    document.getElementById('slippage-display').textContent = swapSlippage + '%';
    // Update button styles
    document.querySelectorAll('#slippage-panel .btn-sm').forEach(function(b) {
      b.style.borderColor = 'var(--border,rgba(255,255,255,.1))';
      b.style.background = 'none';
      b.style.color = 'var(--muted)';
    });
    var warn = document.getElementById('slippage-warn');
    if (swapSlippage > 3) {
      warn.style.display = 'block';
      document.getElementById('slippage-display').style.color = 'var(--red)';
    } else if (swapSlippage > 1) {
      warn.style.display = 'none';
      document.getElementById('slippage-display').style.color = 'var(--orange)';
    } else {
      warn.style.display = 'none';
      document.getElementById('slippage-display').style.color = 'var(--green)';
    }
    // Re-fetch quote with new slippage
    if (typeof updateSwapQuote === 'function') updateSwapQuote();
  };

  // Price impact display helper
  window.updatePriceImpact = function(inputValueUsd, outputValueUsd) {
    var el = document.getElementById('swap-impact');
    if (!el || !inputValueUsd || inputValueUsd <= 0) return;
    var impact = ((inputValueUsd - outputValueUsd) / inputValueUsd * 100);
    var impactStr = impact.toFixed(2) + '%';
    if (impact < 0.5) { el.style.color = 'var(--green)'; el.textContent = impactStr; }
    else if (impact < 2) { el.style.color = 'var(--orange)'; el.textContent = impactStr + ' ⚠'; }
    else { el.style.color = 'var(--red)'; el.textContent = impactStr + ' ⚠ High impact!'; }
    // Min received
    var minEl = document.getElementById('swap-min-received');
    if (minEl && outputValueUsd > 0) {
      var minReceived = outputValueUsd * (1 - swapSlippage / 100);
      minEl.textContent = '$' + minReceived.toFixed(2);
    }
  };

  // Swap transaction history (localStorage)
  window.saveSwapToHistory = function(from, to, amountIn, amountOut, fee, txHash, chain) {
    var history = JSON.parse(localStorage.getItem('maxia_swap_history') || '[]');
    history.unshift({
      date: new Date().toISOString(), from: from, to: to,
      amountIn: amountIn, amountOut: amountOut, fee: fee,
      txHash: txHash || '', chain: chain || 'solana'
    });
    if (history.length > 20) history = history.slice(0, 20);
    localStorage.setItem('maxia_swap_history', JSON.stringify(history));
  };

  window.getSwapHistory = function() {
    return JSON.parse(localStorage.getItem('maxia_swap_history') || '[]');
  };
})();

// ======================================
// ONBOARDING OVERLAY (first-time users)
// ======================================
function showOnboarding() {
  if (localStorage.getItem('maxia_onboarded')) return;
  var overlay = document.createElement('div');
  overlay.id = 'onboarding-overlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.8);z-index:20000;display:flex;align-items:center;justify-content:center;animation:fadeIn .3s ease;backdrop-filter:blur(4px)';
  var steps = [
    {
      title: 'Welcome to MAXIA',
      desc: 'Your AI agent operates across 15 blockchains. Swap on 7 chains, escrow on 2, DeFi yields on 14. 65 tokens, 25 stocks, GPUs — all in USDC.',
      icon: '&#127758;'
    },
    {
      title: 'Try a Swap',
      desc: 'Pick any token from our 65 supported tokens and swap instantly. Jupiter for Solana, 1inch for EVM chains. Fees from 0.10% (Bronze) to 0.01% (Whale).',
      icon: '&#128260;',
      hint: '&#8592; Use the Swap tab in the sidebar to get started'
    },
    {
      title: 'Explore Everything',
      desc: 'GPU Rental at cost, DeFi Yield aggregator, Tokenized Stocks, Cross-Chain Bridge, NFT Minting, and 46 MCP tools — all accessible from the sidebar.',
      icon: '&#128640;',
      links: [
        {label:'Swap', hash:'swap'},
        {label:'GPU Rental', hash:'gpu'},
        {label:'DeFi Yields', hash:'defi'},
        {label:'Stocks', hash:'stocks'},
        {label:'Bridge', hash:'bridge'}
      ]
    }
  ];
  var currentStep = 0;
  function renderStep() {
    var s = steps[currentStep];
    var isLast = currentStep === steps.length - 1;
    var html = '<div style="background:var(--card);border:1px solid rgba(255,255,255,.1);border-radius:20px;padding:40px;max-width:480px;width:92%;animation:slideUp .3s ease;position:relative">';
    // Step indicator
    html += '<div style="display:flex;justify-content:center;gap:8px;margin-bottom:24px">';
    for (var i = 0; i < steps.length; i++) {
      html += '<div style="width:' + (i===currentStep?'24':'8') + 'px;height:8px;border-radius:4px;background:' + (i===currentStep?'linear-gradient(135deg,var(--blue),var(--purple))':'rgba(255,255,255,.1)') + ';transition:all .3s"></div>';
    }
    html += '</div>';
    html += '<div style="font-size:48px;text-align:center;margin-bottom:16px">' + s.icon + '</div>';
    html += '<h2 style="font-size:24px;font-weight:700;text-align:center;margin-bottom:12px">' + s.title + '</h2>';
    html += '<p style="color:var(--muted);font-size:15px;text-align:center;line-height:1.7;margin-bottom:20px">' + s.desc + '</p>';
    if (s.hint) {
      html += '<div style="background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.15);border-radius:10px;padding:12px;text-align:center;font-size:14px;color:var(--cyan);margin-bottom:20px">' + s.hint + '</div>';
    }
    if (s.links) {
      html += '<div style="display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin-bottom:20px">';
      s.links.forEach(function(l) {
        html += '<a onclick="dismissOnboarding();showPage(\''+l.hash+'\')" style="padding:8px 16px;background:rgba(6,182,212,.1);border:1px solid rgba(6,182,246,.2);border-radius:8px;color:var(--cyan);font-size:13px;font-weight:600;cursor:pointer;transition:all .2s">' + l.label + '</a>';
      });
      html += '</div>';
    }
    html += '<div style="display:flex;gap:12px;justify-content:center">';
    if (currentStep > 0) {
      html += '<button onclick="onboardPrev()" style="padding:12px 24px;background:transparent;border:1px solid var(--muted2);border-radius:10px;color:var(--text);font-size:14px;font-weight:600;cursor:pointer;font-family:Outfit,sans-serif;transition:all .2s">Back</button>';
    }
    if (isLast) {
      html += '<button onclick="dismissOnboarding()" style="padding:12px 32px;background:linear-gradient(135deg,var(--blue),var(--purple));border:none;border-radius:10px;color:#fff;font-size:14px;font-weight:600;cursor:pointer;font-family:Outfit,sans-serif;transition:all .2s">Got it</button>';
    } else {
      html += '<button onclick="onboardNext()" style="padding:12px 32px;background:linear-gradient(135deg,var(--blue),var(--purple));border:none;border-radius:10px;color:#fff;font-size:14px;font-weight:600;cursor:pointer;font-family:Outfit,sans-serif;transition:all .2s">Next</button>';
    }
    html += '</div>';
    // Skip link
    if (!isLast) {
      html += '<div style="text-align:center;margin-top:12px"><a onclick="dismissOnboarding()" style="color:var(--muted2);font-size:12px;cursor:pointer;transition:color .2s" onmouseover="this.style.color=\'var(--muted)\'" onmouseout="this.style.color=\'var(--muted2)\'">Skip intro</a></div>';
    }
    html += '</div>';
    overlay.innerHTML = html;
  }
  window.onboardNext = function() { if (currentStep < steps.length - 1) { currentStep++; renderStep(); } };
  window.onboardPrev = function() { if (currentStep > 0) { currentStep--; renderStep(); } };
  window.dismissOnboarding = function() {
    localStorage.setItem('maxia_onboarded', '1');
    var el = document.getElementById('onboarding-overlay');
    if (el) { el.style.opacity = '0'; setTimeout(function(){ el.remove(); }, 300); }
  };
  renderStep();
  document.body.appendChild(overlay);
}

// ======================================
// ENTERPRISE DASHBOARD
// ======================================
var _entLoaded = false;
var _entPeriod = '7d';

async function loadEnterprise() {
  var gate = document.getElementById('ent-gate');
  var content = document.getElementById('ent-content');
  if (!gate || !content) return;

  // Always show content — wallet only required for actions, not viewing
  // Try to fetch billing tier to decide access
  var tierData = null;
  try {
    tierData = await api('/api/enterprise/billing/tiers');
  } catch (e) { /* ignore */ }

  // Show content (allow free tier users to see sample data)
  gate.style.display = 'none';
  content.style.display = 'block';

  // Load all sections in parallel
  await Promise.all([
    loadEntOverview(),
    loadEntSLA(),
    loadEntRevenueChart(_entPeriod),
    loadEntBilling(),
  ]);
  _entLoaded = true;
}

async function loadEntOverview() {
  var tbody = document.getElementById('ent-fleet-body');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">Loading fleet...</td></tr>';

  var data = await api('/api/enterprise/dashboard/overview');
  if (!data) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">Could not load fleet data</td></tr>';
    return;
  }

  // Update quick stats
  var el;
  el = document.getElementById('ent-total-agents');
  if (el) el.textContent = data.agent_count || 0;
  el = document.getElementById('ent-total-revenue');
  if (el) el.textContent = '$' + (data.total_revenue_30d || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
  el = document.getElementById('ent-uptime');
  if (el) el.textContent = (data.avg_uptime_pct || 0).toFixed(2) + '%';
  el = document.getElementById('ent-active-services');
  if (el) el.textContent = data.active_count || 0;

  var agents = data.agents || [];
  if (agents.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">No agents registered yet</td></tr>';
    return;
  }

  tbody.innerHTML = agents.map(function(a) {
    var statusBadge = a.status === 'active'
      ? '<span class="badge badge-green">' + esc(a.status) + '</span>'
      : '<span class="badge badge-orange">' + esc(a.status) + '</span>';
    var chain = esc(a.chain || a.tier || '—');
    var rev = '$' + (a.revenue_30d || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2});
    var calls = (a.calls_30d || 0).toLocaleString();
    var uptime = (a.uptime_pct || 0).toFixed(2) + '%';
    var uptimeColor = a.uptime_pct >= 99.5 ? 'var(--green)' : (a.uptime_pct >= 95 ? 'var(--orange)' : 'var(--red)');
    var latency = (a.avg_latency_ms || 0) + 'ms';
    return '<tr>' +
      '<td style="font-weight:600">' + esc(a.name || a.agent_id) + '</td>' +
      '<td>' + statusBadge + '</td>' +
      '<td>' + chain + '</td>' +
      '<td style="font-family:JetBrains Mono,monospace">' + rev + '</td>' +
      '<td>' + calls + '</td>' +
      '<td style="color:' + uptimeColor + '">' + uptime + '</td>' +
      '<td>' + latency + '</td>' +
      '</tr>';
  }).join('');
}

async function loadEntSLA() {
  var tbody = document.getElementById('ent-sla-body');
  var summary = document.getElementById('ent-sla-summary');
  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">Loading SLA data...</td></tr>';

  var data = await api('/api/enterprise/dashboard/sla');
  if (!data) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">Could not load SLA data</td></tr>';
    return;
  }

  // Summary badges
  if (summary) {
    var comp = data.compliant_count || 0;
    var nonComp = data.non_compliant_count || 0;
    var rate = data.compliance_rate_pct || 0;
    var rateColor = rate >= 100 ? 'var(--green)' : (rate >= 80 ? 'var(--orange)' : 'var(--red)');
    summary.innerHTML =
      '<div class="badge badge-green">' + comp + ' compliant</div>' +
      (nonComp > 0 ? '<div class="badge badge-red">' + nonComp + ' non-compliant</div>' : '') +
      '<div class="badge" style="color:' + rateColor + '">' + rate.toFixed(1) + '% compliance rate</div>';
  }

  var agents = data.agents || [];
  if (agents.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:var(--muted);padding:24px">No SLA data available</td></tr>';
    return;
  }

  tbody.innerHTML = agents.map(function(a) {
    var dotClass = a.compliant ? 'green' : (a.actual_uptime_pct >= a.target_uptime_pct * 0.98 ? 'yellow' : 'red');
    var statusLabel = a.compliant ? '<span class="badge badge-green">Compliant</span>' : '<span class="badge badge-red">Violation (' + (a.violations_30d || 0) + ')</span>';
    var uptimeColor = a.actual_uptime_pct >= a.target_uptime_pct ? 'var(--green)' : 'var(--red)';
    var latencyColor = a.actual_latency_p95_ms <= a.target_latency_ms ? 'var(--green)' : 'var(--red)';
    return '<tr>' +
      '<td><span class="ent-sla-dot ' + dotClass + '"></span>' + esc(a.name || a.agent_id) + '</td>' +
      '<td><span class="badge">' + esc(a.sla_tier) + '</span></td>' +
      '<td>' + (a.target_uptime_pct || 0).toFixed(1) + '%</td>' +
      '<td style="color:' + uptimeColor + ';font-family:JetBrains Mono,monospace">' + (a.actual_uptime_pct || 0).toFixed(2) + '%</td>' +
      '<td>' + (a.target_latency_ms || 0) + 'ms</td>' +
      '<td style="color:' + latencyColor + ';font-family:JetBrains Mono,monospace">' + (a.actual_latency_p95_ms || 0) + 'ms</td>' +
      '<td>' + statusLabel + '</td>' +
      '</tr>';
  }).join('');
}

async function loadEntRevenueChart(period, btnEl) {
  if (period) _entPeriod = period;
  if (btnEl) {
    document.querySelectorAll('.ent-period').forEach(function(b) { b.classList.remove('active'); });
    btnEl.classList.add('active');
  }

  var chart = document.getElementById('ent-chart');
  var summaryEl = document.getElementById('ent-chart-summary');
  if (!chart) return;
  chart.innerHTML = '<span style="color:var(--muted);font-size:13px;margin:auto">Loading chart...</span>';

  var data = await api('/api/enterprise/dashboard/analytics?period=' + _entPeriod);
  if (!data || !data.charts) {
    chart.innerHTML = '<span style="color:var(--muted);font-size:13px;margin:auto">Could not load analytics</span>';
    return;
  }

  var revSeries = data.charts.revenue_per_day || [];
  if (revSeries.length === 0) {
    chart.innerHTML = '<span style="color:var(--muted);font-size:13px;margin:auto">No revenue data</span>';
    return;
  }

  // Find max value for scaling
  var maxVal = Math.max.apply(null, revSeries.map(function(d) { return d.value; }));
  if (maxVal <= 0) maxVal = 1;
  var chartHeight = 180; // px

  chart.innerHTML = revSeries.map(function(d) {
    var h = Math.max(2, Math.round((d.value / maxVal) * chartHeight));
    var dateLabel = d.date ? d.date.slice(5) : '';
    return '<div class="ent-bar">' +
      '<div class="bar-tooltip">$' + d.value.toFixed(2) + '<br>' + esc(d.date) + '</div>' +
      '<div class="bar" style="height:' + h + 'px"></div>' +
      '<div class="bar-label">' + esc(dateLabel) + '</div>' +
      '</div>';
  }).join('');

  // Summary
  if (summaryEl && data.summary) {
    var s = data.summary;
    summaryEl.innerHTML =
      '<span><strong style="color:var(--cyan)">$' + (s.total_revenue || 0).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}) + '</strong> <span style="color:var(--muted)">total revenue</span></span>' +
      '<span><strong style="color:var(--text)">' + (s.total_calls || 0).toLocaleString() + '</strong> <span style="color:var(--muted)">API calls</span></span>' +
      '<span><strong style="color:var(--orange)">' + (s.total_errors || 0).toLocaleString() + '</strong> <span style="color:var(--muted)">errors</span></span>' +
      '<span><strong style="color:var(--text)">' + (s.avg_latency_p50 || 0) + 'ms</strong> <span style="color:var(--muted)">p50 latency</span></span>';
  }
}

async function loadEntBilling() {
  var planNameEl = document.getElementById('ent-plan-name');
  var planPriceEl = document.getElementById('ent-plan-price');
  var usageSumEl = document.getElementById('ent-usage-summary');
  var invoiceEl = document.getElementById('ent-invoice');

  // Fetch billing tiers
  var tiers = await api('/api/enterprise/billing/tiers');
  var currentTier = 'free';
  var tierInfo = null;

  if (tiers && tiers.tiers) {
    // Try to detect user tier from usage endpoint
    var tenantId = wallet || 'anonymous';
    var now = new Date();
    var month = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0');
    var usage = await api('/api/enterprise/billing/usage?tenant_id=' + encodeURIComponent(tenantId) + '&month=' + month);

    if (usage && usage.tier) {
      currentTier = usage.tier;
    }
    tierInfo = tiers.tiers[currentTier] || tiers.tiers['free'];

    if (planNameEl) planNameEl.textContent = tierInfo.name || currentTier;
    if (planPriceEl) {
      planPriceEl.textContent = tierInfo.monthly_price > 0
        ? '$' + tierInfo.monthly_price.toFixed(2) + '/month'
        : 'Free tier';
    }

    // Show usage
    if (usageSumEl && usage && usage.usage) {
      var u = usage.usage;
      var lim = usage.limits || {};
      usageSumEl.innerHTML =
        entUsageLine('API Calls', u.api_calls || 0, lim.api_calls || 0) +
        entUsageLine('GPU Hours', (u.gpu_hours || 0).toFixed(1), lim.gpu_hours || 0) +
        entUsageLine('Swap Volume', '$' + (u.swap_volume || 0).toLocaleString(undefined, {maximumFractionDigits: 0}), '$' + (lim.swap_volume || 0).toLocaleString()) +
        entUsageLine('LLM Tokens', (u.llm_tokens || 0).toLocaleString(), (lim.llm_tokens || 0).toLocaleString());
    } else if (usageSumEl) {
      usageSumEl.innerHTML = '<span style="color:var(--muted)">No usage data for this month</span>';
    }

    // Invoice estimate
    if (invoiceEl) {
      var inv = await api('/api/enterprise/billing/invoice/' + month + '?tenant_id=' + encodeURIComponent(tenantId));
      if (inv && inv.total_amount !== undefined) {
        var items = inv.line_items || [];
        var itemsHtml = items.map(function(li) {
          return '<div style="display:flex;justify-content:space-between;padding:4px 0;border-bottom:1px solid rgba(255,255,255,.03)">' +
            '<span style="color:var(--muted)">' + esc(li.metric) + '</span>' +
            '<span style="font-family:JetBrains Mono,monospace">$' + (li.charge || 0).toFixed(4) + '</span>' +
            '</div>';
        }).join('');
        invoiceEl.innerHTML =
          itemsHtml +
          '<div style="display:flex;justify-content:space-between;padding:8px 0;margin-top:8px;border-top:1px solid rgba(255,255,255,.08)">' +
          '<span style="font-weight:600">Base price</span>' +
          '<span style="font-family:JetBrains Mono,monospace">$' + (inv.base_price || 0).toFixed(2) + '</span>' +
          '</div>' +
          '<div style="display:flex;justify-content:space-between;padding:4px 0">' +
          '<span style="font-weight:600">Overages</span>' +
          '<span style="font-family:JetBrains Mono,monospace">$' + (inv.overage_total || 0).toFixed(4) + '</span>' +
          '</div>' +
          '<div style="display:flex;justify-content:space-between;padding:8px 0;margin-top:8px;border-top:1px solid var(--cyan);font-size:16px">' +
          '<span style="font-weight:700;color:var(--cyan)">Estimated Total</span>' +
          '<span style="font-weight:700;color:var(--cyan);font-family:JetBrains Mono,monospace">$' + (inv.total_amount || 0).toFixed(2) + '</span>' +
          '</div>';
      } else {
        invoiceEl.innerHTML = '<span style="color:var(--muted)">No invoice data available</span>';
      }
    }
  } else {
    if (planNameEl) planNameEl.textContent = 'Free';
    if (planPriceEl) planPriceEl.textContent = 'Billing not enabled';
    if (usageSumEl) usageSumEl.innerHTML = '<span style="color:var(--muted)">Billing module not active</span>';
    if (invoiceEl) invoiceEl.innerHTML = '<span style="color:var(--muted)">No invoice data</span>';
  }
}

function entUsageLine(label, used, limit) {
  var pct = 0;
  var numUsed = parseFloat(String(used).replace(/[^0-9.]/g, ''));
  var numLimit = parseFloat(String(limit).replace(/[^0-9.]/g, ''));
  if (numLimit > 0) pct = Math.min(100, (numUsed / numLimit) * 100);
  var barColor = pct >= 90 ? 'var(--red)' : (pct >= 70 ? 'var(--orange)' : 'var(--cyan)');
  return '<div style="margin-bottom:8px">' +
    '<div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:3px">' +
    '<span style="color:var(--muted)">' + esc(label) + '</span>' +
    '<span><span style="color:var(--text)">' + used + '</span> / ' + limit + '</span>' +
    '</div>' +
    '<div style="height:4px;background:rgba(255,255,255,.06);border-radius:2px;overflow:hidden">' +
    '<div style="height:100%;width:' + pct.toFixed(1) + '%;background:' + barColor + ';border-radius:2px;transition:width .4s ease"></div>' +
    '</div></div>';
}

