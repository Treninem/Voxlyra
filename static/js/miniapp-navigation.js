(() => {
  'use strict';

  function onReady(callback) {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', callback, { once: true });
    else callback();
  }

  function initLibraryNavigation() {
    const toggle = document.getElementById('libraryMoreToggle');
    const secondary = document.getElementById('librarySecondaryTabs');
    if (!toggle || !secondary) return;

    const hasActiveSecondary = Boolean(secondary.querySelector('[data-library-tab].active'));

    function setExpanded(expanded) {
      secondary.hidden = !expanded;
      toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
      toggle.classList.toggle('active', expanded);
      toggle.textContent = expanded ? 'Скрыть' : 'Ещё';
    }

    setExpanded(hasActiveSecondary);
    toggle.addEventListener('click', () => {
      setExpanded(toggle.getAttribute('aria-expanded') !== 'true');
    });

    secondary.addEventListener('click', (event) => {
      const tab = event.target instanceof Element ? event.target.closest('[data-library-tab]') : null;
      if (!tab) return;
      if (window.matchMedia('(max-width: 560px)').matches) setExpanded(false);
    });
  }

  function cleanSettingsHeading(title) {
    const heading = title.querySelector('h2');
    if (!heading) return '';
    const clean = String(heading.textContent || '')
      .replace(/^[^A-Za-zА-Яа-яЁё0-9]+/, '')
      .trim();
    if (clean) heading.textContent = clean;
    return clean;
  }

  function initSettingsAccordion() {
    const titles = Array.from(document.querySelectorAll('.settings-group-title'));
    if (!titles.length) return;

    const sections = titles.map((title, index) => {
      const panel = title.nextElementSibling;
      if (!(panel instanceof HTMLElement) || !panel.classList.contains('settings-panel')) return null;
      const label = cleanSettingsHeading(title) || `Раздел ${index + 1}`;
      const key = `voxlyra-settings-section-${index}`;
      if (!panel.id) panel.id = key;
      title.classList.add('settings-accordion-title');
      title.setAttribute('role', 'button');
      title.setAttribute('tabindex', '0');
      title.setAttribute('aria-controls', panel.id);
      return { title, panel, label };
    }).filter(Boolean);

    if (!sections.length) return;

    function sectionForHash() {
      const hash = String(window.location.hash || '').replace(/^#/, '');
      if (!hash) return null;
      const target = document.getElementById(hash);
      if (!target) return null;
      return sections.find(({ panel }) => panel === target || panel.contains(target)) || null;
    }

    function openSection(target) {
      sections.forEach((section) => {
        const open = section === target;
        section.panel.hidden = !open;
        section.title.classList.toggle('open', open);
        section.title.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
    }

    const requested = sectionForHash();
    const firstUseful = sections.find(({ label }) => /Оформление|Чтение/i.test(label)) || sections[0];
    openSection(requested || firstUseful);

    sections.forEach((section) => {
      const activate = () => openSection(section);
      section.title.addEventListener('click', activate);
      section.title.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        activate();
      });
    });
  }

  onReady(() => {
    initLibraryNavigation();
    initSettingsAccordion();
  });
})();
