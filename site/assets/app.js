const FIELDS = [
  ['研究动机', '🎯'], ['解决问题', '❓'], ['现象分析', '🔍'],
  ['主要方法', '🛠️'], ['数据与实验', '📊'], ['主要贡献', '⭐'],
];
const FAVORITES_KEY = 'interspeech2026_favorites_v1';
let papers = [], stats = {}, narratives = {}, taxonomy = {macros: [], session_to_macro: {}}, query = '';

const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const favorites = () => {
  try { return new Set(JSON.parse(localStorage.getItem(FAVORITES_KEY) || '[]')); }
  catch { return new Set(); }
};
const isFavorite = id => favorites().has(id);
const sampleLabel = () => stats.data_scope === 'sample' ? '15篇开发样本' : '会议论文';
const sessions = () => taxonomy.session_to_macro || {};
const sampleCountFor = session => papers.filter(p => p.normalized_session === session).length;
function setView(hash) { location.hash = hash; }
function current() {
  const [type, ...value] = decodeURIComponent(location.hash.slice(1) || 'all').split('/');
  return {type, value: value.join('/')};
}
function sortedMacros() {
  return [...taxonomy.macros].sort((a, b) => {
    const count = papers.filter(p => sessions()[p.normalized_session] === b.id).length - papers.filter(p => sessions()[p.normalized_session] === a.id).length;
    return count || a.name.localeCompare(b.name);
  });
}
function macroSessions(macro) { return macro.sessions || Object.keys(sessions()).filter(name => sessions()[name] === macro.id); }
function navCount(rows) {
  const q = query.trim().toLocaleLowerCase();
  return q ? rows.filter(p => searchText(p).toLocaleLowerCase().includes(q)).length : rows.length;
}
function searchText(p) {
  return [p.title, p.authors, p.original_session, p.normalized_session, p.abstract,
    ...FIELDS.map(([key]) => p.analysis?.[key] || ''),
    ...Object.values(p.structured || {}).flat(),
  ].join(' ');
}
function navButton(label, hash, count, active, extra = '') {
  return `<button class="nav-item ${active ? 'active' : ''}" data-hash="${esc(hash)}" ${extra}>${label}<span>${count}</span></button>`;
}
function renderNav() {
  const view = current();
  const selectedMacro = view.type === 'macro' ? view.value : view.type === 'session' ? sessions()[view.value] : '';
  const favoriteRows = papers.filter(p => favorites().has(p.paper_id));
  const tree = sortedMacros().map(macro => {
    const children = macroSessions(macro).slice().sort((a, b) => sampleCountFor(b) - sampleCountFor(a) || a.localeCompare(b));
    const rows = papers.filter(p => sessions()[p.normalized_session] === macro.id);
    const expanded = selectedMacro === macro.id;
    return `<div class="nav-group ${expanded ? 'expanded' : ''}">
      <div class="nav-primary-row">
        <button class="nav-expand" data-toggle="${esc(macro.id)}" aria-label="${expanded ? '收起' : '展开'}${esc(macro.name)}" aria-expanded="${expanded}">${expanded ? '▾' : '▸'}</button>
        <button class="nav-primary-link ${view.type === 'macro' && view.value === macro.id ? 'active' : ''}" data-hash="#macro/${encodeURIComponent(macro.id)}">${esc(macro.name)}<span>${navCount(rows)}</span></button>
      </div>
      <div class="nav-sub-list">${children.map(name => {
        const childRows = papers.filter(p => p.normalized_session === name);
        return navButton(esc(name), `#session/${encodeURIComponent(name)}`, navCount(childRows), view.type === 'session' && view.value === name);
      }).join('')}</div>
    </div>`;
  }).join('');
  document.querySelector('#nav').innerHTML = `
    ${navButton('🏠 全部论文', '#all', navCount(papers), view.type === 'all')}
    <div class="nav-section-label">📚 分类目录 · 样本统计</div>${tree}
    <div class="nav-section-label">📊 专项分析</div>
    ${navButton('📚 Dataset / Corpus', '#special/dataset', stats.special?.dataset?.paper_count ?? 0, view.type === 'special' && view.value === 'dataset')}
    ${navButton('📐 Evaluation / Benchmark', '#special/evaluation', stats.special?.evaluation?.paper_count ?? 0, view.type === 'special' && view.value === 'evaluation')}
    <div class="nav-section-label">个人</div>
    ${navButton('☆ 收藏', '#favorites', favoriteRows.length, view.type === 'favorites')}`;
  document.querySelectorAll('[data-hash]').forEach(button => button.onclick = () => setView(button.dataset.hash));
  document.querySelectorAll('[data-toggle]').forEach(button => button.onclick = () => {
    const group = button.closest('.nav-group');
    group.classList.toggle('expanded');
    button.setAttribute('aria-expanded', group.classList.contains('expanded'));
    button.textContent = group.classList.contains('expanded') ? '▾' : '▸';
  });
}
function baseRows(view) {
  if (view.type === 'macro') return papers.filter(p => sessions()[p.normalized_session] === view.value);
  if (view.type === 'session') return papers.filter(p => p.normalized_session === view.value);
  if (view.type === 'special') return papers.filter(p => p.special_tags?.[view.value]?.relevant && ['high', 'medium'].includes(p.special_tags[view.value]?.confidence));
  if (view.type === 'favorites') return papers.filter(p => favorites().has(p.paper_id));
  return papers;
}
function filterRows(rows) {
  const q = query.trim().toLocaleLowerCase();
  return q ? rows.filter(p => searchText(p).toLocaleLowerCase().includes(q)) : rows;
}
function emptyStats() { return {paper_count: 0, top_topics: [], top_tasks: [], top_methods: [], top_datasets: [], top_metrics: [], top_languages: [], top_modalities: [], top_domains: [], contribution_types: []}; }
function uniqueCount(rows, field) {
  return new Set(rows.flatMap(p => p.structured?.[field] || []).filter(Boolean)).size;
}
function kpis(stat, rows) {
  return `<div class="sample-kpis">
    <div><strong>${stat.paper_count ?? rows.length}</strong><span>样本论文</span></div>
    <div><strong>${new Set(rows.map(p => p.normalized_session).filter(Boolean)).size}</strong><span>涉及 Session</span></div>
    <div><strong>${uniqueCount(rows, 'tasks')}</strong><span>任务标签</span></div>
    <div><strong>${uniqueCount(rows, 'datasets')}</strong><span>数据集标签</span></div>
    <div><strong>${uniqueCount(rows, 'languages')}</strong><span>语言标签</span></div>
  </div>`;
}
function ranked(title, items) {
  if (!items?.length) return '';
  return `<section class="rank-section"><h3>${title}</h3><div class="rank-list">${items.slice(0, 8).map(item => `<div><span>${esc(item.name)}</span><b>${item.count}</b></div>`).join('')}</div></section>`;
}
function specialDetails(s, kind) {
  if (kind === 'dataset') return `<div class="badges"><span class="badge">新建数据集 ${s.new_dataset_count || 0}</span><span class="badge">扩展/整理 ${s.expanded_or_curated_count || 0}</span><span class="badge">Benchmark 数据集 ${s.benchmark_dataset_count || 0}</span><span class="badge">确认公开 ${s.public_dataset_count || 0}</span></div>${ranked('数据集建设方式', s.collection_methods)}${ranked('标注方式', s.annotation_methods)}${s.reported_scales?.length ? `<h3>论文明确报告的数据规模</h3>${s.reported_scales.map(item => `<p>${esc(item.title)} · ${esc(item.dataset)}：${esc(item.scale_text)}</p>`).join('')}` : ''}`;
  return `<div class="badges"><span class="badge">Benchmark 论文 ${s.benchmark_paper_count || 0}</span><span class="badge">指标研究 ${s.metric_paper_count || 0}</span><span class="badge">评测协议 ${s.protocol_paper_count || 0}</span><span class="badge">含人工评价 ${s.human_evaluation_paper_count || 0}</span><span class="badge">鲁棒性/可靠性 ${s.robustness_reliability_paper_count || 0}</span></div>${ranked('评测方式', s.evaluation_modes)}${ranked('相关评测对象', s.top_evaluation_entities)}`;
}
function overview(stat, title, rows, narrative, kind) {
  const label = stats.data_scope === 'sample' ? '样本统计' : '会议统计';
  const sectionTitle = ['macro', 'session'].includes(current().type) ? `当前分类统计分析 · ${title}` : title;
  return `<section class="stats-panel">
    <div class="section-kicker">📊 ${label}</div><h2>${esc(sectionTitle)}</h2>
    ${kpis(stat, rows)}
    <div class="rank-grid">${ranked('🔥 研究主题', stat.top_topics)}${ranked('🎯 研究任务', stat.top_tasks)}${ranked('🛠 方法', stat.top_methods)}${ranked('📚 Dataset', stat.top_datasets)}${ranked('📐 Metrics', stat.top_metrics)}${ranked('🌍 Languages', stat.top_languages)}${ranked('🔊 Modalities', stat.top_modalities)}${ranked('🏥 应用领域', stat.top_domains)}${ranked('⭐ 贡献类型', stat.contribution_types)}</div>
    ${kind ? specialDetails(stat, kind) : ''}
    ${narrative ? `<div class="narrative"><h3>样本分析</h3><p>${esc(narrative.overview || '')}</p><p>${esc(narrative.trend_summary || '')}</p>${narrative.research_hotspots?.length ? `<div class="badges">${narrative.research_hotspots.map(x => `<span class="badge">${esc(x)}</span>`).join('')}</div>` : ''}</div>` : ''}
    <p class="sample-note">以上均为15篇开发样本的统计，不代表 Interspeech 2026 全部论文。</p>
  </section>`;
}
function card(p) {
  const a = p.analysis || {}, on = isFavorite(p.paper_id);
  return `<article class="paper-card">
    <div class="paper-actions"><button class="favorite ${on ? 'on' : ''}" aria-label="${on ? '取消收藏' : '收藏'} ${esc(p.title)}" data-fav="${esc(p.paper_id)}">${on ? '★ 已收藏' : '☆ 收藏'}</button>
      <div class="paper-links"><a href="${esc(p.pdf_url || '#')}" target="_blank" rel="noopener noreferrer" ${p.pdf_url ? '' : 'aria-disabled="true"'}>PDF</a><a href="${esc(p.isca_page_url)}" target="_blank" rel="noopener noreferrer">ISCA</a>${p.doi ? `<a href="https://doi.org/${esc(p.doi)}" target="_blank" rel="noopener noreferrer">DOI</a>` : ''}</div>
    </div>
    <h3 class="paper-title"><a href="${esc(p.isca_page_url)}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a></h3>
    <div class="paper-meta">${esc(Array.isArray(p.authors) ? p.authors.join(', ') : p.authors)} · ${esc(p.normalized_session || '')}</div>
    <div class="badges"><span class="badge session-badge">${esc(p.original_session || '')}</span>${(p.structured?.topics || []).slice(0, 3).map(item => `<span class="badge">${esc(item)}</span>`).join('')}</div>
    <div class="dimensions">${FIELDS.map(([key, icon]) => `<div class="dimension"><b>${icon} ${key}</b><p>${esc(a[key] || '')}</p></div>`).join('')}</div>
    <details class="abstract"><summary>查看完整摘要 Abstract</summary><p>${esc(p.abstract)}</p></details>
  </article>`;
}
function titleAndStats(view, rows) {
  if (view.type === 'macro') {
    const macro = taxonomy.macros.find(item => item.id === view.value);
    return {title: macro?.name || view.value, stat: stats.macro_categories?.[view.value] || emptyStats(), narrative: null};
  }
  if (view.type === 'session') return {title: view.value, stat: stats.categories?.[view.value] || emptyStats(), narrative: narratives[`category:${view.value}`]};
  if (view.type === 'special') return {title: view.value === 'dataset' ? 'Dataset / Corpus 专项' : 'Evaluation / Benchmark 专项', stat: stats.special?.[view.value] || emptyStats(), narrative: narratives[`special:${view.value}`], kind: view.value};
  return {title: 'Interspeech 2026 样本总览', stat: stats.all || emptyStats(), narrative: narratives.all};
}
function render() {
  const view = current(), rows = filterRows(baseRows(view));
  const content = document.querySelector('#content');
  const isSpecial = view.type === 'special';
  const page = titleAndStats(view, rows);
  let intro = '';
  if (view.type === 'all') intro = `<section class="sample-intro"><b>15篇开发样本</b><span>当前为15篇论文开发预览版本，完整 Interspeech 2026 论文将在后续版本加入。所有计数均为样本统计。</span></section>`;
  const hasOverview = view.type !== 'favorites';
  const first = hasOverview ? overview(page.stat, page.title, baseRows(view), page.narrative, page.kind) : '';
  const listTitle = view.type === 'all' ? '样本论文' : view.type === 'macro' ? '该一级方向全部论文' : view.type === 'session' ? '该 Official Session 论文' : view.type === 'favorites' ? '已收藏论文' : `${isSpecial ? '命中' : ''}论文`;
  const noRows = view.type === 'favorites' ? '点击论文卡片上的 ☆ 收藏，即可加入本机收藏。' : '当前样本中没有匹配论文。';
  content.innerHTML = intro + first + `<section class="paper-section"><div class="section-kicker">${sampleLabel()}</div><h2>${esc(listTitle)} <small>(${rows.length})</small></h2>${rows.length ? `<div class="paper-list">${rows.map(card).join('')}</div>` : `<div class="empty-state">${noRows}</div>`}</section>`;
  document.querySelectorAll('[data-fav]').forEach(button => button.onclick = () => {
    const saved = favorites();
    saved.has(button.dataset.fav) ? saved.delete(button.dataset.fav) : saved.add(button.dataset.fav);
    localStorage.setItem(FAVORITES_KEY, JSON.stringify([...saved]));
    renderNav(); render();
  });
}
async function start() {
  try {
    [papers, stats, narratives, taxonomy] = await Promise.all([
      fetch('data/papers.json').then(r => r.json()),
      fetch('data/stats.json').then(r => r.json()),
      fetch('data/category_analysis.json').then(r => r.json()).catch(() => ({})),
      fetch('data/taxonomy.json').then(r => r.json()),
    ]);
  } catch {
    document.querySelector('#content').innerHTML = '<section class="paper-section"><h2>样本数据未生成</h2><p>请先运行项目的数据构建流程。</p></section>';
    return;
  }
  document.querySelector('#search').addEventListener('input', event => { query = event.target.value; renderNav(); render(); });
  window.addEventListener('hashchange', () => { renderNav(); render(); });
  renderNav(); render();
}
start();
