"use strict";

(() => {
  const app = document.getElementById("employee-app");
  if (!app) return;
  const byId = (id) => document.getElementById(id);
  const courtSelect = byId("court-select"), customerSelect = byId("customer-select");
  const dateInput = byId("schedule-date"), showCancelled = byId("show-cancelled");
  const createForm = byId("create-booking"), rescheduleForm = byId("reschedule-booking"), cancelForm = byId("cancel-booking");
  const courts = new Map(), customers = new Map(), bookings = new Map();
  const statuses = { active: "Действует", cancelled: "Отменена" };
  const weatherLabels = { clear: "Без дождевого запрета", blocked: "Дождь / морось: период запрещён", unknown: "Погода не проверена", not_applicable: "Для крытого корта не требуется" };
  const surfaces = { available: "Готово", drying: "Просушка", maintenance: "Обслуживание" };
  const events = { created: "Создание", rescheduled: "Перенос", cancelled: "Отмена", archived: "Архив", weather_checked: "Перепроверка погоды" };
  const formatter = new Intl.DateTimeFormat("ru-RU", {
    timeZone: app.dataset.timeZone, day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });
  const inputFormatter = new Intl.DateTimeFormat("en-GB-u-nu-latn", {
    timeZone: app.dataset.timeZone, year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });
  let actionPending = false, scheduleLoaded = false, viewVersion = 0, historyVersion = 0;
  let selectedBooking = null, selectedFresh = false;

  function setStatus(element, message, kind = "") {
    element.textContent = message;
    for (const name of ["error", "warning", "success"]) element.classList.toggle(name, kind === name);
  }
  function formatTime(value) {
    return value && Number.isFinite(Date.parse(value)) ? formatter.format(new Date(value)) : "—";
  }
  function inputTime(value) {
    if (!value || !Number.isFinite(Date.parse(value))) return "";
    const parts = Object.fromEntries(inputFormatter.formatToParts(new Date(value)).map((part) => [part.type, part.value]));
    return parts.year + "-" + parts.month + "-" + parts.day + "T" + parts.hour + ":" + parts.minute;
  }
  function kazanISO(value) {
    if (!/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(?::[0-9]{2})?$/.test(value)) throw new Error("Укажите начало и конец по Казани.");
    // datetime-local has no zone. Never interpret it in the computer's zone.
    return value + "+03:00";
  }
  function apiError(message, status = 0, fields = {}, uncertain = false) {
    return Object.assign(new Error(message), { status, fields, uncertain });
  }
  async function requestJSON(url, payload) {
    const write = payload !== undefined;
    const options = { credentials: "same-origin", cache: "no-store", headers: { Accept: "application/json" } };
    if (write) {
      options.method = "POST";
      options.headers["Content-Type"] = "application/json";
      options.headers["X-CSRFToken"] = document.querySelector('input[name="csrfmiddlewaretoken"]').value;
      options.body = JSON.stringify(payload);
    }
    let response;
    try { response = await fetch(url, options); }
    catch { throw apiError("Связь с сервером прервалась.", 0, {}, write); }
    if (response.status === 401) {
      const login = new URL(app.dataset.loginUrl, window.location.origin);
      login.searchParams.set("next", window.location.pathname + window.location.search);
      window.location.assign(login);
      throw apiError("Сессия завершилась. Войдите снова.", 401);
    }
    let data;
    try { data = await response.json(); }
    catch {
      throw apiError(response.status === 403 ? "Действие отклонено. Обновите страницу и проверьте вход." :
        "Не удалось прочитать ответ. Обновите данные.", response.status, {}, write && (response.ok || response.status >= 500));
    }
    if (!response.ok) throw apiError(data?.error?.message || "Не удалось выполнить действие.", response.status, data?.error?.fields || {}, write && response.status >= 500);
    if (write && ![200, 201].includes(response.status)) throw apiError("Подтверждение сохранения не получено.", response.status, {}, true);
    return data;
  }
  function clearErrors(form) {
    for (const element of form.querySelectorAll("[data-field-error]")) element.textContent = "";
    for (const element of form.querySelectorAll('[aria-invalid="true"]')) element.removeAttribute("aria-invalid");
  }
  function showError(form, element, error) {
    clearErrors(form);
    const messages = [error.message || "Не удалось выполнить действие."];
    for (const [field, values] of Object.entries(error.fields || {})) {
      const text = (Array.isArray(values) ? values : [values]).join(" ");
      const slot = Array.from(form.querySelectorAll("[data-field-error]")).find((item) => item.dataset.fieldError === field);
      if (slot) {
        slot.textContent = text;
        form.elements.namedItem(field)?.setAttribute("aria-invalid", "true");
      } else messages.push(text);
    }
    if (error.uncertain) messages.push("Результат операции неизвестен. Обновите расписание перед повтором; запрос автоматически не повторяется.");
    setStatus(element, messages.join(" "), "error");
  }
  function row(body, values) {
    const element = document.createElement("tr");
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = value;
      element.append(cell);
    }
    body.append(element);
    return element;
  }
  function openButton(id, handler) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Открыть №" + id;
    button.dataset.openBooking = String(id);
    button.disabled = actionPending;
    button.addEventListener("click", handler);
    return button;
  }
  function recordURL(template, id) { return template.replace("/0/", "/" + id + "/"); }
  function dayURL(path, courtId, day) {
    const url = new URL(path, window.location.origin);
    url.searchParams.set("court_id", courtId);
    url.searchParams.set("date", day);
    return url;
  }
  function validBooking(booking) {
    return booking && Number.isSafeInteger(booking.id) && booking.id > 0 &&
      Number.isSafeInteger(booking.court_id) && Number.isSafeInteger(booking.customer_id) &&
      ["active", "cancelled"].includes(booking.status) &&
      Number.isFinite(Date.parse(booking.starts_at)) && Number.isFinite(Date.parse(booking.ends_at));
  }
  function updateControls() {
    courtSelect.disabled = actionPending || !courts.size;
    customerSelect.disabled = actionPending || !customers.size;
    dateInput.disabled = actionPending || !courts.size;
    showCancelled.disabled = actionPending || !scheduleLoaded;
    byId("refresh-schedule").disabled = actionPending || !courts.size;
    for (const input of createForm.querySelectorAll('input[type="datetime-local"]')) input.disabled = actionPending || !courts.size || !customers.size;
    byId("create-submit").disabled = actionPending || !courts.size || !customers.has(Number(customerSelect.value));
    const canChange = !actionPending && selectedFresh && selectedBooking?.status === "active" && !selectedBooking.archived_at;
    byId("reschedule-fields").disabled = !canChange;
    byId("cancel-fields").disabled = !canChange;
    const court = courts.get(Number(courtSelect.value));
    byId("close-surface").disabled = actionPending || !court || court.court_type !== "outdoor" || court.surface_status !== "available";
    byId("ready-surface").disabled = actionPending || !court || court.court_type !== "outdoor" || court.surface_status !== "drying";
    byId("recheck-weather").disabled = actionPending || !courts.size;
    for (const button of app.querySelectorAll("[data-open-booking]")) button.disabled = actionPending;
  }
  function renderCourtState() {
    const court = courts.get(Number(courtSelect.value));
    setStatus(byId("selected-court-state"), court ? court.name + ": покрытие — " + surfaces[court.surface_status] +
      ". Физическая готовность и погодные ограничения проверяются отдельно." : "Выберите корт.",
      court && court.surface_status !== "available" ? "warning" : "");
    updateControls();
  }
  function setCourts(rows) {
    const previous = courtSelect.value;
    courts.clear();
    courtSelect.replaceChildren();
    byId("court-rows").replaceChildren();
    for (const court of rows) {
      courts.set(court.id, court);
      const option = document.createElement("option");
      option.value = court.id;
      option.textContent = court.name;
      courtSelect.append(option);
      row(byId("court-rows"), [court.name, court.court_type === "indoor" ? "Крытый" : "Открытый", surfaces[court.surface_status]]);
    }
    if (courts.has(Number(previous))) courtSelect.value = previous;
    renderCourtState();
  }
  function renderBookings() {
    byId("schedule-rows").replaceChildren();
    const visible = Array.from(bookings.values()).filter((booking) => showCancelled.checked || booking.status === "active");
    for (const booking of visible) {
      const customer = customers.get(booking.customer_id);
      const element = row(byId("schedule-rows"), [formatTime(booking.starts_at), formatTime(booking.ends_at),
        customer ? customer.name + " · " + customer.phone : "Клиент №" + booking.customer_id,
        statuses[booking.status], weatherLabels[booking.weather_status] || "Нет старой отметки", ""]);
      element.classList.toggle("cancelled", booking.status === "cancelled");
      element.lastElementChild.append(openButton(booking.id, () => openBooking(booking.id)));
    }
    setStatus(byId("schedule-status"), visible.length ? "Время по Казани. Отменённые записи не занимают интервал." : "На выбранную дату записей для этого фильтра нет.");
  }
  function renderFreeIntervals(day) {
    const body = byId("free-intervals");
    body.replaceChildren();
    const start = Date.parse(day + "T00:00:00+03:00");
    if (!Number.isFinite(start)) return;
    const end = start + 24 * 60 * 60 * 1000;
    const busy = Array.from(bookings.values()).filter((booking) => booking.status === "active")
      .map((booking) => [Math.max(start, Date.parse(booking.starts_at)), Math.min(end, Date.parse(booking.ends_at))])
      .filter(([begin, finish]) => begin < finish).sort((a, b) => a[0] - b[0]);
    const gaps = [];
    let cursor = start;
    for (const [begin, finish] of busy) {
      if (begin > cursor) gaps.push([cursor, begin]);
      cursor = Math.max(cursor, finish);
    }
    if (cursor < end) gaps.push([cursor, end]);
    for (const [begin, finish] of gaps) {
      const item = document.createElement("li");
      item.textContent = formatTime(new Date(begin).toISOString()) + " — " + formatTime(new Date(finish).toISOString());
      body.append(item);
    }
    setStatus(byId("free-status"), gaps.length ? "Свободно по занятости; это не подтверждение доступности для бронирования." : "Весь день занят действующими бронями.");
  }
  function clearSelection() {
    selectedBooking = null;
    selectedFresh = false;
    ++historyVersion;
    byId("booking-detail").hidden = true;
    byId("history-rows").replaceChildren();
    updateControls();
  }
  function renderSelected(resetDraft = false) {
    if (!selectedBooking) return;
    const booking = selectedBooking, customer = customers.get(booking.customer_id);
    byId("booking-detail").hidden = false;
    byId("detail-heading").textContent = "Бронь №" + booking.id;
    byId("booking-summary").textContent = (customer ? customer.name + " · " + customer.phone : "Клиент №" + booking.customer_id) +
      " · " + (courts.get(booking.court_id)?.name || "Корт №" + booking.court_id) + " · " +
      formatTime(booking.starts_at) + " — " + formatTime(booking.ends_at) + " · " + statuses[booking.status] +
      " · " + (weatherLabels[booking.weather_status] || "Нет старой погодной отметки");
    setStatus(byId("booking-freshness"), !selectedFresh ? "Бронь отсутствует в обновлённом списке. Действия отключены; история доступна." :
      booking.status === "cancelled" ? "Бронь отменена. История сохранена, время освобождено." :
      "Перенос и отмена разрешены только до начала; текущее состояние повторно проверяет сервер.", selectedFresh ? "" : "warning");
    if (resetDraft) {
      rescheduleForm.elements.starts_at.value = inputTime(booking.starts_at);
      rescheduleForm.elements.ends_at.value = inputTime(booking.ends_at);
      rescheduleForm.elements.reason.value = "";
      cancelForm.elements.reason.value = "";
      cancelForm.elements.confirmed.checked = false;
      for (const form of [rescheduleForm, cancelForm]) clearErrors(form);
      setStatus(byId("reschedule-status"), "");
      setStatus(byId("cancel-status"), "");
    }
    updateControls();
  }
  function describeChanges(event) {
    const labels = { starts_at: "Начало", ends_at: "Конец", status: "Статус", weather_status: "Погода",
      weather_checked_at: "Проверка погоды", archived_at: "Архив", cancellation_reason: "Причина отмены" };
    const before = event.before || {}, after = event.after || {};
    function value(field, data) {
      const item = data[field];
      if (item === undefined || item === null || item === "") return "—";
      if (["starts_at", "ends_at", "weather_checked_at", "archived_at"].includes(field)) return formatTime(item);
      if (field === "status") return statuses[item] || item;
      if (field === "weather_status") return weatherLabels[item] || item;
      return String(item);
    }
    return Object.entries(labels).filter(([field]) => before[field] !== after[field])
      .map(([field, label]) => label + ": " + value(field, before) + " → " + value(field, after)).join("\n") ||
      "Значения не изменились; действие сохранено.";
  }
  async function loadHistory(id) {
    const version = ++historyVersion;
    byId("history-rows").replaceChildren();
    setStatus(byId("history-status"), "Загрузка истории…");
    try {
      const data = await requestJSON(recordURL(app.dataset.historyUrl, id));
      if (version !== historyVersion || selectedBooking?.id !== id) return;
      if (!Array.isArray(data?.events)) throw new Error("Не удалось получить историю.");
      for (const event of data.events) {
        const actor = String(event.actor_id) === app.dataset.employeeId ? app.dataset.employeeName + " (№" + event.actor_id + ")" : "Сотрудник №" + event.actor_id;
        row(byId("history-rows"), [formatTime(event.occurred_at), events[event.event_type] || event.event_type, actor, event.reason || "—", describeChanges(event)]);
      }
      setStatus(byId("history-status"), data.events.length ? "История загружена из базы." : "Событий пока нет.");
    } catch (error) {
      if (version === historyVersion && selectedBooking?.id === id) setStatus(byId("history-status"), error.message, "error");
    }
  }
  async function openBooking(id) {
    if (actionPending || !bookings.has(id)) return;
    selectedBooking = bookings.get(id);
    selectedFresh = true;
    renderSelected(true);
    await loadHistory(id);
  }
  function renderWeather(data, day) {
    byId("weather-rows").replaceChildren();
    if (!Array.isArray(data?.blocks)) throw new Error("Не удалось получить погодные периоды.");
    for (const block of data.blocks) row(byId("weather-rows"), [formatTime(block.starts_at), formatTime(block.ends_at), weatherLabels[block.status] || "Погода не проверена"]);
    const warnings = Array.isArray(data.warnings) ? data.warnings.slice() : [];
    if (data.blocks.some((block) => block.status === "blocked")) warnings.unshift("На выбранную дату есть дождевой запрет в указанных периодах. Подтверждение покрытия его не снимает.");
    const message = data.status === "not_applicable" ? weatherLabels.not_applicable : warnings.join(" ") || "Прогноз проверен: " + formatTime(data.checked_at) + ".";
    setStatus(byId("weather-status"), "Дата " + day + ". " + message, warnings.length ? "warning" : "");
  }
  async function refreshContext(reloadCourts = true) {
    const version = ++viewVersion, courtId = courtSelect.value, day = dateInput.value;
    scheduleLoaded = false;
    updateControls();
    bookings.clear();
    for (const id of ["schedule-rows", "weather-rows", "free-intervals"]) byId(id).replaceChildren();
    setStatus(byId("free-status"), "");
    if (!courtId || !day) {
      setStatus(byId("schedule-status"), "Выберите корт и дату.");
      setStatus(byId("weather-status"), "Выберите корт и дату.");
      return;
    }
    setStatus(byId("schedule-status"), "Загрузка расписания…");
    setStatus(byId("weather-status"), "Загрузка прогноза…");
    const requests = [requestJSON(dayURL(app.dataset.bookingsUrl, courtId, day)), requestJSON(dayURL(app.dataset.weatherUrl, courtId, day))];
    if (reloadCourts) requests.push(requestJSON(app.dataset.courtsUrl));
    const results = await Promise.allSettled(requests);
    if (version !== viewVersion) return;
    if (reloadCourts) {
      if (results[2].status === "fulfilled" && Array.isArray(results[2].value?.courts)) setCourts(results[2].value.courts);
      else setStatus(byId("data-status"), "Не удалось обновить состояние кортов. Показаны последние загруженные данные.", "warning");
      if (courtSelect.value !== courtId) {
        clearSelection();
        setStatus(byId("schedule-status"), "Список кортов изменился. Обновите выбранную дату.", "warning");
        return;
      }
    }
    try {
      if (results[0].status === "rejected") throw results[0].reason;
      const rows = results[0].value?.bookings;
      if (!Array.isArray(rows) || !rows.every(validBooking)) throw new Error("Не удалось прочитать расписание.");
      for (const booking of rows) bookings.set(booking.id, booking);
      scheduleLoaded = true;
      renderBookings();
      renderFreeIntervals(day);
      updateControls();
      if (selectedBooking) {
        const fresh = bookings.get(selectedBooking.id);
        selectedFresh = Boolean(fresh);
        if (fresh) selectedBooking = fresh;
        renderSelected();
      }
    } catch (error) {
      setStatus(byId("schedule-status"), "Не удалось обновить расписание. " + error.message, "error");
      setStatus(byId("free-status"), "Данные о свободных промежутках не получены.", "warning");
    }
    try {
      if (results[1].status === "rejected") throw results[1].reason;
      renderWeather(results[1].value, day);
    } catch (error) { setStatus(byId("weather-status"), error.message || "Не удалось проверить погоду.", "error"); }
    if (selectedBooking) await loadHistory(selectedBooking.id);
  }
  function beginWrite() {
    actionPending = true;
    updateControls();
  }
  async function bookingWrite(form, statusElement, url, payload, message) {
    if (actionPending) return;
    clearErrors(form);
    beginWrite();
    setStatus(statusElement, "Сохранение…");
    let saved = false;
    try {
      const data = await requestJSON(url, payload);
      if (!validBooking(data?.booking)) throw apiError("Подтверждённое состояние брони не получено.", 200, {}, true);
      saved = true;
      if (byId("affected-bookings").childElementCount) {
        byId("affected-bookings").replaceChildren();
        setStatus(byId("weather-action-status"), "Бронь изменена. Предыдущий список погодных предупреждений устарел; при необходимости запустите ручную перепроверку.", "warning");
      }
      ++historyVersion;
      byId("history-rows").replaceChildren();
      setStatus(byId("history-status"), "Обновление истории…");
      selectedBooking = data.booking;
      selectedFresh = true;
      courtSelect.value = String(data.booking.court_id);
      dateInput.value = inputTime(data.booking.starts_at).slice(0, 10);
      renderSelected(true);
      const warnings = data.booking.status === "active" && Array.isArray(data.warnings) ? data.warnings : [];
      setStatus(statusElement, message + " №" + data.booking.id + ". " + warnings.join(" "), warnings.length ? "warning" : "success");
      // A read failure cannot turn a committed write into a reported rejection.
      await refreshContext();
    } catch (error) {
      if (saved) setStatus(statusElement, message + " №" + selectedBooking.id + ". Не удалось обновить экран; обновите расписание.", "warning");
      else showError(form, statusElement, error);
    } finally { actionPending = false; updateControls(); }
  }
  async function surfaceAction(template, message) {
    if (actionPending || !courtSelect.value) return;
    const id = Number(courtSelect.value);
    beginWrite();
    setStatus(byId("weather-action-status"), "Сохранение состояния покрытия…");
    let saved = false;
    try {
      const data = await requestJSON(recordURL(template, id), {});
      if (!data?.court || data.court.id !== id) throw apiError("Подтверждение состояния покрытия не получено.", 200, {}, true);
      saved = true;
      Object.assign(courts.get(id), data.court);
      renderCourtState();
      setStatus(byId("weather-action-status"), [message, ...(data.warnings || [])].join(" "), data.warnings?.length ? "warning" : "success");
      await refreshContext();
    } catch (error) {
      setStatus(byId("weather-action-status"), saved ? "Состояние покрытия сохранено. Не удалось обновить экран." :
        error.message + (error.uncertain ? " Результат неизвестен. Обновите состояние перед повтором." : ""), saved ? "warning" : "error");
    } finally { actionPending = false; updateControls(); }
  }
  async function openAffected(booking) {
    if (actionPending) return;
    courtSelect.value = String(booking.court_id);
    dateInput.value = inputTime(booking.starts_at).slice(0, 10);
    clearSelection();
    await refreshContext();
    await openBooking(booking.id);
  }
  async function recheckWeather() {
    if (actionPending) return;
    beginWrite();
    setStatus(byId("weather-action-status"), "Перепроверка будущих открытых броней…");
    let saved = false;
    try {
      const data = await requestJSON(app.dataset.weatherRecheckUrl, {});
      if (!Array.isArray(data?.affected_bookings)) throw apiError("Результат перепроверки не получен.", 200, {}, true);
      saved = true;
      byId("affected-bookings").replaceChildren();
      for (const item of data.affected_bookings) {
        const element = row(byId("affected-bookings"), ["№" + item.booking.id + " / " + item.court_name,
          formatTime(item.booking.starts_at) + " — " + formatTime(item.booking.ends_at),
          item.customer.name + " / " + item.customer.phone, item.warnings.join(" ")]);
        element.lastElementChild.append(openButton(item.booking.id, () => openAffected(item.booking)));
      }
      setStatus(byId("weather-action-status"), "Проверено: " + data.checked_count + ". Требуют внимания: " + data.affected_bookings.length + ". Брони сохранены.", "success");
      await refreshContext();
    } catch (error) {
      setStatus(byId("weather-action-status"), saved ? "Перепроверка сохранена. Не удалось обновить экран." :
        error.message + (error.uncertain ? " Результат неизвестен. Обновите состояние перед повтором." : ""), saved ? "warning" : "error");
    } finally { actionPending = false; updateControls(); }
  }
  async function loadData() {
    try {
      const [courtData, customerData] = await Promise.all([requestJSON(app.dataset.courtsUrl), requestJSON(app.dataset.customersUrl)]);
      if (!Array.isArray(courtData?.courts) || !Array.isArray(customerData?.customers)) throw new Error("Не удалось загрузить справочники.");
      setCourts(courtData.courts);
      for (const customer of customerData.customers) {
        customers.set(customer.id, customer);
        const option = document.createElement("option");
        option.value = customer.id;
        option.textContent = customer.name + " · " + customer.phone;
        customerSelect.append(option);
        row(byId("customer-rows"), [customer.name, customer.phone, customer.email || "—"]);
      }
      setStatus(byId("data-status"), "Загружено: " + courts.size + " кортов, " + customers.size + " клиентов.");
      updateControls();
      if (courts.size) await refreshContext(false);
      else setStatus(byId("schedule-status"), "Корты пока не добавлены.");
    } catch (error) { setStatus(byId("data-status"), error.message || "Не удалось загрузить данные.", "error"); }
  }

  createForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (actionPending || !createForm.reportValidity()) return;
    try {
      bookingWrite(createForm, byId("create-status"), app.dataset.bookingsUrl,
        { customer_id: Number(customerSelect.value), court_id: Number(courtSelect.value),
          starts_at: kazanISO(createForm.elements.starts_at.value), ends_at: kazanISO(createForm.elements.ends_at.value) }, "Бронь создана");
    } catch (error) { showError(createForm, byId("create-status"), error); }
  });
  rescheduleForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (actionPending || !selectedFresh || selectedBooking?.status !== "active" || !rescheduleForm.reportValidity()) return;
    try {
      bookingWrite(rescheduleForm, byId("reschedule-status"), recordURL(app.dataset.rescheduleUrl, selectedBooking.id),
        { starts_at: kazanISO(rescheduleForm.elements.starts_at.value), ends_at: kazanISO(rescheduleForm.elements.ends_at.value),
          reason: rescheduleForm.elements.reason.value }, "Перенос сохранён");
    } catch (error) { showError(rescheduleForm, byId("reschedule-status"), error); }
  });
  cancelForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (actionPending || !selectedFresh || selectedBooking?.status !== "active" || !cancelForm.reportValidity()) return;
    bookingWrite(cancelForm, byId("cancel-status"), recordURL(app.dataset.cancelUrl, selectedBooking.id),
      { reason: cancelForm.elements.reason.value }, "Бронь отменена");
  });
  byId("schedule-filter").addEventListener("submit", (event) => { event.preventDefault(); if (!actionPending) refreshContext(); });
  for (const input of [courtSelect, dateInput]) input.addEventListener("change", () => {
    if (!actionPending) { clearSelection(); renderCourtState(); refreshContext(); }
  });
  showCancelled.addEventListener("change", () => { if (!actionPending && scheduleLoaded) renderBookings(); });
  customerSelect.addEventListener("change", () => {
    const customer = customers.get(Number(customerSelect.value));
    byId("customer-contact").textContent = customer ? "Телефон: " + customer.phone : "Выберите клиента, чтобы увидеть телефон.";
    updateControls();
  });
  byId("close-surface").addEventListener("click", () => surfaceAction(app.dataset.surfaceCloseUrl, "Корт закрыт на просушку."));
  byId("ready-surface").addEventListener("click", () => surfaceAction(app.dataset.surfaceReadyUrl, "Физическая готовность подтверждена после осмотра."));
  byId("recheck-weather").addEventListener("click", recheckWeather);
  loadData();
})();
