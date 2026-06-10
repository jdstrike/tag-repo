(function(){
  function makeSearchable(sel){
    if (!sel || sel.dataset.searchable === '1') return;
    sel.dataset.searchable = '1';
    const wrap = document.createElement('div');
    wrap.className = 'searchable-wrap';
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'searchable-input';
    input.placeholder = sel.options[0] ? sel.options[0].text : 'Search...';
    input.setAttribute('autocomplete', 'off');
    const list = document.createElement('div');
    list.className = 'searchable-list';
    list.style.display = 'none';
    const hidden = document.createElement('input');
    hidden.type = 'hidden';
    hidden.name = sel.name;
    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(input);
    wrap.appendChild(list);
    wrap.appendChild(hidden);
    sel.style.display = 'none';
    const opts = Array.from(sel.options).filter(o => o.value).map(o => ({value: o.value, label: o.text}));
    if (sel.value) {
      const pre = opts.find(o => o.value === sel.value);
      if (pre) { input.value = pre.label; hidden.value = pre.value; }
    }
    function render(filter){
      const f = filter.toLowerCase().trim();
      const matches = (f ? opts.filter(o => o.value.toLowerCase().includes(f) || o.label.toLowerCase().includes(f)) : opts).slice(0, 50);
      if (!matches.length) {
        list.innerHTML = '<div class="searchable-empty">No matches. Press Enter to use "'+filter+'" as raw value.</div>';
      } else {
        list.innerHTML = matches.map((o, i) => '<div class="searchable-item'+(i===0?' active':'')+'" data-value="'+o.value.replace(/"/g,'&quot;')+'">'+o.label+'</div>').join('');
        list.querySelectorAll('.searchable-item').forEach(el => {
          el.addEventListener('click', () => pick(el.dataset.value, el.textContent));
        });
      }
      list.style.display = 'block';
    }
    function pick(value, label){
      input.value = label || value;
      hidden.value = value;
      list.style.display = 'none';
      sel.value = value;
      sel.dispatchEvent(new Event('change', {bubbles: true}));
    }
    input.addEventListener('focus', () => render(input.value));
    input.addEventListener('input', () => { hidden.value = input.value; render(input.value); });
    input.addEventListener('keydown', (e) => {
      const items = list.querySelectorAll('.searchable-item');
      const activeIdx = Array.from(items).findIndex(el => el.classList.contains('active'));
      if (e.key === 'ArrowDown') { e.preventDefault(); if (items.length) { items.forEach(i => i.classList.remove('active')); const ni = items[Math.min(activeIdx+1, items.length-1)]; ni.classList.add('active'); ni.scrollIntoView({block:'nearest'}); } }
      else if (e.key === 'ArrowUp') { e.preventDefault(); if (items.length) { items.forEach(i => i.classList.remove('active')); const ni = items[Math.max(activeIdx-1, 0)]; ni.classList.add('active'); ni.scrollIntoView({block:'nearest'}); } }
      else if (e.key === 'Enter') { e.preventDefault(); const act = list.querySelector('.searchable-item.active'); if (act) pick(act.dataset.value, act.textContent); else { hidden.value = input.value; list.style.display = 'none'; } }
      else if (e.key === 'Escape') { list.style.display = 'none'; }
    });
    document.addEventListener('click', (e) => { if (!wrap.contains(e.target)) list.style.display = 'none'; });
  }
  // Public API
  window.makeSelectsSearchable = function(selector) {
    document.querySelectorAll(selector).forEach(makeSearchable);
  };
  // Auto-init on .searchable selects
  document.addEventListener('DOMContentLoaded', () => {
    window.makeSelectsSearchable('select.searchable');
  });
})();
