'use strict';
const $ = selector => document.querySelector(selector);
const state = {project: null, card: 0, dirty: false, view: 'issues', searchTimer: null, toastTimer: null, plan: null, settings: null, assets: {}, library: [], libraryOpen: new Set(), uploads: 0};
const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const formatDate = value => value ? new Date(value).toLocaleString('ko-KR') : '아직 수집하지 않음';

function toast(message, error = false) {
  clearTimeout(state.toastTimer);
  if ($('#plan-dialog').open) {
    $('#toast').classList.add('hidden');
    const status = $('#plan-status');
    status.textContent = message;
    status.classList.toggle('error', error);
    status.classList.remove('hidden');
    return;
  }
  $('#toast').textContent = message;
  $('#toast').classList.toggle('error', error);
  $('#toast').classList.remove('hidden');
  state.toastTimer = setTimeout(() => $('#toast').classList.add('hidden'), error ? 8000 : 4500);
}
async function api(path, options = {}) {
  let response;
  try {response = await fetch(path, {signal: AbortSignal.timeout((path==='/api/collect'||path.includes('analyze-original'))?120000:30000), headers: {'Content-Type': 'application/json'}, ...options});}
  catch(error) {throw new Error(error.name==='TimeoutError'?'응답이 지연되고 있습니다. 잠시 후 다시 확인하세요.':'서버에 연결할 수 없습니다. 실행 창이 열려 있는지 확인하세요.');}
  if (!response.ok) {
    let data;
    try { data = await response.json(); } catch { throw new Error(`요청 실패 (HTTP ${response.status})`); }
    let detail = data.detail;
    if (Array.isArray(detail)) detail = detail.map(item => `${item.loc.slice(1).join('.')}: ${item.msg}`).join('\n');
    throw new Error(typeof detail === 'string' ? detail : `요청 실패 (HTTP ${response.status})`);
  }
  return response.status === 204 ? null : response.json();
}
async function busy(button, work) {
  button.disabled = true;
  try { await work(); } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
}
function empty(title, description, action = '') {
  return `<div class="empty"><div class="empty-icon">◈</div><h3>${escapeHTML(title)}</h3><p>${escapeHTML(description)}</p>${action}</div>`;
}
async function showView(view) {
  if (state.dirty && !confirm('저장하지 않은 변경 사항이 있습니다. 이동할까요?')) return;
  state.dirty = false;
  state.view = view;
  document.querySelectorAll('.view').forEach(el => el.classList.add('hidden'));
  $(`#${view}-view`).classList.remove('hidden');
  document.querySelectorAll('.nav').forEach(el => el.classList.toggle('active', el.dataset.view === (view === 'editor' ? 'projects' : view)));
  $('#breadcrumb').textContent = {issues:'이슈 탐색',feeds:'RSS 관리',projects:'제작 보관함',editor:'카드뉴스 편집',settings:'환경설정',assets:'자산 보관함'}[view];
  try {
    if (view === 'issues') await loadIssues();
    if (view === 'feeds') await loadFeeds();
    if (view === 'projects') await loadProjects();
    if (view === 'settings') {await loadSettings(); const ai=await api('/api/ai/settings');$('#ai-enabled').checked=ai.enabled;$('#research-enabled').checked=ai.research_enabled;$('#ai-config-status').textContent=ai.configured?'API 키 등록됨':'API 키 미등록';}
    if (view === 'assets') await loadAssets();
  } catch (error) { toast(error.message, true); }
}
async function loadIssues() {
  const [issues, feeds] = await Promise.all([api(`/api/issues?q=${encodeURIComponent($('#search').value)}&days=${$('#days').value}`), api('/api/feeds')]);
  $('#article-count').innerHTML = `${issues.reduce((sum, issue) => sum + issue.articles.length, 0)} <small>ARTICLES</small>`;
  $('#issue-count').innerHTML = `${issues.length} <small>ISSUES</small>`;
  $('#feed-count').innerHTML = `${feeds.length} <small>SOURCES</small>`;
  $('#issue-total').textContent = issues.length;
  const checked=feeds.filter(feed=>feed.last_checked);
  const failed=checked.filter(feed=>feed.last_error);
  $('#collection-status').textContent=checked.length?`RSS 확인 ${checked.length}/${feeds.length}개 · 실패 ${failed.length}개. ${failed.length?'RSS 관리에서 언론사별 오류를 확인하세요.':''}`:(feeds.length?'첫 RSS 수집을 진행 중입니다. 잠시 후 자동 갱신됩니다.':'RSS 관리에서 기본 언론사를 추가하세요.');
  if (!issues.length) {
    $('#issue-list').innerHTML = empty(feeds.length ? '아직 발견한 이슈가 없습니다' : '첫 번째 뉴스 소스를 연결하세요', feeds.length ? '지금 수집하기를 누르거나 검색 조건을 바꿔 보세요. 수집 실패는 RSS 관리에서 확인할 수 있습니다.' : '언론사의 공개 RSS를 등록하면 새 기사를 모아 카드뉴스 초안을 만들 수 있습니다.', '<button class="primary" id="empty-feeds">RSS 관리로 이동 →</button>');
    $('#empty-feeds').onclick = () => showView('feeds');
    return;
  }
  $('#issue-list').innerHTML = issues.map((issue, index) => `<article class="issue-card"><div class="card-meta"><span class="badge ${issue.needs_review ? 'warn' : ''}">${issue.needs_review ? '정보 부족 · 원문 확인 필요' : (issue.publisher_count>1?'여러 기사 문장 요약':'RSS 문장 요약')}</span><span>${issue.publisher_count}개 언론사</span></div><h3><button class="issue-title" data-issue-open="${index}">${escapeHTML(issue.title)}</button></h3><p class="issue-summary">${escapeHTML(issue.summary || 'RSS에 본문 정보가 없어 신뢰할 수 있는 요약을 만들 수 없습니다. 원문을 확인하고 카드 내용을 작성하세요.')}</p><a class="original-link" href="${escapeHTML(issue.articles[0].url)}" target="_blank" rel="noopener">원문 확인 ↗</a><div class="card-bottom"><span>기사 ${issue.articles.length}개 · 점수 ${issue.score}</span><button class="text-button" data-draft="${index}">새 구성</button><button class="text-button" data-issue-open="${index}">이슈 열기 ↗</button></div></article>`).join('');
  document.querySelectorAll('[data-issue-open]').forEach(button => button.onclick = () => busy(button,async()=>{
    const issue=issues[Number(button.dataset.issueOpen)];
    const project=await api('/api/issues/open',{method:'POST',body:JSON.stringify({article_ids:issue.articles.slice(0,20).map(article=>article.id)})});
    await openEditor(project);
  }));
  document.querySelectorAll('[data-draft]').forEach(button => button.onclick = () => busy(button, async () => {
    const issue = issues[Number(button.dataset.draft)];
    await openPlan(issue);
  }));
}
async function loadFeeds() {
  const feeds = await api('/api/feeds');
  $('#feed-list').innerHTML = feeds.length ? feeds.map(feed => `<div class="feed-row"><div><h3>${escapeHTML(feed.name)}</h3><p>${escapeHTML(feed.url)}</p><p class="last-check">최근 확인: ${escapeHTML(formatDate(feed.last_checked))}</p>${feed.last_error ? `<p class="error">${escapeHTML(feed.last_error)}</p>` : ''}</div><button class="text-button" data-delete-feed="${feed.id}">삭제</button></div>`).join('') : empty('등록된 RSS가 없습니다', '위 양식에 언론사 이름과 공식 RSS 주소를 입력해 주세요.');
  document.querySelectorAll('[data-delete-feed]').forEach(button => button.onclick = () => busy(button, async () => {
    if (!confirm('RSS 등록을 삭제할까요? 이미 수집한 기사와 제작물은 유지됩니다.')) return;
    await api(`/api/feeds/${button.dataset.deleteFeed}`, {method:'DELETE'});
    await loadFeeds();
    toast('RSS를 삭제했습니다.');
  }));
}
async function loadProjects() {
  const projects = await api('/api/projects');
  $('#project-list').innerHTML = projects.length ? projects.map(project => `<article class="issue-card"><div class="card-meta"><span class="badge ${project.reviewed ? '' : 'warn'}">${project.reviewed ? '검토 완료' : '작성 중'}</span><span>${project.card_count}장의 카드</span></div><h3>${escapeHTML(project.title)}</h3><p>최근 저장: ${escapeHTML(formatDate(project.updated_at))}</p><div class="card-bottom"><button class="text-button" data-delete-project="${project.id}">삭제</button><button class="text-button" data-open="${project.id}">편집하기 ↗</button></div></article>`).join('') : empty('아직 제작한 카드뉴스가 없습니다', '이슈 탐색에서 이야기를 선택하고 초안을 만들어 보세요.');
  document.querySelectorAll('[data-open]').forEach(button => button.onclick = () => busy(button, async () => openEditor(await api(`/api/projects/${button.dataset.open}`))));
  document.querySelectorAll('[data-delete-project]').forEach(button => button.onclick = () => busy(button, async () => {
    if (!confirm('이 카드뉴스 프로젝트를 삭제할까요?')) return;
    await api(`/api/projects/${button.dataset.deleteProject}`, {method:'DELETE'});
    await loadProjects();
  }));
}
async function openEditor(project) {
  await showView('editor');
  state.project = project;
  state.card = 0;
  state.dirty = false;
  $('#project-title').value = project.title;
  $('#caption').value = project.caption;
  $('#accent').value = project.accent;
  $('#reviewed').checked = project.reviewed;
  $('#draft-warning').textContent = project.warning;
  $('#editor-count').textContent = project.cards.length;
  await loadAssetNames(project.design);
  renderDesigner('editor', project.design);
  unlockResearch();
  renderResearchEvidence(project);
  monitorResearch(project.id);
  monitorBackgrounds(project.id);
  $('#source-links').innerHTML = '<p class="muted">원문 확인</p>' + project.sources.map(source => `<a href="${escapeHTML(source.url)}" target="_blank" rel="noopener noreferrer">↗ ${escapeHTML(source.publisher)} · ${escapeHTML(source.title)}</a>`).join('');
  showCard();
}
function applyCard() {
  if (!state.project) return;
  const card = state.project.cards[state.card];
  card.heading = $('#card-heading').value;
  card.body = $('#card-body').value;
  card.font_style=$('#card-font').value;
  card.font_size=$('#card-font-size').value ? Number($('#card-font-size').value) : null;
  card.text_align=$('#card-align').value || null;
  card.text_top=$('#card-top').value ? Number($('#card-top').value) : null;
  card.line_height=$('#card-line-height').value ? Number($('#card-line-height').value) : null;
  card.layout = $('#card-layout').value || null;
  const imageId=$('#card-image').value || null;
  if(card.image_id!==imageId) card.ai_background=false;
  card.image_id=imageId;
  card.source_ids = [...document.querySelectorAll('[data-source]:checked')].map(input => Number(input.dataset.source));
}
function markDirty(unreview = true) {
  state.dirty = true;
  if (unreview) $('#reviewed').checked = false;
  $('#preview-state').textContent = '저장하지 않은 변경 사항이 있습니다. 변경 저장을 누르면 미리보기가 갱신됩니다.';
}
function showCard() {
  const card = state.project.cards[state.card];
  $('#card-tabs').innerHTML = state.project.cards.map((_, index) => `<button class="${index === state.card ? 'active' : ''}" data-card="${index}" aria-label="${index+1}번 카드">${String(index+1).padStart(2,'0')}</button>`).join('');
  document.querySelectorAll('[data-card]').forEach(button => button.onclick = () => {applyCard(); state.card = Number(button.dataset.card); showCard();});
  $('#card-label').textContent = `${String(state.card + 1).padStart(2,'0')} / ${roleLabels[card.role] || '본문'}`;
  $('#card-heading').value = card.heading;
  $('#card-body').value = card.body;
  $('#card-font').value=card.font_style || 'bold';
  $('#card-font-size').value=card.font_size ?? '';
  $('#card-align').value=card.text_align || '';
  $('#card-top').value=card.text_top ?? '';
  $('#card-line-height').value=card.line_height ?? '';
  $('#card-layout').value = card.layout || '';
  $('#card-image').innerHTML = '<option value="">이미지 없음</option>' + state.project.design.image_ids.map(id => `<option value="${id}">${escapeHTML(state.assets[id]?.name || '첨부 이미지')}</option>`).join('');
  $('#card-image').value = card.image_id || '';
  $('#card-sources').innerHTML = state.project.sources.map(source => `<label><input type="checkbox" data-source="${source.article_id}" ${card.source_ids.includes(source.article_id) ? 'checked' : ''}>${escapeHTML(source.publisher)} · ${escapeHTML(source.title)}</label>`).join('');
  document.querySelectorAll('[data-source]').forEach(input => input.onchange = () => markDirty());
  refreshPreview();
}
$('#preview').onerror=()=>{$('#preview-state').textContent='이미지를 만들지 못했습니다. 문구 길이와 첨부 파일을 확인하고 다시 저장하세요.';};
function refreshPreview() {
  $('#preview').src = `/api/projects/${state.project.id}/cards/${state.card}.png?v=${Date.now()}`;
}
async function saveProject() {
  if (state.uploads) throw new Error('이미지 첨부가 끝난 뒤 저장해 주세요.');
  applyCard();
  state.project.design.accent = $('#accent').value;
  const payload = {...state.project, design: {...state.project.design, references: normalizedReferences(state.project.design.references)}, title:$('#project-title').value, caption:$('#caption').value, accent:$('#accent').value, reviewed:$('#reviewed').checked};
  state.project = await api(`/api/projects/${state.project.id}`, {method:'PUT', body:JSON.stringify(payload)});
  state.dirty = false;
  renderDesigner('editor', state.project.design);
  $('#preview-state').textContent = '미리보기는 저장된 문구를 표시합니다.';
  refreshPreview();
}
document.querySelectorAll('.nav').forEach(button => button.onclick = () => showView(button.dataset.view));
$('#back').onclick = () => showView('projects');
$('#search').oninput = () => {clearTimeout(state.searchTimer); state.searchTimer = setTimeout(() => loadIssues().catch(error => toast(error.message,true)), 250);};
$('#days').onchange = () => loadIssues().catch(error => toast(error.message,true));
$('#feed-form').onsubmit = event => {
  event.preventDefault();
  const button = event.target.querySelector('button');
  busy(button, async () => {
    const fields = new FormData(event.target);
    await api('/api/feeds', {method:'POST', body:JSON.stringify({name:fields.get('name'), url:fields.get('url')})});
    event.target.reset();
    await loadFeeds();
    toast('RSS를 등록했습니다. 이슈 탐색에서 지금 수집하기를 누르세요.');
  });
};
$('#restore-feeds').onclick=()=>busy($('#restore-feeds'),async()=>{await api('/api/feeds/presets',{method:'POST'});await loadFeeds();toast('기본 언론사 RSS를 추가했습니다. 지금 수집하기로 결과를 확인하세요.');});
$('#breaking-default').onclick=()=>busy($('#breaking-default'),async()=>{state.settings=await api('/api/settings/breaking-template');await loadAssetNames(state.settings.design);renderDesigner('settings',state.settings.design);$('#settings-count').value=state.settings.outline.length;renderOutline('settings');toast('기본 속보 디자인을 불러왔습니다. 기본 설정 저장을 눌러 적용하세요.');});
$('#collect').onclick = () => busy($('#collect'), async () => {
  const result = await api('/api/collect', {method:'POST'});
  if (result.busy) {toast('이미 수집 중입니다. 잠시 후 다시 확인하세요.'); return;}
  await loadIssues();
  if (!result.results.length) {toast('먼저 RSS 관리에서 피드를 등록해 주세요.'); return;}
  const errors = result.results.filter(item => item.error);
  const total = result.results.reduce((sum, item) => sum + item.added, 0);
  toast(`${total}개의 새 기사를 수집했습니다.${errors.length ? `\n${errors.map(item => item.name + ': ' + item.error).join('\n')}` : ''}`, errors.length > 0);
});
['#card-heading','#card-body','#project-title','#caption','#accent','#card-layout','#card-image'].forEach(selector => $(selector).oninput = () => markDirty());
$('#reviewed').onchange = () => markDirty(false);
$('#ai-save').onclick=()=>busy($('#ai-save'),async()=>{
  const result=await api('/api/ai/settings',{method:'PUT',body:JSON.stringify({key:$('#ai-key').value,enabled:$('#ai-enabled').checked,research_enabled:$('#research-enabled').checked})});
  $('#ai-key').value='';$('#ai-config-status').textContent=result.configured?'API 키 등록됨':'API 키 미등록';toast('AI 검색과 이미지 설정을 저장했습니다.');
});
let researchTimer;
function unlockResearch() {
  $('.editor-panel').inert=false;$('#save').disabled=false;$('#export').disabled=false;
}
function renderResearchEvidence(project) {
  const evidence=project.research_evidence||[];
  $('#research-details').classList.toggle('hidden',!evidence.length);
  $('#research-evidence').innerHTML=evidence.map(item=>`<p><strong>${item.card_index+1}번째 카드</strong> · ${escapeHTML(item.quote)}<br><a href="${escapeHTML(item.url)}" target="_blank" rel="noopener">확인 기사 ↗</a></p>`).join('');
}
async function monitorResearch(id) {
  clearTimeout(researchTimer);
  if(state.project?.id!==id || state.view!=='editor') return;
  try {
    const job=await api(`/api/projects/${id}/research`);
    if(state.project?.id!==id || state.view!=='editor') return;
    $('#research-progress').textContent=job.status==='running'?'AI가 기사를 재검색하고 본문 근거를 확인하는 중입니다.':(job.error||'');
    if(job.status==='running') {
      $('.editor-panel').inert=true;$('#save').disabled=true;$('#export').disabled=true;
      researchTimer=setTimeout(()=>monitorResearch(id),3000);
    } else if(job.status==='done' && state.project.generation_mode!=='web-researched') {
      const selected=state.card;
      const latest=await api(`/api/projects/${id}`);
      if(state.project?.id!==id || state.view!=='editor') return;
      state.project=latest;state.dirty=false;state.card=selected;
      $('#caption').value=latest.caption;$('#reviewed').checked=false;$('#draft-warning').textContent=latest.warning;
      $('#source-links').innerHTML='<p class="muted">원문 확인</p>'+latest.sources.map(source=>`<a href="${escapeHTML(source.url)}" target="_blank" rel="noopener">↗ ${escapeHTML(source.publisher)} · ${escapeHTML(source.title)}</a>`).join('');
      renderResearchEvidence(latest);showCard();unlockResearch();
      $('#research-progress').textContent=`기사 ${job.source_count}개에 근거해 본문을 작성했습니다.`;
      monitorBackgrounds(id);
    } else {unlockResearch();if(job.status==='done') $('#research-progress').textContent=`기사 ${job.source_count}개에 근거해 본문을 작성했습니다.`;}
  } catch(error) {unlockResearch();$('#research-progress').textContent=error.message;}
}
$('#research-news').onclick=()=>busy($('#research-news'),async()=>{
  await saveProject();await api(`/api/projects/${state.project.id}/research`,{method:'POST'});
  state.project.generation_mode='research-pending';monitorResearch(state.project.id);
});
let backgroundTimer;
async function monitorBackgrounds(id) {
  clearTimeout(backgroundTimer);
  if(state.project?.id!==id || state.view!=='editor') return;
  try {
    const job=await api(`/api/projects/${id}/ai-backgrounds`);
    $('#ai-progress').textContent=job.status==='running'?`AI 배경 생성 중 · ${job.completed}장 완료`:(job.error||'');
    if(job.status==='running') backgroundTimer=setTimeout(()=>monitorBackgrounds(id),3000);
    else if(job.status==='done') {
      const latest=await api(`/api/projects/${id}`);
      applyCard();
      state.project.design.image_ids=[...new Set([...state.project.design.image_ids,...latest.design.image_ids])];
      state.project.cards.forEach((card,index)=>{card.image_id=latest.cards[index].image_id;card.ai_background=latest.cards[index].ai_background;});
      await loadAssetNames(state.project.design);renderDesigner('editor',state.project.design);
      $('#reviewed').checked=false;$('#card-image').innerHTML='<option value="">이미지 없음</option>'+state.project.design.image_ids.map(asset=>`<option value="${asset}">${escapeHTML(state.assets[asset]?.name||'배경')}</option>`).join('');
      $('#card-image').value=state.project.cards[state.card].image_id||'';refreshPreview();
      $('#ai-progress').textContent=`AI 배경 ${job.completed}장 생성 완료`;
    }
  } catch(error) {$('#ai-progress').textContent=error.message;}
}
$('#ai-backgrounds').onclick=()=>busy($('#ai-backgrounds'),async()=>{
  await saveProject();await api(`/api/projects/${state.project.id}/ai-backgrounds`,{method:'POST'});monitorBackgrounds(state.project.id);
});
$('#impact-card').onclick=()=>busy($('#impact-card'),async()=>{
  await saveProject();
  const project=await api(`/api/projects/${state.project.id}/impact-card`,{method:'POST'});
  await openEditor(project);state.card=2;showCard();
  toast(project.cards[2].body?'세 번째 카드에 영향과 전망 문장을 적용했습니다.':'RSS에 전망 정보가 없어 문구를 비워 두었습니다. 원문을 확인해 작성하세요.');
});
$('#analyze-original').onclick=()=>busy($('#analyze-original'),async()=>{
  await saveProject();
  const selected=state.card;
  const project=await api(`/api/projects/${state.project.id}/analyze-original`,{method:'POST'});
  await openEditor(project);state.card=selected;showCard();
  toast('기사 본문에서 경기 결과와 주요 활약 내용을 적용했습니다.');
});
$('#format-body').onclick=()=>busy($('#format-body'),async()=>{
  await saveProject();
  const project=await api(`/api/projects/${state.project.id}/format-body`,{method:'POST'});
  const selected=state.card;
  await openEditor(project);
  state.card=selected;showCard();
  toast('본문 카드의 기자명과 문단을 정리했습니다.');
});
$('#summarize-cover').onclick=()=>busy($('#summarize-cover'),async()=>{
  await saveProject();
  const project=await api(`/api/projects/${state.project.id}/summarize-cover`,{method:'POST'});
  await openEditor(project);
  toast('RSS 제목에서 짧은 표지 문구를 만들었습니다. 원래 제목은 출처와 캡션에 유지됩니다.');
});
$('#save').onclick = () => busy($('#save'), async () => {await saveProject(); toast('카드뉴스를 저장했습니다.');});
$('#export').onclick = () => busy($('#export'), async () => {
  await saveProject();
  const response = await fetch(`/api/projects/${state.project.id}/export.zip`);
  if (!response.ok) {const data = await response.json(); throw new Error(data.detail);}
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = `medialog-${state.project.id}.zip`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast(`${state.project.cards.length}장 이미지와 캡션, 출처를 다운로드했습니다.`);
});
$('#accent').oninput = () => {state.project.design.accent = $('#accent').value; const color = $('#editor-design input[type=color]'); if (color) color.value = $('#accent').value; markDirty();};

window.addEventListener('beforeunload', event => {if (state.dirty) {event.preventDefault(); event.returnValue = '';}});
showView('issues');
setInterval(()=>{if(state.view==='issues' && !$('#plan-dialog').open) loadIssues().catch(()=>{});},10000);


const roleLabels = {cover:'표지',facts:'핵심 사실',background:'배경',impact:'영향 · 전망',sources:'출처',custom:'직접 작성'};
const layouts = {classic:'텍스트 중심',editorial:'에디토리얼',image:'이미지 + 텍스트',poster:'중앙 포스터',breaking:'미디어로그 속보'};
async function openPlan(issue) {
  const defaults = await api('/api/settings');
  state.plan = {...defaults, article_ids:issue.articles.slice(0,20).map(article => article.id)};
  await loadAssetNames(state.plan.design);
  $('#plan-issue').textContent = issue.title;
  $('#plan-count').innerHTML = Array.from({length:8}, (_,i) => `<option value="${i+3}">${i+3}장</option>`).join('');
  $('#plan-count').value = String(state.plan.outline.length);
  renderDesigner('plan',state.plan.design);
  renderOutline();
  $('#plan-status').classList.add('hidden');
  $('#plan-dialog').showModal();
}
function renderOutline(context='plan') {
  const model=state[context];
  const root=$(`#${context}-outline`);
  root.innerHTML = model.outline.map((row,index) => `<div class="outline-row"><span>${String(index+1).padStart(2,'0')}</span><select aria-label="${index+1}번 카드 역할" data-role="${index}">${Object.entries(roleLabels).map(([role,label]) => `<option value="${role}" ${row.role===role?'selected':''}>${label}</option>`).join('')}</select><input aria-label="${index+1}번 카드 제목" data-heading="${index}" value="${escapeHTML(row.heading)}" maxlength="100" required><button type="button" data-move="${index}" data-direction="-1" ${index===0?'disabled':''} aria-label="${index+1}번 카드 위로">↑</button><button type="button" data-move="${index}" data-direction="1" ${index===model.outline.length-1?'disabled':''} aria-label="${index+1}번 카드 아래로">↓</button></div>`).join('');
  root.querySelectorAll('[data-role]').forEach(input => input.onchange = () => {const row=model.outline[Number(input.dataset.role)]; row.role=input.value; row.heading=roleLabels[input.value]; if(context==='settings') settingsDirty(); renderOutline(context);});
  root.querySelectorAll('[data-heading]').forEach(input => input.oninput = () => {model.outline[Number(input.dataset.heading)].heading=input.value; if(context==='settings') settingsDirty();});
  root.querySelectorAll('[data-move]').forEach(button => button.onclick = () => {const index=Number(button.dataset.move); const next=index+Number(button.dataset.direction); [model.outline[index],model.outline[next]]=[model.outline[next],model.outline[index]]; if(context==='settings') settingsDirty(); renderOutline(context);});
}
function designChanged(context) {if (context==='editor') markDirty(); if(context==='settings') settingsDirty();}
function renderDesigner(context,design) {
  const root=$(`#${context}-design`);
  root.innerHTML = `<h3>기본 레이아웃</h3><div class="layout-picker">${Object.entries(layouts).map(([key,label]) => `<button type="button" data-layout="${key}" class="layout-choice ${design.layout===key?'selected':''}" aria-pressed="${design.layout===key}"><span class="layout-wireframe ${key}"><i></i><b></b><em></em></span>${label}</button>`).join('')}</div><div class="design-fields"><label>강조색<input type="color" data-accent value="${design.accent}"></label><label>배경색<input type="color" data-background value="${design.background || (design.layout==='editorial'?'#f6f4eb':'#111820')}"></label><label>문자색<input type="color" data-text-color value="${design.text_color || (design.layout==='editorial'?'#182922':'#f1f4f6')}"></label><label class="grow">디자인 방향<textarea data-notes rows="2" maxlength="2000" placeholder="예: 넓은 여백, 간결한 문구, 참고 이미지의 색감">${escapeHTML(design.notes)}</textarea></label></div><label class="logo-selector">공통 로고<select data-logo><option value="">로고 없음 · MEDIALOG 표시</option>${state.library.map(asset=>`<option value="${asset.id}" ${asset.id===design.logo_id?'selected':''}>${escapeHTML(asset.name)}</option>`).join('')}</select></label><label class="logo-selector">표지 뉴스속보 배너<select data-badge><option value="">기본 뉴스속보 표시</option>${state.library.map(asset=>`<option value="${asset.id}" ${asset.id===design.badge_id?'selected':''}>${escapeHTML(asset.name)}</option>`).join('')}</select></label><div class="card-design-controls">${[['logo_width','로고 폭',300,100,700],['logo_top','로고 상단 위치',1190,100,1250],['badge_width','뉴스속보 폭',350,100,700],['badge_top','뉴스속보 상단 위치',180,50,900]].map(([key,label,fallback,min,max])=>`<label>${label} (px)<input type="number" data-brand-position="${key}" min="${min}" max="${max}" value="${design[key] ?? fallback}"></label>`).join('')}</div><div class="reference-heading"><h3>참고 링크</h3><button type="button" class="text-button" data-add-reference>+ 링크 추가</button></div><div class="reference-list">${design.references.map((ref,index) => `<div class="reference-row"><input type="url" data-ref-url="${index}" value="${escapeHTML(ref.url)}" placeholder="https://…" aria-label="참고 링크 ${index+1}" maxlength="2000"><input data-ref-note="${index}" value="${escapeHTML(ref.note)}" placeholder="참고할 요소 (색감, 구성 등)" aria-label="참고 설명 ${index+1}" maxlength="300"><button type="button" class="text-button" data-remove-ref="${index}" aria-label="참고 링크 ${index+1} 삭제">✕</button></div>`).join('')}</div><div class="reference-heading"><h3>참고 이미지</h3><button type="button" class="text-button" data-open-library>보관함에서 선택</button><label class="upload-button">+ 이미지 첨부<input type="file" data-upload accept=".jpg,.jpeg,.jfif,.png,.webp,image/jpeg,image/png,image/webp" multiple></label></div><p class="muted">PNG · JPEG · WebP / 파일당 8MB · 2천만 화소 이하 / 최대 10개. 첨부 이미지는 참고용으로 보관되며, 이미지 레이아웃에서 카드에 사용할 이미지를 선택할 수 있습니다. ‘디자인에 적용’을 누르면 이미지의 대표 색상과 명암을 추출해 중앙 포스터 구성에 적용합니다. 글자·사진·정확한 서체를 복제하지는 않습니다.</p><div data-library-picker class="library-picker ${state.libraryOpen.has(context)?'':'hidden'}">${state.library.length?state.library.map(asset=>`<button type="button" class="asset-choice ${design.image_ids.includes(asset.id)?'selected':''}" data-pick-asset="${asset.id}" aria-pressed="${design.image_ids.includes(asset.id)}"><img src="/api/assets/${asset.id}.png" alt="${escapeHTML(asset.name)}"><span>${escapeHTML(asset.name)}</span></button>`).join(''):'자산 보관함에 이미지를 먼저 업로드하세요.'}</div><div class="attachment-grid">${design.image_ids.map(id => `<div class="attachment"><a href="/api/assets/${id}.png" target="_blank" rel="noopener"><img src="/api/assets/${id}.png" alt="${escapeHTML(state.assets[id]?.name || '참고 이미지')}"></a><span>${escapeHTML(state.assets[id]?.name || '참고 이미지')}</span><button type="button" class="text-button" data-apply-reference="${id}">디자인에 적용</button><button type="button" class="text-button" data-detach="${id}" aria-label="${escapeHTML(state.assets[id]?.name || '이미지')} 첨부 해제">✕</button></div>`).join('')}</div>`;
  root.querySelectorAll('[data-layout]').forEach(button => button.onclick = () => {design.layout=button.dataset.layout; designChanged(context); renderDesigner(context,design);});
  root.querySelector('[data-background]').oninput=event=>{design.background=event.target.value;designChanged(context);};
  root.querySelector('[data-text-color]').oninput=event=>{design.text_color=event.target.value;designChanged(context);};
  root.querySelectorAll('[data-apply-reference]').forEach(button=>button.onclick=async()=>{
    button.disabled=true;
    try {
      const style=await api(`/api/assets/${button.dataset.applyReference}/style`,{method:'POST'});
      if(context==='editor') applyCard();
      Object.assign(design,style);
      designChanged(context);
      if(context==='editor') {
        state.project.cards.forEach(card=>{card.layout=null;});
        $('#card-layout').value=''; $('#accent').value=design.accent;
        await saveProject(); showCard();
        toast('참고 이미지의 색감과 중앙 포스터 구성을 전체 카드에 적용하고 저장했습니다.');
      } else {
        renderDesigner(context,design);
        toast(context==='settings'?'디자인을 적용했습니다. 기본 설정 저장을 눌러 보관하세요.':'디자인을 적용했습니다. 이 구성으로 초안을 만들 수 있습니다.');
      }
    } catch(error) {toast(error.message,true);button.disabled=false;}
  });
  root.querySelectorAll('[data-brand-position]').forEach(input=>input.oninput=()=>{design[input.dataset.brandPosition]=Number(input.value);designChanged(context);});
  root.querySelector('[data-badge]').onchange=event=>{design.badge_id=event.target.value||null;designChanged(context);};
  root.querySelector('[data-logo]').onchange=event=>{design.logo_id=event.target.value||null; designChanged(context);};
  root.querySelector('[data-open-library]').onclick=()=>{if(state.libraryOpen.has(context)) state.libraryOpen.delete(context); else state.libraryOpen.add(context); root.querySelector('[data-library-picker]').classList.toggle('hidden',!state.libraryOpen.has(context));};
  root.querySelectorAll('[data-pick-asset]').forEach(button=>button.onclick=()=>{
    const id=button.dataset.pickAsset;
    if(design.image_ids.includes(id)) {
      design.image_ids=design.image_ids.filter(value=>value!==id);
      if(context==='editor') {applyCard();state.project.cards.forEach(card=>{if(card.image_id===id) card.image_id=null;});showCard();}
    } else {if(design.image_ids.length>=10) {toast('참고 이미지는 최대 10개입니다.',true);return;} design.image_ids.push(id);}
    designChanged(context);renderDesigner(context,design);if(context==='editor') {applyCard();showCard();}
  });
  root.querySelector('[data-accent]').oninput = event => {design.accent=event.target.value; if(context==='editor') $('#accent').value=design.accent; designChanged(context);};
  root.querySelector('[data-notes]').oninput = event => {design.notes=event.target.value; designChanged(context);};
  root.querySelector('[data-add-reference]').onclick = () => {if(design.references.length>=10) {toast('참고 링크는 최대 10개입니다.',true);return;} design.references.push({url:'',note:''}); designChanged(context); renderDesigner(context,design);};
  root.querySelectorAll('[data-ref-url]').forEach(input => input.oninput = () => {design.references[Number(input.dataset.refUrl)].url=input.value; designChanged(context);});
  root.querySelectorAll('[data-ref-note]').forEach(input => input.oninput = () => {design.references[Number(input.dataset.refNote)].note=input.value; designChanged(context);});
  root.querySelectorAll('[data-remove-ref]').forEach(button => button.onclick = () => {design.references.splice(Number(button.dataset.removeRef),1); designChanged(context); renderDesigner(context,design);});
  root.querySelectorAll('[data-detach]').forEach(button => button.onclick = () => {
    const id=button.dataset.detach;
    design.image_ids=design.image_ids.filter(value=>value!==id);
    if(context==='editor') {applyCard(); state.project.cards.forEach(card=>{if(card.image_id===id) card.image_id=null;}); showCard();}
    designChanged(context); renderDesigner(context,design);
  });
  root.querySelector('[data-upload]').onchange = async event => {
    const files=[...event.target.files];
    if (!files.length) return;
    if (state.uploads) {toast('진행 중인 이미지 첨부가 끝난 뒤 다시 선택하세요.',true); return;}
    state.uploads++;
    event.target.disabled=true;
    if (context==='plan') {
      $('#plan-form [type=submit]').disabled=true;
      $('#close-plan').disabled=true;
    }
    let added=0;
    const failures=[];
    try {
      for(const file of files) {
        try {
          toast(`${file.name} 첨부 중…`);
          if(design.image_ids.length>=10) throw new Error('이미지는 최대 10개입니다.');
          if(file.size>8*1024*1024) throw new Error('이미지는 파일당 8MB 이하로 첨부하세요.');
          const asset=await api(`/api/assets?name=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'Content-Type':file.type || 'application/octet-stream'},body:file});
          state.assets[asset.id]=asset; state.library.unshift(asset); design.image_ids.push(asset.id); designChanged(context); added++;
        } catch(error) {failures.push(`${file.name}: ${error.message}`);}
      }
      toast(`${added}개 이미지 첨부 완료${failures.length ? '\n'+failures.join('\n') : ''}`,failures.length>0);
    } finally {
      state.uploads--;
      if (context==='plan') {
        $('#plan-form [type=submit]').disabled=false;
        $('#close-plan').disabled=false;
      }
      renderDesigner(context,design);
      if(context==='editor') {applyCard(); showCard();}
    }
  };
}
$('#close-plan').onclick = () => {if(state.uploads) {toast('첨부가 끝난 뒤 닫아 주세요.');return;} $('#plan-dialog').close();};
$('#plan-dialog').addEventListener('cancel',event=>{if(state.uploads) event.preventDefault();});
$('#plan-count').onchange = () => resizeOutline('plan');
$('#plan-form').onsubmit = event => {
  event.preventDefault();
  busy(event.target.querySelector('[type=submit]'),async()=>{
    if(state.uploads) throw new Error('이미지 첨부가 끝난 뒤 초안을 만들어 주세요.');
    if(state.plan.outline.some(row=>!row.heading.trim())) throw new Error('모든 카드의 제목을 입력해 주세요.');
    const payload={...state.plan,design:{...state.plan.design,references:normalizedReferences(state.plan.design.references)}};
    toast('설정한 구성으로 초안을 만들고 있습니다…');
    const project=await api('/api/projects',{method:'POST',body:JSON.stringify(payload)});
    $('#plan-dialog').close();
    await openEditor(project);
  });
};


function normalizedReferences(references) {
  return references.flatMap((reference,index)=>{
    let url=reference.url.trim();
    const note=reference.note.trim();
    if(!url) {
      if(note) throw new Error(`참고 링크 ${index+1}번에 주소를 입력하거나 해당 행을 삭제하세요.`);
      return [];
    }
    if(!/^[a-z][a-z0-9+.-]*:/i.test(url)) url='https://'+url;
    try {
      const parsed=new URL(url);
      if(!['http:','https:'].includes(parsed.protocol) || !parsed.hostname || parsed.username || parsed.password) throw new Error();
      return [{url:parsed.href,note}];
    } catch {throw new Error(`참고 링크 ${index+1}번 주소를 확인하세요. HTTP(S) 링크만 사용할 수 있습니다.`);}
  });
}


async function loadAssetNames(design) {
  state.library=await api('/api/assets');
  state.library.forEach(asset=>state.assets[asset.id]=asset);
  await Promise.all([...design.image_ids,...(design.logo_id?[design.logo_id]:[])].map(async id=>{
    if(!state.assets[id]) {
      try {state.assets[id]=await api(`/api/assets/${id}/info`);} catch {state.assets[id]={id,name:'첨부 이미지 (파일 확인 필요)'};}
    }
  }));
}
function settingsDirty() {
  state.dirty=true;
  $('#settings-saved').textContent='저장하지 않은 변경 사항이 있습니다.';
}
async function loadSettings() {
  state.settings=await api('/api/settings');
  await loadAssetNames(state.settings.design);
  $('#settings-count').innerHTML=Array.from({length:8},(_,index)=>`<option value="${index+3}">${index+3}장</option>`).join('');
  $('#settings-count').value=String(state.settings.outline.length);
  renderDesigner('settings',state.settings.design);
  renderOutline('settings');
  $('#settings-saved').textContent='저장된 기본 설정을 불러왔습니다.';
}
function resizeOutline(context) {
  const count=Number($(`#${context}-count`).value);
  const existing=state[context].outline;
  const last=existing.at(-1);
  const middle=existing.slice(0,-1);
  while(middle.length<count-1) middle.push({role:'facts',heading:'핵심 사실'});
  state[context].outline=[...middle.slice(0,count-1),last];
  if(context==='settings') settingsDirty();
  renderOutline(context);
}
$('#settings-count').onchange=()=>resizeOutline('settings');
$('#settings-form').onsubmit=event=>{
  event.preventDefault();
  busy(event.target.querySelector('[type=submit]'),async()=>{
    if(state.uploads) throw new Error('이미지 첨부가 끝난 뒤 저장해 주세요.');
    if(state.settings.outline.some(row=>!row.heading.trim())) throw new Error('모든 카드의 제목을 입력해 주세요.');
    const payload={...state.settings,design:{...state.settings.design,references:normalizedReferences(state.settings.design.references)}};
    state.settings=await api('/api/settings',{method:'PUT',body:JSON.stringify(payload)});
    state.dirty=false;
    renderDesigner('settings',state.settings.design);
    renderOutline('settings');
    $('#settings-saved').textContent='기본 설정을 저장했습니다. 다음 카드뉴스부터 적용됩니다.';
    toast('기본 디자인과 구성을 저장했습니다.');
  });
};


async function loadAssets() {
  state.library=await api('/api/assets');
  state.library.forEach(asset=>state.assets[asset.id]=asset);
  $('#asset-library').innerHTML=state.library.length ? state.library.map(asset=>`<article class="library-card"><a href="/api/assets/${asset.id}.png" target="_blank" rel="noopener"><img src="/api/assets/${asset.id}.png" alt="${escapeHTML(asset.name)}"></a><h3>${escapeHTML(asset.name)}</h3><p class="muted">${asset.width} × ${asset.height} px</p><button class="text-button" data-delete-asset="${asset.id}">삭제</button></article>`).join('') : empty('저장된 자산이 없습니다','로고와 자주 쓰는 사진을 위에서 업로드하세요.');
  document.querySelectorAll('[data-delete-asset]').forEach(button=>button.onclick=()=>busy(button,async()=>{
    if(!confirm('이 이미지 파일을 삭제할까요? 사용 중인 자산은 삭제할 수 없습니다.')) return;
    await api(`/api/assets/${button.dataset.deleteAsset}`,{method:'DELETE'});
    delete state.assets[button.dataset.deleteAsset];
    await loadAssets();
  }));
}
$('#library-upload').onchange=async event=>{
  const input=event.target;
  const files=[...input.files];
  if(!files.length) return;
  if(state.uploads) {toast('진행 중인 첨부가 끝난 뒤 다시 시도하세요.',true);return;}
  state.uploads++; input.disabled=true;
  let added=0;
  const errors=[];
  try {
    for(const file of files) {
      try {
        if(file.size>8*1024*1024) throw new Error('파일당 8MB 이하로 첨부하세요.');
        toast(`${file.name} 업로드 중…`);
        await api(`/api/assets?name=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'Content-Type':file.type||'application/octet-stream'},body:file});
        added++;
      } catch(error) {errors.push(`${file.name}: ${error.message}`);}
    }
    await loadAssets();
    toast(`${added}개 자산을 저장했습니다.${errors.length?'\n'+errors.join('\n'):''}`,errors.length>0);
  } catch(error) {toast(error.message,true);} finally {state.uploads--;input.disabled=false;input.value='';}
};

['#card-font','#card-font-size','#card-align','#card-top','#card-line-height'].forEach(selector=>$(selector).oninput=()=>markDirty());
for(const kind of ['logo','badge']) {
  $(`#upload-${kind}`).onchange=async event=>{
    const file=event.target.files[0]; if(!file || !state.project) return;
    state.uploads++; event.target.disabled=true;
    try {
      applyCard();
      const asset=await api(`/api/assets?name=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'Content-Type':file.type || 'application/octet-stream'},body:file});
      state.assets[asset.id]=asset; state.library.unshift(asset);
      state.project.design[`${kind}_id`]=asset.id;
      markDirty(); renderDesigner('editor',state.project.design);
      toast('원본 파일을 선택했습니다. 변경 저장을 눌러 적용하세요.');
    } catch(error) {toast(error.message,true);}
    finally {state.uploads--; event.target.disabled=false;event.target.value='';}
  };
}

$('#upload-card-photo').onchange=async event=>{
  const file=event.target.files[0]; if(!file || !state.project) return;
  const project=state.project, cardIndex=state.card;
  if(project.design.image_ids.length>=10) {toast('참고 이미지에서 사용하지 않는 사진을 해제하세요. 최대 10개입니다.',true);event.target.value='';return;}
  applyCard();state.uploads++;event.target.disabled=true;
  try {
    const asset=await api(`/api/assets?name=${encodeURIComponent(file.name)}`,{method:'POST',headers:{'Content-Type':file.type || 'application/octet-stream'},body:file});
    state.assets[asset.id]=asset;state.library.unshift(asset);project.design.image_ids.push(asset.id);
    project.cards[cardIndex].image_id=asset.id;project.cards[cardIndex].ai_background=false;
    if(state.project===project) {markDirty();renderDesigner('editor',project.design);showCard();}
    toast('사진을 교체했습니다. 변경 저장을 눌러 적용하세요.');
  } catch(error) {toast(error.message,true);}
  finally {state.uploads--;event.target.disabled=false;event.target.value='';}
};
$('#clear-card-photo').onclick=()=>{applyCard();state.project.cards[state.card].image_id=null;state.project.cards[state.card].ai_background=false;markDirty();showCard();};
