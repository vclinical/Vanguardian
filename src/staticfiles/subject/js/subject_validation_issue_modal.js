(function () {
  const modal = document.querySelector('[data-validation-issue-modal]');
  if (!(modal instanceof HTMLElement)) {
    return;
  }

  const titleNode = modal.querySelector('[data-validation-issue-modal-title]');
  const briefNode = modal.querySelector('[data-validation-issue-modal-brief]');
  const valueNode = modal.querySelector('[data-validation-issue-modal-value]');
  const listNode = modal.querySelector('[data-validation-issue-modal-list]');
  const historyListNode = modal.querySelector('[data-validation-issue-history-list]');
  const historyEmptyNode = modal.querySelector('[data-validation-issue-history-empty]');
  const submitButton = modal.querySelector('[data-validation-issue-modal-submit]');
  const closeButton = modal.querySelector('[data-validation-issue-modal-close]');
  const formRoot = document.querySelector('[data-datacapture-form-root]');
  const pageStatusLabel = document.querySelector('[data-page-status-label]');
  const acknowledgeUrl = String(modal.dataset.acknowledgeUrl || '').trim();
  const languageCode = String(modal.dataset.languageCode || '').trim().toLowerCase();
  const titleEnPrefix = String(modal.dataset.titleEnPrefix || 'Validation issues for field').trim();
  const titleViPrefix = String(modal.dataset.titleViPrefix || 'Canh bao validation cho truong').trim();
  const notificationDurationMs = 2600;
  let activeTrigger = null;

  function isVietnamese(value) {
    const normalized = String(value || '').trim().toLowerCase();
    return normalized === 'vi' || normalized.startsWith('vi-');
  }

  function setText(node, value) {
    if (node) {
      node.textContent = String(value || '');
    }
  }

  function notificationHost() {
    let host = document.querySelector('[data-validation-issue-notifications]');
    if (host instanceof HTMLElement) {
      return host;
    }
    host = document.createElement('div');
    host.className = 'subject-detail-screen__notifications';
    host.setAttribute('data-validation-issue-notifications', '');
    document.body.appendChild(host);
    return host;
  }

  function showNotification(message, tone) {
    const text = String(message || '').trim();
    if (!text) {
      return;
    }
    const notice = document.createElement('div');
    notice.className = `subject-detail-screen__notification subject-detail-screen__notification--${tone === 'error' ? 'error' : 'success'}`;
    notice.textContent = text;
    notificationHost().appendChild(notice);
    window.setTimeout(function () {
      notice.classList.add('is-leaving');
      window.setTimeout(function () {
        notice.remove();
      }, 220);
    }, notificationDurationMs);
  }

  function sourceItems(trigger) {
    const container = trigger.closest('[data-query-field-container]') || trigger.closest('tr');
    const source = container ? container.querySelector('[data-validation-issue-source]') : null;
    if (!source) {
      return [];
    }
    return Array.from(source.querySelectorAll('[data-validation-issue]')).filter(function (node) {
      return node instanceof HTMLElement;
    });
  }

  function clearRows() {
    if (listNode) {
      listNode.replaceChildren();
    }
  }

  function clearHistory() {
    if (!historyListNode) {
      return;
    }
    Array.from(historyListNode.querySelectorAll('[data-validation-issue-history-entry]')).forEach(function (node) {
      node.remove();
    });
    if (historyEmptyNode instanceof HTMLElement) {
      historyEmptyNode.hidden = false;
    }
  }

  function historySourceItems(trigger) {
    const container = trigger.closest('[data-query-field-container]') || trigger.closest('tr');
    const source = container ? container.querySelector('[data-validation-issue-history-source]') : null;
    if (!source) {
      return [];
    }
    return Array.from(source.querySelectorAll('[data-validation-issue-history]')).filter(function (node) {
      return node instanceof HTMLElement;
    });
  }

  function buildHistoryMessageNode(sourceNode) {
    const row = document.createElement('article');
    row.className = 'subject-form-verification-query-modal__message';
    row.setAttribute('data-validation-issue-history-entry', '');

    const meta = document.createElement('div');
    meta.className = 'subject-form-verification-query-modal__message-meta';
    [
      String(sourceNode.dataset.historyLabel || '').trim(),
      String(sourceNode.dataset.historyClosedAt || sourceNode.dataset.historyOpenedAt || '').trim(),
      String(sourceNode.dataset.historyStatus || '').trim(),
    ].filter(Boolean).forEach(function (value) {
      const span = document.createElement('span');
      span.textContent = value;
      meta.appendChild(span);
    });
    if (meta.childNodes.length > 0) {
      row.appendChild(meta);
    }

    const value = String(sourceNode.dataset.historyValue || '').trim();
    if (value) {
      const valueNode = document.createElement('p');
      valueNode.className = 'subject-form-verification-query-modal__message-text';
      valueNode.textContent = `Value: ${value}`;
      row.appendChild(valueNode);
    }

    Array.from(sourceNode.querySelectorAll('[data-validation-issue-history-message]')).forEach(function (messageNode) {
      const eventMeta = document.createElement('div');
      eventMeta.className = 'subject-form-verification-query-modal__message-meta';
      eventMeta.textContent = [messageNode.dataset.messageOpenedAt, messageNode.dataset.messageStatus,
        messageNode.dataset.messageOpenedBy].filter(Boolean).join(' · ');
      row.appendChild(eventMeta);
      const textNode = document.createElement('p');
      textNode.className = 'subject-form-verification-query-modal__message-text';
      textNode.textContent = String(messageNode.dataset.messageText || '').trim();
      row.appendChild(textNode);
      if (messageNode.dataset.messageValue) {
        const eventValue = document.createElement('p');
        eventValue.className = 'subject-form-verification-query-modal__message-text';
        eventValue.textContent = `Value: ${messageNode.dataset.messageValue}`;
        row.appendChild(eventValue);
      }
    });
    return row;
  }

  function loadHistory(trigger) {
    clearHistory();
    if (!historyListNode) {
      return;
    }
    const histories = historySourceItems(trigger);
    histories.forEach(function (sourceNode) {
      historyListNode.appendChild(buildHistoryMessageNode(sourceNode));
    });
    if (historyEmptyNode instanceof HTMLElement) {
      historyEmptyNode.hidden = histories.length > 0;
    }
  }

  function buildRow(sourceNode) {
    const row = document.createElement('tr');
    row.className = 'subject-form-validation-issue-modal__row';
    row.setAttribute('data-validation-issue-modal-row', '');
    row.dataset.issueId = String(sourceNode.dataset.issueId || '');

    const checkCell = document.createElement('td');
    checkCell.className = 'subject-form-validation-issue-modal__check';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = true;
    checkbox.setAttribute('data-validation-issue-modal-check', '');
    checkCell.appendChild(checkbox);

    const contentCell = document.createElement('td');
    contentCell.className = 'subject-form-validation-issue-modal__content';
    const question = document.createElement('p');
    question.className = 'subject-form-validation-issue-modal__question';
    question.textContent = String(sourceNode.dataset.issueMessage || '');
    contentCell.appendChild(question);

    const meta = document.createElement('div');
    meta.className = 'subject-form-validation-issue-modal__meta';
    [sourceNode.dataset.issueSeverity, sourceNode.dataset.issueMode, sourceNode.dataset.issueStatus]
      .filter(Boolean)
      .forEach(function (value) {
        const item = document.createElement('span');
        item.textContent = String(value);
        meta.appendChild(item);
      });
    const failedValue = String(sourceNode.dataset.issueFailedValue || '').trim();
    if (failedValue) {
      const item = document.createElement('span');
      item.textContent = `Failed value: ${failedValue}`;
      meta.appendChild(item);
    }
    if (meta.childNodes.length > 0) {
      contentCell.appendChild(meta);
    }

    const textarea = document.createElement('textarea');
    textarea.className = 'subject-form-validation-issue-modal__textarea';
    textarea.rows = 3;
    textarea.setAttribute('data-validation-issue-modal-comment', '');
    contentCell.appendChild(textarea);

    checkbox.addEventListener('change', function () {
      textarea.disabled = !checkbox.checked;
      if (!checkbox.checked) {
        textarea.value = '';
      }
    });

    row.appendChild(checkCell);
    row.appendChild(contentCell);
    return row;
  }

  function setSubmitEnabled(enabled) {
    if (submitButton instanceof HTMLButtonElement) {
      submitButton.disabled = !enabled;
    }
  }

  function openModal(trigger) {
    const fieldLabel = String(trigger.dataset.fieldLabel || '').trim();
    const fieldKey = String(trigger.dataset.fieldKey || '').trim();
    const fieldValue = String(trigger.dataset.fieldValue || '').trim();
    const titleField = isVietnamese(languageCode) ? fieldKey || fieldLabel : fieldLabel || fieldKey;
    const titlePrefix = isVietnamese(languageCode) ? titleViPrefix : titleEnPrefix;
    activeTrigger = trigger;
    setText(titleNode, `${titlePrefix} ${titleField}`.trim());
    setText(briefNode, fieldLabel || fieldKey || '-');
    const issues = sourceItems(trigger);
    const firstFailedValue = issues.length > 0 ? String(issues[0].dataset.issueFailedValue || '').trim() : '';
    setText(valueNode, firstFailedValue || fieldValue || '-');
    clearRows();
    loadHistory(trigger);
    issues.forEach(function (sourceNode) {
      listNode?.appendChild(buildRow(sourceNode));
    });
    setSubmitEnabled(!!acknowledgeUrl && !!listNode?.querySelector('[data-validation-issue-modal-row]'));
    modal.hidden = false;
    const firstTextarea = modal.querySelector('[data-validation-issue-modal-comment]');
    if (firstTextarea instanceof HTMLTextAreaElement && !firstTextarea.disabled) {
      firstTextarea.focus();
    } else {
      closeButton?.focus();
    }
  }

  function closeModal() {
    modal.hidden = true;
    if (activeTrigger?.isConnected) activeTrigger.focus();
    activeTrigger = null;
    clearRows();
    clearHistory();
  }

  function normalizeErrorMessage(result) {
    const errs = result && result.data && result.data.error;
    if (Array.isArray(errs) && errs.length > 0) {
      return errs.join(', ');
    }
    if (result && result.status >= 500) {
      return 'Server error.';
    }
    return 'Request failed.';
  }

  function normalizePageStatus(value) {
    return String(value || '').trim();
  }

  function updatePageStatus(pageStatus) {
    const normalizedStatus = normalizePageStatus(pageStatus);
    if (formRoot instanceof HTMLElement) {
      formRoot.dataset.pageStatus = normalizedStatus.toLowerCase();
    }
    if (!(pageStatusLabel instanceof HTMLElement)) {
      return;
    }
    if (!normalizedStatus) {
      pageStatusLabel.remove();
      return;
    }
    pageStatusLabel.textContent = `(${normalizedStatus.toUpperCase()})`;
  }

  function selectedAcknowledgements() {
    const rows = Array.from(modal.querySelectorAll('[data-validation-issue-modal-row]'));
    const out = [];
    for (const row of rows) {
      if (!(row instanceof HTMLElement)) {
        continue;
      }
      const checkbox = row.querySelector('[data-validation-issue-modal-check]');
      const textarea = row.querySelector('[data-validation-issue-modal-comment]');
      if (!(checkbox instanceof HTMLInputElement) || !checkbox.checked) {
        continue;
      }
      const comment = textarea instanceof HTMLTextAreaElement ? String(textarea.value || '').trim() : '';
      if (!comment) {
        if (textarea instanceof HTMLTextAreaElement) {
          textarea.focus();
        }
        showNotification('Acknowledgement comment is required.', 'error');
        return null;
      }
      out.push({
        issue_id: parseInt(row.dataset.issueId || '', 10),
        comment: comment,
      });
    }
    return out;
  }

  function removeAcknowledgedIssues(issues) {
    if (!(activeTrigger instanceof HTMLElement)) {
      return;
    }
    const issueList = Array.isArray(issues) ? issues : [];
    const idSet = new Set(issueList.map(function (issue) {
      return String(issue.issue_id);
    }));
    const container = activeTrigger.closest('[data-query-field-container]') || activeTrigger.closest('tr');
    const source = container ? container.querySelector('[data-validation-issue-source]') : null;
    if (source) {
      Array.from(source.querySelectorAll('[data-validation-issue]')).forEach(function (node) {
        if (node instanceof HTMLElement && idSet.has(String(node.dataset.issueId || ''))) {
          node.remove();
        }
      });
    }
    const remaining = source ? source.querySelectorAll('[data-validation-issue]').length : 0;
    const badge = activeTrigger.querySelector('[data-validation-issue-badge]');
    if (badge) {
      badge.textContent = String(remaining);
    }
    const historySource = container?.querySelector('[data-validation-issue-history-source]');
    const hasHistory = historySource instanceof HTMLElement && historySource.children.length > 0;
    if (remaining <= 0) {
      if (!hasHistory) {
        activeTrigger.remove();
        if (source) {
          source.remove();
        }
      } else if (badge) {
        badge.remove();
      }
      if (
        container instanceof HTMLElement &&
        !(hasHistory || container.querySelector('[data-validation-issue-modal-trigger]:not([hidden])'))
      ) {
        container.classList.remove('subject-form-field--has-validation-issue');
      }
    }
  }

  function submitAcknowledgements() {
    if (!acknowledgeUrl) {
      return;
    }
    const issues = selectedAcknowledgements();
    if (issues === null) {
      return;
    }
    if (issues.length === 0) {
      closeModal();
      return;
    }
    setSubmitEnabled(false);
    window
      .fetch(acknowledgeUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ issues: issues }),
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
          showNotification(normalizeErrorMessage(result), 'error');
          return;
        }
        const acknowledgedIds = new Set((result.data.acknowledged_issue_ids || []).map(String));
        removeAcknowledgedIssues(
          issues.filter(function (issue) {
            return acknowledgedIds.has(String(issue.issue_id));
          })
        );
        updatePageStatus(result.data.page_status);
        closeModal();
      })
      .catch(function () {
        showNotification('Network error.', 'error');
      })
      .finally(function () {
        setSubmitEnabled(true);
      });
  }

  document.addEventListener('click', function (event) {
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    const trigger = target.closest('[data-validation-issue-modal-trigger]');
    if (trigger instanceof HTMLButtonElement) {
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

  if (submitButton instanceof HTMLElement) {
    submitButton.addEventListener('click', submitAcknowledgements);
  }

  document.addEventListener('keydown', function (event) {
    if (modal.hidden) return;
    if (event.key === 'Escape' && !modal.hidden) {
      event.preventDefault();
      closeModal();
    } else if (event.key === 'Tab') {
      const controls = Array.from(modal.querySelectorAll(
        'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])'
      )).filter(function (node) { return node.getClientRects().length > 0; });
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (!first) return;
      if (!modal.contains(document.activeElement) || (event.shiftKey && document.activeElement === first)) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });
})();
