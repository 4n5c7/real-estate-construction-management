// 共通 JS

// タブ切り替え
function activateTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
  const btn = document.querySelector(`.tab-btn[data-tab="${tabId}"]`);
  const content = document.getElementById(tabId);
  if (btn) btn.classList.add('active');
  if (content) content.classList.add('active');
}

document.addEventListener('DOMContentLoaded', () => {
  // タブクリック
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => activateTab(btn.dataset.tab));
  });

  // URL ハッシュからタブ復元
  if (location.hash) {
    const tabId = location.hash.replace('#', '');
    if (document.getElementById(tabId)) activateTab(tabId);
  }

  // 削除確認
  document.querySelectorAll('form[data-confirm]').forEach(f => {
    f.addEventListener('submit', e => {
      if (!confirm(f.dataset.confirm)) e.preventDefault();
    });
  });

  // テーブルのソート (簡易: クリックで昇降切替)
  document.querySelectorAll('table.data.sortable th').forEach((th, idx) => {
    th.style.cursor = 'pointer';
    th.addEventListener('click', () => {
      const tbody = th.closest('table').querySelector('tbody');
      const rows = Array.from(tbody.querySelectorAll('tr'));
      const asc = !th.classList.contains('asc');
      document.querySelectorAll('table.data.sortable th').forEach(x => {
        x.classList.remove('asc', 'desc');
      });
      th.classList.add(asc ? 'asc' : 'desc');
      rows.sort((a, b) => {
        const av = a.children[idx]?.innerText.trim() || '';
        const bv = b.children[idx]?.innerText.trim() || '';
        const anum = parseFloat(av.replace(/,/g, ''));
        const bnum = parseFloat(bv.replace(/,/g, ''));
        if (!isNaN(anum) && !isNaN(bnum)) return asc ? anum - bnum : bnum - anum;
        return asc ? av.localeCompare(bv, 'ja') : bv.localeCompare(av, 'ja');
      });
      rows.forEach(r => tbody.appendChild(r));
    });
  });
});
