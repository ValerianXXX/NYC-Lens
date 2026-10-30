// A server task outlives a page reload. AbortController alone cannot stop SQL.
const jobStatus = document.querySelector('#job-status');
const jobCancel = document.querySelector('#job-cancel');
const jobResume = document.querySelector('#job-resume');
const jobStorageKey = 'nyc-lens-last-analysis-job';
const jobTerminal = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'INTERRUPTED']);
let activeJob = null;
let watchingJob = false;
const jobCopy = (en, zh) => currentLanguage === 'zh' ? zh : en;

function rememberJob(receipt) {
  activeJob = receipt;
  safeStorageSet(jobStorageKey, JSON.stringify({ job_id: receipt.job_id, operation: receipt.operation }));
  document.querySelector('#job-identity').textContent = receipt.job_id;
  jobResume.disabled = watchingJob;
  jobCancel.disabled = jobTerminal.has(receipt.status) || receipt.status === 'CANCELLING';
}

function describeJob(receipt) {
  const copy = {
    RUNNING: ['Computing local data. Refresh reconnects; use Cancel to stop.', '正在计算本地数据。刷新会重新连接；请使用取消按钮停止计算。'],
    CANCELLING: ['Cancellation requested. Waiting for the worker to stop and saved states to settle.', '已请求取消，正在等待工作进程退出并保存中断状态。'],
    SUCCEEDED: ['Analysis complete. Evidence and exports refer to the saved result.', '分析完成。证据及导出指向已保存的结果。'],
    CANCELLED: ['Cancelled. Completed child analyses are retained; retry creates a new attempt.', '已取消。此前完成的子分析仍保留；重新分析会创建新任务。'],
    TIMED_OUT: ['Time limit reached. Completed child analyses are retained; inspect the request before retrying.', '已达到时间上限。此前完成的子分析仍保留；请检查请求后再重试。'],
    INTERRUPTED: ['The supervisor stopped. No automatic retry was started. Review saved states before retrying.', '监督程序已中断，没有自动重试。请检查保存状态后再试。'],
    FAILED: ['Analysis failed. No automatic retry was started.', '分析失败，没有自动重试。']
  };
  const pair = copy[receipt.status] || ['Task status is unknown.', '任务状态尚不明确。'];
  let message = jobCopy(...pair);
  if (jobTerminal.has(receipt.status) && receipt.status !== 'SUCCEEDED') {
    message += receipt.database_cleanup_confirmed
      ? jobCopy(' Database state was reconciled.', ' 数据库状态已核对。')
      : jobCopy(' Database cleanup is unconfirmed; check the original task before running again.', ' 数据库收尾尚未确认，请先检查原任务。');
  }
  jobStatus.textContent = message;
}

async function jobFetch(url, options) {
  const response = await fetch(url, options);
  const payload = await readApiPayload(response);
  if (!response.ok) {
    const error = new Error(apiErrorMessage(payload, jobCopy('Task status unavailable.', '无法获取任务状态。')));
    error.httpStatus = response.status;
    error.notStarted = payload?.detail?.started === false;
    throw error;
  }
  return payload;
}

async function watchAnalysisJob(identifier) {
  if (watchingJob) throw new Error(jobCopy('Already checking this task.', '正在检查此任务。'));
  watchingJob = true;
  jobResume.disabled = true;
  try {
    while (true) {
      const receipt = await jobFetch(`/api/analysis-jobs/${identifier}`);
      rememberJob(receipt);
      describeJob(receipt);
      if (jobTerminal.has(receipt.status)) {
        if (receipt.status !== 'SUCCEEDED') throw new Error(jobStatus.textContent);
        return await jobFetch(`/api/analysis-jobs/${identifier}/result`);
      }
      await new Promise(resolve => setTimeout(resolve, 350));
    }
  } catch (error) {
    if (!activeJob || !jobTerminal.has(activeJob.status)) {
      jobStatus.textContent = jobCopy('Connection lost or status unavailable. Check the original task before retrying; it may still be running. ',
        '连接中断或状态不可用。原任务可能仍在运行，请检查原任务后再重试。 ') + error.message;
    }
    throw error;
  } finally {
    watchingJob = false;
    jobResume.disabled = !activeJob;
  }
}

async function runAnalysisJob(operation, request) {
  if (activeJob && (!jobTerminal.has(activeJob.status) || activeJob.database_cleanup_confirmed === false)) {
    throw new Error(jobCopy('Check or cancel the existing task first.', '请先检查或取消已有任务。'));
  }
  const request_id = crypto.randomUUID().replaceAll('-', '');
  rememberJob({ job_id: `analysis_${request_id}`, operation, status: 'RUNNING' });
  describeJob(activeJob);
  try {
    const receipt = await jobFetch('/api/analysis-jobs', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ operation, request, request_id }) });
    rememberJob(receipt);
    return await watchAnalysisJob(receipt.job_id);
  } catch (error) {
    if (error.notStarted) {
      activeJob = null;
      safeStorageSet(jobStorageKey, 'null');
      jobCancel.disabled = true;
      jobResume.disabled = true;
      document.querySelector('#job-identity').textContent = '';
    }
    if (error.httpStatus === 422) {
      // Server validation precedes any worker creation.
      rememberJob({ ...activeJob, status: 'FAILED', database_cleanup_confirmed: true });
    }
    jobStatus.textContent = error.message;
    throw error;
  }
}

function displayRestoredAnalysis(payload) {
  if (activeJob.operation === 'site') {
    currentResult = payload;
    document.querySelector('#address').value = payload.standardized_address;
    const available = Object.keys(payload.catchments);
    const preferred = available.includes('500m') ? '500m' : available[0];
    syncCatchmentControls(preferred, available);
    renderResult(payload, preferred);
    setStatus('status.success', { count: payload.missing_data.length }, 'ready');
  } else {
    currentComparison = payload;
    document.querySelector('#comparison-addresses').value = payload.sites.map(site => site.standardized_address).join('\n');
    document.querySelector('#comparison-catchment').value = payload.comparison_catchment;
    renderComparison(payload);
    renderComparisonMap(payload);
    setComparisonStatus('comparison.success', { sites: payload.sites.length, metrics: Object.keys(payload.metric_comparisons).length }, 'ready');
  }
}

async function restoreAnalysisJob() {
  if (!activeJob || watchingJob) return;
  try { displayRestoredAnalysis(await watchAnalysisJob(activeJob.job_id)); }
  catch (_) { /* The task panel retains the precise error and original ID. */ }
}

jobCancel.addEventListener('click', async () => {
  if (!activeJob) return;
  jobCancel.disabled = true;
  try {
    const receipt = await jobFetch(`/api/analysis-jobs/${activeJob.job_id}/cancel`, { method: 'POST' });
    rememberJob(receipt);
    describeJob(receipt);
    if (!watchingJob) await restoreAnalysisJob();
  } catch (error) {
    jobStatus.textContent = jobCopy('Cancellation is unconfirmed. Check the original task. ', '取消尚未确认，请检查原任务。 ') + error.message;
    jobResume.disabled = false;
  }
});
jobResume.addEventListener('click', restoreAnalysisJob);
document.querySelector('#language-toggle').addEventListener('click', () => { if (activeJob) describeJob(activeJob); });
document.addEventListener('DOMContentLoaded', () => {
  try {
    const saved = JSON.parse(safeStorageGet(jobStorageKey));
    if (saved && /^analysis_[0-9a-f]{32}$/.test(saved.job_id)) {
      rememberJob(saved);
      restoreAnalysisJob();
    }
  } catch (_) { /* Malformed browser storage is not an analysis instruction. */ }
});
