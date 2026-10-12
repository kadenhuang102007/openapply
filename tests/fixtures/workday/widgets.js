// Test-only stand-in for Workday's client-side widgets, injected into each fixture page.
// Dropdown buttons open a listbox, search boxes show results on Enter, "Add" clones a panel
// template, and the footer button navigates (or records a Submit click in window.__submitted).
(function () {
  const closePopups = () => document.querySelectorAll('[data-oa-popup]').forEach(p => p.remove());
  function openList(anchor, items, onPick) {
    closePopups();
    const ul = document.createElement('ul');
    ul.setAttribute('role', 'listbox');
    ul.setAttribute('data-oa-popup', '1');
    for (const text of items) {
      const li = document.createElement('li');
      li.setAttribute('role', 'option');
      const div = document.createElement('div');
      div.setAttribute('data-automation-id', 'promptOption');
      div.textContent = text;
      li.appendChild(div);
      li.addEventListener('click', (ev) => { ev.stopPropagation(); onPick(text); closePopups(); });
      ul.appendChild(li);
    }
    anchor.insertAdjacentElement('afterend', ul);
  }
  document.addEventListener('click', (ev) => {
    const t = ev.target;
    const apply = t.closest('[data-automation-id="adventureButton"]');
    if (apply) { ev.preventDefault(); document.getElementById('popup').style.display = 'block'; return; }
    const dd = t.closest('button[aria-haspopup="listbox"]');
    if (dd) {
      openList(dd, dd.dataset.options.split('|'), (text) => { dd.textContent = text; });
      return;
    }
    const add = t.closest('[data-automation-id="add-button"]');
    if (add) {
      const sec = add.closest('[data-section]');
      const n = sec.querySelectorAll('[data-panel]').length + 1;
      add.insertAdjacentHTML('beforebegin', sec.querySelector('template').innerHTML.replaceAll('{n}', n));
      add.textContent = 'Add Another';
      return;
    }
    const next = t.closest('[data-automation-id="bottom-navigation-next-button"]');
    if (next) {
      if (next.textContent.trim() === 'Submit') { window.__submitted = true; sessionStorage.setItem('submitted', '1'); }
      location.href = next.dataset.next;
      return;
    }
    if (t.closest('[data-automation-id="createAccountSubmitButton"], [data-automation-id="signInLink"]')) {
      window.__signinClicked = true;
    }
  });
  document.addEventListener('keydown', (ev) => {
    const box = ev.target.closest && ev.target.closest('[data-automation-id="searchBox"]');
    if (!box || ev.key !== 'Enter') return;
    const words = box.value.toLowerCase().split(/\W+/).filter(w => w.length > 1);
    const items = box.dataset.options.split('|').filter(o => words.some(w => o.toLowerCase().includes(w)));
    const ff = box.closest('[data-automation-id^="formField-"]');
    openList(box, items, (text) => {
      ff.insertAdjacentHTML('beforeend', `<div data-automation-id="selectedItem">${text}</div>`);
      box.value = '';
    });
  });
  document.addEventListener('change', (ev) => {
    if (ev.target.matches('[data-automation-id="file-upload-input-ref"]')) {
      ev.target.insertAdjacentHTML('afterend', '<div data-automation-id="file-upload-successful">Successfully Uploaded!</div>');
    }
  });
})();
