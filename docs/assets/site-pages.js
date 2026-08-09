(() => {
  const STORAGE_KEY = 'forkprobe.lang';
  const supported = new Set(['zh', 'en']);

  const normalizeLanguage = (value) => {
    const candidate = String(value || '').toLowerCase();
    if (candidate.startsWith('zh')) return 'zh';
    return supported.has(candidate) ? candidate : 'en';
  };

  const getLanguage = () => {
    const query = new URLSearchParams(window.location.search).get('lang');
    if (query) return normalizeLanguage(query);
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      if (stored) return normalizeLanguage(stored);
    } catch (error) {
      // Local previews can disable storage.
    }
    return normalizeLanguage(navigator.language);
  };

  const setLanguage = (language, updateUrl = false) => {
    const lang = normalizeLanguage(language);
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
    document.body.dataset.lang = lang;

    const title = document.body.dataset[`title${lang === 'zh' ? 'Zh' : 'En'}`];
    if (title) document.title = title;

    document.querySelectorAll('[data-zh][data-en]').forEach((element) => {
      element.textContent = element.dataset[lang];
    });
    document.querySelectorAll('[data-lang-block]').forEach((element) => {
      element.hidden = element.dataset.langBlock !== lang;
    });
    document.querySelectorAll('[data-lang-option]').forEach((button) => {
      const active = button.dataset.langOption === lang;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-pressed', String(active));
    });
    document.querySelectorAll('[data-preserve-lang]').forEach((link) => {
      const url = new URL(link.getAttribute('href'), window.location.href);
      if (url.origin === window.location.origin || window.location.protocol === 'file:') {
        url.searchParams.set('lang', lang);
        link.setAttribute('href', `${url.pathname.split('/').pop() || 'index.html'}${url.search}${url.hash}`);
      }
    });

    try {
      window.localStorage.setItem(STORAGE_KEY, lang);
    } catch (error) {
      // Local previews can disable storage.
    }
    if (updateUrl) {
      const url = new URL(window.location.href);
      url.searchParams.set('lang', lang);
      window.history.replaceState({}, '', url);
    }
    window.dispatchEvent(new CustomEvent('forkprobe:language', { detail: { lang } }));
  };

  document.querySelectorAll('[data-lang-option]').forEach((button) => {
    button.addEventListener('click', () => setLanguage(button.dataset.langOption, true));
  });

  const activateTab = (tabName, updateHash = true) => {
    const buttons = Array.from(document.querySelectorAll('[data-tab]'));
    const panels = Array.from(document.querySelectorAll('[data-tab-panel]'));
    if (!buttons.some((button) => button.dataset.tab === tabName)) return;
    buttons.forEach((button) => {
      const active = button.dataset.tab === tabName;
      button.setAttribute('aria-selected', String(active));
      button.tabIndex = active ? 0 : -1;
    });
    panels.forEach((panel) => {
      panel.hidden = panel.dataset.tabPanel !== tabName;
    });
    if (updateHash) {
      const url = new URL(window.location.href);
      url.hash = tabName;
      window.history.replaceState({}, '', url);
    }
  };

  document.querySelectorAll('[data-tab]').forEach((button) => {
    button.addEventListener('click', () => activateTab(button.dataset.tab));
    button.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
      const buttons = Array.from(document.querySelectorAll('[data-tab]'));
      const current = buttons.indexOf(button);
      const direction = event.key === 'ArrowRight' ? 1 : -1;
      const next = buttons[(current + direction + buttons.length) % buttons.length];
      activateTab(next.dataset.tab);
      next.focus();
    });
  });

  const requestedTab = window.location.hash.replace('#', '');
  const firstTab = document.querySelector('[data-tab]')?.dataset.tab;
  if (requestedTab || firstTab) activateTab(requestedTab || firstTab, false);

  setLanguage(getLanguage());
})();
