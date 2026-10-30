(() => {
  document.querySelectorAll('[data-excel-picker]').forEach(zone => {
    const input = zone.querySelector('input[type="file"]');
    const button = zone.querySelector('button');
    const filename = zone.querySelector('[data-excel-filename]');
    const error = zone.querySelector('[data-excel-error]');
    const empty = filename.textContent;

    button.addEventListener('click', () => { if (!input.disabled) input.click(); });
    input.addEventListener('change', () => {
      const files = input.files;
      const invalid = files.length && (files.length !== 1 || !files[0].name.toLowerCase().endsWith('.xlsx') || files[0].size > 5 * 1024 * 1024);
      error.textContent = invalid ? zone.dataset.invalid : '';
      if (invalid) input.value = '';
      filename.textContent = input.files.length ? input.files[0].name : empty;
    });
    input.addEventListener('invalid', event => {
      event.preventDefault();
      error.textContent = zone.dataset.invalid;
      button.focus();
    });
    zone.addEventListener('dragover', event => {
      event.preventDefault();
      if (!input.disabled) zone.classList.add('excel-file-picker--dragging');
    });
    zone.addEventListener('dragleave', event => {
      if (!zone.contains(event.relatedTarget)) zone.classList.remove('excel-file-picker--dragging');
    });
    zone.addEventListener('drop', event => {
      event.preventDefault();
      zone.classList.remove('excel-file-picker--dragging');
      if (input.disabled || !event.dataTransfer.files.length) return;
      input.files = event.dataTransfer.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
    });
  });
})();
