/* ======================================
   MAXIA App — Portfolio, Stats & WebSocket Prices
   ====================================== */

// ======================================
// PORTFOLIO
// ======================================
async function loadPortfolio() {
  var content = document.getElementById('portfolio-content');
  if (!wallet) {
    content.innerHTML = '<div class="empty-state"><div class="icon">&#128176;</div><div class="msg">Connect your wallet to see your portfolio</div><div class="sub">Phantom, Backpack, Solflare, MetaMask, Rabby, Coinbase live &middot; Aptos / SUI / TON / TRON coming soon</div></div>';
    return;
  }

  content.innerHTML = '<div style="text-align:center;padding:40px;color:var(--muted)"><div class="swap-spinner" style="width:24px;height:24px;border-width:3px;margin-bottom:12px"></div><br>Reading on-chain balances...</div>';

  // Fetch prices from backend (best-effort)
  var prices = {};
  try {
    var priceResp = await fetch('/api/public/crypto/prices');
    if (!priceResp.ok) { console.warn('Price fetch failed:', priceResp.status); throw new Error('price fetch failed'); }
    var priceData = await priceResp.json();
    prices = (priceData && priceData.prices) || {};
  } catch(e) { /* prices stay empty, values will show $0 */ }

  var holdings = [];
  var chainSet = {};

  try {
    // -- Solana wallet: read SOL + all SPL token accounts on-chain --
    if (isSolanaWallet()) {
      var connection = new solanaWeb3.Connection(SOLANA_RPC, 'confirmed');
      var publicKey = new solanaWeb3.PublicKey(wallet);

      // 1) SOL native balance
      var solLamports = await connection.getBalance(publicKey);
      var solAmount = solLamports / 1e9;
      var solPrice = (prices.SOL && prices.SOL.price) ? prices.SOL.price : 0;

      if (solAmount > 0.0001) {
        holdings.push({
          token: 'SOL',
          balance: solAmount,
          price: solPrice,
          value: solAmount * solPrice,
          mint: 'So11111111111111111111111111111111111111112',
          chain: 'Solana'
        });
      }

      // 2) All SPL token accounts
      var tokenAccounts = await connection.getParsedTokenAccountsByOwner(publicKey, {
        programId: new solanaWeb3.PublicKey('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA')
      });

      // Build reverse map: mint address -> symbol
      var mintToSymbol = {};
      for (var sym in TOKEN_MINTS) {
        mintToSymbol[TOKEN_MINTS[sym].mint] = sym;
      }

      var tokenList = (tokenAccounts && tokenAccounts.value) || [];
      tokenList.forEach(function(acc) {
        if (!acc || !acc.account || !acc.account.data || !acc.account.data.parsed || !acc.account.data.parsed.info) return;
        var info = acc.account.data.parsed.info;
        var mint = info.mint;
        var tokenAmount = (info.tokenAmount) || {};
        var amount = parseFloat(tokenAmount.uiAmountString || '0');
        if (amount > 0) {
          var symbol = mintToSymbol[mint] || (mint.slice(0, 4) + '...' + mint.slice(-4));
          var tokenPrice = (prices[symbol] && prices[symbol].price) ? prices[symbol].price : 0;
          holdings.push({
            token: symbol,
            balance: amount,
            price: tokenPrice,
            value: amount * tokenPrice,
            mint: mint,
            chain: 'Solana'
          });
        }
      });

      chainSet['Solana'] = true;
    }

    // -- EVM wallet: read native balance + known ERC-20 tokens --
    if (isEvmWallet() && window.ethereum) {
      var chainId = await getEvmChainId();
      var chainInfo = EVM_CHAINS[chainId];
      var chainName = chainInfo ? chainInfo.name : ('EVM #' + chainId);
      var nativeSymbol = chainInfo ? chainInfo.symbol : 'ETH';

      // 1) Native balance (ETH, POL, AVAX, BNB, etc.)
      var rawBal = await window.ethereum.request({
        method: 'eth_getBalance',
        params: [wallet, 'latest']
      });
      var nativeWei = parseInt(rawBal, 16);
      var nativeAmount = nativeWei / 1e18;
      var nativePrice = (prices[nativeSymbol] && prices[nativeSymbol].price) ? prices[nativeSymbol].price : 0;

      if (nativeAmount > 0.00001) {
        holdings.push({
          token: nativeSymbol,
          balance: nativeAmount,
          price: nativePrice,
          value: nativeAmount * nativePrice,
          mint: EVM_NATIVE,
          chain: chainName
        });
      }

      // 2) ERC-20 tokens from EVM_TOKENS for the current chain
      var chainTokens = EVM_TOKENS[chainId] || {};
      var erc20BalanceOf = '0x70a08231'; // balanceOf(address) selector

      // Pad wallet address to 32 bytes for calldata
      var paddedWallet = '000000000000000000000000' + wallet.replace('0x', '').toLowerCase();

      var erc20Promises = [];
      var erc20Symbols = [];

      for (var tokenSym in chainTokens) {
        var tokenInfo = chainTokens[tokenSym];
        // Skip native token (already fetched above)
        if (tokenInfo.address === EVM_NATIVE) continue;

        erc20Symbols.push({ symbol: tokenSym, decimals: tokenInfo.decimals, address: tokenInfo.address });
        erc20Promises.push(
          window.ethereum.request({
            method: 'eth_call',
            params: [{
              to: tokenInfo.address,
              data: erc20BalanceOf + paddedWallet
            }, 'latest']
          }).catch(function() { return '0x0'; })
        );
      }

      var erc20Results = await Promise.all(erc20Promises);

      for (var i = 0; i < erc20Results.length; i++) {
        var rawVal = erc20Results[i];
        if (!rawVal || rawVal === '0x' || rawVal === '0x0') continue;
        var bigVal = parseInt(rawVal, 16);
        if (isNaN(bigVal) || bigVal === 0) continue;

        var erc20Info = erc20Symbols[i];
        var erc20Amount = bigVal / Math.pow(10, erc20Info.decimals);

        if (erc20Amount > 0.00001) {
          var erc20Price = (prices[erc20Info.symbol] && prices[erc20Info.symbol].price) ? prices[erc20Info.symbol].price : 0;
          holdings.push({
            token: erc20Info.symbol,
            balance: erc20Amount,
            price: erc20Price,
            value: erc20Amount * erc20Price,
            mint: erc20Info.address,
            chain: chainName
          });
        }
      }

      chainSet[chainName] = true;
    }

    // Sort by value descending (tokens with value first, then by balance)
    holdings.sort(function(a, b) {
      if (b.value !== a.value) return b.value - a.value;
      return b.balance - a.balance;
    });

    // Calculate totals
    var totalValue = holdings.reduce(function(sum, h) { return sum + h.value; }, 0);

    // Update stat cards
    // Holdings summary (logged for debugging, stat cards handled by loadPortfolioStats)
    console.log('[Portfolio] Holdings:', holdings.length, 'tokens across', Object.keys(chainSet).length, 'chains, total $' + totalValue.toFixed(2));

    // Render table
    if (holdings.length > 0) {
      content.innerHTML = '<div class="tw"><table class="ct"><thead><tr><th>Token</th><th>Chain</th><th>Balance</th><th>Price</th><th>Value</th><th></th></tr></thead><tbody>' +
        holdings.map(function(h) {
          return '<tr>' +
            '<td style="font-weight:600">' + esc(h.token) + '</td>' +
            '<td><span class="badge badge-purple" style="font-size:11px">' + esc(h.chain) + '</span></td>' +
            '<td style="font-family:JetBrains Mono,monospace">' + fmtAmount(h.balance) + '</td>' +
            '<td style="font-family:JetBrains Mono,monospace;color:var(--muted)">' + (h.price > 0 ? fmtUSD(h.price) : '<span style="color:var(--muted2)">--</span>') + '</td>' +
            '<td style="font-family:JetBrains Mono,monospace;font-weight:600;color:var(--green)">' + (h.value > 0 ? fmtUSD(h.value) : '<span style="color:var(--muted2)">$0.00</span>') + '</td>' +
            '<td><button onclick="goSwap(\'' + esc(h.token) + '\')" style="padding:4px 12px;background:linear-gradient(135deg,#00e5ff,#7c3aed);color:white;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-family:Outfit">Swap</button></td>' +
          '</tr>';
        }).join('') +
      '</tbody></table></div>';
    } else {
      content.innerHTML = '<div class="empty-state"><div class="icon">&#128176;</div><div class="msg">No tokens found in this wallet</div><div class="sub">Your tokens will appear here once you hold SOL, USDC, or any SPL/ERC-20 token</div></div>';
    }
  } catch (e) {
    console.error('Portfolio load error:', e);
    content.innerHTML = '<div class="empty-state"><div class="icon">&#128176;</div><div class="msg">Could not load portfolio</div><div class="sub">' + esc(e.message || 'RPC error — try again in a moment') + '</div></div>';
  }
}

// ======================================
// PORTFOLIO STATS, BADGES & ACTIVITY
// ======================================

// Badge definitions (must match backend referral.py + 7 gamification badges from spec)
var ALL_BADGES = [
  { name: 'first_trade',    icon: '&#128293;', label: 'First Trade',    desc: 'Complete your first swap' },
  { name: 'volume_1k',      icon: '&#128176;', label: '$1K Volume',     desc: 'Reach $1,000 in 30-day volume' },
  { name: 'whale',          icon: '&#128011;', label: 'Whale Trader',   desc: 'Reach WHALE tier ($5,000+ volume)' },
  { name: 'referee',        icon: '&#129309;', label: 'Referral Master',desc: 'Refer 5 users' },
  { name: 'early_adopter',  icon: '&#127749;', label: 'Early Adopter',  desc: 'One of the first 100 users' },
  { name: 'trader',         icon: '&#128202;', label: 'Volume King',    desc: 'Reach $10,000 in volume' },
  { name: 'top_referrer',   icon: '&#127942;', label: 'Top Referrer',   desc: 'Most referrals this month' }
];

async function loadPortfolioStats() {
  var activityEl = document.getElementById('pf-activity');
  var badgesEl = document.getElementById('pf-badges');
  if (!wallet) {
    document.getElementById('pf-swap-count').textContent = '0';
    document.getElementById('pf-volume').textContent = '$0';
    document.getElementById('pf-tier').textContent = 'BRONZE';
    document.getElementById('pf-fees-saved').textContent = '$0';
    activityEl.innerHTML = '<span style="color:var(--muted)">Connect wallet to see your activity</span>';
    renderBadges([], badgesEl);
    return;
  }

  // Fetch portfolio stats from backend
  var data = await api('/api/agents/' + encodeURIComponent(wallet) + '/portfolio-stats');
  if (!data) {
    activityEl.innerHTML = '<span style="color:var(--muted)">Could not load activity</span>';
    renderBadges([], badgesEl);
    return;
  }

  // Update stat cards
  document.getElementById('pf-swap-count').textContent = (data.swap_count || 0).toLocaleString();
  document.getElementById('pf-volume').textContent = '$' + (data.volume30d || 0).toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
  document.getElementById('pf-tier').textContent = data.tier || 'BRONZE';
  document.getElementById('pf-fees-saved').textContent = '$' + (data.fees_saved || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  // Color the tier
  var tierEl = document.getElementById('pf-tier');
  var tierColors = { 'WHALE': 'var(--cyan)', 'GOLD': '#fbbf24', 'SILVER': '#9ca3af', 'BRONZE': '#cd7f32' };
  tierEl.style.color = tierColors[data.tier] || 'var(--cyan)';

  // Render activity
  var activity = data.activity || [];
  if (activity.length === 0) {
    activityEl.innerHTML = '<span style="color:var(--muted)">No activity yet. Make your first swap to get started!</span>';
  } else {
    var html = '';
    activity.forEach(function(a) {
      var purpose = a.purpose || 'other';
      var purposeClass = purpose.indexOf('swap') >= 0 ? 'swap' : (purpose.indexOf('service') >= 0 ? 'service' : 'other');
      var purposeLabel = purpose.replace(/_/g, ' ').replace('crypto ', '');
      var amount = (a.amount || 0);
      var dateStr = '';
      if (a.date) {
        var ts = typeof a.date === 'number' ? a.date : parseInt(a.date);
        if (ts > 1e12) ts = Math.floor(ts / 1000); // ms to s
        if (ts > 1e9) {
          var d = new Date(ts * 1000);
          dateStr = d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
        }
      }
      var txShort = a.tx ? (a.tx.slice(0, 8) + '...' + a.tx.slice(-4)) : '';
      html += '<div class="activity-row">';
      html += '<div style="display:flex;align-items:center;gap:8px"><span class="activity-purpose ' + purposeClass + '">' + esc(purposeLabel) + '</span>';
      if (txShort) html += '<span style="font-family:JetBrains Mono,monospace;font-size:11px;color:var(--muted2)">' + esc(txShort) + '</span>';
      html += '</div>';
      html += '<div style="display:flex;align-items:center;gap:12px">';
      html += '<span style="font-family:JetBrains Mono,monospace;font-weight:600;color:var(--green)">$' + amount.toFixed(2) + '</span>';
      if (dateStr) html += '<span style="font-size:11px;color:var(--muted2)">' + dateStr + '</span>';
      html += '</div></div>';
    });
    activityEl.innerHTML = html;
  }

  // Render badges
  var earnedNames = (data.badges || []).map(function(b) { return b.name; });
  renderBadges(earnedNames, badgesEl);
}

function renderBadges(earnedNames, container) {
  var html = '';
  ALL_BADGES.forEach(function(b) {
    var earned = earnedNames.indexOf(b.name) >= 0;
    html += '<div class="badge-card ' + (earned ? 'earned' : 'locked') + '">';
    html += '<span class="badge-icon">' + b.icon + '</span>';
    html += '<span class="badge-name">' + b.label + '</span>';
    html += '<span class="badge-desc">' + b.desc + '</span>';
    if (earned) html += '<span style="font-size:10px;color:var(--green);margin-top:2px">&#10003; Earned</span>';
    else html += '<span style="font-size:10px;color:var(--muted2);margin-top:2px">&#128274; Locked</span>';
    html += '</div>';
  });
  container.innerHTML = html;
}

// ======================================
// NOTIFICATION PANEL
// ======================================

function toggleNotifPanel() {
  var panel = document.getElementById('notif-panel');
  if (panel.style.display === 'none') {
    panel.style.display = 'block';
    // Close on outside click
    setTimeout(function() {
      document.addEventListener('click', _closeNotifOnOutside);
    }, 10);
  } else {
    panel.style.display = 'none';
    document.removeEventListener('click', _closeNotifOnOutside);
  }
}

function _closeNotifOnOutside(e) {
  var panel = document.getElementById('notif-panel');
  var bell = document.getElementById('notif-bell');
  if (panel && !panel.contains(e.target) && !bell.contains(e.target)) {
    panel.style.display = 'none';
    document.removeEventListener('click', _closeNotifOnOutside);
  }
}

// ======================================
// WEBSOCKET LIVE PRICE TICKER
// ======================================

var _priceWs = null;
var _priceWsRetries = 0;

function connectPriceWs() {
  if (_priceWs && _priceWs.readyState <= 1) return; // Already connected or connecting
  var protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  var wsUrl = protocol + '//' + location.host + '/ws/prices';
  try {
    _priceWs = new WebSocket(wsUrl);
  } catch (e) {
    console.warn('[WS/prices] Could not connect:', e);
    return;
  }

  _priceWs.onopen = function() {
    _priceWsRetries = 0;
    var dot = document.getElementById('ticker-dot');
    if (dot) dot.style.background = 'var(--green)';
  };

  _priceWs.onmessage = function(evt) {
    try {
      var msg = JSON.parse(evt.data);
      if (msg.type === 'prices' && msg.data) {
        updatePriceTicker(msg.data);
        // If on swap page, update the rate display if tokens match
        updateSwapLiveRate(msg.data);
      }
      // -- Notification toasts for events --
      if (msg.type === 'swap_completed') {
        showToast('Swap Complete', msg.data && msg.data.detail ? msg.data.detail : 'Your swap has been confirmed on-chain', 'success');
      }
      if (msg.type === 'escrow_created') {
        showToast('Escrow Created', msg.data && msg.data.detail ? msg.data.detail : 'Funds locked in escrow', 'info');
      }
      if (msg.type === 'escrow_released') {
        showToast('Escrow Released', msg.data && msg.data.detail ? msg.data.detail : 'Funds released from escrow', 'success');
      }
      if (msg.type === 'price_alert') {
        showToast('Price Alert', msg.data && msg.data.detail ? msg.data.detail : 'Price target reached', 'warning');
      }
      if (msg.type === 'service_completed') {
        showToast('Service Completed', msg.data && msg.data.detail ? msg.data.detail : 'AI service execution finished', 'success');
      }
      if (msg.type === 'gpu_ready') {
        showToast('GPU Ready', msg.data && msg.data.detail ? msg.data.detail : 'Your GPU instance is ready', 'info');
      }
      if (msg.type === 'dispute_resolved') {
        showToast('Dispute Resolved', msg.data && msg.data.detail ? msg.data.detail : 'Dispute has been resolved', 'warning');
      }
      if (msg.type === 'error') {
        showToast('Error', msg.data && msg.data.detail ? msg.data.detail : 'An error occurred', 'error');
      }
      if (msg.type === 'forum_reply') {
        showToast('Forum Reply', (msg.replier || 'Someone') + ' replied to "' + (msg.post_title || 'your post').slice(0,40) + '"', 'info');
      }
    } catch (e) { /* ignore parse errors */ }
  };

  _priceWs.onclose = function() {
    var dot = document.getElementById('ticker-dot');
    if (dot) dot.style.background = 'var(--muted2)';
    // Reconnect with backoff (max 30s)
    _priceWsRetries++;
    var delay = Math.min(5000 * _priceWsRetries, 30000);
    setTimeout(connectPriceWs, delay);
  };

  _priceWs.onerror = function() {
    // onclose will fire after this, triggering reconnect
  };
}

var _latestPrices = null;
function updatePriceTicker(prices) {
  _latestPrices = prices;
  var container = document.getElementById('price-ticker-row');
  if (!container) return;
  // Top 8 tokens by relevance
  var topSymbols = ['SOL', 'ETH', 'BTC', 'BNB', 'AVAX', 'MATIC', 'ARB', 'USDC'];
  var html = '';
  topSymbols.forEach(function(sym) {
    var p = prices[sym];
    if (!p) return;
    var price = p.price || p;
    var change = p.change_24h || 0;
    var changeColor = change >= 0 ? 'var(--green)' : 'var(--red, #ef4444)';
    var changeSign = change >= 0 ? '+' : '';
    html += '<div class="ticker-chip">';
    html += (typeof getTokenIcon==='function'?getTokenIcon(sym,16):'') + '<span class="sym">' + sym + '</span>';
    html += '<span class="price">$' + (typeof price === 'number' ? price.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: price > 100 ? 0 : 2 }) : price) + '</span>';
    if (change !== 0) html += '<span style="font-size:10px;color:' + changeColor + '">' + changeSign + change.toFixed(1) + '%</span>';
    html += '</div>';
  });
  container.innerHTML = html;
}

function updateSwapLiveRate(prices) {
  var swapPage = document.getElementById('page-swap');
  if (!swapPage || !swapPage.classList.contains('active')) return;
  var fromSel = document.getElementById('swap-from-token');
  var toSel = document.getElementById('swap-to-token');
  if (!fromSel || !toSel) return;
  var from = fromSel.value;
  var to = toSel.value;
  // Only update if both tokens have price data and there is no active quote being fetched
  var fromPrice = prices[from];
  var toPrice = prices[to];
  if (!fromPrice || !toPrice) return;
  var fp = fromPrice.price || fromPrice;
  var tp = toPrice.price || toPrice;
  if (typeof fp !== 'number' || typeof tp !== 'number' || tp === 0) return;
  // Update the rate label as a hint (non-intrusive — does not override quote result)
  var rateEl = document.getElementById('swap-rate');
  if (rateEl && rateEl.textContent === '\u2014') {
    // Only update if rate is still showing the default dash
    rateEl.textContent = '1 ' + from + ' \u2248 ' + (fp / tp).toFixed(6) + ' ' + to + ' (live)';
  }
}

// Start price WS on page load
connectPriceWs();

