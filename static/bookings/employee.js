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
        ]);
      }
      setStatus(scheduleStatus, data.bookings.length ? "Время указано по Казани." : "На выбранную дату действующих броней нет.");
    } catch (error) {
      if (version === scheduleVersion) setStatus(scheduleStatus, error.message || "Ошибка загрузки расписания.", true);
    }
  }

  async function loadData() {
    try {
      const [courtData, customerData] = await Promise.all([
        getJSON(app.dataset.courtsUrl), getJSON(app.dataset.customersUrl),
      ]);
      const courtRows = document.getElementById("court-rows");
      const customerRows = document.getElementById("customer-rows");
      const types = { indoor: "Крытый", outdoor: "Открытый" };
      const surfaces = { available: "Готово", drying: "Просушка", maintenance: "Обслуживание" };
      for (const court of courtData.courts) {
        const option = document.createElement("option");
        option.value = court.id;
        option.textContent = court.name;
        courtSelect.append(option);
        appendRow(courtRows, [court.name, types[court.court_type], surfaces[court.surface_status]]);
      }
      for (const customer of customerData.customers) {
        customers.set(customer.id, customer);
        appendRow(customerRows, [customer.name, customer.phone, customer.email || "—"]);
      }
      setStatus(dataStatus, `Загружено: ${courtData.courts.length} кортов, ${customerData.customers.length} клиентов.`);
      courtSelect.disabled = !courtData.courts.length;
      document.getElementById("refresh-schedule").disabled = !courtData.courts.length;
      if (courtData.courts.length) await loadSchedule();
      else setStatus(scheduleStatus, "Корты пока не добавлены.");
    } catch (error) {
      setStatus(dataStatus, error.message || "Не удалось загрузить корты и клиентов.", true);
    }
  }

  document.getElementById("schedule-filter").addEventListener("submit", (event) => {
    event.preventDefault();
    loadSchedule();
  });
  courtSelect.addEventListener("change", loadSchedule);
  dateInput.addEventListener("change", loadSchedule);
  loadData();
})();
