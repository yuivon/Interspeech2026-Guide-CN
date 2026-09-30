const FIELDS = [
  ['研究动机', '🎯'], ['解决问题', '❓'], ['现象分析', '🔍'],
  ['主要方法', '🛠️'], ['数据与实验', '📊'], ['主要贡献', '⭐'],
];
const STRUCTURED_LABELS = {
  topics:'研究主题', tasks:'研究任务', method_families:'方法类别', specific_methods:'具体方法',
  models:'模型', representations:'表示方式', datasets:'数据集', metrics:'评价指标',
  languages:'语言', language_details:'语言细节', modalities:'模态', application_domains:'应用领域',
  contribution_types:'贡献类型',
};
const FAVORITES_KEY = 'interspeech2026_favorites_v1';
let papers = [], stats = {}, narratives = {}, taxonomy = {macros: [], session_to_macro: {}}, sessionLabels = {}, query = '';
let searchReturnHash = '#all', lastListHash = '#all', showEmpty = false, detailCache = new Map(), renderId = 0;

const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const favorites = () => {
  try { return new Set(JSON.parse(localStorage.getItem(FAVORITES_KEY) || '[]')); }
  catch { return new Set(); }
};
const isFavorite = id => favorites().has(id);
const sessions = () => taxonomy.session_to_macro || {};
const sampleTotal = () => stats.analysis_complete_count ?? papers.length;
const macroId = p => p.macro_id || sessions()[p.normalized_session] || '';
const scopeName = () => stats.data_scope === 'full' ? '全会议' : stats.data_scope === 'partial' ? '已分析论文' : '15篇开发样本';
const scopeStats = () => stats.data_scope === 'full' ? '会议统计' : '样本统计';
const sessionName = value => sessionLabels[value] || value;
const parseHash = () => {
  const raw = location.hash.slice(1) || 'all', slash = raw.indexOf('/');
  let type = slash < 0 ? raw : raw.slice(0, slash), value = slash < 0 ? '' : raw.slice(slash + 1);
  try { value = decodeURIComponent(value); } catch { /* 保留损坏的编码内容，让页面按空结果处理。 */ }
  return {type, value};
};
const setView = hash => { if (location.hash === hash) render(); else location.hash = hash; };
const navCount = rows => query.trim() ? rows.filter(p => searchText(p).includes(query.trim().toLocaleLowerCase())).length : rows.length;
function searchText(p) {
  return [p.search_text, p.paper_id, p.title, ...(Array.isArray(p.authors) ? p.authors : [p.authors]),
    p.macro, p.macro_name, p.normalized_session, p.session_zh, p.original_session, p.tldr_cn,
    ...FIELDS.map(([key]) => p.analysis?.[key] || ''), ...(p.topics || [])].filter(Boolean).join(' ').toLocaleLowerCase();
}
function navButton(label, hash, count, active, extra = '') {
  return `<button class="nav-item ${active ? 'active' : ''}" data-hash="${esc(hash)}" ${extra}>${label}<span>${count}</span></button>`;
}
function rowMacro(p) { return macroId(p); }
function renderNav() {
  const view = parseHash(), q = query.trim().toLocaleLowerCase();
  const selectedMacro = view.type === 'macro' ? view.value : view.type === 'session' ? sessions()[view.value] : '';
  const favoriteRows = papers.filter(p => favorites().has(p.paper_id));
  const matches = rows => navCount(rows);
  const tree = [...(taxonomy.macros || [])].sort((a, b) =>
    papers.filter(p => rowMacro(p) === b.id).length - papers.filter(p => rowMacro(p) === a.id).length || a.name.localeCompare(b.name))
    .map(macro => {
      const allChildren = (macro.sessions || Object.keys(sessions()).filter(name => sessions()[name] === macro.id)).slice();
      const children = allChildren.filter(name => showEmpty || !q || matches(papers.filter(p => p.normalized_session === name)) > 0)
        .filter(name => showEmpty || papers.some(p => p.normalized_session === name));
      const rows = papers.filter(p => rowMacro(p) === macro.id), count = matches(rows);
      if (!showEmpty && (!rows.length || (q && !count))) return '';
      const expanded = selectedMacro === macro.id;
      return `<div class="nav-group ${expanded ? 'expanded' : ''}">
        <div class="nav-primary-row"><button class="nav-expand" data-toggle="${esc(macro.id)}" aria-label="${expanded ? '收起' : '展开'}${esc(macro.name)}" aria-expanded="${expanded}">${expanded ? '▾' : '▸'}</button>
          <button class="nav-primary-link ${view.type === 'macro' && view.value === macro.id ? 'active' : ''}" data-hash="#macro/${encodeURIComponent(macro.id)}">${esc(macro.name)}<span>${count}</span></button></div>
        <div class="nav-sub-list">${children.map(name => {
          const childRows = papers.filter(p => p.normalized_session === name), childCount = matches(childRows);
           return navButton(esc(sessionName(name)), `#session/${encodeURIComponent(name)}`, childCount, view.type === 'session' && view.value === name);
        }).join('')}</div></div>`;
    }).join('');
  const datasetCount = papers.filter(p => p.special_tags?.dataset?.relevant).length;
  const evaluationCount = papers.filter(p => p.special_tags?.evaluation?.relevant).length;
  document.querySelector('#nav').innerHTML = `${navButton('🏠 全部论文', '#all', matches(papers), view.type === 'all')}
    ${navButton('🔎 搜索结果', `#search/${encodeURIComponent(query)}`, q ? matches(papers) : 0, view.type === 'search', q ? '' : 'hidden')}
     <div class="nav-section-label">📚 分类目录 · ${scopeStats()}</div>${tree || '<p class="nav-empty">当前搜索没有命中分类</p>'}
    <button class="nav-empty-toggle" data-empty-toggle>${showEmpty ? '隐藏无样本分类' : '显示全部会议分类'}</button>
    <div class="nav-section-label">📊 专项分析</div>
    ${navButton('📚 Dataset / Corpus', '#special/dataset', datasetCount, view.type === 'special' && view.value === 'dataset')}
    ${navButton('📐 Evaluation / Benchmark', '#special/evaluation', evaluationCount, view.type === 'special' && view.value === 'evaluation')}
    <div class="nav-section-label">个人</div>${navButton('⭐ 收藏', '#favorites', favoriteRows.length, view.type === 'favorites')}`;
}
function baseRows(view) {
  if (view.type === 'macro') return papers.filter(p => rowMacro(p) === view.value);
  if (view.type === 'session') return papers.filter(p => p.normalized_session === view.value);
  if (view.type === 'special') return papers.filter(p => p.special_tags?.[view.value]?.relevant);
  if (view.type === 'favorites') return papers.filter(p => favorites().has(p.paper_id));
  return papers;
}
function filterRows(rows) {
  const q = query.trim().toLocaleLowerCase();
  return q ? rows.filter(p => searchText(p).includes(q)) : rows;
}
function uniqueCount(rows, field) { return new Set(rows.flatMap(p => p.structured?.[field] || p[field] || []).filter(Boolean)).size; }
function shareLine(stat, rows) {
  const share = stat.sample_share_percentage ?? (sampleTotal() ? rows.length * 100 / sampleTotal() : 0);
  return `<p class="share-line">${stats.data_scope === 'full' ? '全会议占比' : '已分析论文中占比'} <b>${Number(share).toFixed(share % 1 ? 2 : 0)}%</b></p>`;
}
function kpis(stat, rows) {
  const tagCount = name => stat[`unique_${name}_count`] ?? uniqueCount(rows, name === 'task' ? 'tasks' : `${name}s`);
  return `<div class="sample-kpis">
    <div><strong>${stat.paper_count ?? rows.length}</strong><span>样本论文</span></div>
    <div><strong>${new Set(rows.map(p => p.normalized_session).filter(Boolean)).size}</strong><span>涉及 Session</span></div>
    <div><strong>${tagCount('task')}</strong><span>任务标签种类</span></div>
    <div><strong>${tagCount('dataset')}</strong><span>数据集种类</span></div>
    <div><strong>${tagCount('language')}</strong><span>语言种类</span></div>
  </div>`;
}
function ranked(title, items) {
  if (!items?.length) return '';
  return `<section class="rank-section"><h3>${title}</h3><div class="rank-list">${items.slice(0, 8).map(item => `<div><span>${esc(item.name)}</span><b>${esc(item.count)}</b></div>`).join('')}</div></section>`;
}
function expandableRanked(title, items) {
  return items?.length ? `<details class="rank-expand"><summary>${title}（展开）</summary>${ranked(title, items)}</details>` : '';
}
function distribution(title, items) { return ranked(title, items); }
function specialDetails(s, kind) {
  if (kind === 'dataset') return `<div class="badges"><span class="badge">新建数据集 ${s.new_dataset_count ?? 0}</span><span class="badge">扩充/整理 ${s.expanded_or_curated_count ?? 0}</span><span class="badge">Benchmark 数据集 ${s.benchmark_dataset_count ?? 0}</span><span class="badge">确认公开 ${s.public_dataset_count ?? 0}</span><span class="badge">多语言 ${s.multilingual_paper_count ?? 0}</span><span class="badge">低资源语言 ${s.low_resource_paper_count ?? 0}</span><span class="badge">濒危语言 ${s.endangered_language_paper_count ?? 0}</span></div>
     <div class="rank-grid">${ranked('数据集建设方式', s.collection_methods)}${ranked('标注方式', s.annotation_methods)}${distribution('数据集角色', s.dataset_role_distribution)}${distribution('任务分布', s.task_distribution)}${distribution('语言分布', s.language_distribution)}${distribution('模态分布', s.modality_distribution)}${distribution('可用性', s.availability_distribution)}</div>
    ${s.reported_scales?.length ? `<h3>论文明确报告的数据规模</h3><ul class="scale-list">${s.reported_scales.map(item => `<li>${esc(item.title)} · ${esc(item.dataset)}：${esc(item.scale_text)}</li>`).join('')}</ul>` : ''}`;
  return `<div class="badges"><span class="badge">Benchmark 论文 ${s.benchmark_paper_count ?? 0}</span><span class="badge">指标研究 ${s.metric_paper_count ?? 0}</span><span class="badge">评测协议 ${s.protocol_paper_count ?? 0}</span><span class="badge">自动评价 ${s.automatic_evaluation_count ?? 0}</span><span class="badge">人工评价 ${s.human_evaluation_count ?? 0}</span><span class="badge">混合评价 ${s.both_evaluation_count ?? 0}</span><span class="badge">客观 ${s.objective_evaluation_count ?? 0}</span><span class="badge">主观 ${s.subjective_evaluation_count ?? 0}</span><span class="badge">Foundation Model ${s.foundation_model_evaluation_count ?? 0}</span><span class="badge">Speech LLM ${s.speech_llm_evaluation_count ?? 0}</span><span class="badge">公平性 ${s.fairness_evaluation_count ?? 0}</span><span class="badge">安全性 ${s.safety_evaluation_count ?? 0}</span><span class="badge">泛化 ${s.generalization_evaluation_count ?? 0}</span><span class="badge">鲁棒性 ${s.robustness_evaluation_count ?? 0}</span><span class="badge">可靠性 ${s.reliability_evaluation_count ?? 0}</span></div>
     <div class="rank-grid">${ranked('评测方式', s.evaluation_mode_distribution)}${ranked('客观 / 主观', s.objective_subjective_distribution)}${ranked('评测属性', Object.entries(s.attribute_evaluation_counts || {}).map(([name, count]) => ({name, count})))}${ranked('相关评测对象', s.top_evaluation_entities)}${ranked('常用指标', s.top_metrics)}</div>`;
}
function overview(stat, title, rows, narrative, kind) {
  const view = parseHash(), isCategory = ['macro', 'session'].includes(view.type);
  return `<section class="stats-panel"><div class="section-kicker">📊 ${scopeStats()}</div><h2>${esc(isCategory ? `当前分类统计分析 · ${title}` : title)}</h2>
    ${shareLine(stat, rows)}${query.trim() ? '<p class="filtered-note">分类统计为该目录全样本汇总；论文列表按搜索词筛选。</p>' : ''}${kpis(stat, rows)}
    <div class="rank-grid">${ranked('🔥 研究主题', stat.top_topics)}${ranked('🎯 研究任务', stat.top_tasks)}${ranked('🧩 方法类别', stat.top_method_families)}${expandableRanked('🛠 具体方法', stat.top_specific_methods)}${ranked('📚 Dataset', stat.top_datasets)}${ranked('📐 Metrics', stat.top_metrics)}${ranked('🌍 Languages', stat.top_languages)}${ranked('🔊 Modalities', stat.top_modalities)}${ranked('🏥 应用领域', stat.top_domains)}${ranked('⭐ 贡献类型', stat.contribution_types)}</div>
    ${kind ? specialDetails(stat, kind) : ''}
    ${narrative ? `<div class="narrative"><h3>样本分析</h3><p>${esc(narrative.overview || '')}</p>${narrative.research_hotspots?.length ? `<div class="badges">${narrative.research_hotspots.map(x => `<span class="badge">${esc(x)}</span>`).join('')}</div>` : ''}${narrative.major_results?.length ? `<h3>主要发现</h3><ul>${narrative.major_results.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}<p>${esc(narrative.trend_summary || '')}</p></div>` : ''}
     <p class="sample-note">${stats.data_scope === 'full' ? '统计基于全部成功分析的正式论文。' : `仅统计当前${scopeName()}，不代表 Interspeech 2026 全部论文。`}</p></section>`;
}
function paperMore(p) {
  const fields = [
    ['官方原始 Session', p.original_session], ['归一化 Official Session', p.normalized_session],
    ['中文 Session', p.session_zh || sessionName(p.normalized_session)], ['一级研究方向', p.macro_name || p.macro],
    ['页码', p.pages], ['DOI', p.doi],
  ].filter(([, value]) => value);
  const metadata = fields.length ? `<dl class="detail-fields">${fields.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>` : '';
  return `${metadata}${detailStructured(p.structured) || '<p>没有可显示的结构化信息。</p>'}`;
}
function paperCard(p) {
  const a = p.analysis || {}, on = isFavorite(p.paper_id), authors = Array.isArray(p.authors) ? p.authors.join(', ') : p.authors;
  return `<article class="paper-card"><div class="paper-actions"><button class="favorite ${on ? 'on' : ''}" aria-label="${on ? '取消收藏' : '收藏'} ${esc(p.title)}" data-fav="${esc(p.paper_id)}">${on ? '★ 已收藏' : '☆ 收藏'}</button>
      <div class="paper-links">${p.pdf_url ? `<a href="${esc(p.pdf_url)}" target="_blank" rel="noopener noreferrer">PDF</a>` : ''}${p.isca_page_url ? `<a href="${esc(p.isca_page_url)}" target="_blank" rel="noopener noreferrer">ISCA</a>` : ''}${p.doi ? `<a href="${esc(p.doi.startsWith('http') ? p.doi : `https://doi.org/${p.doi}`)}" target="_blank" rel="noopener noreferrer">DOI</a>` : ''}</div></div>
    <h3 class="paper-title"><a href="#paper/${encodeURIComponent(p.paper_id)}" data-paper="${esc(p.paper_id)}">${esc(p.title)}</a></h3><div class="paper-meta">${esc(authors || '')}</div>
     <div class="badges">${p.macro || p.macro_name ? `<span class="badge">${esc(p.macro || p.macro_name)}</span>` : ''}${p.normalized_session ? `<span class="badge session-badge">${esc(p.session_zh || sessionName(p.normalized_session))}</span>` : ''}${(p.topics || []).slice(0, 4).map(item => `<span class="badge">${esc(item)}</span>`).join('')}</div>
    ${p.tldr_cn ? `<div class="tldr"><b>💡 一句话看懂</b><span>${esc(p.tldr_cn)}</span></div>` : ''}
     <div class="dimensions">${FIELDS.map(([key, icon]) => `<div class="dimension"><b>${icon} ${key}</b><p>${esc(a[key] || '')}</p></div>`).join('')}</div>
     <details class="paper-more" data-paper-more="${esc(p.paper_id)}"><summary>查看详细信息</summary><div class="paper-more-body">展开后按需读取结构化信息…</div></details>
     <details class="abstract" data-abstract="${esc(p.paper_id)}"><summary>查看完整摘要 Abstract</summary><p class="abstract-body">展开后按需读取摘要…</p></details></article>`;
}
function titleAndStats(view) {
  if (view.type === 'macro') {
    const m = taxonomy.macros.find(item => item.id === view.value);
    return {title:m?.name || view.value, stat:stats.macro_categories?.[view.value] || {}, narrative:narratives[`macro:${view.value}`]};
  }
  if (view.type === 'session') return {title:sessionName(view.value), stat:stats.categories?.[view.value] || {}, narrative:narratives[`category:${view.value}`]};
  if (view.type === 'special') return {title:view.value === 'dataset' ? 'Dataset / Corpus 专项' : 'Evaluation / Benchmark 专项', stat:stats.special?.[view.value] || {}, narrative:narratives[`special:${view.value}`], kind:view.value};
  return {title:'Interspeech 2026 样本总览', stat:stats.all || {}, narrative:narratives.all};
}
function homeOverview() {
  const s = stats.all || {}, topics = s.top_topics || [];
  const listed = stats.source_listed_count || 0, covered = sampleTotal();
  return `<section class="home-overview"><div class="section-kicker">📊 ${scopeStats()}</div><h2>${scopeName()}概览</h2>
    <div class="home-kpis"><div><b>${papers.length}</b><span>篇论文</span></div><div><b>${new Set(papers.map(p => p.normalized_session).filter(Boolean)).size}</b><span>个 Official Session</span></div><div><b>${new Set(papers.map(rowMacro).filter(Boolean)).size}</b><span>个一级方向</span></div><div><b>${stats.special?.dataset?.paper_count ?? 0}</b><span>篇 Dataset 专项</span></div><div><b>${stats.special?.evaluation?.paper_count ?? 0}</b><span>篇 Evaluation 专项</span></div></div>
     ${topics.length ? `<p><b>热门主题：</b>${topics.slice(0, 5).map(x => `${esc(x.name)}（${esc(x.count)}）`).join('、')}</p>` : ''}${Number.isFinite(s.coverage_percentage) ? `<p>${stats.data_scope === 'full' ? '正式论文覆盖' : '样本覆盖'}官方目录：<b>${covered} / ${listed}（${Number(s.coverage_percentage).toFixed(2)}%）</b></p>` : ''}<p class="sample-note">${stats.data_scope === 'full' ? '所有数字均由已分析的正式论文计算。' : '所有数字均为样本统计，不代表整届会议。'}</p></section>`;
}
function renderList(view, rows) {
  if (view.type === 'all') return `${homeOverview()}${paperList('论文', rows)}`;
  if (view.type === 'search') return `<section class="paper-section"><div class="section-kicker">全站搜索 · 样本统计</div><h2>搜索结果 <small>(${rows.length})</small></h2>${paperRows(rows)}</section>`;
  if (view.type === 'favorites') return paperList('已收藏论文', rows, false);
  const page = titleAndStats(view), first = overview(page.stat, page.title, baseRows(view), page.narrative, page.kind);
  const label = view.type === 'macro' ? '该一级方向全部论文' : view.type === 'session' ? '该 Official Session 论文' : '命中论文';
  return `${first}${paperList(label, rows)}`;
}
function paperRows(rows, emptyText = '当前样本中没有匹配论文。') {
  return rows.length ? `<div class="paper-list">${rows.map(paperCard).join('')}</div>` : `<div class="empty-state">${emptyText}</div>`;
}
function paperList(title, rows, showKicker = true) {
  const empty = parseHash().type === 'favorites' ? '点击论文卡片上的 ☆ 收藏，即可加入本机收藏。' : '当前样本中没有匹配论文。';
  return `<section class="paper-section">${showKicker ? `<div class="section-kicker">${scopeName()}</div>` : ''}<h2>${esc(title)} <small>(${rows.length})</small></h2>${paperRows(rows, empty)}</section>`;
}
function valueText(v) {
  if (v == null || v === '') return '';
  if (Array.isArray(v)) return v.map(valueText).filter(Boolean).join('；');
  if (typeof v === 'object') return Object.entries(v).map(([key, value]) => [key, valueText(value)])
    .filter(([key, value]) => !/evidence|quote|excerpt/i.test(key) && value).map(([key, value]) => `${key}: ${value}`).join('；');
  return String(v);
}
function detailStructured(structured) {
  const rows = Object.entries(structured || {}).map(([key, value]) => [STRUCTURED_LABELS[key] || key, valueText(value)]).filter(([, value]) => value);
  return rows.length ? `<section class="detail-block"><h2>结构化信息</h2><dl class="detail-fields">${rows.map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl></section>` : '';
}
function entitySection(title, items) {
  if (!Array.isArray(items) || !items.length) return '';
  return `<section class="detail-block"><h2>${esc(title)}</h2><ul class="entity-list">${items.map(item => `<li>${esc(valueText(item))}</li>`).join('')}</ul></section>`;
}
function detailPage(p) {
  const a = p.analysis || {}, on = isFavorite(p.paper_id), authors = Array.isArray(p.authors) ? p.authors.join(', ') : p.authors;
  const evidence = (p.evidence_locations || []).filter(x => x && (x.claim_type || x.field || x.page_or_section || x.source));
  const links = `${p.pdf_url ? `<a href="${esc(p.pdf_url)}" target="_blank" rel="noopener noreferrer">PDF</a>` : ''}${p.isca_page_url ? `<a href="${esc(p.isca_page_url)}" target="_blank" rel="noopener noreferrer">ISCA</a>` : ''}${p.doi ? `<a href="${esc(p.doi.startsWith('http') ? p.doi : `https://doi.org/${p.doi}`)}" target="_blank" rel="noopener noreferrer">DOI</a>` : ''}`;
  return `<button class="back-link" data-back>← 返回论文列表</button><article class="paper-detail">
    <div class="paper-actions"><button class="favorite ${on ? 'on' : ''}" data-fav="${esc(p.paper_id)}">${on ? '★ 已收藏' : '☆ 收藏'}</button><div class="paper-links">${links}</div></div>
     <h1>${esc(p.title)}</h1><p class="detail-authors">${esc(authors || '')}</p><div class="badges">${p.macro || p.macro_name ? `<span class="badge">${esc(p.macro || p.macro_name)}</span>` : ''}${p.session_zh ? `<span class="badge session-badge">${esc(p.session_zh)}</span>` : ''}${p.normalized_session ? `<span class="badge original-badge">归一化 Official Session：${esc(p.normalized_session)}</span>` : ''}${p.original_session ? `<span class="badge original-badge">原始 Official Session：${esc(p.original_session)}</span>` : ''}${p.pages ? `<span class="badge">页码 ${esc(p.pages)}</span>` : ''}</div>
     ${p.tldr_cn ? `<div class="tldr detail-tldr"><b>💡 一句话看懂</b><span>${esc(p.tldr_cn)}</span></div>` : ''}
    <section class="detail-block"><h2>六维中文导读</h2><div class="dimensions">${FIELDS.map(([key, icon]) => `<div class="dimension"><b>${icon} ${key}</b><p>${esc(a[key] || '')}</p></div>`).join('')}</div></section>
    ${detailStructured(p.structured)}${entitySection('Dataset 实体', p.dataset_entities)}${entitySection('Evaluation 实体', p.evaluation_entities)}
    <section class="detail-block"><h2>Official Abstract</h2><p class="detail-abstract">${esc(p.abstract || '')}</p></section>
    <section class="detail-provenance"><b>分析来源：</b>${p.analysis_source === 'full_text' ? '全文 + Abstract' : 'Official Abstract'}${evidence.length ? `<details><summary>证据位置（${evidence.length}）</summary><ul>${evidence.map(x => `<li>${esc([x.claim_type || x.field, x.page_or_section, x.source].filter(Boolean).join(' · '))}</li>`).join('')}</ul></details>` : ''}</section>
  </article>`;
}
async function loadDetail(id) {
  if (detailCache.has(id)) return detailCache.get(id);
  const response = await fetch(`data/paper-details/${encodeURIComponent(id)}.json`);
  if (!response.ok) throw new Error(`详情加载失败：${response.status}`);
  const detail = await response.json();
  if (detail.paper_id !== id) throw new Error('论文详情 ID 与请求不一致');
  detailCache.set(id, detail); return detail;
}
async function renderPaper(id, idAtStart) {
  const paper = papers.find(p => p.paper_id === id), content = document.querySelector('#content');
  if (!paper) { content.innerHTML = '<div class="empty-state">找不到这篇论文。</div>'; return; }
  content.innerHTML = '<div class="detail-loading">正在读取论文详情…</div>';
  try {
    const detail = await loadDetail(id);
    if (idAtStart !== renderId || parseHash().type !== 'paper') return;
    content.innerHTML = detailPage(detail);
  } catch (error) {
    if (idAtStart !== renderId) return;
    content.innerHTML = `<div class="empty-state">${esc(error.message)}。请稍后重试。</div>`;
  }
}
function render() {
  const id = ++renderId, view = parseHash(), content = document.querySelector('#content');
  if (view.type === 'search') query = view.value;
  const searchInput = document.querySelector('#search');
  if (searchInput && searchInput.value !== query) searchInput.value = query;
  if (view.type === 'paper') { renderPaper(view.value, id); return; }
  const rows = filterRows(baseRows(view));
  if (view.type === 'all' || view.type === 'macro' || view.type === 'session' || view.type === 'special' || view.type === 'favorites' || view.type === 'search') content.innerHTML = renderList(view, rows);
  else content.innerHTML = '<div class="empty-state">页面不存在。</div>';
}
function setDrawer(open) {
  document.body.classList.toggle('drawer-open', open);
  document.querySelector('.sidebar')?.setAttribute('aria-hidden', String(!open && window.innerWidth <= 900));
  document.querySelector('[data-menu]')?.setAttribute('aria-expanded', String(open));
  if (open) document.querySelector('#search')?.focus();
  else if (window.innerWidth <= 900) document.querySelector('[data-menu]')?.focus();
}
function bindEvents() {
  const search = document.querySelector('#search');
  search.addEventListener('input', event => {
    query = event.target.value;
    const view = parseHash();
    if (query.trim()) {
      if (view.type !== 'search') searchReturnHash = location.hash || '#all';
      const hash = `#search/${encodeURIComponent(query)}`;
      if (view.type === 'search') history.replaceState(null, '', hash); else location.hash = hash;
    } else if (view.type === 'search') {
      location.hash = searchReturnHash || '#all';
    }
    renderNav(); render();
  });
  document.querySelector('#nav').addEventListener('click', event => {
    const toggle = event.target.closest('[data-toggle]');
    if (toggle) {
      const group = toggle.closest('.nav-group'); group.classList.toggle('expanded');
      toggle.setAttribute('aria-expanded', String(group.classList.contains('expanded'))); toggle.textContent = group.classList.contains('expanded') ? '▾' : '▸'; return;
    }
    if (event.target.closest('[data-empty-toggle]')) { showEmpty = !showEmpty; renderNav(); return; }
    const button = event.target.closest('[data-hash]');
    if (button) { setView(button.dataset.hash); setDrawer(false); }
  });
  document.querySelector('#content').addEventListener('click', event => {
    const back = event.target.closest('[data-back]');
    if (back) { setView(lastListHash || '#all'); return; }
    const paperLink = event.target.closest('[data-paper]');
    if (paperLink) lastListHash = location.hash || '#all';
    const favorite = event.target.closest('[data-fav]');
    if (favorite) {
      const saved = favorites(); saved.has(favorite.dataset.fav) ? saved.delete(favorite.dataset.fav) : saved.add(favorite.dataset.fav);
      localStorage.setItem(FAVORITES_KEY, JSON.stringify([...saved])); renderNav(); render();
    }
  });
  document.querySelector('#content').addEventListener('toggle', async event => {
    const item = event.target.closest('details[data-abstract], details[data-paper-more]');
    if (!item || !item.open || item.dataset.loaded) return;
    item.dataset.loaded = 'true';
    const abstract = item.matches('[data-abstract]'), body = item.querySelector(abstract ? '.abstract-body' : '.paper-more-body');
    try {
      const detail = await loadDetail(abstract ? item.dataset.abstract : item.dataset.paperMore);
      if (abstract) body.textContent = detail.abstract || '官方摘要未提供。';
      else body.innerHTML = paperMore(detail);
    }
    catch (error) { body.textContent = error.message; item.dataset.loaded = ''; }
  }, true);
  document.querySelector('[data-menu]').addEventListener('click', () => setDrawer(true));
  document.querySelector('[data-drawer-close]').addEventListener('click', () => setDrawer(false));
  document.querySelector('[data-backdrop]').addEventListener('click', () => setDrawer(false));
  window.addEventListener('keydown', event => { if (event.key === 'Escape') setDrawer(false); });
  window.addEventListener('resize', () => document.querySelector('.sidebar')?.setAttribute('aria-hidden', String(window.innerWidth <= 900 && !document.body.classList.contains('drawer-open'))));
  window.addEventListener('hashchange', () => {
    const view = parseHash();
    if (view.type === 'search') query = view.value;
    else if (view.type !== 'paper') { searchReturnHash = location.hash || '#all'; query = ''; }
    renderNav(); render();
  });
}
async function start() {
  try {
    [papers, stats, narratives, taxonomy, sessionLabels] = await Promise.all([
      fetch('data/papers-index.json').then(r => { if (!r.ok) throw new Error(`论文索引加载失败：${r.status}`); return r.json(); }),
      fetch('data/stats.json').then(r => { if (!r.ok) throw new Error(`统计数据加载失败：${r.status}`); return r.json(); }),
      fetch('data/category_analysis.json').then(r => r.ok ? r.json() : {}),
      fetch('data/taxonomy.json').then(r => { if (!r.ok) throw new Error(`分类目录加载失败：${r.status}`); return r.json(); }),
      fetch('data/session_labels_zh.json').then(r => { if (!r.ok) throw new Error(`session 中文目录加载失败：${r.status}`); return r.json(); }),
    ]);
    const full = stats.data_scope === 'full';
    document.querySelector('.main-header h1').textContent = full ? 'Interspeech 2026 全部论文 · 中文导读' : 'Interspeech 2026 · 中文论文导读';
    document.querySelector('.sample-pill').hidden = full;
    document.querySelector('.side-sample').hidden = full;
    document.querySelector('footer').textContent = full ? '论文来源：ISCA Archive。论文分析覆盖范围以页面统计为准。' : '15篇开发样本的分析与计数用于网站验收，不代表整届会议统计。论文来源：ISCA Archive。';
    document.title = full ? 'Interspeech 2026 全部论文 · 中文导读' : 'Interspeech 2026 · 15篇开发样本中文导读';
    const view = parseHash();
    if (view.type === 'search') { query = view.value; searchReturnHash = '#all'; }
    if (view.type === 'paper') lastListHash = '#all';
    bindEvents();
    document.querySelector('.sidebar').setAttribute('aria-hidden', String(window.innerWidth <= 900));
    renderNav(); render();
  } catch (error) {
    document.querySelector('#content').innerHTML = `<section class="paper-section"><h2>样本数据加载失败</h2><p>${esc(error.message)}</p></section>`;
  }
}
start();
