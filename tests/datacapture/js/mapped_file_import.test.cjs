const assert = require('node:assert/strict');
const { test } = require('node:test');
const { displayValue, fieldValue, mayApply, requestBody, localize } = require('../../../src/staticfiles/datacapture/js/mapped-file-import.js');
const ready = { status: 'preview', token: 'signed', can_apply: true, errors: [], overwrite_count: 0 };

test('zero and false remain visible rather than appearing as missing data', () => {
  assert.equal(displayValue(0), '0');
  assert.equal(displayValue(false), 'false');
  assert.equal(displayValue(null), '—');
  assert.equal(displayValue(''), '—');
});
test('general confirmation is required even when filling empty fields', () => {
  assert.equal(mayApply(ready, false, {}, false), false);
  assert.equal(mayApply(ready, true, {}, false), true);
});
test('each changed field requires a file or eCRF decision plus general confirmation', () => {
  const overwrite = { ...ready, overwrite_count: 2, items: [
    { key: '2:4', status: 'overwrite' }, { key: '2:5', status: 'overwrite' },
  ] };
  assert.equal(mayApply(overwrite, true, {}, false), false);
  assert.equal(mayApply(overwrite, true, { '2:4': 'file' }, false), false);
  assert.equal(mayApply(overwrite, true, { '2:4': 'file', '2:5': 'invalid' }, false), false);
  const decisions = { '2:4': 'file', '2:5': 'ecrf' };
  assert.equal(mayApply(overwrite, true, decisions, false), true);
  assert.equal(mayApply(overwrite, false, decisions, false), false);
  assert.equal(mayApply(overwrite, true, { '2:4': 'ecrf', '2:5': 'ecrf' }, false), true);
});
test('no apply for unresolved selection, errors, invalidated token, or active request', () => {
  [null, { ...ready, status: 'selection_required' }, { ...ready, token: '' },
    { ...ready, can_apply: false }, { ...ready, errors: ['Locked'] }].forEach((value) => {
    assert.equal(mayApply(value, true, true, false), false);
  });
  assert.equal(mayApply(ready, true, true, true), false);
});
test('apply sends signed preview and explicit decisions, never editable clinical payload', () => {
  assert.deepEqual(requestBody(ready, {}), { token: 'signed', confirmed: true, field_decisions: {} });
  assert.deepEqual(requestBody(ready, { '2:4': 'ecrf' }), { token: 'signed', confirmed: true, field_decisions: { '2:4': 'ecrf' } });
});

test('choice comparisons show both approved label and stored code', () => {
  assert.equal(fieldValue('0', [{ value: '0', label: 'Negative' }]), 'Negative (0)');
  assert.equal(fieldValue('unknown', [{ value: '0', label: 'Negative' }]), 'unknown');
});

test('localized messages interpolate named placeholders without losing zero or false', () => {
  assert.equal(localize({ row: 'Row {number}: {value}' }, 'row', { number: 0, value: false }), 'Row 0: false');
  assert.equal(localize({ row: 'Dòng {number}: {value}' }, 'row', { number: 5, value: 'Âm tính' }), 'Dòng 5: Âm tính');
  assert.equal(localize({ raw: 'In file: {value}' }, 'raw', { value: '<script>$&{other}</script>' }), 'In file: <script>$&{other}</script>');
});

test('every dynamic message has a Django-translated template attribute', () => {
  const fs = require('node:fs');
  const path = require('node:path');
  const source = fs.readFileSync(path.resolve(__dirname, '../../../src/staticfiles/datacapture/js/mapped-file-import.js'), 'utf8');
  const template = fs.readFileSync(path.resolve(__dirname, '../../../src/templates/subject/includes/mapped_file_import.html'), 'utf8');
  const keys = [...source.matchAll(/\bt\('([^']+)'/g)].map((match) => match[1]);
  assert.ok(keys.length > 25);
  keys.forEach((key) => {
    const attribute = key.replace(/[A-Z]/g, (letter) => '-' + letter.toLowerCase());
    assert.ok(template.includes('data-' + attribute + '="{% trans '), 'Missing translation for ' + key);
  });
});

function comparisonFixture() {
  const fs = require('node:fs');
  const path = require('node:path');
  const vm = require('node:vm');
  const source = fs.readFileSync(path.resolve(__dirname, '../../../src/staticfiles/datacapture/js/mapped-file-import.js'), 'utf8');
  const node = (tag = 'div') => {
    const classes = new Set();
    return { tag, children: [], dataset: {}, listeners: {}, attributes: {}, checked: false, disabled: false,
      classList: { add: name => classes.add(name), remove: name => classes.delete(name),
        toggle(name, active) { if (active) classes.add(name); else classes.delete(name); }, contains: name => classes.has(name) },
      append(...children) { children.forEach(child => { child.parent = this; this.children.push(child); }); },
      replaceChildren() { this.children = []; },
      setAttribute(name, value) { this.attributes[name] = value; },
      addEventListener(name, callback) { this.listeners[name] = callback; },
      closest(selector) { return selector.split(',').map(s => s.trim()).includes(this.tag) ? this : this.parent?.closest(selector); },
      querySelectorAll(selector) { return this.children.flatMap(child => [
        ...(selector === '[data-import-decision]' && child.dataset.importDecision ? [child] : []),
        ...child.querySelectorAll(selector),
      ]); },
      click() { if (!this.disabled) { this.checked = true; this.listeners.change(); } },
    };
  };
  const nodes = { items: node(), comparison: node(), confirmation: node() };
  const context = vm.createContext({ document: { createElement: node }, get: name => nodes[name],
    root: { querySelectorAll: selector => nodes.items.querySelectorAll(selector), setAttribute() {} },
    form: { querySelectorAll: () => [] },
    t: key => key, fieldValue, displayValue, overrides: {}, decisions: {}, confirm: { checked: true },
    busy: false, applied: false, preview: {}, updateButton() {},
    selectedOption(select, value, text) { const option = node('option'); option.value = value; option.textContent = text; select.append(option); } });
  vm.runInContext(source.slice(source.indexOf('    function setBusy('), source.indexOf('    function errors(')), context);
  vm.runInContext(source.slice(source.indexOf('    function renderItems('), source.indexOf('    function close(')), context);
  context.renderItems({ items: [ { key: '2:4', field_key: 'LAB', column: 'A', status: 'overwrite', old_value: '8', new_value: '5' },
    { key: '2:5', column: 'B', status: 'new', new_value: '7' } ] });
  const row = nodes.items.children[0];
  const current = row.children[2];
  const incoming = row.children[3];
  const radios = row.querySelectorAll('[data-import-decision]');
  return { context, nodes, current, incoming, radios, node,
    click: cell => cell.listeners.click({ target: cell }) };
}

test('clicking either value cell chooses only that source and clears acknowledgement', () => {
  const f = comparisonFixture();
  assert.ok(f.radios.every(radio => radio.type === 'radio' && !radio.checked));
  assert.equal(f.radios[0].name, f.radios[1].name);
  assert.equal(f.nodes.items.children[1].children[2].listeners.click, undefined);
  f.click(f.current);
  assert.equal(f.context.decisions['2:4'], 'ecrf');
  assert.equal(f.context.confirm.checked, false);
  assert.equal(f.radios[0].checked, true);
  assert.equal(f.current.classList.contains('mapped-import__value--selected'), true);
  f.click(f.incoming);
  assert.equal(f.context.decisions['2:4'], 'file');
  assert.equal(f.radios[0].checked, false);
  assert.equal(f.radios[1].checked, true);
  assert.equal(f.current.classList.contains('mapped-import__value--selected'), false);
  assert.equal(f.incoming.classList.contains('mapped-import__value--selected'), true);
  assert.equal(f.nodes.items.children[0].children[4].textContent, 'useFile');
});

test('native radio change supports keyboard selection and option controls do not pick a source', () => {
  const f = comparisonFixture();
  f.radios[0].checked = true;
  f.radios[0].listeners.change();
  assert.equal(f.context.decisions['2:4'], 'ecrf');
  f.incoming.listeners.click({ target: f.node('select') });
  assert.equal(f.context.decisions['2:4'], 'ecrf');
});

test('busy or invalidated preview cannot be selected and invalidation clears highlights', () => {
  const f = comparisonFixture();
  f.click(f.current);
  f.context.setBusy(true);
  assert.ok(f.radios.every(radio => radio.disabled));
  f.click(f.incoming);
  assert.equal(f.context.decisions['2:4'], 'ecrf');
  f.context.invalidate();
  f.context.setBusy(false);
  assert.ok(f.radios.every(radio => !radio.checked && radio.disabled));
  assert.equal(f.current.classList.contains('mapped-import__value--selected'), false);
  assert.equal(Object.keys(f.context.decisions).length, 0);
  f.click(f.incoming);
  assert.equal(Object.keys(f.context.decisions).length, 0);
});
