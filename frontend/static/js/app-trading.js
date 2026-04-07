/* ======================================
   MAXIA App — Trading Tools & Charts
   ====================================== */

// ======================================
// TRADING TOOLS
// ======================================
async function loadAppWhales() {
  var el = document.getElementById('app-whales');
  try {
    var r = await api('/api/trading/whales?limit=8');
    var items = r.movements || r.transfers || [];
    if (items.length > 0) {
      el.innerHTML = items.map(function(w) {
        return '<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.04)">'
          + '<span style="color:var(--cyan);min-width:60px">' + esc(w.chain) + '</span>'
          + '<span style="font-weight:600;min-width:50px">' + esc(w.token) + '</span>'
          + '<span style="color:var(--green);font-family:JetBrains Mono;flex:1;text-align:right">$' + Number(w.amount_usd||0).toLocaleString() + '</span>'
          + '<button onclick="goSwap(\'' + esc(w.token) + '\')" style="margin-left:10px;padding:4px 12px;background:linear-gradient(135deg,#00e5ff,#7c3aed);color:white;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-family:Outfit">Swap</button>'
          + '</div>';
      }).join('');
    } else { el.textContent = 'No whale activity detected'; }
  } catch(e) { el.textContent = 'Error loading whales'; }
}

async function loadAppSignal() {
  var token = document.getElementById('app-signal-token').value.trim().toUpperCase();
  if (!token) return;
  var el = document.getElementById('app-signal-result');
  el.innerHTML = 'Analyzing...';
  try {
  var d = await api('/api/trading/signals/' + token);
  if (d && d.signal) {
    var sigColor = (d.signal||'').indexOf('BUY') >= 0 ? 'var(--green)' : (d.signal||'').indexOf('SELL') >= 0 ? 'var(--red)' : 'var(--orange)';
    var swapBtn = (d.signal||'').indexOf('BUY') >= 0
      ? '<button onclick="goSwap(\'' + esc(token) + '\')" style="margin-top:12px;width:100%;padding:10px;background:linear-gradient(135deg,#10B981,#06B6D4);color:white;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;font-family:Outfit">Buy ' + esc(token) + ' — 0.10% fee</button>'
      : (d.signal||'').indexOf('SELL') >= 0
      ? '<button onclick="goSwap(\'' + esc(token) + '\')" style="margin-top:12px;width:100%;padding:10px;background:linear-gradient(135deg,#EF4444,#F59E0B);color:white;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;font-family:Outfit">Sell ' + esc(token) + ' — 0.10% fee</button>'
      : '';
    el.innerHTML = '<div style="text-align:center;margin-bottom:12px"><span style="font-size:28px;font-weight:700;color:' + sigColor + '">' + esc(d.signal||'--') + '</span></div>'
      + '<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;font-size:13px">'
      + '<div>RSI(14): <strong>' + (d.rsi||0).toFixed(1) + '</strong></div>'
      + '<div>SMA(20): <strong>$' + (d.sma_20||0).toFixed(2) + '</strong></div>'
      + '<div>SMA(50): <strong>$' + (d.sma_50||0).toFixed(2) + '</strong></div>'
      + '<div>MACD: <strong>' + (typeof d.macd === 'object' ? (d.macd.histogram||0) : (d.macd||0)).toFixed(4) + '</strong></div></div>'
      + '<div style="font-size:11px;color:var(--muted);text-align:center;margin-top:8px">Lowest fees: 0.03% Gold | 0.10% Bronze | 0.01% Whale | Jupiter: 0% + slippage</div>'
      + swapBtn;
  } else { el.textContent = 'Token not found'; }
  } catch(e) { el.textContent = 'Error: ' + e.message; }
}

async function loadAppCopy() {
  try {
  var r = await api('/api/trading/copy/wallets?limit=8');
  var tb = document.getElementById('app-copy');
  var wallets = r.wallets || [];
  if (wallets.length > 0) {
    tb.innerHTML = wallets.map(function(w) {
      var c7 = (w.pnl_7d_pct||0) >= 0 ? 'var(--green)' : 'var(--red)';
      var c30 = (w.pnl_30d_pct||0) >= 0 ? 'var(--green)' : 'var(--red)';
      return '<tr><td style="font-size:11px;font-family:JetBrains Mono">' + esc((w.address||'').slice(0,12)) + '...</td>'
        + '<td style="color:' + c7 + ';font-weight:600">' + ((w.pnl_7d_pct||0)>=0?'+':'') + (w.pnl_7d_pct||0).toFixed(1) + '%</td>'
        + '<td style="color:' + c30 + ';font-weight:600">' + ((w.pnl_30d_pct||0)>=0?'+':'') + (w.pnl_30d_pct||0).toFixed(1) + '%</td>'
        + '<td>' + ((w.win_rate||0) < 1 ? (w.win_rate*100) : (w.win_rate||0)).toFixed(0) + '%</td>'
        + '<td>' + (w.trades_count||0) + '</td>'
        + '<td><button onclick="copyTrade(\'' + esc(w.address) + '\')" style="padding:4px 10px;background:linear-gradient(135deg,#00e5ff,#7c3aed);color:white;border:none;border-radius:6px;font-size:11px;cursor:pointer;font-family:Outfit">Copy</button></td></tr>';
    }).join('');
  } else { tb.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;color:var(--muted)">No data</td></tr>'; }
  } catch(e) { document.getElementById('app-copy').innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;color:var(--muted)">Error loading</td></tr>'; }
}

async function createAppAlert() {
  var token = document.getElementById('alert-token').value.trim().toUpperCase();
  var condition = document.getElementById('alert-condition').value;
  var price = parseFloat(document.getElementById('alert-price').value);
  var telegramChatId = (document.getElementById('alert-telegram')||{}).value||'';
  if (!token || !price) { toast('Fill token and price', 'error'); return; }
  try {
    var body = { token: token, condition: condition, target_price: price, wallet: wallet || 'anonymous' };
    if (telegramChatId) body.telegram_chat_id = telegramChatId.trim();
    var r = await api('/api/trading/alerts', { method: 'POST', body: JSON.stringify(body) });
    if (r && r.alert_id) {
      toast('Alert created: ' + token + ' ' + condition + ' $' + price, 'success');
      loadAppAlerts();
    }
  } catch(e) { toast('Error: ' + e.message, 'error'); }
}

// Load and display active alerts
async function loadAppAlerts() {
  var el = document.getElementById('app-alerts');
  try {
    var r = await api('/api/trading/alerts?wallet=' + encodeURIComponent(wallet || 'anonymous'));
    var alerts = (r && r.alerts) || [];
    if (alerts.length > 0) {
      el.innerHTML = alerts.map(function(a) {
        var statusColor = a.triggered ? 'var(--green)' : 'var(--muted)';
        var statusText = a.triggered ? 'TRIGGERED' : 'ACTIVE';
        var notified = a.notified ? ' (notified)' : '';
        return '<div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid rgba(255,255,255,.04)">'
          + '<span><strong>' + esc(a.token) + '</strong> ' + esc(a.condition) + ' $' + a.target_price.toLocaleString('en-US') + '</span>'
          + '<span style="color:' + statusColor + ';font-size:12px;font-weight:600">' + statusText + notified + '</span>'
          + '<span style="color:var(--muted);font-size:12px">$' + (a.current_price||0).toFixed(2) + '</span>'
          + '<button onclick="deleteAlert(\'' + esc(a.alert_id) + '\')" style="padding:2px 8px;background:rgba(239,68,68,.2);color:var(--red);border:1px solid rgba(239,68,68,.3);border-radius:4px;font-size:11px;cursor:pointer">X</button>'
          + '</div>';
      }).join('');
    } else { el.textContent = 'No alerts — create one above'; }
  } catch(e) { el.textContent = 'No alerts yet'; }
}

async function deleteAlert(id) {
  try { await api('/api/trading/alerts/' + id, { method: 'DELETE' }); loadAppAlerts(); toast('Alert deleted', 'success'); } catch(e) {}
}

// ======================================
// PENDING TRANSACTIONS (DCA / Grid / Sniper)
// ======================================

async function loadPendingTxs() {
  var list = document.getElementById('pending-txs-list');
  var badge = document.getElementById('pending-count');
  if (!list) return;

  var allTxs = [];
  // Fetch from all 3 bot endpoints in parallel
  var results = await Promise.allSettled([
    wallet ? api('/api/dca/pending/' + encodeURIComponent(wallet)) : Promise.resolve(null),
    wallet ? api('/api/grid/pending/' + encodeURIComponent(wallet)) : Promise.resolve(null),
    wallet ? api('/api/sniper/pending?wallet=' + encodeURIComponent(wallet)) : Promise.resolve(null)
  ]);

  // DCA pending
  if (results[0].status === 'fulfilled' && results[0].value) {
    var dcaData = results[0].value;
    var dcaTxs = Array.isArray(dcaData) ? dcaData : (dcaData.pending || dcaData.transactions || []);
    dcaTxs.forEach(function(tx) { tx._type = 'DCA'; });
    allTxs = allTxs.concat(dcaTxs);
  }
  // Grid pending
  if (results[1].status === 'fulfilled' && results[1].value) {
    var gridData = results[1].value;
    var gridTxs = Array.isArray(gridData) ? gridData : (gridData.pending || gridData.transactions || []);
    gridTxs.forEach(function(tx) { tx._type = 'GRID'; });
    allTxs = allTxs.concat(gridTxs);
  }
  // Sniper pending
  if (results[2].status === 'fulfilled' && results[2].value) {
    var sniperData = results[2].value;
    var sniperTxs = Array.isArray(sniperData) ? sniperData : (sniperData.pending || sniperData.transactions || []);
    sniperTxs.forEach(function(tx) { tx._type = 'SNIPER'; });
    allTxs = allTxs.concat(sniperTxs);
  }

  // Sort by created_at desc
  allTxs.sort(function(a, b) {
    return new Date(b.created_at || 0) - new Date(a.created_at || 0);
  });

  // Update count badge
  if (allTxs.length > 0) {
    badge.textContent = allTxs.length;
    badge.style.display = 'inline';
  } else {
    badge.style.display = 'none';
  }

  // Render
  if (allTxs.length === 0) {
    list.innerHTML = '<div style="color:#52525b;font-size:13px">No pending transactions</div>';
    return;
  }

  list.innerHTML = allTxs.map(function(tx) {
    var typeColors = { DCA: '#3b82f6', GRID: '#f59e0b', SNIPER: '#f43f5e' };
    var typeColor = typeColors[tx._type] || '#a1a1aa';
    var token = esc(tx.to_token || tx.token || '???');
    var amount = Number(tx.amount_usdc || 0).toFixed(2);
    var price = Number(tx.price_usdc || 0).toFixed(4);
    var commission = Number(tx.commission_usdc || 0).toFixed(4);
    var txId = esc(tx.tx_id || '');
    var swapTx = tx.swap_transaction || '';
    var timeAgo = tx.created_at ? _pendingTimeAgo(tx.created_at) : '';
    var expiresInfo = tx.expires_at ? '<span style="color:var(--orange);font-size:10px;margin-left:6px">exp ' + _pendingTimeAgo(tx.expires_at) + '</span>' : '';

    return '<div style="background:rgba(255,255,255,.03);border:1px solid rgba(255,255,255,.06);border-radius:12px;padding:14px 16px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">'
      + '<span style="background:' + typeColor + '22;color:' + typeColor + ';font-size:10px;font-weight:700;padding:3px 8px;border-radius:6px;letter-spacing:.5px">' + tx._type + '</span>'
      + '<div style="flex:1;min-width:120px">'
      + '<div style="font-size:14px;font-weight:600">' + amount + ' USDC &#8594; ' + token + '</div>'
      + '<div style="font-size:11px;color:var(--muted)">Price: $' + price + ' &middot; Fee: $' + commission + ' &middot; ' + timeAgo + expiresInfo + '</div>'
      + '</div>'
      + '<div style="display:flex;gap:6px">'
      + '<button onclick="signPendingTx(\'' + txId + '\',\'' + tx._type.toLowerCase() + '\',\'' + _pendingSafeB64(swapTx) + '\')" style="padding:7px 14px;background:linear-gradient(135deg,#00e5ff,#7c3aed);color:white;border:none;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;font-family:DM Sans,sans-serif;transition:opacity .2s" onmouseenter="this.style.opacity=\'0.85\'" onmouseleave="this.style.opacity=\'1\'">Sign &amp; Execute</button>'
      + '<button onclick="cancelPendingTx(\'' + txId + '\',\'' + tx._type.toLowerCase() + '\')" style="padding:7px 14px;background:none;color:var(--muted);border:1px solid rgba(255,255,255,.1);border-radius:8px;font-size:12px;cursor:pointer;font-family:DM Sans,sans-serif;transition:all .2s" onmouseenter="this.style.borderColor=\'rgba(244,63,94,.4)\';this.style.color=\'#f43f5e\'" onmouseleave="this.style.borderColor=\'rgba(255,255,255,.1)\';this.style.color=\'var(--muted)\'">Cancel</button>'
      + '</div>'
      + '</div>';
  }).join('');
}

function _pendingTimeAgo(dateStr) {
  var diff = Date.now() - new Date(dateStr).getTime();
  if (diff < 0) return 'in ' + Math.ceil(Math.abs(diff) / 60000) + 'm';
  if (diff < 60000) return Math.floor(diff / 1000) + 's ago';
  if (diff < 3600000) return Math.floor(diff / 60000) + 'm ago';
  return Math.floor(diff / 3600000) + 'h ago';
}

function _pendingSafeB64(str) {
  // Escape single quotes in base64 for safe inline onclick
  return (str || '').replace(/'/g, "\\'");
}

async function signPendingTx(txId, type, swapTransaction) {
  if (!requireWalletFor('sign transaction')) return;
  if (!swapTransaction) { toast('No transaction data', 'error'); return; }

  try {
    // Check Phantom availability
    if (!window.solana || !window.solana.isPhantom) {
      toast('Phantom wallet not found', 'error');
      return;
    }
    // Connect if not connected
    if (!window.solana.isConnected) {
      await window.solana.connect();
    }

    toast('Signing transaction...', 'info');

    // Decode the base64 swap transaction
    var txBytes = Uint8Array.from(atob(swapTransaction), function(c) { return c.charCodeAt(0); });

    // Try VersionedTransaction first, fall back to legacy Transaction
    var tx;
    try {
      tx = solanaWeb3.VersionedTransaction.deserialize(txBytes);
    } catch(e) {
      tx = solanaWeb3.Transaction.from(txBytes);
    }

    // Sign with Phantom
    var signed = await window.solana.signTransaction(tx);

    // Send to Solana
    var connection = new solanaWeb3.Connection(SOLANA_RPC, 'confirmed');
    var rawTx = signed.serialize();
    var sig = await connection.sendRawTransaction(rawTx, { skipPreflight: false, preflightCommitment: 'confirmed' });

    toast('Transaction sent! Confirming...', 'info');

    // Wait for confirmation
    await connection.confirmTransaction(sig, 'confirmed');

    // Report confirmation to backend
    var confirmPath = '/api/' + encodeURIComponent(type) + '/confirm/' + encodeURIComponent(txId);
    await api(confirmPath, {
      method: 'POST',
      body: JSON.stringify({ tx_signature: sig })
    });

    toast('Transaction confirmed! ' + sig.slice(0, 8) + '...', 'success');
    loadPendingTxs();
  } catch(e) {
    console.error('[signPendingTx]', e);
    toast('Signing failed: ' + (e.message || 'Unknown error'), 'error');
  }
}

async function cancelPendingTx(txId, type) {
  if (!txId || !type) return;
  try {
    var deletePath = '/api/' + encodeURIComponent(type) + '/pending/' + encodeURIComponent(txId);
    await api(deletePath, { method: 'DELETE' });
    toast('Transaction cancelled', 'success');
    loadPendingTxs();
  } catch(e) {
    toast('Cancel failed: ' + (e.message || 'Unknown error'), 'error');
  }
}

// ==========================================
// Trading Terminal Logic
// ==========================================

var _ttSide = 'buy';
var _ttOrderType = 'market';
var _ttHFT = false;
var _ttPriceWS = null;
var _ttOrders = [];

function setOrderSide(side) {
  _ttSide = side;
  var buyTab = document.getElementById('tt-buy-tab');
  var sellTab = document.getElementById('tt-sell-tab');
  var btn = document.getElementById('tt-submit-btn');
  var token = (document.getElementById('chart-token').value||'SOL').toUpperCase();
  if (side === 'buy') {
    buyTab.style.background = 'rgba(16,185,129,.15)'; buyTab.style.color = 'var(--green)';
    sellTab.style.background = 'rgba(255,255,255,.03)'; sellTab.style.color = 'var(--muted)';
    btn.textContent = 'BUY ' + token;
    btn.style.background = 'linear-gradient(135deg,#10B981,#059669)';
  } else {
    sellTab.style.background = 'rgba(239,68,68,.15)'; sellTab.style.color = 'var(--red)';
    buyTab.style.background = 'rgba(255,255,255,.03)'; buyTab.style.color = 'var(--muted)';
    btn.textContent = 'SELL ' + token;
    btn.style.background = 'linear-gradient(135deg,#EF4444,#DC2626)';
  }
  updateTTEstimate();
}

function setOrderType(type, btn) {
  _ttOrderType = type;
  document.querySelectorAll('.tt-otype').forEach(function(b) { b.classList.remove('active'); });
  if (btn) btn.classList.add('active');
  document.getElementById('tt-limit-row').style.display = type === 'limit' ? 'block' : 'none';
  document.getElementById('tt-stop-row').style.display = type === 'stop' ? 'block' : 'none';
}

function setTTAmount(pct) {
  // Set amount to percentage of balance (placeholder: $10000 sandbox)
  var balance = 10000;
  document.getElementById('tt-amount').value = (balance * pct / 100).toFixed(2);
  updateTTEstimate();
}

function updateTTEstimate() {
  var amount = parseFloat(document.getElementById('tt-amount').value) || 0;
  var priceText = document.getElementById('tt-live-price').textContent.replace(/[^0-9.]/g, '');
  var price = parseFloat(priceText) || 1;
  var qty = amount / price;
  var fee = amount * 0.001; // 0.1% default
  document.getElementById('tt-est-qty').textContent = qty.toFixed(6);
  document.getElementById('tt-est-fee').textContent = '$' + fee.toFixed(4);
  document.getElementById('tt-est-total').textContent = '$' + (amount + fee).toFixed(2);
}

function toggleTradingMode() {
  _ttHFT = !_ttHFT;
  var badge = document.getElementById('tt-mode-badge');
  badge.textContent = _ttHFT ? 'HFT' : 'NORMAL';
  badge.style.background = _ttHFT ? 'rgba(139,92,246,.15)' : '';
  badge.style.borderColor = _ttHFT ? 'rgba(139,92,246,.3)' : '';
  badge.style.color = _ttHFT ? 'var(--purple)' : '';
  if (_ttHFT) connectTTPriceStream(); else disconnectTTPriceStream();
}

function connectTTPriceStream() {
  if (_ttPriceWS && _ttPriceWS.readyState === WebSocket.OPEN) return;
  var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  _ttPriceWS = new WebSocket(proto + '//' + location.host + '/ws/prices');
  _ttPriceWS.onopen = function() {
    _ttPriceWS.send(JSON.stringify({mode: 'hft'}));
    document.getElementById('tt-stream-badge').textContent = 'HFT Live';
    document.getElementById('tt-stream-badge').style.background = 'rgba(139,92,246,.15)';
    document.getElementById('tt-stream-badge').style.borderColor = 'rgba(139,92,246,.3)';
    document.getElementById('tt-stream-badge').style.color = 'var(--purple)';
  };
  _ttPriceWS.onmessage = function(ev) {
    try {
      var d = JSON.parse(ev.data);
      if (d.data && d.data.symbol) {
        var sel = (document.getElementById('chart-token').value||'').toUpperCase();
        if (d.data.symbol === sel) {
          document.getElementById('tt-live-price').textContent = _fmtPrice(d.data.price);
          updateTTEstimate();
        }
      }
    } catch(e) {}
  };
  _ttPriceWS.onclose = function() {
    document.getElementById('tt-stream-badge').textContent = 'Offline';
    document.getElementById('tt-stream-badge').style.background = '';
    document.getElementById('tt-stream-badge').style.color = '';
    if (_ttHFT) setTimeout(connectTTPriceStream, 3000);
  };
}

function disconnectTTPriceStream() {
  if (_ttPriceWS) { _ttPriceWS.close(); _ttPriceWS = null; }
  document.getElementById('tt-stream-badge').textContent = 'Normal';
  document.getElementById('tt-stream-badge').style.background = 'rgba(16,185,129,.1)';
  document.getElementById('tt-stream-badge').style.color = 'var(--green)';
}

async function submitTTOrder() {
  var token = (document.getElementById('chart-token').value||'SOL').toUpperCase();
  var amount = parseFloat(document.getElementById('tt-amount').value) || 0;
  var resultEl = document.getElementById('tt-order-result');
  if (amount <= 0) { resultEl.style.display = 'block'; resultEl.style.color = 'var(--red)'; resultEl.textContent = 'Enter an amount'; return; }

  var btn = document.getElementById('tt-submit-btn');
  btn.disabled = true; btn.textContent = 'Processing...';
  resultEl.style.display = 'none';

  // Detect if stock or crypto
  var stocks = ['AAPL','TSLA','NVDA','MSFT','GOOG','AMZN','META','COIN','SPY','QQQ','MSTR'];
  var isStock = stocks.indexOf(token) >= 0;

  try {
    var url, body;
    if (isStock) {
      url = '/api/stocks/' + (_ttSide === 'buy' ? 'buy' : 'sell');
      body = {symbol: token, amount_usdc: amount, payment_tx: 'pending'};
    } else {
      var fromTk = _ttSide === 'buy' ? 'USDC' : token;
      var toTk   = _ttSide === 'buy' ? token : 'USDC';
      url = '/api/public/crypto/quote?from_token=' + fromTk + '&to_token=' + toTk + '&amount=' + amount;
      body = null;
    }

    var resp;
    if (body !== null) {
      resp = await fetch(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + (sessionStorage.getItem('session_token')||'')},
        body: JSON.stringify(body)
      });
    } else {
      resp = await fetch(url);
    }
    var data = await resp.json();

    resultEl.style.display = 'block';
    if (data.success || data.shares || data.output_amount || data.to_amount) {
      resultEl.style.color = 'var(--green)';
      resultEl.innerHTML = (_ttSide==='buy'?'Bought':'Sold') + ' ' + esc(token) + ' — ' + (isStock ? esc(data.shares)+' shares' : esc(data.output_amount||data.to_amount||data.amount_out)) + ' <span style="color:var(--muted);font-size:11px">' + esc(data.tier||'') + ' fee:$' + esc(data.commission_usd||data.commission_usdc||'0') + '</span>';
      // Add to local order history
      _ttOrders.unshift({time: new Date().toLocaleTimeString(), asset: token, side: _ttSide, amount: '$'+amount.toFixed(2), status: 'filled'});
      renderTTOrders();
    } else {
      resultEl.style.color = 'var(--red)';
      resultEl.textContent = data.error || data.message || 'Order failed';
    }
  } catch(e) {
    resultEl.style.display = 'block'; resultEl.style.color = 'var(--red)'; resultEl.textContent = 'Network error';
  }
  btn.disabled = false;
  setOrderSide(_ttSide); // Reset button text
}

function renderTTOrders() {
  var tbody = document.getElementById('tt-orders');
  if (!_ttOrders.length) { tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:16px;color:var(--muted)">No orders yet</td></tr>'; return; }
  tbody.innerHTML = _ttOrders.slice(0, 20).map(function(o) {
    var sideColor = o.side === 'buy' ? 'var(--green)' : 'var(--red)';
    return '<tr><td>' + esc(o.time) + '</td><td style="font-weight:600">' + esc(o.asset) + '</td><td style="color:'+sideColor+';font-weight:600;text-transform:uppercase">' + esc(o.side) + '</td><td>' + esc(o.amount) + '</td><td><span class="badge badge-green" style="font-size:10px">' + esc(o.status) + '</span></td></tr>';
  }).join('');
}

async function loadTTPortfolio() {
  var tbody = document.getElementById('tt-portfolio');
  try {
    var resp = await fetch('/api/public/sandbox/portfolio', {headers: {'X-API-Key': sessionStorage.getItem('session_token')||'sandbox-test'}});
    var data = await resp.json();
    if (!data.positions || !data.positions.length) {
      tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:16px;color:var(--muted)">No positions</td></tr>';
      document.getElementById('tt-total-val').textContent = '$0.00';
      return;
    }
    var totalVal = 0;
    tbody.innerHTML = data.positions.map(function(p) {
      var val = p.value_usdc || (p.quantity * p.current_price);
      var pnl = p.pnl_usdc || 0;
      var pnlColor = pnl >= 0 ? 'var(--green)' : 'var(--red)';
      totalVal += val;
      return '<tr><td style="font-weight:600">' + esc(p.symbol) + '</td><td>' + (p.quantity||0).toFixed(4) + '</td><td>$' + (p.avg_price||0).toFixed(2) + '</td><td>$' + val.toFixed(2) + '</td><td style="color:'+pnlColor+';font-weight:600">' + (pnl>=0?'+':'') + pnl.toFixed(2) + '</td></tr>';
    }).join('');
    document.getElementById('tt-total-val').textContent = '$' + totalVal.toFixed(2);
  } catch(e) {
    tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:16px;color:var(--muted)">Error loading portfolio</td></tr>';
  }
}

async function loadTTOrders() { renderTTOrders(); }

// Auto-update live price when chart token changes
document.getElementById('chart-token').addEventListener('change', function() {
  setOrderSide(_ttSide);
  loadTTLivePrice();
});

var _ttAllPrices = {};
async function loadTTLivePrice() {
  var token = (document.getElementById('chart-token').value||'SOL').toUpperCase();
  // Fetch crypto + stocks in parallel
  try {
    var [cryptoResp, stockResp] = await Promise.all([
      fetch('/api/public/crypto/prices').then(function(r){return r.json()}).catch(function(){return {}}),
      fetch('/api/public/stocks').then(function(r){return r.json()}).catch(function(){return []})
    ]);
    var cp = cryptoResp.prices || cryptoResp;
    for (var k in cp) { _ttAllPrices[k] = (typeof cp[k]==='object') ? cp[k].price : cp[k]; }
    var stocks = Array.isArray(stockResp) ? stockResp : (stockResp.stocks || []);
    for (var i=0; i<stocks.length; i++) { _ttAllPrices[stocks[i].symbol] = stocks[i].price_usd || stocks[i].price || 0; }
  } catch(e) {}
  // Fallback for tokens not in crypto/stocks (XRP, AVAX, MATIC etc.)
  if (!_ttAllPrices[token] || _ttAllPrices[token] <= 0) {
    try {
      var ids = {XRP:'ripple',AVAX:'avalanche-2',MATIC:'matic-network'};
      var cgId = ids[token];
      if (cgId) {
        var r = await fetch('https://api.coingecko.com/api/v3/simple/price?ids='+cgId+'&vs_currencies=usd');
        var d = await r.json();
        if (d[cgId] && d[cgId].usd) _ttAllPrices[token] = d[cgId].usd;
      }
    } catch(e) {}
  }
  var price = _ttAllPrices[token];
  if (price && price > 0) {
    document.getElementById('tt-live-price').textContent = _fmtPrice(price);
    updateTTEstimate();
  } else {
    document.getElementById('tt-live-price').textContent = '--';
  }
}
setInterval(loadTTLivePrice, 10000);
loadTTLivePrice();

// Update amount estimate on input
document.getElementById('tt-amount').addEventListener('input', updateTTEstimate);

// ==================================================================
// TRADING TERMINAL — Professional candlestick chart (Lightweight Charts v4.2)
// Recoded from scratch 2026-03-29 — zero bugs, all tokens, volume, OHLCV legend
// ==================================================================

var _chart = null;
var _candleSeries = null;
var _volumeSeries = null;
var _chartTF = '5s';
var _chartWS = null;
var _chartWSId = 0;
var _chartRO = null;  // ResizeObserver
var _chartPollTimer = null;  // REST poll timer for non-WS tokens

var _TIMEFRAMES = {
  '1s':  { seconds: 1,     limit: 300, label: '1S' },
  '5s':  { seconds: 5,     limit: 300, label: '5S' },
  '1m':  { seconds: 60,    limit: 120, label: '1M' },
  '1h':  { seconds: 3600,  limit: 48,  label: '1H' },
  '6h':  { seconds: 21600, limit: 48,  label: '6H' },
  '1d':  { seconds: 86400, limit: 90,  label: '1D' },
  '1w':  { seconds: 604800,limit: 52,  label: '1W' },
};

// -- Price formatting --
function _fmtPrice(p) {
  if (p == null || isNaN(p)) return '—';
  if (p >= 10000)  return '$' + p.toFixed(2);
  if (p >= 1)      return '$' + p.toFixed(4);
  if (p >= 0.001)  return '$' + p.toFixed(6);
  return '$' + p.toFixed(8);
}
function _fmtVol(v) {
  if (!v || v <= 0) return '';
  if (v >= 1e9) return (v/1e9).toFixed(1) + 'B';
  if (v >= 1e6) return (v/1e6).toFixed(1) + 'M';
  if (v >= 1e3) return (v/1e3).toFixed(1) + 'K';
  return v.toFixed(0);
}
function _priceFormat(price) {
  if (price >= 1000) return { type: 'price', precision: 2, minMove: 0.01 };
  if (price >= 1)    return { type: 'price', precision: 4, minMove: 0.0001 };
  if (price >= 0.01) return { type: 'price', precision: 6, minMove: 0.000001 };
  return { type: 'price', precision: 8, minMove: 0.00000001 };
}

// -- Timeframe switching --
function setChartTF(tf, btn) {
  if (!_TIMEFRAMES[tf]) return;
  _chartTF = tf;
  document.querySelectorAll('.chart-tf').forEach(function(b) {
    b.classList.remove('active');
    b.style.color = ''; b.style.borderColor = '';
  });
  if (btn) {
    btn.classList.add('active');
    btn.style.color = '#10b981'; btn.style.borderColor = '#10b981';
  }
  try { loadChart(); } catch(e) { console.warn('[chart]', e); }
}

// -- Chart creation (destroy + recreate = clean state) --
function _destroyChart() {
  _chartWSId++;
  if (_chartPollTimer) { clearInterval(_chartPollTimer); _chartPollTimer = null; }
  if (_chartWS) { _chartWS.onclose = null; _chartWS.onerror = null; _chartWS.onmessage = null; _chartWS.close(); _chartWS = null; }
  if (_chartRO) { _chartRO.disconnect(); _chartRO = null; }
  if (_chart) { _chart.remove(); _chart = null; }
  _candleSeries = null; _volumeSeries = null;
}

function _createChart(container) {
  _destroyChart();
  if (typeof LightweightCharts === 'undefined' || !container) return;
  var tf = _TIMEFRAMES[_chartTF] || _TIMEFRAMES['5s'];
  var isShort = tf.seconds < 60;

  _chart = LightweightCharts.createChart(container, {
    width: container.clientWidth, height: 420,
    layout: {
      background: { type: 'solid', color: '#0a0e17' },
      textColor: '#94a3b8', fontSize: 12,
      fontFamily: "'DM Sans', 'Inter', -apple-system, sans-serif",
    },
    grid: {
      vertLines: { color: 'rgba(255,255,255,.03)' },
      horzLines: { color: 'rgba(255,255,255,.03)' },
    },
    rightPriceScale: {
      borderColor: 'rgba(255,255,255,.06)',
      scaleMargins: { top: 0.05, bottom: 0.28 },
    },
    timeScale: {
      borderColor: 'rgba(255,255,255,.06)',
      timeVisible: true,
      secondsVisible: isShort,
      rightOffset: isShort ? 5 : 2,
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { width: 1, color: 'rgba(255,255,255,.12)', style: LightweightCharts.LineStyle.Dashed, labelBackgroundColor: '#7c3aed' },
      horzLine: { width: 1, color: 'rgba(255,255,255,.12)', style: LightweightCharts.LineStyle.Dashed, labelBackgroundColor: '#7c3aed' },
    },
    localization: { priceFormatter: function(p) { return _fmtPrice(p).replace('$',''); } },
    handleScroll: { vertTouchDrag: false },
  });

  // Candlestick series
  _candleSeries = _chart.addCandlestickSeries({
    upColor: '#10b981', downColor: '#ef4444',
    borderUpColor: '#10b981', borderDownColor: '#ef4444',
    wickUpColor: '#10b981', wickDownColor: '#ef4444',
    borderVisible: false,
  });

  // Volume histogram (bottom 25%)
  _volumeSeries = _chart.addHistogramSeries({
    priceFormat: { type: 'volume' },
    priceScaleId: '',
  });
  _volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.75, bottom: 0 } });

  // OHLCV legend on crosshair move
  _chart.subscribeCrosshairMove(function(param) {
    var el = document.getElementById('chart-legend');
    if (!el) return;
    if (!param || !param.time || !param.seriesData) { el.innerHTML = ''; return; }
    var d = param.seriesData.get(_candleSeries);
    if (!d) { el.innerHTML = ''; return; }
    var c = d.close >= d.open ? '#10b981' : '#ef4444';
    var pct = d.open ? ((d.close - d.open) / d.open * 100).toFixed(2) : '0.00';
    var sign = pct >= 0 ? '+' : '';
    el.innerHTML = '<span style="color:#64748b">O</span> <span style="color:'+c+'">'+_fmtPrice(d.open).replace('$','')+'</span> '
      + '<span style="color:#64748b">H</span> <span style="color:'+c+'">'+_fmtPrice(d.high).replace('$','')+'</span> '
      + '<span style="color:#64748b">L</span> <span style="color:'+c+'">'+_fmtPrice(d.low).replace('$','')+'</span> '
      + '<span style="color:#64748b">C</span> <span style="color:'+c+'">'+_fmtPrice(d.close).replace('$','')+'</span> '
      + '<span style="color:'+c+'">'+sign+pct+'%</span>';
    var vd = param.seriesData.get(_volumeSeries);
    if (vd && vd.value) el.innerHTML += ' <span style="color:#64748b">Vol</span> <span style="color:#475569">'+_fmtVol(vd.value)+'</span>';
  });

  // Responsive resize via ResizeObserver
  if (typeof ResizeObserver !== 'undefined') {
    _chartRO = new ResizeObserver(function(entries) {
      if (_chart && entries[0]) _chart.applyOptions({ width: entries[0].contentRect.width });
    });
    _chartRO.observe(container);
  } else {
    window.addEventListener('resize', function() { if (_chart) _chart.applyOptions({ width: container.clientWidth }); });
  }
}

// -- WebSocket chart connection --
function _connectChartWS(symbol, interval_s) {
  _chartWSId++;
  var myId = _chartWSId;
  if (_chartWS) { _chartWS.onclose = null; _chartWS.onerror = null; _chartWS.close(); _chartWS = null; }
  var proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  _chartWS = new WebSocket(proto + '//' + location.host + '/ws/chart');

  _chartWS.onopen = function() {
    if (myId !== _chartWSId) return;
    _chartWS.send(JSON.stringify({ symbol: symbol, interval: interval_s }));
    _setBadge('LIVE ' + (interval_s < 60 ? interval_s + 's' : interval_s < 3600 ? (interval_s/60)+'m' : (interval_s/3600)+'h'), true);
  };

  _chartWS.onmessage = function(e) {
    if (myId !== _chartWSId) return;
    try {
      var msg = JSON.parse(e.data);
      if (msg.type === 'history' && Array.isArray(msg.candles) && msg.candles.length > 20 && _candleSeries) {
        var candles = msg.candles;
        _candleSeries.setData(candles);
        // Volume data
        if (_volumeSeries) {
          _volumeSeries.setData(candles.map(function(c) {
            return { time: c.time, value: c.volume || 0, color: c.close >= c.open ? 'rgba(16,185,129,.35)' : 'rgba(239,68,68,.35)' };
          }));
        }
        // Auto-adjust price format based on price range
        if (candles.length > 0) {
          var lastP = candles[candles.length-1].close;
          _candleSeries.applyOptions({ priceFormat: _priceFormat(lastP) });
          _updateLivePrice(lastP, candles.length > 1 ? candles[candles.length-2].close : lastP);
        }
        _chart.timeScale().fitContent();
      } else if (msg.type === 'candle_update' && _candleSeries) {
        var bar = { time: msg.time, open: msg.open, high: msg.high, low: msg.low, close: msg.close };
        _candleSeries.update(bar);
        if (_volumeSeries) _volumeSeries.update({ time: msg.time, value: msg.volume || 0, color: msg.close >= msg.open ? 'rgba(16,185,129,.35)' : 'rgba(239,68,68,.35)' });
        _chart.timeScale().scrollToRealTime();
        _updateLivePrice(msg.close, msg.open);
      } else if (msg.type === 'candle_complete' && _candleSeries) {
        var bar = { time: msg.time, open: msg.open, high: msg.high, low: msg.low, close: msg.close };
        _candleSeries.update(bar);
        if (_volumeSeries) _volumeSeries.update({ time: msg.time, value: msg.volume || 0, color: msg.close >= msg.open ? 'rgba(16,185,129,.35)' : 'rgba(239,68,68,.35)' });
      }
    } catch(err) {}
  };

  _chartWS.onerror = function() {};
  _chartWS.onclose = function() {
    if (myId !== _chartWSId) return;
    _setBadge('Offline', false);
    setTimeout(function() { if (myId === _chartWSId) _connectChartWS(symbol, interval_s); }, 3000);
  };
}

// -- REST polling fallback (uses /api/public/crypto/prices — not rate limited) --
var _allPricesCache = {};
var _allPricesLastFetch = 0;

async function _fetchAllPrices() {
  var now = Date.now();
  if (now - _allPricesLastFetch < 5000 && Object.keys(_allPricesCache).length > 0) return _allPricesCache;
  try {
    var r = await fetch('/api/public/crypto/prices');
    var data = await r.json();
    _allPricesCache = data.prices || data;
    _allPricesLastFetch = now;
  } catch(e) {}
  return _allPricesCache;
}

function _startRESTPolling(token, intervalTF) {
  if (_chartPollTimer) { clearInterval(_chartPollTimer); _chartPollTimer = null; }
  var tf = _TIMEFRAMES[intervalTF] || _TIMEFRAMES['1m'];
  var pollInterval = Math.max(tf.seconds * 1000, 10000);  // minimum 10s
  // Immediate first fetch
  _pollOnce(token, tf);
  _chartPollTimer = setInterval(function() { _pollOnce(token, tf); }, pollInterval);
}

async function _pollOnce(token, tf) {
  try {
    var prices = await _fetchAllPrices();
    var entry = prices[token];
    var p = entry ? (typeof entry === 'object' ? entry.price : entry) : null;
    if (p && _candleSeries) {
      var now = Math.floor(Date.now() / 1000 / tf.seconds) * tf.seconds;
      _candleSeries.update({ time: now, open: p, high: p, low: p, close: p });
      _updateLivePrice(p, null);
    }
  } catch(e) {}
}

// -- UI helpers --
function _setBadge(text, live) {
  var b = document.getElementById('tt-stream-badge');
  if (!b) return;
  b.textContent = text;
  if (live) { b.className = 'badge badge-green'; }
  else { b.className = 'badge'; b.style.background = ''; b.style.color = ''; }
}

function _updateLivePrice(price, prevPrice) {
  var el = document.getElementById('tt-live-price');
  var chEl = document.getElementById('tt-price-change');
  if (el) el.textContent = _fmtPrice(price);
  if (chEl && prevPrice && prevPrice > 0) {
    var pct = ((price - prevPrice) / prevPrice * 100);
    var sign = pct >= 0 ? '+' : '';
    chEl.textContent = sign + pct.toFixed(2) + '%';
    chEl.style.color = pct >= 0 ? '#10b981' : '#ef4444';
  }
  updateTTEstimate();
}

// -- Main load function --
async function loadChart() {
  var token = (document.getElementById('chart-token').value || 'SOL').trim().toUpperCase();
  var wrap = document.getElementById('tv-chart-wrap');
  var signalEl = document.getElementById('chart-signal');
  if (!wrap) return;

  // Map MAXIA token symbols to TradingView symbols
  var tvMap = {
    'SOL':'BINANCE:SOLUSDT','BTC':'BINANCE:BTCUSDT','ETH':'BINANCE:ETHUSDT',
    'XRP':'BINANCE:XRPUSDT','AVAX':'BINANCE:AVAXUSDT','LINK':'BINANCE:LINKUSDT',
    'UNI':'BINANCE:UNIUSDT','AAVE':'BINANCE:AAVEUSDT','DOGE':'BINANCE:DOGEUSDT',
    'MATIC':'BINANCE:MATICUSDT','NEAR':'BINANCE:NEARUSDT','APT':'BINANCE:APTUSDT',
    'SUI':'BINANCE:SUIUSDT','SEI':'BINANCE:SEIUSDT','INJ':'BINANCE:INJUSDT',
    'ARB':'BINANCE:ARBUSDT','OP':'BINANCE:OPUSDT','TIA':'BINANCE:TIAUSDT',
    'RENDER':'BINANCE:RENDERUSDT','FET':'BINANCE:FETUSDT','TAO':'BINANCE:TAOUSDT',
    'AKT':'BINANCE:AKTUSDT','FIL':'BINANCE:FILUSDT','AR':'BINANCE:ARUSDT',
    'HNT':'BINANCE:HNTUSDT','LDO':'BINANCE:LDOUSDT','JUP':'BINANCE:JUPUSDT',
    'RAY':'BINANCE:RAYUSDT','ORCA':'BINANCE:ORCAUSDT','DRIFT':'BYBIT:DRIFTUSDT',
    'JTO':'BINANCE:JTOUSDT','PYTH':'BINANCE:PYTHUSDT','W':'BINANCE:WUSDT',
    'ONDO':'BINANCE:ONDOUSDT','BONK':'BINANCE:BONKUSDT','WIF':'BINANCE:WIFUSDT',
    'POPCAT':'BYBIT:POPCATUSDT','PENGU':'BINANCE:PENGUUSDT','PEPE':'BINANCE:PEPEUSDT',
    'SHIB':'BINANCE:SHIBUSDT','FARTCOIN':'BYBIT:FARTCOINUSDT',
    'TRUMP':'BYBIT:TRUMPUSDT','BNB':'BINANCE:BNBUSDT','TON':'BINANCE:TONUSDT',
    'TRX':'BINANCE:TRXUSDT','STX':'BINANCE:STXUSDT',
    'AAPL':'NASDAQ:AAPL','TSLA':'NASDAQ:TSLA','NVDA':'NASDAQ:NVDA',
    'MSFT':'NASDAQ:MSFT','GOOGL':'NASDAQ:GOOGL','AMZN':'NASDAQ:AMZN',
    'META':'NASDAQ:META','AMD':'NASDAQ:AMD','NFLX':'NASDAQ:NFLX',
    'PLTR':'NASDAQ:PLTR','COIN':'NASDAQ:COIN','MSTR':'NASDAQ:MSTR',
    'SPY':'AMEX:SPY','QQQ':'NASDAQ:QQQ',
  };
  var tvSymbol = tvMap[token] || ('BINANCE:' + token + 'USDT');

  wrap.innerHTML = '';
  var widgetDiv = document.createElement('div');
  widgetDiv.className = 'tradingview-widget-container';
  widgetDiv.style.cssText = 'width:100%;height:100%';
  widgetDiv.innerHTML = '<div id="tv-widget" style="width:100%;height:100%"></div>';
  wrap.appendChild(widgetDiv);

  var script = document.createElement('script');
  script.src = 'https://s3.tradingview.com/tv.js';
  script.onload = function() {
    new TradingView.widget({
      container_id: 'tv-widget',
      symbol: tvSymbol,
      interval: '60',
      timezone: 'Etc/UTC',
      theme: 'dark',
      style: '1',
      locale: 'en',
      toolbar_bg: '#0a0e17',
      enable_publishing: false,
      allow_symbol_change: false,
      hide_top_toolbar: false,
      hide_legend: false,
      save_image: false,
      width: '100%',
      height: '100%',
      backgroundColor: '#0a0e17',
      gridColor: 'rgba(255,255,255,0.03)',
    });
  };
  wrap.appendChild(script);

  signalEl.innerHTML = '';

  // Load trading signal
  try {
    var sig = await api('/api/trading/signals/' + token);
    if (sig && sig.signal) {
      var sigColor = (sig.signal||'').indexOf('BUY') >= 0 ? '#10b981' : (sig.signal||'').indexOf('SELL') >= 0 ? '#ef4444' : '#f59e0b';
      signalEl.innerHTML += ' <span style="margin-left:12px;font-weight:700;color:' + sigColor + '">' + esc(sig.signal) + '</span>'
        + ' <span style="color:var(--muted)">RSI ' + (sig.rsi||0).toFixed(1) + '</span>'
        + ' <button onclick="goSwap(\'' + esc(token) + '\')" style="margin-left:8px;padding:3px 12px;background:linear-gradient(135deg,#00e5ff,#7c3aed);color:white;border:none;border-radius:6px;font-size:11px;cursor:pointer">Swap</button>';
    }
  } catch(e) {}
}

// Token Risk Check
async function checkTokenRisk() {
  var mint = document.getElementById('risk-mint').value.trim();
  var el = document.getElementById('risk-result');
  if (!mint || mint.length < 20) { el.innerHTML = '<span style="color:var(--red)">Paste a valid Solana mint address</span>'; return; }
  el.innerHTML = '<span style="color:var(--cyan)">Analyzing token risk...</span>';
  try {
    var r = await api('/api/trading/token-risk/' + encodeURIComponent(mint));
    if (!r) { el.innerHTML = '<span style="color:var(--red)">Error analyzing token</span>'; return; }
    var riskColor = r.risk_level === 'LOW' ? 'var(--green)' : r.risk_level === 'MEDIUM' ? 'var(--orange)' : 'var(--red)';
    var flagsHtml = (r.flags||[]).map(function(f) { return '<div style="padding:4px 0;border-bottom:1px solid rgba(255,255,255,.04)">- ' + esc(f) + '</div>'; }).join('');
    el.innerHTML = '<div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">'
      + '<div style="font-size:32px;font-weight:700;color:' + riskColor + '">' + r.risk_score + '</div>'
      + '<div><div style="font-weight:600;color:' + riskColor + '">' + esc(r.risk_level) + ' RISK</div>'
      + '<div style="font-size:12px;color:var(--muted)">' + esc(r.recommendation) + '</div></div></div>'
      + flagsHtml;
  } catch(e) { el.innerHTML = '<span style="color:var(--red)">Error: ' + esc(e.message) + '</span>'; }
}

async function copyTrade(address) {
  try {
    var r = await api('/api/trading/copy/wallet/' + address);
    var trades = r.trades || [];
    // Trouver le dernier trade "buy"
    var lastBuy = null;
    for (var i = 0; i < trades.length; i++) {
      if (trades[i].side === 'buy') { lastBuy = trades[i]; break; }
    }
    if (!lastBuy) {
      // Pas de buy récent, prendre le top token du wallet
      toast('No recent buy — showing top token', 'info');
      goSwap(trades.length > 0 ? trades[0].token : 'SOL');
      return;
    }
    // Aller au swap avec le token et le montant pré-rempli
    showPage('swap');
    var fromSel = document.getElementById('swap-from-token');
    var toSel = document.getElementById('swap-to-token');
    var amountInput = document.getElementById('swap-from-amount');
    // Mettre USDC en From (on achète avec USDC)
    if (fromSel) {
      for (var i = 0; i < fromSel.options.length; i++) {
        if (fromSel.options[i].value.toUpperCase() === 'USDC') { fromSel.selectedIndex = i; break; }
      }
    }
    // Mettre le token copié en To
    if (toSel) {
      for (var i = 0; i < toSel.options.length; i++) {
        if (toSel.options[i].value.toUpperCase() === lastBuy.token.toUpperCase()) { toSel.selectedIndex = i; break; }
      }
    }
    // Pré-remplir le montant (en USD, arrondi)
    if (amountInput) {
      amountInput.value = Math.min(Math.round(lastBuy.amount_usd), 1000);
    }
    toast('Copying ' + address.slice(0,8) + '... → Buy ' + lastBuy.token + ' ($' + Math.round(lastBuy.amount_usd) + ')', 'success');
    // Déclencher le quote
    if (typeof getSwapQuote === 'function') getSwapQuote();
  } catch(e) {
    toast('Error copying trade: ' + e.message, 'error');
    showPage('swap');
  }
}

function goSwap(token) {
  showPage('swap');
  var fromSel = document.getElementById('swap-from-token');
  var toSel = document.getElementById('swap-to-token');
  // Token en From, USDC en To
  if (fromSel) {
    for (var i = 0; i < fromSel.options.length; i++) {
      if (fromSel.options[i].value.toUpperCase() === token.toUpperCase()) { fromSel.selectedIndex = i; break; }
    }
  }
  if (toSel) {
    for (var i = 0; i < toSel.options.length; i++) {
      if (toSel.options[i].value.toUpperCase() === 'USDC') { toSel.selectedIndex = i; break; }
    }
  }
  toast('Swap ' + token + ' — lowest fees on 7 chains', 'info');
}

