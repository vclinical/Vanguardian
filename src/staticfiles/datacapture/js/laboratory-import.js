(() => {
  const dialog = document.querySelector('[data-lab-import-dialog]');
  if (!dialog) return;
  const form = dialog.querySelector('form');
  const status = dialog.querySelector('[data-lab-import-status]');
  const tbody = dialog.querySelector('[data-lab-import-rows]');
  const confirm = dialog.querySelector('[data-lab-import-confirm]');
  const apply = dialog.querySelector('[data-lab-import-apply]');
  let pending = [];

  function resetPreview() {
    pending = [];
    tbody.replaceChildren();
    confirm.checked = false;
    apply.disabled = true;
    status.textContent = '';
  }

  function targetFor(item) {
    if (!item.field_key) return null;
    const fields = [...document.querySelectorAll('.subject-form-panel [data-field-id]')]
      .filter(field => field.dataset.fieldKey === item.field_key);
    if (fields.length !== 1) return null;
    const field = fields[0];
    const controls = [...field.querySelectorAll('input:not([type="hidden"]), select, textarea')];
    if (!controls.length || controls.some(control => control.disabled || control.readOnly)) return null;
    if (!field.getClientRects().length) return null;
    const radio = controls.every(control => control.type === 'radio');
    const control = radio ? controls.find(input => input.value === item.value) : controls[0];
    if (!control || (!radio && controls.length !== 1)) return null;
    if (control.tagName === 'SELECT' && ![...control.options].some(option => option.value === item.value)) return null;
    if (!radio && !['text', 'number', 'select-one', 'textarea'].includes(control.type)) return null;
    const old = radio ? (controls.find(input => input.checked)?.value || '') : control.value;
    const label = field.querySelector('[data-field-label]')?.dataset.fieldLabel || item.field_key;
    return { field, controls, control, radio, old, label };
  }

  document.querySelector('[data-lab-import-open]').addEventListener('click', () => {
    resetPreview();
    dialog.showModal();
  });
  dialog.querySelector('[data-lab-import-close]').addEventListener('click', () => dialog.close());
  form.elements.file.addEventListener('change', resetPreview);
  confirm.addEventListener('change', () => { apply.disabled = !confirm.checked || !pending.length; });

  form.addEventListener('submit', async event => {
    event.preventDefault();
    resetPreview();
    const file = form.elements.file.files[0];
    if (!file || file.size > 2 * 1024 * 1024) {
      status.textContent = 'Chọn file .xlsx không quá 2 MB.';
      return;
    }
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    form.elements.file.disabled = true;
    status.textContent = 'Đang đọc kết quả…';
    try {
      const body = new FormData();
      body.append('file', file);
      body.append('csrfmiddlewaretoken', form.elements.csrfmiddlewaretoken.value);
      const response = await fetch(form.action, { method: 'POST', body, credentials: 'same-origin' });
      if (response.redirected) throw new Error('Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.');
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Không thể đọc file hoặc không có quyền import.');
      for (const item of data.results) {
        const target = targetFor(item);
        if (target) pending.push({ item, old: target.old });
        const row = document.createElement('tr');
        const cells = [
          `${item.cell}: ${item.header}`, item.raw, target?.label || item.field_key || '—',
          target ? `${target.old || '(trống)'} → ${item.value}` : '—',
          item.warning || (target ? 'Sẽ điền' : 'Bỏ qua: không thuộc biểu mẫu hoặc trường đang ẩn/khóa.'),
        ];
        for (const text of cells) {
          const cell = document.createElement('td');
          cell.textContent = text;
          row.appendChild(cell);
        }
        tbody.appendChild(row);
      }
      status.textContent = `${pending.length}/${data.results.length} kết quả có thể điền vào biểu mẫu đang mở. Chưa lưu dữ liệu.`;
    } catch (error) {
      pending = [];
      status.textContent = error.message;
    } finally {
      submit.disabled = false;
      form.elements.file.disabled = false;
    }
  });

  apply.addEventListener('click', () => {
    if (!confirm.checked || !pending.length) return;
    const targets = pending.map(({ item, old }) => ({ item, old, target: targetFor(item) }));
    if (targets.some(({ target, old }) => !target || target.old !== old)) {
      resetPreview();
      status.textContent = 'Biểu mẫu đã thay đổi. Hãy xem trước lại trước khi điền.';
      return;
    }
    for (const { item, target } of targets) {
      if (target.radio) {
        target.controls.forEach(control => { control.checked = control === target.control; });
      } else {
        target.control.value = item.value;
      }
    }
    // Change refreshes form behavior without the number-input sanitizer clamping lab results.
    for (const { target } of targets) target.control.dispatchEvent(new Event('change', { bubbles: true }));
    pending = [];
    apply.disabled = true;
    status.textContent = 'Đã điền vào biểu mẫu, chưa lưu. Đóng hộp thoại, kiểm tra và nhấn Save để lưu nháp.';
  });
})();
