// Uses the existing bilingual application, map, comparison and export surfaces.
const assistantForm = document.querySelector('#assistant-form');
const assistantDraft = document.querySelector('#assistant-draft');
const assistantNotice = document.querySelector('#assistant-notice');
let assistantProposal = null;
const assistantCopy = (en, zh) => currentLanguage === 'zh' ? zh : en;


assistantForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  assistantDraft.hidden = true;
  assistantProposal = null;
  const button = assistantForm.querySelector('button');
  button.disabled = true;
  assistantNotice.textContent = assistantCopy('Reading requirements...', '正在读取要求……');
  try {
    const response = await fetch('/api/assistant/interpret', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: document.querySelector('#assistant-text').value, language: currentLanguage }) });
    const result = await readApiPayload(response);
    if (!response.ok) throw new Error(apiErrorMessage(result, 'Interpretation unavailable'));
    assistantProposal = result;
    document.querySelector('#assistant-first').value = result.intent.addresses[0] || '';
    document.querySelector('#assistant-second').value = result.intent.addresses[1] || '';
    document.querySelector('#assistant-business').value = result.intent.business_type || '';
    document.querySelector('#assistant-radius').value = result.intent.catchment;
    assistantNotice.textContent = assistantCopy('Review addresses, business type and radial scope. ', '请确认地址、业态和直线半径。 ')
      + (result.interpretation === 'EXPLICIT_TEXT_HELPER'
        ? assistantCopy('AI is disabled; quoted addresses and known keywords only. Other requirements remain unresolved.', 'AI 未启用；仅读取引号内地址与已知关键词，其余要求仍待核对。')
        : assistantCopy('AI interpretation needs your confirmation.', 'AI 理解结果需要您确认。'));
    assistantDraft.hidden = false;
  } catch (error) { assistantNotice.textContent = error.message; }
  finally { button.disabled = false; }
});

assistantDraft.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!assistantProposal) return;
  const button = assistantDraft.querySelector('button');
  button.disabled = true;
  assistantNotice.textContent = assistantCopy('Querying local data...', '正在查询本地数据……');
  try {
    const intent = { ...assistantProposal.intent,
      addresses: [document.querySelector('#assistant-first').value.trim(), document.querySelector('#assistant-second').value.trim()],
      business_type: document.querySelector('#assistant-business').value,
      catchment: document.querySelector('#assistant-radius').value };
    const response = await fetch('/api/assistant/compare', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ intent, confirmed: true, language: currentLanguage, original_request: assistantProposal.original_request }) });
    const payload = await readApiPayload(response);
    if (!response.ok) throw new Error(apiErrorMessage(payload, 'Comparison unavailable'));
    currentComparison = payload;
    document.querySelector('#comparison-addresses').value = intent.addresses.join('\n');
    document.querySelector('#comparison-catchment').value = intent.catchment;
    renderComparison(payload);
    setComparisonStatus('comparison.success', { sites: payload.sites.length, metrics: Object.keys(payload.metric_comparisons).length }, 'ready');
    assistantNotice.textContent = assistantCopy('Comparison ready. Review evidence and unresolved requirements below.', '比较已完成。请查看下方证据及待补资料。');
    renderComparisonMap(payload);
    document.querySelector('#multi-comparison').scrollIntoView({ behavior: 'smooth' });
  } catch (error) { assistantNotice.textContent = error.message; }
  finally { button.disabled = false; }
});

function renderAssistantBrief(payload) {
  const old = document.querySelector('#assistant-brief');
  if (old) old.remove();
  if (!payload.assistant) return;
  const brief = payload.assistant.brief;
  const section = document.createElement('section');
  section.id = 'assistant-brief';
  const heading = document.createElement('h3');
  heading.textContent = assistantCopy('Decision differences and missing evidence', '决策差异与待补证据');
  section.append(heading);
  const notice = document.createElement('p');
  notice.textContent = (payload.data_mode === 'SYNTHETIC_DEMO' ? assistantCopy('SYNTHETIC DEMO. ', '合成演示数据。 ') : '')
    + assistantCopy('Higher is not necessarily better; no composite score. ', '高低不等于更好；未生成综合评分。');
  section.append(notice);
  for (const item of brief.differences) {
    const line = document.createElement('p');
    line.textContent = `${metricLabel(item.metric_id)}: ${assistantCopy('first minus second', '第一地址减第二地址')} = ${item.first_minus_second} ${item.unit}. `;
    item.values.forEach((value, index) => {
      const link = document.createElement('a');
      link.href = item.evidence_urls[index];
      link.target = '_blank'; link.rel = 'noopener';
      link.textContent = `${index + 1}: ${value.source_id} / ${value.source_year} / ${value.measure_type} / ${assistantCopy('confidence', '置信信息')} ${value.confidence ?? assistantCopy('unmeasured', '未测得')} `;
      line.append(link);
    });
    section.append(line);
  }
  for (const item of brief.missing) {
    const line = document.createElement('p');
    line.textContent = `${metricLabel(item.metric_id)}: ${assistantCopy('missing or incomparable source data', '来源缺失或口径不可比')}`;
    section.append(line);
  }
  const unmet = document.createElement('p');
  unmet.textContent = assistantCopy('Requirements to verify: ', '仍需核对的要求：') + brief.unmodeled_requirements.join(' | ');
  section.append(unmet);
  document.querySelector('#comparison-result').append(section);
}
