/**
 * llm-router i18n core — zh/en bilingual support
 *
 * Storage key: llm-router-lang
 * Default: browser language detection (zh* → zh, else en)
 *
 * Usage in templates:
 *   data-i18n="key"              → replaces textContent
 *   data-i18n-placeholder="key" → replaces placeholder attribute
 *   data-i18n-title="key"       → replaces title attribute
 *   data-i18n-page / data-i18n-total → server-side page info spans
 *   data-lang="zh|en"           → show/hide blocks (docs bilingual sections)
 *
 * Global:
 *   window.t(key)        → translate a key
 *   window.i18n.setLang(lang)
 *   window.i18n.onLangChange(fn)
 */
(function () {
  'use strict';

  var STORAGE_KEY = 'llm-router-lang';
  var rerenderCallbacks = [];
  var currentLang;

  // Fallback translations (used before i18nDATA is loaded)
  var FALLBACK_ZH = {};
  var FALLBACK_EN = {};

  function detectLang() {
    // Cookie is set by setLang() so the server can render the correct language
    // on the next page load, eliminating flash of untranslated content (FOUC)
    var cookies = document.cookie.split(';').reduce(function (acc, c) {
      var p = c.trim().split('=');
      acc[decodeURIComponent(p[0])] = decodeURIComponent(p[1] || '');
      return acc;
    }, {});
    var cookie = cookies[STORAGE_KEY];
    if (cookie === 'zh' || cookie === 'en') return cookie;
    var stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'zh' || stored === 'en') return stored;
    var nav = (navigator.language || '').toLowerCase();
    return nav.startsWith('zh') ? 'zh' : 'en';
  }

  function getDict(lang) {
    var data = window.i18nDATA || {};
    return data[lang] || (lang === 'en' ? FALLBACK_EN : FALLBACK_ZH);
  }

  function t(key) {
    var dict = getDict(currentLang);
    if (dict[key] !== undefined) return dict[key];
    // Fallback to opposite language, then to key itself
    var fallback = getDict(currentLang === 'en' ? 'zh' : 'en');
    return fallback[key] !== undefined ? fallback[key] : key;
  }

  function apply() {
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      var v = t(el.getAttribute('data-i18n'));
      if (v !== undefined) el.textContent = v;
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(function (el) {
      var v = t(el.getAttribute('data-i18n-placeholder'));
      if (v !== undefined) el.placeholder = v;
    });
    document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
      var v = t(el.getAttribute('data-i18n-title'));
      if (v !== undefined) el.title = v;
    });
    document.querySelectorAll('[data-i18n-label]').forEach(function (el) {
      var v = t(el.getAttribute('data-i18n-label'));
      if (v !== undefined) el.label = v;
    });
    // Server-side pagination spans with data-i18n-page + data-i18n-total
    document.querySelectorAll('[data-i18n-page]').forEach(function (el) {
      var cur = el.getAttribute('data-i18n-page');
      var total = el.getAttribute('data-i18n-total');
      el.textContent = t('common.page_info').replace('{cur}', cur).replace('{total}', total);
    });
    // Language blocks for docs bilingual sections
    document.querySelectorAll('[data-lang]').forEach(function (el) {
      el.style.display = el.getAttribute('data-lang') === currentLang ? '' : 'none';
    });
    // Toggle button label
    var btn = document.getElementById('lang-toggle-btn');
    if (btn) btn.textContent = currentLang === 'zh' ? 'EN' : '中文';
    // html lang attr
    document.documentElement.lang = currentLang === 'zh' ? 'zh-CN' : 'en';
  }

  function setLang(lang) {
    currentLang = lang;
    localStorage.setItem(STORAGE_KEY, lang);
    // Write cookie so the server renders the correct language on next page load
    document.cookie = STORAGE_KEY + '=' + lang + '; path=/; max-age=31536000; SameSite=Lax';
    apply();
    rerenderCallbacks.forEach(function (cb) { try { cb(); } catch (e) {} });
  }

  currentLang = detectLang();

  // Apply immediately then again after DOM ready
  apply();
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', apply);
  }

  window.t = t;
  window.i18n = {
    get lang() { return currentLang; },
    t: t,
    apply: apply,
    setLang: setLang,
    onLangChange: function (fn) { rerenderCallbacks.push(fn); }
  };
})();