/* squeal landing — tabs, copy, scroll spy, reveal. No dependencies. */

(function () {
  'use strict';

  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- code tabs ---------- */
  document.querySelectorAll('[data-tabs]').forEach(function (group) {
    var tabs = Array.prototype.slice.call(group.querySelectorAll('[role="tab"]'));
    var panels = tabs.map(function (tab) {
      return group.querySelector('#' + tab.getAttribute('aria-controls'));
    });
    var copyBtn = group.querySelector('[data-copy]');

    tabs.forEach(function (tab, index) {
      tab.addEventListener('click', function () {
        select(index, false);
      });
      tab.addEventListener('keydown', function (event) {
        var next = null;
        var down = event.key === 'ArrowRight' || event.key === 'ArrowDown';
        var up = event.key === 'ArrowLeft' || event.key === 'ArrowUp';
        if (down) next = (index + 1) % tabs.length;
        if (up) next = (index - 1 + tabs.length) % tabs.length;
        if (event.key === 'Home') next = 0;
        if (event.key === 'End') next = tabs.length - 1;
        if (next === null) return;
        event.preventDefault();
        select(next, true);
      });
    });

    function select(index, focus) {
      tabs.forEach(function (tab, i) {
        var active = i === index;
        tab.setAttribute('aria-selected', active ? 'true' : 'false');
        tab.tabIndex = active ? 0 : -1;
        if (panels[i]) panels[i].hidden = !active;
      });
      if (copyBtn && panels[index]) copyBtn.setAttribute('data-copy', panels[index].id);
      if (focus) tabs[index].focus();
    }
  });

  /* ---------- copy buttons ---------- */
  function plainText(node) {
    var clone = node.cloneNode(true);
    clone.querySelectorAll('.ln').forEach(function (line) { line.remove(); });
    return clone.textContent.replace(/\s+$/, '') + '\n';
  }

  var status = document.getElementById('copy-status');

  document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-copy]');
    if (!button) return;
    var source = document.getElementById(button.getAttribute('data-copy'));
    if (!source) return;

    var text = plainText(source);
    var done = function () {
      var label = button.getAttribute('data-label') || button.textContent;
      button.setAttribute('data-label', label);
      button.textContent = 'copied';
      button.classList.add('copied');
      if (status) status.textContent = 'Copied to clipboard';
      window.setTimeout(function () {
        button.textContent = label;
        button.classList.remove('copied');
        if (status) status.textContent = '';
      }, 1400);
    };
    var failed = function () {
      button.textContent = 'ctrl+c';
      window.setTimeout(function () { button.textContent = 'copy'; }, 1400);
    };

    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done, failed);
      return;
    }
    var area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.opacity = '0';
    document.body.appendChild(area);
    area.select();
    try { document.execCommand('copy'); done(); } catch (error) { failed(); }
    document.body.removeChild(area);
  });

  /* ---------- reveal on scroll ---------- */
  if (!reduced && 'IntersectionObserver' in window) {
    var candidates = document.querySelectorAll(
      '.big-head, .syntax, .stdlib, .tradeoffs, .square, .strip, .viewer'
    );
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add('in');
        observer.unobserve(entry.target);
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.08 });

    candidates.forEach(function (node, index) {
      node.setAttribute('data-reveal', '');
      node.style.transitionDelay = (index % 4) * 55 + 'ms';
      observer.observe(node);
    });
  }
})();
