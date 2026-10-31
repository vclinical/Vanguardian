const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.resolve(__dirname,
  '../../src/staticfiles/study/js/file_importer.js'), 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('  function normalize('), source.indexOf('  function element(')), context);

test('counts one-to-many targets separately from mapped source columns', () => {
  const result = context.mappingSummary([
    { column: 'A', targets: [{ binding_id: 1, field_id: 10 }, { binding_id: 2, field_id: 20 }], skip_reason: '' },
    { column: 'B', targets: [], skip_reason: 'Chưa có field' },
    { column: 'C', targets: [], skip_reason: '  ' },
  ]);
  assert.equal(result.mapped, 1);
  assert.equal(result.targets, 2);
  assert.equal(result.skipped, 1);
  assert.equal(result.pending, 1);
});

test('a field is unique within its binding, but can be selected under another binding', () => {
  const rows = [{ column: 'A', targets: [{ binding_id: 1, field_id: 10 }], skip_reason: '' }];
  assert.equal(context.ownerOfTarget(rows, 1, 10).column, 'A');
  assert.equal(context.ownerOfTarget(rows, 2, 10), undefined);
});

test('Vietnamese search matches accents and case', () => {
  assert.equal(context.normalize('Định lượng'), 'dinh luong');
  assert.equal(context.normalize('Hồng Cầu'), 'hong cau');
});

test('native form validation explains why clicking Read Excel does not submit', () => {
  const status = { textContent: '' };
  let invalidHandler;
  const context = vm.createContext({
    status,
    uploadForm: { addEventListener(name, callback, capture) {
      assert.equal(name, 'invalid');
      assert.equal(capture, true);
      invalidHandler = callback;
    } },
  });
  vm.runInContext(source.slice(source.indexOf("  uploadForm.addEventListener('invalid'"),
    source.indexOf("  uploadForm.addEventListener('submit'")), context);
  invalidHandler({ target: { name: 'file' } });
  assert.match(status.textContent, /Chưa chọn file Excel/);
  assert.match(status.textContent, /Mở mapping/);
  invalidHandler({ target: { name: 'header_row' } });
  assert.match(status.textContent, /1 đến 100/);
});

test('field search spans forms in one visit and saves each result with its own binding', () => {
  const makeElement = (tag, textContent = '') => ({
    tag, textContent, dataset: {}, children: [], hidden: false,
    append(...children) { this.children.push(...children); },
    replaceChildren() { this.children = []; },
    querySelectorAll(selector) {
      if (selector === 'label') return this.children;
      assert.equal(selector, 'label:not([hidden]) input:checked:not(:disabled)');
      return this.children.filter(label => !label.hidden)
        .flatMap(label => label.children.filter(node => node.tag === 'input' && node.checked && !node.disabled));
    },
  });
  let add;
  const nodes = {
    fields: makeElement('div'), binding: { value: '10' }, visit: { value: '1' },
    'field-search': { value: '' }, 'target-message': { textContent: '' },
    'add-target': { addEventListener(event, handler) { add = handler; } },
  };
  const mappings = [{ column: 'A', targets: [], skip_reason: '' },
    { column: 'B', targets: [{ binding_id: 10, field_id: 8 }], skip_reason: '' }];
  const field = { id: 7, key: 'RBC', label: 'Red blood cells', labels: ['Hồng cầu', 'Red blood cells'], type: 'DECIMAL', unit: '' };
  const scope = vm.createContext({
    normalize: context.normalize, ownerOfTarget: context.ownerOfTarget,
    element: makeElement, find: name => nodes[name], mappings,
    upload: {}, loading: false, selectedColumn: 'A', changed() {}, renderColumns() {},
    catalog: { bindings: [
      { id: 10, visit_id: 1, form_code: 'BLOOD', fields: [field, { ...field, id: 8 }, { ...field, id: 9, key: 'WBC', label: 'Bạch cầu', labels: ['Bạch cầu'] }] },
      { id: 11, visit_id: 1, form_code: 'URINE', fields: [field] },
      { id: 12, visit_id: 2, form_code: 'OTHER_VISIT', fields: [field] },
    ] },
  });
  vm.runInContext(source.slice(source.indexOf('  function selectedBinding('),
    source.indexOf('  function openTargets(')), scope);
  vm.runInContext(source.slice(source.indexOf("  find('add-target').addEventListener"),
    source.indexOf("  find('close-target').addEventListener")), scope);
  scope.renderFields();
  assert.equal(nodes.fields.children.length, 4, 'never load results from another visit');
  assert.equal(nodes.fields.children.filter(label => !label.hidden).length, 3);
  nodes.fields.children[2].children[0].checked = true;
  nodes['field-search'].value = 'hong cau';
  scope.filterFields();
  const results = nodes.fields.children.filter(label => !label.hidden);
  assert.equal(results.length, 3, 'search by unaccented name across both forms');
  assert.equal(results[1].children[0].disabled, true, 'already mapped targets stay disabled');
  assert.match(results[2].children[1].textContent, /URINE/);
  assert.equal(nodes.binding.disabled, true);
  results[0].children[0].checked = true;
  results[2].children[0].checked = true;
  add();
  assert.deepEqual(mappings[0].targets.map(target => [target.binding_id, target.field_id]), [[10, 7], [11, 7]]);
  assert.equal(mappings[0].targets.some(target => target.field_id === 9), false, 'hidden selections are not added');
  nodes['field-search'].value = 'missing';
  scope.filterFields();
  assert.match(nodes['target-message'].textContent, /Không tìm thấy field/);
  nodes['field-search'].value = '';
  scope.filterFields();
  assert.equal(nodes.binding.disabled, false);
  assert.equal(nodes.fields.children.filter(label => !label.hidden).length, 3);
  nodes.visit.value = '2';
  nodes['field-search'].value = 'RBC';
  scope.renderFields();
  assert.equal(nodes.fields.children.length, 1);
  assert.equal(nodes.fields.children[0].dataset.bindingId, '12');
});

test('suggests exact translated labels using field codes and keeps all visit bindings', () => {
  const bindings = [
    { id: 10, visit_code: 'SCREENING', form_code: 'BLOOD', fields: [
      { id: 1, key: 'LAB_BIOCHEM_GPT', label: 'ALT (GPT)', labels: ['ALT (GPT)'] },
      { id: 2, key: 'LAB_BIOCHEM_GOT', label: 'AST (GOT)' },
      { id: 3, key: 'RBC', label: 'Red blood cells', labels: ['Hồng cầu'] },
    ] },
    { id: 11, visit_code: 'VISIT1', form_code: 'BLOOD', fields: [
      { id: 4, key: 'RBC', label: 'Hồng cầu' },
    ] },
  ];
  const result = context.suggestMappings([
    { column: 'B', header: 'ALT (GPT)' }, { column: 'C', header: 'AST (GOT)' },
    { column: 'D', header: ' hong   cau ' }, { column: 'E', header: 'Unknown' },
  ], bindings);
  assert.equal(result[0].targets[0].field_key, 'LAB_BIOCHEM_GPT');
  assert.equal(result[1].targets[0].field_key, 'LAB_BIOCHEM_GOT');
  assert.equal(result[2].targets.length, 2);
  assert.equal(result[2].targets[1].binding_id, 11);
  assert.equal(result[3].targets.length, 0);
});

test('does not suggest ambiguous labels, duplicate columns or competing translations', () => {
  const bindings = [{ id: 10, fields: [
    { id: 1, key: 'A', label: 'Same' }, { id: 2, key: 'B', label: 'Same' },
    { id: 3, key: 'RBC', label: 'Red blood cells', labels: ['Hồng cầu'] },
    { id: 4, key: 'ALT', label: 'ALT (GPT)' },
  ] }];
  const result = context.suggestMappings([
    { column: 'A', header: 'Same' }, { column: 'B', header: 'Red blood cells' },
    { column: 'C', header: 'Hồng cầu' }, { column: 'D', header: 'ALT (GPT)' },
    { column: 'E', header: 'ALT (GPT)' }, { column: 'F', header: 'ALT' },
  ], bindings);
  assert.equal(result.every(row => row.targets.length === 0), true);
});

function editHarness() {
  const handlers = {};
  const nodes = {};
  const find = name => nodes[name] ||= {
    checked: false, disabled: false, hidden: false, textContent: '',
    addEventListener(event, callback) { handlers[name] = callback; },
  };
  const requests = [];
  const scope = vm.createContext({
    find, loading: false, editing: false, dirty: false, upload: null, mappings: [],
    catalog: { bindings: [{ id: 1, fields: [{ id: 10 }, { id: 11 }] }] }, history: [], api: '/api/imports/', csrf: 'csrf-token',
    status: { textContent: '' }, mappingSummary: context.mappingSummary, unavailableTargets: context.unavailableTargets,
    suggestMappings: context.suggestMappings, window: { confirm: () => true },
    renderColumns() { scope.updateSummary(); }, renderHistory() {},
    setLoading(value) { scope.loading = value; scope.updateSummary(); },
    async request(url, options) {
      if (!options) {
        if (scope.failCatalog) throw new Error('Không tải được cấu hình');
        return { catalog: scope.freshCatalog || scope.catalog };
      }
      requests.push({ url, ...options });
      if (scope.failSave) throw new Error('Mapping đã thay đổi');
      return { mappings: JSON.parse(options.body).mappings, confirmed_at: 'v2', confirmed_by: 9 };
    },
  });
  vm.runInContext(source.slice(source.indexOf('  function updateSummary('),
    source.indexOf('  function setLoading(')), scope);
  vm.runInContext(source.slice(source.indexOf('  function showUpload('),
    source.indexOf('  function selectedBinding(')), scope);
  vm.runInContext(source.slice(source.indexOf("  find('edit').addEventListener"),
    source.indexOf('  async function initialize(')), scope);
  const record = { id: 'upload', filename: 'results.xlsx',
    preview: { sheet: 'Results', header_row: 2, row_count: 1, columns: [{ column: 'A' }] },
    confirmation: { confirmed_at: 'v1', mappings: [
      { column: 'A', targets: [{ binding_id: 1, field_id: 10 }], skip_reason: '' },
    ] },
  };
  scope.showUpload(record);
  return { scope, handlers, nodes, requests, record };
}

test('confirmed mapping can be edited and cancel restores saved targets without mutating them', async () => {
  const { scope, handlers, nodes, record } = editHarness();
  assert.equal(nodes.edit.hidden, false);
  assert.equal(nodes.acknowledge.disabled, true);
  await handlers.edit();
  assert.equal(scope.editing, true);
  assert.equal(nodes.acknowledge.disabled, false);
  assert.equal(nodes['cancel-edit'].hidden, false);
  scope.mappings[0].targets[0].field_id = 11;
  scope.changed();
  assert.equal(record.confirmation.mappings[0].targets[0].field_id, 10);
  scope.window.confirm = () => false;
  handlers['cancel-edit']();
  assert.equal(scope.editing, true, 'declining discard keeps edits');
  scope.window.confirm = () => true;
  handlers['cancel-edit']();
  assert.equal(scope.mappings[0].targets[0].field_id, 10);
  assert.equal(scope.dirty, false);
  assert.equal(scope.editing, false);
  assert.equal(nodes.confirm.disabled, true);
});

test('saving edits requires acknowledgement and sends the original version with the updated mapping', async () => {
  const { scope, handlers, nodes, requests } = editHarness();
  await handlers.edit();
  scope.mappings[0].targets[0].field_id = 11;
  scope.changed();
  await handlers.confirm();
  assert.equal(requests.length, 0);
  nodes.acknowledge.checked = true;
  await handlers.confirm();
  assert.equal(requests.length, 1);
  const body = JSON.parse(requests[0].body);
  assert.equal(body.expected_confirmed_at, 'v1');
  assert.equal(body.mappings[0].targets[0].field_id, 11);
  assert.equal(requests[0].headers['X-CSRFToken'], 'csrf-token');
  assert.equal(scope.upload.confirmation.confirmed_at, 'v2');
  assert.equal(scope.editing, false);
  assert.equal(scope.dirty, false);
});

test('failed edit keeps the draft and original version available for retry or cancel', async () => {
  const { scope, handlers, nodes } = editHarness();
  await handlers.edit();
  scope.mappings[0].targets[0].field_id = 11;
  scope.changed();
  nodes.acknowledge.checked = true;
  scope.failSave = true;
  await handlers.confirm();
  assert.equal(scope.editing, true);
  assert.equal(scope.dirty, true);
  assert.equal(scope.loading, false);
  assert.equal(scope.mappings[0].targets[0].field_id, 11);
  assert.equal(scope.upload.confirmation.confirmed_at, 'v1');
  assert.match(scope.status.textContent, /Mapping đã thay đổi/);
});

test('refreshing catalog identifies stale targets and explicit cleanup keeps valid targets', async () => {
  const { scope, handlers, nodes, record, requests } = editHarness();
  record.confirmation.mappings[0].targets.push({ binding_id: 2, field_id: 20 });
  scope.showUpload(record);
  scope.freshCatalog = { bindings: [{ id: 2, fields: [{ id: 20 }] }] };
  await handlers.edit();
  assert.equal(scope.catalog, scope.freshCatalog);
  assert.match(nodes.summary.textContent, /1 field không còn hợp lệ ở cột A/);
  assert.equal(nodes['remove-unavailable'].hidden, false);
  nodes.acknowledge.checked = true;
  await handlers.confirm();
  assert.equal(requests.length, 0, 'stale targets cannot be submitted');
  scope.window.confirm = () => false;
  handlers['remove-unavailable']();
  assert.equal(scope.mappings[0].targets.length, 2);
  scope.window.confirm = () => true;
  handlers['remove-unavailable']();
  assert.equal(scope.mappings[0].targets.length, 1);
  assert.equal(scope.mappings[0].targets[0].binding_id, 2);
  assert.equal(record.confirmation.mappings[0].targets.length, 2, 'saved version stays intact');
  assert.equal(nodes.acknowledge.checked, false);
  nodes.acknowledge.checked = true;
  await handlers.confirm();
  assert.equal(requests.length, 1);
});

test('removing the last stale target requires an explicit column decision', async () => {
  const { scope, handlers, nodes } = editHarness();
  scope.freshCatalog = { bindings: [] };
  await handlers.edit();
  handlers['remove-unavailable']();
  assert.equal(scope.mappings[0].targets.length, 0);
  assert.equal(scope.mappings[0].skip_reason, '');
  nodes.acknowledge.checked = true;
  scope.updateSummary();
  assert.equal(nodes.confirm.disabled, true);
  handlers['cancel-edit']();
  assert.equal(scope.mappings[0].targets[0].field_id, 10);
});

test('catalog refresh failure does not enable editing with stale configuration', async () => {
  const { scope, handlers } = editHarness();
  scope.failCatalog = true;
  await handlers.edit();
  assert.equal(scope.editing, false);
  assert.equal(scope.loading, false);
  assert.match(scope.status.textContent, /Không tải được cấu hình/);
});
