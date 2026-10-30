(function () {
  const modal = document.querySelector('[data-field-audit-history-modal]');
  if (!(modal instanceof HTMLElement)) {
    return;
  }

  const titleNode = modal.querySelector('[data-field-audit-history-modal-title]');
  const loadingNode = modal.querySelector('[data-field-audit-history-loading]');
  const tableBody = modal.querySelector('[data-field-audit-history-table-body]');
  const closeButton = modal.querySelector('[data-field-audit-history-modal-close]');
  let activeTrigger = null;

  function setText(node, value) {
    if (node) {
      node.textContent = String(value || '');
    }
  }

  function setLoading(isLoading) {
    if (loadingNode instanceof HTMLElement) {
      loadingNode.hidden = !isLoading;
    }
  }

  function clearRows() {
    if (!(tableBody instanceof HTMLElement)) {
      return;
    }
    Array.from(tableBody.querySelectorAll('tr')).forEach(function (row) {
      row.remove();
    });
  }

  function showEmpty(message) {
    if (!(tableBody instanceof HTMLElement)) {
      return;
    }
    clearRows();
    const row = document.createElement('tr');
    row.setAttribute('data-field-audit-history-empty-row', '');
    const cell = document.createElement('td');
    cell.colSpan = 6;
    cell.className = 'subject-detail-screen__audit-history-empty';
    cell.textContent = String(message || 'No audit history available.');
    row.appendChild(cell);
    tableBody.appendChild(row);
  }

  function buildRow(record) {
    const row = document.createElement('tr');
    [
      record.audit_event,
      record.changed_at,
      record.changed_by,
      record.field_name,
      record.value_from,
      record.value_to,
    ].forEach(function (value) {
      const cell = document.createElement('td');
      cell.textContent = String(value || '');
      row.appendChild(cell);
    });
    return row;
  }

  function renderRows(rows) {
    if (!(tableBody instanceof HTMLElement)) {
      return;
    }
    clearRows();
    const records = Array.isArray(rows) ? rows : [];
    if (records.length === 0) {
      showEmpty('No audit history available.');
      return;
    }
    records.forEach(function (record) {
      tableBody.appendChild(buildRow(record));
    });
  }

  function buildRequestUrl(trigger) {
    const baseUrl = String(trigger.dataset.auditHistoryUrl || '').trim();
    if (!baseUrl) {
      return '';
    }
    const url = new URL(baseUrl, window.location.origin);
    const fieldTemplateId = String(trigger.dataset.fieldTemplateId || '').trim();
    const fieldKey = String(trigger.dataset.fieldKey || '').trim();
    if (fieldTemplateId) {
      url.searchParams.set('field_template_id', fieldTemplateId);
    }
    if (fieldKey) {
      url.searchParams.set('field_key', fieldKey);
    }
    return url.toString();
  }

  function openModal(trigger) {
    const url = buildRequestUrl(trigger);
    if (!url) {
      return;
    }
    activeTrigger = trigger;
    setText(titleNode, 'AUDIT HISTORY');
    setLoading(true);
    clearRows();
    modal.hidden = false;
    closeButton?.focus();

    window
      .fetch(url, {
        method: 'GET',
        headers: {
          Accept: 'application/json',
        },
      })
      .then(function (response) {
        return response.text().then(function (text) {
          let data = {};
          if (text) {
            try {
              data = JSON.parse(text);
            } catch (_) {
              data = {};
            }
          }
          return { ok: response.ok, status: response.status, data: data };
        });
      })
      .then(function (result) {
        if (!result.ok || !result.data || result.data.ok !== true) {
          showEmpty('Unable to load audit history.');
          return;
        }
        const history = result.data.history || {};
        renderRows(history.rows || []);
      })
      .catch(function () {
        showEmpty('Unable to load audit history.');
      })
      .finally(function () {
        setLoading(false);
      });
  }

  function closeModal() {
    modal.hidden = true;
    activeTrigger?.focus();
    activeTrigger = null;
  }

  document.addEventListener('click', function (event) {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    const trigger = target.closest('[data-field-audit-history-modal-trigger]');
    if (trigger instanceof HTMLButtonElement) {
      if (trigger.disabled) {
        return;
      }
      openModal(trigger);
      return;
    }
    if (target === modal) {
      closeModal();
    }
  });

  if (closeButton instanceof HTMLElement) {
    closeButton.addEventListener('click', closeModal);
  }

  document.addEventListener('keydown', function (event) {
    if (modal.hidden) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      closeModal();
    } else if (event.key === 'Tab') {
      // Close is the only interactive control in this read-only dialog.
      event.preventDefault();
      closeButton?.focus();
    }
  });
})();
