/* ======================================
   MAXIA App — Navigation
   ====================================== */


// NAVIGATION
// ======================================
function showPage(name, btn) {
  // Hide all pages
  document.querySelectorAll('.page').forEach(function(p) { p.classList.remove('active'); });
  // Show target
  var target = document.getElementById('page-' + name);
  if (target) target.classList.add('active');

  // Update sidebar active state
  document.querySelectorAll('.nav-link').forEach(function(l) { l.classList.remove('active'); });
  if (btn) {
    btn.classList.add('active');
  } else {
    document.querySelectorAll('.nav-link').forEach(function(l) {
      var txt = l.textContent.trim().toLowerCase().replace(/[\s&#;\d]/g, '');
      if (txt.indexOf(name) >= 0 || (name === 'agentid' && txt.indexOf('agent') >= 0) ||
          (name === 'nfts' && txt.indexOf('nft') >= 0) || (name === 'home' && txt.indexOf('home') >= 0)) {
        l.classList.add('active');
      }
    });
  }

  // Close mobile sidebar
  document.querySelector('.sidebar').classList.remove('open');
  var overlay = document.querySelector('.sidebar-overlay');
  if (overlay) overlay.classList.remove('open');

  // Legacy compat
  document.querySelectorAll('.nav-link').forEach(function(l) {
    var txt = l.textContent.trim().toLowerCase().replace(/\s+/g, '');
    if (txt === name || (l.textContent.trim() === 'Agent ID' && name === 'agentid') ||
        (l.textContent.trim() === 'NFT' && name === 'nfts')) {
      l.classList.add('active');
    }
  });

  // Update hash
  window.location.hash = name;

  // Close mobile menu (sidebar)
  var navLinks = document.querySelector('.nav-links');
  if (navLinks) navLinks.classList.remove('open');

  // Lazy load data
  if (name === 'swap' && swapTokens.length === 0) loadSwapTokens();
  if (name === 'portfolio') { loadPortfolio(); loadPortfolioStats(); }
  if (name === 'gpu') { loadGPU(); _startAutoRefresh('gpu', loadGPU, 60000); }
  if (name === 'yields') { loadYields(); _startAutoRefresh('yields', loadYields, 60000); }
  if (name === 'stocks') loadStocks();
  if (name === 'nfts') loadNFTs();
  if (name === 'agentid') { loadAgentList(); loadCreditScore(); }
  if (name === 'trading') { loadAppWhales(); loadAppCopy(); loadPendingTxs(); requestAnimationFrame(function(){ requestAnimationFrame(function(){ try { loadChart(); } catch(e) { console.warn('[chart]', e); } }); }); loadAppAlerts(); _startAutoRefresh('trading', function(){ loadAppWhales(); try { loadChart(); } catch(e) {} loadAppAlerts(); loadPendingTxs(); }, 30000); }
  if (name === 'escrow') loadEscrows();
}
