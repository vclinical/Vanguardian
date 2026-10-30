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
