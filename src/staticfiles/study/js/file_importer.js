(() => {
  const root = document.querySelector('[data-file-importer]');
  if (!root) return;
  const find = name => root.querySelector(`[data-import-${name}]`);
  const api = root.dataset.apiUrl;
  const uploadForm = find('upload');
  const csrf = uploadForm.elements.csrfmiddlewaretoken.value;
  const status = find('status');
  const dialog = find('target-dialog');
  let catalog = { events: [], bindings: [] };
  let history = [];
  let upload = null;
  let mappings = [];
  let selectedColumn = null;
  let loading = false;
  let dirty = false;
  let editing = false;

  function normalize(value) {
    return String(value || '').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/đ/g, 'd');
  }

  function mappingSummary(rows) {
    return {
      mapped: rows.filter(row => row.targets.length).length,
      skipped: rows.filter(row => !row.targets.length && row.skip_reason.trim()).length,
      pending: rows.filter(row => !row.targets.length && !row.skip_reason.trim()).length,
      targets: rows.reduce((count, row) => count + row.targets.length, 0),
    };
  }

  function ownerOfTarget(rows, bindingId, fieldId) {
    return rows.find(row => row.targets.some(target => target.binding_id === bindingId && target.field_id === fieldId));
  }

  function suggestMappings(columns, bindings) {
    const labelKey = value => normalize(value).trim().replace(/\s+/g, ' ');
    const rows = columns.map(column => ({ column: column.column, targets: [], skip_reason: '' }));
    columns.forEach((column, index) => {
      const header = labelKey(column.header);
      if (!header || columns.filter(item => labelKey(item.header) === header).length !== 1) return;
      const matches = bindings.flatMap(binding => binding.fields
        .filter(field => [field.label, ...(field.labels || [])].some(label => labelKey(label) === header))
        .map(field => ({ binding, field })));
      if (new Set(matches.map(match => match.field.key)).size !== 1) return;
      rows[index].targets = matches.map(({ binding, field }) => ({
        binding_id: binding.id, field_id: field.id, visit_code: binding.visit_code,
        form_code: binding.form_code, field_key: field.key, field_label: field.label,
        data_type: field.type, unit: field.unit,
      }));
    });
    // Two different labels can refer to the same field; leave both columns for manual review.
    const counts = new Map();
    rows.forEach(row => row.targets.forEach(target => {
      const key = `${target.binding_id}:${target.field_id}`;
      counts.set(key, (counts.get(key) || 0) + 1);
    }));
    rows.forEach(row => {
      row.targets = row.targets.filter(target => counts.get(`${target.binding_id}:${target.field_id}`) === 1);
    });
    return rows;
  }

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function button(text, action) {
    const node = element('button', text, 'entity-table__footer-button');
    node.type = 'button';
    node.addEventListener('click', action);
    return node;
  }

  async function request(url, options = {}) {
    const response = await fetch(url, { credentials: 'same-origin', ...options });
    if (response.redirected) throw new Error('Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.');
    if (!(response.headers.get('content-type') || '').includes('application/json')) {
      throw new Error(`Không thực hiện được yêu cầu (${response.status}). Kiểm tra quyền truy cập hoặc tải lại trang.`);
    }
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Không có quyền hoặc yêu cầu không hợp lệ.');
    return data;
  }

  function updateSummary() {
    const summary = mappingSummary(mappings);
    find('summary').textContent = `${summary.mapped} cột đã map → ${summary.targets} field đích · ${summary.skipped} cột bỏ qua · ${summary.pending} cột cần xác nhận.`;
    const saved = !!upload?.confirmation && !editing;
    find('edit').hidden = !upload?.confirmation || editing;
    find('edit').disabled = loading;
    find('cancel-edit').hidden = !editing;
    find('cancel-edit').disabled = loading;
    find('confirm').textContent = editing ? 'Xác nhận và lưu thay đổi' : 'Xác nhận và lưu mapping';
    find('acknowledge').disabled = loading || saved;
    find('confirm').disabled = loading || saved || !find('acknowledge').checked || summary.pending > 0 || summary.targets === 0;
    find('skip-empty').disabled = loading || saved;
    find('skip-reason').disabled = loading || saved;
  }

  function changed() {
    dirty = true;
    find('acknowledge').checked = false;
    updateSummary();
  }

  function setLoading(value) {
    loading = value;
    root.setAttribute('aria-busy', String(value));
    uploadForm.querySelector('button').disabled = value;
    [...uploadForm.elements].forEach(control => { if (control.type !== 'hidden') control.disabled = value; });
    find('reopen').disabled = value;
    find('history').disabled = value;
    if (upload) renderColumns();
    else updateSummary();
  }

  function renderHistory() {
    const select = find('history');
    select.replaceChildren(new Option('Chọn file để mở lại', ''));
    history.forEach(item => select.add(new Option(`${item.filename} · ${item.confirmed ? 'Đã xác nhận' : 'Chưa xác nhận'} · ${item.uploaded_at.slice(0, 16)}`, item.id)));
    if (upload) select.value = upload.id;
  }

  function filterColumns() {
    const search = normalize(find('search').value);
    find('columns').querySelectorAll('tr').forEach(row => { row.hidden = !normalize(row.dataset.search).includes(search); });
  }

  function renderColumns() {
    const body = find('columns');
    body.replaceChildren();
    const saved = !!upload.confirmation && !editing;
    for (const column of upload.preview.columns) {
      const mapping = mappings.find(row => row.column === column.column);
      const row = element('tr');
      row.dataset.search = `${column.column} ${column.header}`;
      row.append(element('td', column.column), element('td', column.header));
      const examples = element('td');
      column.samples.forEach(sample => examples.append(element('p', `${sample.cell}: ${sample.value}${sample.formula ? ' (công thức, chưa tính giá trị)' : ''}`)));
      if (!column.samples.length) examples.textContent = 'Cột chưa có dữ liệu';
      row.append(examples);
      const destinations = element('td');
      for (const target of mapping.targets) {
        const chip = element('div', undefined, 'file-importer__target');
        chip.append(element('strong', `${target.visit_code} · ${target.field_key}`));
        chip.append(element('small', target.field_label));
        chip.append(element('small', target.form_code));
        chip.append(element('small', `${target.data_type} · Đơn vị: ${target.unit || 'chưa cấu hình / định tính'}`));
        if (!saved) chip.append(button('Bỏ field này', () => {
          if (loading) return;
          mapping.targets = mapping.targets.filter(item => item !== target);
          changed(); renderColumns();
        }));
        destinations.append(chip);
      }
      if (!mapping.targets.length) destinations.textContent = 'Chưa chọn field';
      row.append(destinations);
      const skipCell = element('td');
      const skip = element('input');
      skip.type = 'text'; skip.maxLength = 500;
      skip.value = mapping.skip_reason;
      skip.placeholder = 'Ghi lý do nếu không map';
      skip.setAttribute('aria-label', `Lý do bỏ qua cột ${column.column}`);
      skip.disabled = loading || saved || mapping.targets.length > 0;
      skip.addEventListener('input', () => { mapping.skip_reason = skip.value; changed(); });
      skipCell.append(skip); row.append(skipCell);
      const actions = element('td');
      if (!saved) actions.append(button('Thêm field', () => { if (!loading) openTargets(column); }));
      else actions.textContent = 'Đã xác nhận';
      row.append(actions); body.append(row);
    }
    filterColumns(); updateSummary();
  }

  function showUpload(record) {
    dirty = false;
    editing = false;
    upload = record;
    mappings = record.confirmation
      ? JSON.parse(JSON.stringify(record.confirmation.mappings))
      : suggestMappings(record.preview.columns, catalog.bindings);
    dirty = !record.confirmation && mappings.some(row => row.targets.length);
    find('workspace').hidden = false;
    find('filename').textContent = record.filename;
    find('source-info').textContent = `Sheet: ${record.preview.sheet} · Tiêu đề dòng ${record.preview.header_row} · ${record.preview.row_count} dòng dữ liệu · ${record.preview.columns.length} cột. Các ô mẫu giữ nguyên nội dung Excel.`;
    find('acknowledge').checked = false;
    renderColumns();
    status.textContent = record.confirmation
      ? `Mapping đã xác nhận lúc ${record.confirmation.confirmed_at}. Chọn Chỉnh sửa mapping để thay đổi field đích hoặc lý do bỏ qua.`
      : `Đã gợi ý ${mappingSummary(mappings).mapped} cột theo label trùng khớp trong các Visit. Hãy kiểm tra các Visit/field đích, sửa hoặc xóa gợi ý trước khi xác nhận. Các cột chưa khớp cần chọn field hoặc ghi lý do bỏ qua.`;
  }

  function selectedBinding() {
    return catalog.bindings.find(item => item.id === Number(find('binding').value));
  }

  function filterFields() {
    const search = normalize(find('field-search').value.trim());
    const binding = selectedBinding();
    find('binding').disabled = !!search;
    let count = 0;
    find('fields').querySelectorAll('label').forEach(label => {
      label.hidden = search
        ? !normalize(label.dataset.search).includes(search)
        : Number(label.dataset.bindingId) !== binding?.id;
      if (!label.hidden) count += 1;
    });
    find('target-message').textContent = count
      ? (search ? `${count} field khớp trong các biểu mẫu của Visit. Chọn field rồi nhấn Thêm.`
        : 'Chọn một hoặc nhiều field, nhấn Thêm; sau đó có thể chọn Visit khác cho cùng cột.')
      : (search ? 'Không tìm thấy field khớp trong Visit này.'
        : 'Biểu mẫu này chưa có field hoặc Visit chưa có biểu mẫu được gắn và bật.');
  }

  function renderFields() {
    const list = find('fields'); list.replaceChildren();
    for (const binding of catalog.bindings.filter(item => item.visit_id === Number(find('visit').value))) {
      for (const field of binding.fields) {
        const owner = ownerOfTarget(mappings, binding.id, field.id);
        const label = element('label');
        label.dataset.search = `${field.key} ${field.label} ${(field.labels || []).join(' ')}`;
        label.dataset.bindingId = String(binding.id);
        const checkbox = element('input'); checkbox.type = 'checkbox'; checkbox.value = String(field.id);
        checkbox.dataset.bindingId = String(binding.id);
        checkbox.disabled = !!owner;
        label.append(checkbox, element('span', `${binding.form_code} · ${field.key} — ${field.label} (${field.type}; ${field.unit || 'chưa có đơn vị / định tính'})${owner ? ` · Đã map từ cột ${owner.column}` : ''}`));
        list.append(label);
      }
    }
    filterFields();
  }

  function renderBindings() {
    const select = find('binding'); select.replaceChildren();
    catalog.bindings.filter(item => item.visit_id === Number(find('visit').value))
      .forEach(item => select.add(new Option(item.form_code, item.id)));
    renderFields();
  }

  function openTargets(column) {
    selectedColumn = column.column;
    find('target-source').textContent = `Cột ${column.column}: ${column.header}`;
    const previousVisit = find('visit').value;
    find('visit').replaceChildren();
    catalog.events.forEach(event => find('visit').add(new Option(`${event.code} — ${event.name}`, event.id)));
    if ([...find('visit').options].some(option => option.value === previousVisit)) find('visit').value = previousVisit;
    find('field-search').value = '';
    renderBindings();
    dialog.showModal();
  }

  find('add-target').addEventListener('click', () => {
    if ((upload.confirmation && !editing) || loading) return;
    const selected = [...find('fields').querySelectorAll('label:not([hidden]) input:checked:not(:disabled)')];
    if (!selected.length) { find('target-message').textContent = 'Chọn ít nhất một field chưa được map.'; return; }
    const mapping = mappings.find(item => item.column === selectedColumn);
    for (const node of selected) {
      const binding = catalog.bindings.find(item => item.id === Number(node.dataset.bindingId)
        && item.visit_id === Number(find('visit').value));
      const field = binding?.fields.find(item => item.id === Number(node.value));
      if (!field) continue;
      if (ownerOfTarget(mappings, binding.id, field.id)) continue;
      mapping.targets.push({ binding_id: binding.id, field_id: field.id, visit_code: binding.visit_code,
        form_code: binding.form_code, field_key: field.key, field_label: field.label, data_type: field.type, unit: field.unit });
    }
    mapping.skip_reason = '';
    changed(); renderColumns(); renderFields();
    find('target-message').textContent = `Đã thêm ${selected.length} field cho cột ${selectedColumn}. Có thể chọn Visit khác để thêm tiếp, hoặc Đóng.`;
  });
  find('close-target').addEventListener('click', () => dialog.close());
  find('visit').addEventListener('change', renderBindings);
  find('binding').addEventListener('change', renderFields);
  find('field-search').addEventListener('input', filterFields);
  find('search').addEventListener('input', filterColumns);
  find('acknowledge').addEventListener('change', updateSummary);
  find('skip-empty').addEventListener('click', () => {
    if (loading || (upload.confirmation && !editing)) return;
    const reason = find('skip-reason').value.trim();
    if (!reason) { status.textContent = 'Nhập lý do trước khi bỏ qua các cột chưa map.'; return; }
    mappings.filter(row => !row.targets.length && !row.skip_reason.trim()).forEach(row => { row.skip_reason = reason; });
    changed(); renderColumns();
  });
  uploadForm.elements.file.addEventListener('change', () => {
    uploadForm.elements.sheet_name.replaceChildren(new Option('Sheet đầu tiên', ''));
  });

  uploadForm.addEventListener('invalid', event => {
    status.textContent = event.target.name === 'file'
      ? 'Chưa chọn file Excel. Hãy chọn file .xlsx ở phía trên; nếu dùng file đã tải, chọn file trong danh sách rồi bấm “Mở mapping”.'
      : 'Dòng tiêu đề phải là số nguyên từ 1 đến 100. Với ket_qua_ntn.xlsx, nhập 2.';
  }, true);

  uploadForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (loading) return;
    if (dirty && !window.confirm('Mapping hiện tại chưa được xác nhận. Đọc file mới sẽ bỏ các lựa chọn chưa lưu. Tiếp tục?')) return;
    const body = new FormData(uploadForm);
    setLoading(true); status.textContent = 'Đang đọc file Excel…';
    try {
      const record = await request(api, { method: 'POST', body });
      const sheetSelect = uploadForm.elements.sheet_name;
      sheetSelect.replaceChildren();
      record.preview.sheets.forEach(name => sheetSelect.add(new Option(name, name)));
      sheetSelect.value = record.preview.sheet;
      history.unshift({ id: record.id, filename: record.filename, uploaded_at: record.uploaded_at, confirmed: false });
      showUpload(record); renderHistory();
    } catch (error) { status.textContent = error.message; }
    finally { setLoading(false); }
  });

  find('reopen').addEventListener('click', async () => {
    const id = find('history').value;
    if (!id || loading) return;
    if (dirty && !window.confirm('Mapping hiện tại chưa được xác nhận. Mở file khác sẽ bỏ các lựa chọn chưa lưu. Tiếp tục?')) return;
    setLoading(true); status.textContent = 'Đang mở mapping…';
    try { showUpload(await request(`${api}${encodeURIComponent(id)}/`)); }
    catch (error) { status.textContent = error.message; }
    finally { setLoading(false); }
  });

  find('edit').addEventListener('click', () => {
    if (loading || !upload?.confirmation || editing) return;
    editing = true;
    find('acknowledge').checked = false;
    renderColumns();
    status.textContent = 'Đang chỉnh sửa mapping. Xác nhận và lưu thay đổi để áp dụng cho lần nhập tiếp theo; dữ liệu đã nhập không thay đổi.';
  });

  find('cancel-edit').addEventListener('click', () => {
    if (loading || !editing) return;
    if (dirty && !window.confirm('Bỏ các thay đổi mapping chưa lưu?')) return;
    showUpload(upload);
  });

  find('confirm').addEventListener('click', async () => {
    const summary = mappingSummary(mappings);
    if (loading || !upload || (upload.confirmation && !editing) || !find('acknowledge').checked || summary.pending || !summary.targets) return;
    setLoading(true); status.textContent = 'Đang kiểm tra Visit/field và lưu mapping…';
    try {
      const confirmation = await request(`${api}${upload.id}/confirm/`, { method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
        body: JSON.stringify({ confirmed: true, mappings,
          ...(editing ? { expected_confirmed_at: upload.confirmation.confirmed_at } : {}),
        }),
      });
      upload.confirmation = confirmation;
      const item = history.find(record => record.id === upload.id);
      if (item) item.confirmed = true;
      showUpload(upload); renderHistory();
      status.textContent = 'Đã xác nhận và lưu mapping. Chưa ghi kết quả vào hồ sơ người tham gia.';
    } catch (error) { status.textContent = error.message; }
    finally { setLoading(false); }
  });

  async function initialize() {
    setLoading(true);
    try {
      const data = await request(api);
      catalog = data.catalog; history = data.uploads;
      renderHistory();
      status.textContent = 'Chọn file Excel. Với ket_qua_ntn.xlsx, dòng tiêu đề là dòng 2.';
    } catch (error) { status.textContent = error.message; }
    finally { setLoading(false); }
  }
  window.addEventListener('beforeunload', event => {
    if (!dirty) return;
    event.preventDefault();
    event.returnValue = '';
  });
  initialize();
})();
