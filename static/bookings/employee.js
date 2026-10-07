"use strict";

(() => {
  const app = document.getElementById("employee-app");
  if (!app) return;
  const courtSelect = document.getElementById("court-select");
  const dateInput = document.getElementById("schedule-date");
  const scheduleRows = document.getElementById("schedule-rows");
  const dataStatus = document.getElementById("data-status");
  const scheduleStatus = document.getElementById("schedule-status");
  const customers = new Map();
  const courts = new Map();
  const weatherRows = document.getElementById("weather-rows");
  const weatherStatus = document.getElementById("weather-status");
  const actionStatus = document.getElementById("weather-action-status");
  const affectedRows = document.getElementById("affected-bookings");
  const closeButton = document.getElementById("close-surface");
  const readyButton = document.getElementById("ready-surface");
  const recheckButton = document.getElementById("recheck-weather");
  const weatherLabels = { clear: "Без дождевого запрета", blocked: "Дождь / морось: весь период закрыт", unknown: "Погода не проверена", not_applicable: "Для крытого корта не требуется" };
  const surfaces = { available: "Готово", drying: "Просушка", maintenance: "Обслуживание" };
  let actionPending = false;
  let scheduleVersion = 0;
  const formatter = new Intl.DateTimeFormat("ru-RU", {
    timeZone: app.dataset.timeZone, day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });

  function setStatus(element, message, failed = false) {
    element.textContent = message;
    element.classList.toggle("error", failed);
  }

  function appendRow(body, values) {
    const row = document.createElement("tr");
    for (const value of values) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    body.append(row);
  }

  async function getJSON(url) {
    const response = await fetch(url, { credentials: "same-origin", cache: "no-store" });
    if (response.status === 401) {
      const login = new URL(app.dataset.loginUrl, window.location.origin);
      login.searchParams.set("next", window.location.pathname + window.location.search);
      window.location.assign(login);
      throw new Error("Сессия завершилась. Войдите снова.");
    }
    let data;
    try { data = await response.json(); }
    catch { throw new Error("Не удалось получить данные. Повторите запрос."); }
    if (!response.ok) throw new Error(data.error?.message || "Не удалось загрузить данные.");
    return data;
  }

  async function loadSchedule() {
    const version = ++scheduleVersion;
    scheduleRows.replaceChildren();
    if (!courtSelect.value || !dateInput.value) {
      setStatus(scheduleStatus, "Выберите корт и дату.");
      return;
    }
    setStatus(scheduleStatus, "Загрузка расписания…");
    const url = new URL(app.dataset.scheduleUrl, window.location.origin);
    url.searchParams.set("court_id", courtSelect.value);
    url.searchParams.set("date", dateInput.value);
    try {
      const data = await getJSON(url);
      if (version !== scheduleVersion) return;
      for (const booking of data.bookings) {
        appendRow(scheduleRows, [
          formatter.format(new Date(booking.starts_at)),
          formatter.format(new Date(booking.ends_at)),
          customers.get(booking.customer_id)?.name || "Клиент не найден",
          weatherLabels[booking.weather_status] || "Погода не проверена",
        ]);
      }
      setStatus(scheduleStatus, data.bookings.length ? "Время указано по Казани." : "На выбранную дату действующих броней нет.");
    } catch (error) {
      if (version === scheduleVersion) setStatus(scheduleStatus, error.message || "Ошибка загрузки расписания.", true);
    }
  }

  function updateSurfaceButtons() {
    const court = courts.get(Number(courtSelect.value));
    closeButton.disabled = actionPending || !court || court.court_type !== "outdoor" || court.surface_status !== "available";
    readyButton.disabled = actionPending || !court || court.court_type !== "outdoor" || court.surface_status !== "drying";
    recheckButton.disabled = actionPending || !courts.size;
  }

  function renderCourts() {
    const rows = document.getElementById("court-rows");
    rows.replaceChildren();
    for (const court of courts.values()) {
      appendRow(rows, [court.name, court.court_type === "indoor" ? "Крытый" : "Открытый", surfaces[court.surface_status]]);
    }
    updateSurfaceButtons();
  }

  async function loadWeather() {
    const version = scheduleVersion;
    updateSurfaceButtons();
    weatherRows.replaceChildren();
    if (!courtSelect.value || !dateInput.value) return;
    setStatus(weatherStatus, "Загрузка прогноза…");
    const url = new URL(app.dataset.weatherUrl, window.location.origin);
    url.searchParams.set("court_id", courtSelect.value);
    url.searchParams.set("date", dateInput.value);
    try {
      const data = await getJSON(url);
      if (version !== scheduleVersion) return;
      for (const block of data.blocks) {
        appendRow(weatherRows, [formatter.format(new Date(block.starts_at)), formatter.format(new Date(block.ends_at)), weatherLabels[block.status]]);
      }
      weatherStatus.classList.toggle("warning", Boolean(data.warnings.length));
      setStatus(weatherStatus, data.status === "not_applicable" ? weatherLabels.not_applicable :
        (data.warnings.join(" ") || `Прогноз проверен: ${formatter.format(new Date(data.checked_at))}.`));
    } catch (error) {
      if (version === scheduleVersion) setStatus(weatherStatus, error.message || "Не удалось проверить погоду.", true);
    }
  }

  async function postJSON(url) {
    const token = document.querySelector('input[name="csrfmiddlewaretoken"]').value;
    const response = await fetch(url, { method: "POST", credentials: "same-origin", cache: "no-store",
      headers: { "Content-Type": "application/json", "X-CSRFToken": token }, body: "{}" });
    if (response.status === 401) {
      const login = new URL(app.dataset.loginUrl, window.location.origin);
      login.searchParams.set("next", window.location.pathname + window.location.search);
      window.location.assign(login);
      throw new Error("Сессия завершилась. Войдите снова.");
    }
    let data;
    try { data = await response.json(); }
    catch { throw new Error("Не удалось получить ответ. Повторите запрос."); }
    if (!response.ok) throw new Error(data.error?.message || "Не удалось выполнить действие.");
    return data;
  }

  async function surfaceAction(template, message) {
    const courtId = Number(courtSelect.value);
    if (actionPending || !courtId) return;
    actionPending = true;
    updateSurfaceButtons();
    setStatus(actionStatus, "Сохранение состояния покрытия…");
    try {
      const data = await postJSON(template.replace("/0/", `/${courtId}/`));
      Object.assign(courts.get(courtId), data.court);
      renderCourts();
      setStatus(actionStatus, [message, ...data.warnings].join(" "));
      await loadWeather();
    } catch (error) { setStatus(actionStatus, error.message, true); }
    finally { actionPending = false; updateSurfaceButtons(); }
  }

  closeButton.addEventListener("click", () => surfaceAction(app.dataset.surfaceCloseUrl, "Корт закрыт на просушку."));
  readyButton.addEventListener("click", () => surfaceAction(app.dataset.surfaceReadyUrl, "Готовность покрытия подтверждена после осмотра."));
  recheckButton.addEventListener("click", async () => {
    if (actionPending) return;
    actionPending = true;
    updateSurfaceButtons();
    setStatus(actionStatus, "Перепроверка будущих открытых броней…");
    try {
      const data = await postJSON(app.dataset.weatherRecheckUrl);
      affectedRows.replaceChildren();
      for (const row of data.affected_bookings) {
        appendRow(affectedRows, [`№${row.booking.id} / ${row.court_name}`,
          `${formatter.format(new Date(row.booking.starts_at))} — ${formatter.format(new Date(row.booking.ends_at))}`,
          `${row.customer.name} / ${row.customer.phone}`, row.warnings.join(" ")]);
      }
      setStatus(actionStatus, `Проверено: ${data.checked_count}. Требуют внимания: ${data.affected_bookings.length}. Брони сохранены.`);
      await refreshSelection();
    } catch (error) { setStatus(actionStatus, error.message, true); }
    finally { actionPending = false; updateSurfaceButtons(); }
  });

  async function refreshSelection() {
    await Promise.all([loadSchedule(), loadWeather()]);
  }

  async function loadData() {
    try {
      const [courtData, customerData] = await Promise.all([
        getJSON(app.dataset.courtsUrl), getJSON(app.dataset.customersUrl),
      ]);
      const customerRows = document.getElementById("customer-rows");
      for (const court of courtData.courts) {
        const option = document.createElement("option");
        option.value = court.id;
        option.textContent = court.name;
        courtSelect.append(option);
        courts.set(court.id, court);
      }
      for (const customer of customerData.customers) {
        customers.set(customer.id, customer);
        appendRow(customerRows, [customer.name, customer.phone, customer.email || "—"]);
      }
      setStatus(dataStatus, `Загружено: ${courtData.courts.length} кортов, ${customerData.customers.length} клиентов.`);
      courtSelect.disabled = !courtData.courts.length;
      document.getElementById("refresh-schedule").disabled = !courtData.courts.length;
      renderCourts();
      if (courtData.courts.length) await refreshSelection();
      else setStatus(scheduleStatus, "Корты пока не добавлены.");
    } catch (error) {
      setStatus(dataStatus, error.message || "Не удалось загрузить корты и клиентов.", true);
    }
  }

  document.getElementById("schedule-filter").addEventListener("submit", (event) => {
    event.preventDefault();
    refreshSelection();
  });
  courtSelect.addEventListener("change", refreshSelection);
  dateInput.addEventListener("change", refreshSelection);
  loadData();
})();
