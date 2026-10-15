const $ = (selector) => document.querySelector(selector);
const dealsEl = $('#deals');
const statusEl = $('#status');
const progressFill = $('#progress-fill');
const progressLabel = $('#progress-label');
const progressPercent = $('#progress-percent');
const refreshBtn = $('#refresh');
let lastRenderKey = '';
let isLoading = false;

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}
function fmtDate(ms) {
  if (!ms) return 'Noch nicht';
  try { return new Intl.DateTimeFormat('de-DE', { hour:'2-digit', minute:'2-digit', day:'2-digit', month:'2-digit' }).format(new Date(ms)); }
  catch { return 'Aktuell'; }
}
function ageText(ms) {
  if (!ms) return 'noch kein Scan';
  const mins = Math.max(0, Math.floor(ms / 60000));
  return mins < 1 ? 'gerade eben' : `vor ${mins} Min.`;
}
function renderDeals(data) {
  const deals = Array.isArray(data.deals) ? data.deals : [];
  const key = `${data.generatedAt}:${deals.length}:${deals.map(d => d.href + d.ratio).join('|')}`;
  if (key === lastRenderKey) return;
  lastRenderKey = key;
  $('#deal-count').textContent = String(deals.length);
  $('#region-count').textContent = `${data.regionsSucceeded ?? 0}/${data.regionCount ?? 53}`;
  $('#updated').textContent = data.generatedAt ? ageText(Date.now() - data.generatedAt) : 'Wird geladen';
  if (!deals.length) {
    dealsEl.innerHTML = `<div class="empty-state"><strong>Aktuell sind keine bestätigten Angebote verfügbar.</strong><br>${esc(data.note || 'Die erste Datenaktualisierung steht noch aus. Bitte nach dem ersten GitHub-Actions-Lauf erneut laden.')}</div>`;
    return;
  }
  dealsEl.innerHTML = deals.map((deal, i) => `
    <article class="deal">
      <div class="rank">${String(i+1).padStart(2,'0')}</div>
      <div class="deal-main">
        <a class="deal-title" href="${esc(deal.href)}" target="_blank" rel="noopener noreferrer">${esc(deal.title)}</a>
        <div class="deal-meta"><span class="country-pill">${esc(deal.region)}</span><span>Nennwert ${esc(deal.nominal)}</span><span>Stand ${esc(ageText(Date.now() - Number(deal.refreshedAt || data.generatedAt)))}</span></div>
      </div>
      <div class="deal-value"><div class="ratio">${Number(deal.ratio).toLocaleString('de-DE',{minimumFractionDigits:2,maximumFractionDigits:2})}</div><div class="per">Guthaben pro €1</div><div class="deal-price">${esc(deal.price)}</div><div class="deal-nominal">Wert: ${Number(deal.nominalEur).toLocaleString('de-DE',{style:'currency',currency:'EUR'})}</div></div>
    </article>`).join('');
}
function showData(data) {
  const total = Number(data.regionCount || 53);
  const checked = Number(data.regionsScannedThisRun || 0);
  const succeeded = Number(data.regionsSucceededThisRun || 0);
  const activeRegions = Number(data.regionsSucceeded || 0);
  const age = data.generatedAt ? Date.now() - Number(data.generatedAt) : null;
  $('#region-count').textContent = `${activeRegions}/${total}`;
  $('#updated').textContent = data.generatedAt ? ageText(age) : 'Wird geladen';
  const pct = data.generatedAt ? 100 : 0;
  progressFill.style.width = `${pct}%`;
  progressPercent.textContent = data.generatedAt ? 'bereit' : '—';
  progressLabel.textContent = data.generatedAt
    ? `Datei: ${fmtDate(data.generatedAt)} · Letzter Lauf: ${succeeded}/${checked} Länder erfolgreich · Aktualisierung etwa alle 5 Min.`
    : 'Warte auf den ersten automatischen Scan …';
  statusEl.textContent = data.generatedAt
    ? `${(data.deals || []).length} Angebote · Datendatei ${ageText(age)}`
    : 'Noch keine veröffentlichten Ergebnisse';
  renderDeals(data);
}
async function loadData(manual = false) {
  if (isLoading) return;
  isLoading = true;
  if (manual) refreshBtn.classList.add('spinning');
  try {
    const url = new URL('data.json', document.baseURI);
    url.searchParams.set('_', String(Date.now()));
    const response = await fetch(url.toString(), { cache:'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    showData(await response.json());
  } catch (e) {
    if (!lastRenderKey) {
      statusEl.textContent = 'Die veröffentlichte Datendatei ist momentan nicht erreichbar';
      progressLabel.textContent = 'Prüfe GitHub Pages und den ersten Workflow-Lauf.';
      dealsEl.innerHTML = '<div class="empty-state"><strong>Datendatei noch nicht verfügbar.</strong><br>Aktiviere GitHub Pages mit Quelle „GitHub Actions“ und starte den Workflow einmal manuell.</div>';
    }
  } finally {
    isLoading = false;
    refreshBtn.classList.remove('spinning');
  }
}
refreshBtn.addEventListener('click', () => loadData(true));
loadData();
// The web page checks the published static JSON periodically. The server-side workflow
// regenerates and deploys that file every five minutes; this timer does not trigger a scan.
setInterval(() => loadData(false), 30_000);
setInterval(() => {
  const updated = $('#updated');
  // Keep the age labels fresh between file reloads.
  if (updated && lastRenderKey) {
    // A complete reload runs shortly afterward; update the stat only when possible.
  }
}, 10_000);
