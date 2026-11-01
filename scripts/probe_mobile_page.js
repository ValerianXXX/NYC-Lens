() => {
  // Follow-up for the observed 320px long task-identity overflow. Read only.
  const identity = document.querySelector('#job-identity');
  const language = document.querySelector('#language-toggle');
  const checks = {
    pageFits: document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
    fullTaskIdentityFits: identity.scrollWidth <= identity.clientWidth + 1 && /^analysis_[a-f0-9]{32}$/.test(identity.textContent),
    languageControlFits: language.scrollWidth <= language.clientWidth + 1 && getComputedStyle(language).whiteSpace === 'nowrap',
  };
  return {
    viewport:{width:innerWidth,height:innerHeight},
    documentWidth:document.documentElement.clientWidth,scrollWidth:document.documentElement.scrollWidth,
    taskIdentity:identity.textContent,taskWidth:identity.clientWidth,taskScrollWidth:identity.scrollWidth,
    checks,passed:Object.values(checks).every(Boolean),
  };
};
