(function () {
  'use strict';
  function localize(messages, key, variables = {}) {
    return String(messages[key] || key).replace(/\{(\w+)\}/g, function (placeholder, name) {
      return Object.prototype.hasOwnProperty.call(variables, name) ? String(variables[name]) : placeholder;
    });
  }
  function displayValue(value) {
    if (value === null || value === undefined || value === '') return '—';
    return typeof value === 'object' ? JSON.stringify(value) : String(value);
  }
  function fieldValue(value, options) {
    const option = (options || []).find((item) => String(item.value) === String(value));
    return option ? option.label + ' (' + displayValue(value) + ')' : displayValue(value);
  }
  function mayApply(preview, confirmed, decisions, busy) {
    const overwrites = (preview?.items || []).filter((item) => item.status === 'overwrite');
    return Boolean(!busy && preview && preview.status === 'preview' && preview.token &&
      preview.can_apply && !(preview.errors || []).length && confirmed &&
      overwrites.length === (preview.overwrite_count || 0) &&
      overwrites.every((item) => ['file', 'ecrf'].includes((decisions || {})[item.key])));
  }
  function requestBody(preview, decisions) {
    return { token: preview.token, confirmed: true, field_decisions: { ...decisions } };
  }
  function selectedOption(select, value, text) {
    const option = document.createElement('option');
    option.value = String(value);
    option.textContent = text;
    select.append(option);
  }
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { displayValue, fieldValue, mayApply, requestBody, localize };
  }
  if (typeof document === 'undefined') return;
  document.addEventListener('DOMContentLoaded', function () {
    const root = document.querySelector('[data-mapped-import]');
    const trigger = document.querySelector('[data-mapped-import-open]');
    if (!root || !trigger) return;
    const get = (name) => root.querySelector('[data-import-' + name + ']');
    const messages = get('translations').dataset;
    const t = (key, variables) => localize(messages, key, variables);
    const form = get('upload');
    const file = form.querySelector('[name="file"]');
    const mapping = get('mapping');
    const row = get('row');
    const confirm = get('confirm');
    const apply = get('apply');
    let preview = null;
    let busy = false;
    let overrides = {};
    let decisions = {};
    let applied = false;

    function updateButton() {
      apply.disabled = !mayApply(preview, confirm.checked, decisions, busy);
    }
    function setBusy(value) {
      busy = value;
      root.setAttribute('aria-busy', String(value));
      form.querySelectorAll('input, select, button').forEach((control) => { control.disabled = value; });
      root.querySelectorAll('[data-import-choice], [data-import-close]').forEach((control) => { control.disabled = value; });
      root.querySelectorAll('[data-import-decision]').forEach((control) => { control.disabled = value || !preview || applied; });
      confirm.disabled = value;
      updateButton();
    }
    function invalidate() {
      preview = null;
      confirm.checked = false;
      decisions = {};
      root.querySelectorAll('[data-import-decision]').forEach((control) => {
        control.checked = false;
        control.disabled = true;
        control.closest('td').classList.remove('mapped-import__value--selected');
        control.closest('tr').children[4].textContent = t('overwrite');
      });
      get('confirmation').hidden = true;
      updateButton();
    }
    function errors(messages) {
      get('errors').replaceChildren();
      messages.forEach((message) => {
        const item = document.createElement('li');
        item.textContent = String(message);
        get('errors').append(item);
      });
    }
    async function send(url, body, json) {
      const headers = { 'X-CSRFToken': form.querySelector('[name="csrfmiddlewaretoken"]').value };
      if (json) headers['Content-Type'] = 'application/json';
      let response;
      try { response = await fetch(url, { method: 'POST', credentials: 'same-origin', headers, body }); }
      catch (_) { throw new Error(t('networkError')); }
      let result;
      try { result = await response.json(); }
      catch (_) { throw new Error(t('responseError')); }
      if (!response.ok) throw new Error(result.error || result.message || t('processError'));
      return result;
    }
    function renderSelections(result) {
      mapping.replaceChildren();
      row.replaceChildren();
      selectedOption(mapping, '', t('chooseMapping'));
      (result.mappings || []).forEach((item) => selectedOption(mapping, item.id,
        (item.filename || t('mapping')) + ' · ' + String(item.id).slice(0, 8)));
      selectedOption(row, '', t('chooseRow'));
      (result.rows || []).forEach((item) => selectedOption(row, item.row_number,
        t('rowSummary', { row: item.row_number, values: (item.values || []).map(displayValue).join(' · ') })));
      mapping.value = result.selected_mapping_id || '';
      row.value = result.selected_row_number || '';
      get('selection').hidden = !(result.mappings || []).length && !(result.rows || []).length;
    }
    function renderItems(result) {
      const labels = { new: t('new'), overwrite: t('overwrite'), unchanged: t('unchanged'),
        empty: t('empty'), error: t('error'), skipped: t('skipped') };
      get('items').replaceChildren();
      (result.items || []).forEach((item) => {
        const tr = document.createElement('tr');
        tr.dataset.status = item.status;
        const values = [item.column + ' · ' + (item.header || ''),
          (item.form_code || '') + ' / ' + (item.field_label || item.field_key || ''),
          item.old_value === undefined ? t('unknown') : fieldValue(item.old_value, item.options), fieldValue(item.new_value, item.options), labels[item.status] || item.status];
        values.forEach((value) => {
          const td = document.createElement('td');
          td.textContent = value;
          tr.append(td);
        });
        const fieldDetail = document.createElement('small');
        fieldDetail.textContent = (item.field_key || '') + (item.unit ? ' · ' + t('unit', { unit: item.unit }) : '');
        tr.children[1].append(fieldDetail);
        if (item.raw_value !== undefined) {
          const raw = document.createElement('small');
          raw.textContent = t('rawValue', { value: displayValue(item.raw_value) });
          tr.children[3].append(raw);
        }
        if (item.error || item.skip_reason) {
          const detail = document.createElement('small');
          detail.textContent = item.error || item.skip_reason;
          tr.children[4].append(detail);
        }
        if (item.status === 'overwrite') {
          decisions[item.key] = 'ecrf';
          tr.children[4].textContent = t('keepEcrf');
          [['ecrf', 2, t('keepEcrf')], ['file', 3, t('useFile')]].forEach(([source, index, text]) => {
            const cell = tr.children[index];
            cell.classList.add('mapped-import__value-choice');
            const label = document.createElement('label');
            label.className = 'mapped-import__value-label';
            const decision = document.createElement('input');
            decision.type = 'radio';
            decision.name = 'import-source-' + item.key;
            decision.value = source;
            decision.checked = source === 'ecrf';
            cell.classList.toggle('mapped-import__value--selected', decision.checked);
            decision.dataset.importDecision = item.key;
            decision.setAttribute('aria-label', text + ': ' + (item.field_label || item.field_key));
            const caption = document.createElement('span');
            caption.textContent = text;
            label.append(decision, caption);
            cell.append(label);
            decision.addEventListener('change', function () {
              if (busy || !preview || applied || !decision.checked) return;
              decisions[item.key] = source;
              tr.querySelectorAll('[data-import-decision]').forEach((control) => {
                control.checked = control.value === source;
                control.closest('td').classList.toggle('mapped-import__value--selected', control.checked);
              });
              tr.children[4].textContent = text;
              confirm.checked = false;
              updateButton();
            });
            cell.addEventListener('click', function (event) {
              if (busy || !preview || applied || decision.disabled || event.target.closest('input, select, option, label, button, a')) return;
              decision.click();
            });
          });
        }
        if (item.options && item.options.length) {
          const select = document.createElement('select');
          select.dataset.importChoice = item.key;
          select.setAttribute('aria-label', t('chooseValue', { field: item.field_label || item.field_key }));
          selectedOption(select, '', item.status === 'error' ? t('resolveChoice') : t('matchedValue'));
          item.options.forEach((option) => selectedOption(select, option.value, option.label));
          if (Object.prototype.hasOwnProperty.call(overrides, item.key)) select.value = overrides[item.key];
          select.addEventListener('change', function () {
            if (select.value === '') delete overrides[item.key];
            else overrides[item.key] = select.value;
            invalidate();
            get('message').textContent = t('choiceChanged');
          });
          tr.children[3].append(select);
        }
        get('items').append(tr);
      });
      get('comparison').hidden = !(result.items || []).length;
    }
    function close() {
      if (busy) return;
      if (applied) { window.location.reload(); return; }
      root.close();
      trigger.focus();
    }
    trigger.addEventListener('click', function () {
      root.showModal();
      file.focus();
    });
    root.querySelectorAll('[data-import-close]').forEach((button) => button.addEventListener('click', close));
    root.addEventListener('cancel', function (event) {
      if (busy || applied) event.preventDefault();
      if (applied) window.location.reload();
    });
    file.addEventListener('change', function () {
      invalidate();
      overrides = {};
      mapping.replaceChildren();
      row.replaceChildren();
      get('selection').hidden = true;
      get('comparison').hidden = true;
      get('message').textContent = '';
      errors([]);
    });
    [mapping, row].forEach((select) => select.addEventListener('change', function () {
      invalidate();
      overrides = {};
      get('comparison').hidden = true;
      get('message').textContent = t('selectionChanged');
    }));
    confirm.addEventListener('change', updateButton);
    form.addEventListener('submit', async function (event) {
      event.preventDefault();
      if (busy || applied || !file.files.length) return;
      invalidate();
      errors([]);
      if (!file.files[0].name.toLowerCase().endsWith('.xlsx')) {
        errors([t('xlsxOnly')]);
        return;
      }
      const data = new FormData();
      data.append('file', file.files[0]);
      if (mapping.value) data.append('mapping_id', mapping.value);
      if (row.value) data.append('row_number', row.value);
      data.append('value_overrides', JSON.stringify(overrides));
      setBusy(true);
      get('message').textContent = t('reading');
      try {
        const result = await send(root.dataset.previewUrl, data, false);
        preview = result;
        renderSelections(result);
        renderItems(result);
        errors(result.errors || []);
        get('confirmation').hidden = result.status !== 'preview' || !result.can_apply;
        get('message').textContent = result.status === 'selection_required'
          ? t('selectionRequired')
          : result.can_apply
            ? t('ready')
            : t('notReady');
      } catch (error) {
        errors([error.message]);
        get('comparison').hidden = true;
        get('message').textContent = t('notSaved');
      } finally { setBusy(false); }
    });
    apply.addEventListener('click', async function () {
      if (!mayApply(preview, confirm.checked, decisions, busy)) return;
      const guard = window.DatacaptureUnsavedChangesGuard;
      if (guard && typeof guard.confirmDiscardUnsavedChanges === 'function' && !guard.confirmDiscardUnsavedChanges()) return;
      const body = JSON.stringify(requestBody(preview, decisions));
      setBusy(true);
      errors([]);
      try {
        const result = await send(root.dataset.applyUrl, body, true);
        applied = true;
        invalidate();
        get('message').textContent = result.message || t('applied', { count: result.applied_fields });
        get('confirmation').hidden = true;
        get('reload').hidden = false;
        apply.hidden = true;
        form.hidden = true;
      } catch (error) {
        invalidate();
        errors([error.message]);
        get('message').textContent = t('applyUnconfirmed');
      } finally { setBusy(false); }
    });
    get('reload').addEventListener('click', function () { window.location.reload(); });
  });
})();
