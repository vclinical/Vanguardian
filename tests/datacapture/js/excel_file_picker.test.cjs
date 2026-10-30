const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.resolve(__dirname,
  '../../../src/staticfiles/shared/js/components/excel-file-picker.js'), 'utf8');

function picker() {
  const listeners = {};
  const input = {
    files: [], disabled: false, clicks: 0,
    click() { this.clicks++; },
    set value(value) { assert.equal(value, ''); this.files = []; },
    addEventListener(type, callback) { listeners[type] = callback; },
    dispatchEvent(event) { listeners[event.type](event); this.changes = (this.changes || 0) + 1; },
  };
  const button = { addEventListener(type, callback) { this[type] = callback; }, focus() { this.focused = true; } };
  const filename = { textContent: 'No file selected' };
  const error = { textContent: '' };
  const classes = new Set();
  const zone = {
    dataset: { invalid: 'Choose one .xlsx file no larger than 5 MB.' },
    querySelector(selector) { return ({ 'input[type="file"]': input, button,
      '[data-excel-filename]': filename, '[data-excel-error]': error })[selector]; },
    addEventListener(type, callback) { this[type] = callback; },
    classList: { add: name => classes.add(name), remove: name => classes.delete(name) },
    contains: node => node === button,
  };
  vm.runInNewContext(source, { document: { querySelectorAll: () => [zone] }, Event });
  const drop = files => {
    const event = { dataTransfer: { files }, preventDefault() { this.prevented = true; } };
    zone.drop(event);
    assert.equal(event.prevented, true);
  };
  return { input, button, filename, error, zone, drop, listeners, classes };
}

test('Excel button opens picker and chosen filename is displayed', () => {
  const p = picker();
  p.button.click();
  assert.equal(p.input.clicks, 1);
  p.input.files = [{ name: 'ket_qua_ntn.xlsx', size: 100 }];
  p.input.dispatchEvent(new Event('change'));
  assert.equal(p.filename.textContent, 'ket_qua_ntn.xlsx');
  assert.equal(p.error.textContent, '');
});

test('drop replaces the file and fires change for existing preview invalidation', () => {
  const p = picker();
  const file = { name: 'RESULT.XLSX', size: 5 * 1024 * 1024 };
  p.zone.dragover({ preventDefault() {} });
  assert.equal(p.classes.size, 1);
  p.drop([file]);
  assert.equal(p.classes.size, 0);
  assert.equal(p.input.files[0], file);
  assert.equal(p.filename.textContent, file.name);
  assert.equal(p.input.changes, 1);
});

test('invalid drops clear previous selection and explain the constraint', () => {
  for (const files of [ [{ name: 'bad.csv', size: 1 }], [{ name: 'large.xlsx', size: 5242881 }],
    [{ name: 'one.xlsx', size: 1 }, { name: 'two.xlsx', size: 1 }] ]) {
    const p = picker();
    p.input.files = [{ name: 'old.xlsx', size: 1 }];
    p.drop(files);
    assert.equal(p.input.files.length, 0);
    assert.equal(p.filename.textContent, 'No file selected');
    assert.match(p.error.textContent, /one .xlsx/);
    assert.equal(p.input.changes, 1);
  }
});

test('disabled input cannot be replaced while importing', () => {
  const p = picker();
  p.input.disabled = true;
  p.button.click();
  p.drop([{ name: 'new.xlsx', size: 1 }]);
  assert.equal(p.input.clicks, 0);
  assert.equal(p.input.files.length, 0);
  assert.equal(p.input.changes, undefined);
});

test('required hidden input redirects validation focus to visible button', () => {
  const p = picker();
  let prevented = false;
  p.listeners.invalid({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(p.button.focused, true);
  assert.match(p.error.textContent, /xlsx/);
});
