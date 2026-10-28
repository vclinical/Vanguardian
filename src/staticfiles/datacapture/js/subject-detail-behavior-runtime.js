(function () {
  function getFieldContainers() {
    return Array.from(document.querySelectorAll('[data-query-field-container][data-field-key]'));
  }

  if (!getFieldContainers().length) {
    return;
  }

  const formRoot = document.querySelector('[data-datacapture-form-root]');
  const lockStatuses = new Set(['finalized', 'locked']);
  const touchedFields = new Set();
  let isApplying = false;
  let pendingApplyTimeout = null;

  function normalizeStatus(value) {
    return String(value ?? '').trim().toLowerCase();
  }

  function isEditablePage() {
    if (!formRoot) {
      return false;
    }
    const pageStatus = normalizeStatus(formRoot.dataset.pageStatus);
    return !lockStatuses.has(pageStatus);
  }

  function isEmpty(value) {
    if (value == null) {
      return true;
    }
    if (Array.isArray(value)) {
      return value.length === 0;
    }
    if (typeof value === 'object') {
      return Object.keys(value).length === 0;
    }
    return String(value).trim() === '';
  }

  function toNumber(value) {
    const normalized = String(value ?? '').trim().replace(',', '.');
    if (!normalized) {
      return null;
    }
    const parsed = Number(normalized);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function toBoolean(value, fallback) {
    if (value == null) {
      return fallback;
    }
    if (typeof value === 'boolean') {
      return value;
    }
    if (typeof value === 'number') {
      return value !== 0;
    }
    if (Array.isArray(value)) {
      return value.length > 0;
    }
    const normalized = String(value).trim().toLowerCase();
    if (!normalized) {
      return fallback;
    }
    if (['1', 'true', 'yes', 'on'].includes(normalized)) {
      return true;
    }
    if (['0', 'false', 'no', 'off'].includes(normalized)) {
      return false;
    }
    return fallback;
  }

  function toText(value) {
    if (value == null) {
      return '';
    }
    if (typeof value === 'string') {
      return value;
    }
    if (typeof value === 'number' || typeof value === 'boolean') {
      return String(value);
    }
    try {
      return JSON.stringify(value);
    } catch (error) {
      return '';
    }
  }

  function parseCalendarDate(value) {
    let year;
    let month;
    let day;
    if (value != null && typeof value === 'object' && !Array.isArray(value)) {
      year = toNumber(value.year);
      month = toNumber(value.month);
      day = toNumber(value.day);
    } else {
      // Date controls serialize dates as YYYY-MM-DD; ignore any time component.
      const match = String(value ?? '').trim().match(/^(\d{4})-(\d{2})-(\d{2})(?:[T ].*)?$/);
      if (!match) {
        return null;
      }
      year = Number(match[1]);
      month = Number(match[2]);
      day = Number(match[3]);
    }
    if (![year, month, day].every(Number.isInteger) || year < 1 || year > 9999 || month < 1 || month > 12) {
      return null;
    }
    const isLeapYear = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
    const daysInMonth = [31, isLeapYear ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
    if (day < 1 || day > daysInMonth[month - 1]) {
      return null;
    }
    return { year, month, day };
  }

  function resolveStateValue(state, rawRef) {
    if (rawRef == null) {
      return null;
    }
    if (typeof rawRef === 'string') {
      const key = rawRef.trim();
      if (!key) {
        return null;
      }
      if (Object.prototype.hasOwnProperty.call(state, key)) {
        return state[key];
      }
      if (Object.prototype.hasOwnProperty.call(state, `${key}__year`)) {
        return {
          day: state[`${key}__day`] ?? '',
          month: state[`${key}__month`] ?? '',
          year: state[`${key}__year`] ?? '',
          time: state[`${key}__time`] ?? '',
        };
      }
    }
    return rawRef;
  }

  function ageYearsBetween(state, fromDateRef, toDateRef) {
    const fromValue = resolveStateValue(state, fromDateRef);
    const toValue = resolveStateValue(state, toDateRef);
    const fromDate = parseCalendarDate(fromValue);
    const toDate = parseCalendarDate(toValue);
    if (fromDate == null || toDate == null) {
      return null;
    }
    const birthdayPending = toDate.month < fromDate.month ||
      (toDate.month === fromDate.month && toDate.day < fromDate.day);
    return toDate.year - fromDate.year - (birthdayPending ? 1 : 0);
  }

  function expressionLooksSafe(expression) {
    if (!expression) {
      return false;
    }
    const forbiddenTokens = /[`;]|(?:\b(?:window|document|globalThis|Function|constructor|prototype|__proto__|eval|import|XMLHttpRequest|fetch|localStorage|sessionStorage)\b)/i;
    return !forbiddenTokens.test(expression);
  }

  function evalExpr(expression, state, fallback) {
    const normalized = String(expression ?? '').trim();
    if (!normalized) {
      return fallback;
    }
    if (!expressionLooksSafe(normalized)) {
      return fallback;
    }
    try {
      const scope = {
        ...state,
        empty: isEmpty,
        exists: (value) => !isEmpty(value),
        number: toNumber,
        bool: (value) => toBoolean(value, false),
        text: toText,
        age_years_between: (fromDateRef, toDateRef) =>
          ageYearsBetween(state, fromDateRef, toDateRef),
        len: (value) => {
          if (Array.isArray(value) || typeof value === 'string') {
            return value.length;
          }
          if (value && typeof value === 'object') {
            return Object.keys(value).length;
          }
          return 0;
        },
      };
      const runner = new Function(
        'scope',
        `return (function () { with (scope) { return (${normalized}); } })();`,
      );
      const value = runner(scope);
      return value == null ? fallback : value;
    } catch (error) {
      return fallback;
    }
  }

  function getFieldControls(container) {
    return Array.from(container.querySelectorAll('input, textarea, select'));
  }

  function syncCompositeControlValue(container) {
    const controlModules = window.DatacaptureSubjectDetailModules?.controls || {};
    controlModules.datePicker?.syncDateCompositeInput?.(container);
    controlModules.dateText?.syncDateTextInput?.(container);
    controlModules.datetime?.syncDatetimeCompositeInput?.(container);
    window.VanguardianDateAndTime?.syncControl?.(container.querySelector('[data-dateandtime-control]') || container);
  }

  function ensureInputNames(container) {
    const fieldKey = String(container.dataset.fieldKey || '').trim();
    if (!fieldKey) {
      return;
    }
    getFieldControls(container).forEach((control) => {
      if (control.name) {
        return;
      }
      if (
        control.hasAttribute('data-date-text-input') ||
        (control.hasAttribute('data-dateandtime-input') && !control.name) ||
        control.classList.contains('subject-date-picker__input--day') ||
        control.classList.contains('subject-date-picker__input--month') ||
        control.classList.contains('subject-date-picker__input--year') ||
        control.classList.contains('subject-date-picker__input--time')
      ) {
        return;
      }
      control.name = fieldKey;
    });
  }

  function bindControlListeners(container) {
    const fieldKey = String(container.dataset.fieldKey || '').trim();
    if (!fieldKey) {
      return;
    }
    getFieldControls(container).forEach((control) => {
      if (control.dataset.behaviorRuntimeBound === '1') {
        return;
      }
      control.dataset.behaviorRuntimeBound = '1';
      ['input', 'change'].forEach((eventName) => {
        control.addEventListener(eventName, (event) => {
          if (event.isTrusted) {
            touchedFields.add(fieldKey);
          }
          window.setTimeout(() => {
            syncCompositeControlValue(container);
            recomputeExpressionFields();
          }, 0);
          scheduleApplyAll();
        });
      });
    });
  }

  function readContainerValue(container) {
    syncCompositeControlValue(container);

    const controls = getFieldControls(container);
    if (!controls.length) {
      return '';
    }

    const dateParts = {
      day: container.querySelector('.subject-date-picker__input--day')?.value || '',
      month: container.querySelector('.subject-date-picker__input--month')?.value || '',
      year: container.querySelector('.subject-date-picker__input--year')?.value || '',
      time: container.querySelector('.subject-date-picker__input--time')?.value || '',
    };
    const hasDateCompositeControl = Boolean(
      container.querySelector('input[type="hidden"][data-date-composite-input]') ||
      dateParts.day ||
      dateParts.month ||
      dateParts.year,
    );
    if (hasDateCompositeControl && (dateParts.day || dateParts.month || dateParts.year || dateParts.time)) {
      return dateParts;
    }

    const dateTextHiddenInput = container.querySelector('input[type="hidden"][data-date-text-composite-input]');
    if (dateTextHiddenInput) {
      return dateTextHiddenInput.value || '';
    }

    const radios = controls.filter((control) => control instanceof HTMLInputElement && control.type === 'radio');
    if (radios.length) {
      const checkedRadio = radios.find((control) => control.checked);
      return checkedRadio ? checkedRadio.value : '';
    }

    const checkboxes = controls.filter((control) => control instanceof HTMLInputElement && control.type === 'checkbox');
    if (checkboxes.length > 1) {
      return checkboxes.filter((control) => control.checked).map((control) => control.value);
    }
    if (checkboxes.length === 1) {
      return checkboxes[0].checked;
    }

    const firstControl = controls[0];
    if (firstControl instanceof HTMLSelectElement && firstControl.multiple) {
      return Array.from(firstControl.selectedOptions).map((option) => option.value);
    }
    return firstControl.value ?? '';
  }

  function buildState() {
    const state = {};
    getFieldContainers().forEach((container) => {
      const fieldKey = String(container.dataset.fieldKey || '').trim();
      if (!fieldKey) {
        return;
      }
      const value = readContainerValue(container);
      state[fieldKey] = value;
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        if ('day' in value) {
          state[`${fieldKey}__day`] = value.day || '';
        }
        if ('month' in value) {
          state[`${fieldKey}__month`] = value.month || '';
        }
        if ('year' in value) {
          state[`${fieldKey}__year`] = value.year || '';
        }
        if ('time' in value) {
          state[`${fieldKey}__time`] = value.time || '';
        }
      }
    });
    return state;
  }

  function isContainerEmpty(container) {
    const value = readContainerValue(container);
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return Object.values(value).every((item) => isEmpty(item));
    }
    return isEmpty(value);
  }

  function setContainerValue(container, nextValue) {
    const controls = getFieldControls(container);
    if (!controls.length) {
      return false;
    }

    const dayInput = container.querySelector('.subject-date-picker__input--day');
    const monthInput = container.querySelector('.subject-date-picker__input--month');
    const yearInput = container.querySelector('.subject-date-picker__input--year');
    const timeInput = container.querySelector('.subject-date-picker__input--time');
    const hasDateCompositeControl = Boolean(
      container.querySelector('input[type="hidden"][data-date-composite-input]') ||
      dayInput ||
      monthInput ||
      yearInput,
    );
    if (hasDateCompositeControl && (dayInput || monthInput || yearInput || timeInput)) {
      const payload = nextValue && typeof nextValue === 'object' ? nextValue : {};
      if (dayInput && 'day' in payload) {
        dayInput.value = payload.day ?? '';
      }
      if (monthInput && 'month' in payload) {
        monthInput.value = payload.month ?? '';
      }
      if (yearInput && 'year' in payload) {
        yearInput.value = payload.year ?? '';
      }
      if (timeInput && 'time' in payload) {
        timeInput.value = payload.time ?? '';
      }
      return true;
    }

    const dateTextHiddenInput = container.querySelector('input[type="hidden"][data-date-text-composite-input]');
    if (dateTextHiddenInput) {
      const nextTextValue = nextValue == null ? '' : String(nextValue);
      const dateAndTimeControl = container.querySelector('[data-dateandtime-control]');
      if (dateAndTimeControl) {
        window.VanguardianDateAndTime?.applyCanonicalValue?.(dateAndTimeControl, nextTextValue);
        return true;
      }
      const controlModules = window.DatacaptureSubjectDetailModules?.controls || {};
      controlModules.dateText?.applyDateTextCompositeValue?.(container, nextTextValue);
      return true;
    }

    const radios = controls.filter((control) => control instanceof HTMLInputElement && control.type === 'radio');
    if (radios.length) {
      const normalized = String(nextValue ?? '');
      radios.forEach((radio) => {
        radio.checked = String(radio.value ?? '') === normalized;
      });
      return true;
    }

    const checkboxes = controls.filter((control) => control instanceof HTMLInputElement && control.type === 'checkbox');
    if (checkboxes.length > 1) {
      const values = Array.isArray(nextValue)
        ? nextValue.map((item) => String(item))
        : String(nextValue ?? '')
            .split(',')
            .map((item) => item.trim())
            .filter((item) => item);
      checkboxes.forEach((checkbox) => {
        checkbox.checked = values.includes(String(checkbox.value ?? ''));
      });
      return true;
    }
    if (checkboxes.length === 1) {
      checkboxes[0].checked = toBoolean(nextValue, false);
      return true;
    }

    const firstControl = controls[0];
    firstControl.value = nextValue == null ? '' : String(nextValue);
    return true;
  }

  function normalizeComparableValue(value) {
    if (value == null) {
      return '';
    }
    if (typeof value === 'string') {
      return value.trim();
    }
    if (typeof value === 'number' || typeof value === 'boolean') {
      return String(value);
    }
    if (Array.isArray(value)) {
      return JSON.stringify(value.map((item) => normalizeComparableValue(item)));
    }
    if (typeof value === 'object') {
      const normalized = {};
      Object.keys(value)
        .sort()
        .forEach((key) => {
          normalized[key] = normalizeComparableValue(value[key]);
        });
      return JSON.stringify(normalized);
    }
    return String(value).trim();
  }

  function hasSameValue(container, nextValue) {
    const currentValue = readContainerValue(container);
    return normalizeComparableValue(currentValue) === normalizeComparableValue(nextValue);
  }

  function setRequiredIndicator(container, isRequired) {
    container.querySelectorAll(
      '.subject-form-flat-field__required, .subject-form-field-card__required, .subject-form-table-row__required',
    ).forEach((marker) => {
      marker.hidden = !isRequired;
    });
  }

  function applyBehavior(container, state) {
    const fieldKey = String(container.dataset.fieldKey || '').trim();
    if (!fieldKey) {
      return;
    }
    const controlType = String(container.dataset.fieldControlType || '').trim().toLowerCase();
    const isLabelOnly = controlType === 'label_only';
    const visibleWhen = String(container.dataset.visibleWhen || '').trim();
    const readonlyWhen = String(container.dataset.readonlyWhen || '').trim();
    const requiredWhen = String(container.dataset.requiredWhen || '').trim();
    const defaultValue = String(container.dataset.defaultValue || '');
    const defaultValueExpr = String(container.dataset.defaultValueExpr || '').trim();

    const visible = toBoolean(evalExpr(visibleWhen, state, true), true);
    const readonly = isLabelOnly || toBoolean(evalExpr(readonlyWhen, state, false), false);
    const required = toBoolean(evalExpr(requiredWhen, state, false), false);
    const editable = isEditablePage();

    container.hidden = !visible;
    container.style.display = visible ? '' : 'none';
    container.setAttribute('aria-hidden', visible ? 'false' : 'true');

    const controls = getFieldControls(container);
    controls.forEach((control) => {
      const shouldDisable = !visible || !editable || readonly;
      let disabledReason = '';
      if (shouldDisable) {
        if (readonly && visible && editable) {
          disabledReason = 'readonly';
        } else if (!visible) {
          disabledReason = 'hidden';
        } else if (!editable) {
          disabledReason = 'not_editable';
        }
      }
      control.dataset.datacaptureDisabledReason = disabledReason;
      control.disabled = shouldDisable;
      if ('readOnly' in control) {
        control.readOnly = shouldDisable;
      }
      if ('required' in control) {
        control.required = required && visible && editable && !readonly;
      }
    });
    setRequiredIndicator(container, required);

    if (!visible || !editable) {
      return;
    }

    const shouldRespectTouchedState = !defaultValueExpr;
    if (shouldRespectTouchedState && touchedFields.has(fieldKey)) {
      return;
    }

    let nextValue = '';
    if (defaultValueExpr) {
      nextValue = evalExpr(defaultValueExpr, state, '');
    } else {
      if (!isContainerEmpty(container)) {
        return;
      }
      nextValue = defaultValue;
    }

    if (nextValue == null || nextValue === '') {
      return;
    }
    if (hasSameValue(container, nextValue)) {
      return;
    }
    if (setContainerValue(container, nextValue)) {
      controls.forEach((control) => {
        control.dispatchEvent(new Event('input', { bubbles: true }));
        control.dispatchEvent(new Event('change', { bubbles: true }));
      });
    }
  }

  function recomputeExpressionFields() {
    const state = buildState();
    getFieldContainers().forEach((container) => {
      const fieldKey = String(container.dataset.fieldKey || '').trim();
      const defaultValueExpr = String(container.dataset.defaultValueExpr || '').trim();
      if (!fieldKey || !defaultValueExpr) {
        return;
      }

      const visibleWhen = String(container.dataset.visibleWhen || '').trim();
      const visible = toBoolean(evalExpr(visibleWhen, state, true), true);
      if (!visible || !isEditablePage()) {
        return;
      }

      const nextValue = evalExpr(defaultValueExpr, state, '');
      if (nextValue == null || nextValue === '' || hasSameValue(container, nextValue)) {
        return;
      }

      if (setContainerValue(container, nextValue)) {
        getFieldControls(container).forEach((control) => {
          control.dispatchEvent(new Event('input', { bubbles: true }));
          control.dispatchEvent(new Event('change', { bubbles: true }));
        });
      }
    });
  }

  function applyAllBehaviors() {
    if (isApplying) {
      return;
    }
    isApplying = true;
    try {
      getFieldContainers().forEach((container) => {
        ensureInputNames(container);
        syncCompositeControlValue(container);
        bindControlListeners(container);
      });
      const state = buildState();
      getFieldContainers().forEach((container) => applyBehavior(container, state));
      recomputeExpressionFields();
    } finally {
      isApplying = false;
    }
  }

  function scheduleApplyAll() {
    if (pendingApplyTimeout !== null) {
      window.clearTimeout(pendingApplyTimeout);
    }
    pendingApplyTimeout = window.setTimeout(() => {
      pendingApplyTimeout = null;
      applyAllBehaviors();
    }, 0);
  }

  document.addEventListener(
    'input',
    (event) => {
      const container = event.target.closest('[data-field-key]');
      if (container && event.isTrusted) {
        touchedFields.add(String(container.dataset.fieldKey || '').trim());
      }
      if (container) {
        window.setTimeout(() => {
          syncCompositeControlValue(container);
          recomputeExpressionFields();
        }, 0);
      }
      scheduleApplyAll();
    },
    true,
  );

  document.addEventListener(
    'change',
    (event) => {
      const container = event.target.closest('[data-field-key]');
      if (container && event.isTrusted) {
        touchedFields.add(String(container.dataset.fieldKey || '').trim());
      }
      if (container) {
        window.setTimeout(() => {
          syncCompositeControlValue(container);
          recomputeExpressionFields();
        }, 0);
      }
      scheduleApplyAll();
    },
    true,
  );

  applyAllBehaviors();
  window.setTimeout(() => {
    window.DatacaptureUnsavedChangesGuard?.markCurrentPayloadClean?.();
  }, 0);
})();
