const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.resolve(__dirname,
  '../../../src/staticfiles/datacapture/js/laboratory-import.js'), 'utf8');
const targetSource = source.slice(source.indexOf('  function targetFor('),
  source.indexOf("  document.querySelector('[data-lab-import-open]')"));

function resolve(controls, { visible = true, copies = 1, value = '52' } = {}) {
  const field = {
    dataset: { fieldKey: 'LAB_BIOCHEM_GPT' },
    querySelectorAll: () => controls,
    querySelector: () => null,
    getClientRects: () => visible ? [{}] : [],
  };
  const context = vm.createContext({ document: { querySelectorAll: () => Array(copies).fill(field) } });
  vm.runInContext(targetSource, context);
  return context.targetFor({ field_key: 'LAB_BIOCHEM_GPT', value });
}

test('numeric preview preserves both old and incoming values without clamping', () => {
  const input = { type: 'text', value: '35', dataset: { rangeMax: '40' } };
  const target = resolve([input]);
  assert.equal(target.old, '35');
  assert.equal(target.control, input);
  assert.equal(input.value, '35');
});

test('hidden, locked, readonly and repeated fields are never import targets', () => {
  assert.equal(resolve([{ type: 'text', value: '1', disabled: true }]), null);
  assert.equal(resolve([{ type: 'text', value: '1', readOnly: true }]), null);
  assert.equal(resolve([{ type: 'text', value: '1' }], { visible: false }), null);
  assert.equal(resolve([{ type: 'text', value: '1' }], { copies: 2 }), null);
});

test('radio choices must contain the mapped code and preserve current choice', () => {
  const radios = [{ type: 'radio', value: '0', checked: true }, { type: 'radio', value: '1' }];
  assert.equal(resolve(radios, { value: '1' }).old, '0');
  assert.equal(resolve(radios, { value: '1' }).control, radios[1]);
  assert.equal(resolve(radios, { value: '2' }), null);
});

test('unrecognized multi-control and option inputs are skipped', () => {
  assert.equal(resolve([{ type: 'text' }, { type: 'text' }]), null);
  assert.equal(resolve([{ type: 'checkbox', value: '52' }]), null);
  assert.equal(resolve([{ type: 'select-one', tagName: 'SELECT', options: [{ value: '1' }] }]), null);
});
