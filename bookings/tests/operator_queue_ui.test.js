// Runs with Node's built-in test runner; no browser or external packages required.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

class Element {
  constructor() {
    this.children = []; this.listeners = {}; this.dataset = {}; this.value = "";
    this.elements = {}; this.classList = { toggle() {} }; this.disabled = false;
    this.textContent = "";
  }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  dispatch(name) { return this.listeners[name]({ preventDefault() {} }); }
  append(element) { this.children.push(element); }
  replaceChildren(...items) { this.children = items; }
  get lastElementChild() { return this.children.at(-1); }
  get childElementCount() { return this.children.length; }
  querySelectorAll() { return []; }
  setAttribute() {}
  removeAttribute() {}
}

function setup(withBooking = false, storageBroken = false) {
  const elements = new Map();
  const element = (id) => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  const app = element("employee-app");
  app.dataset = { timeZone: "Europe/Moscow", employeeId: "1", employeeName: "test",
    courtsUrl: "/api/courts/", customersUrl: "/api/customers/", weatherQueueUrl: "/api/weather/queue/",
    weatherHealthUrl: "/api/weather/health/", weatherRecheckUrl: "/api/weather/recheck/", loginUrl: "/login/",
    bookingsUrl: "/api/bookings/", weatherUrl: "/api/weather/", historyUrl: "/api/bookings/0/history/",
    contactNoAnswerUrl: "/api/bookings/0/contact/no-answer/" };
  const pending = { queue: [], recheck: [], contact: [], contactIds: [] };
  const response = (value) => ({ ok: true, status: 200, json: async () => value });
  function defer(list) { return new Promise((resolve) => list.push((data) => resolve(response(data)))); }
  const item = queue("Synthetic").action[0].booking;
  item.weather_status = "blocked";
  const fetch = (url, options = {}) => {
    if (String(url).includes("/contact/no-answer/")) {
      pending.contactIds.push(JSON.parse(options.body).request_id);
      return new Promise((resolve, reject) => pending.contact.push({
        fail: () => reject(new Error("synthetic network")),
        success: () => resolve(response({ booking_id: 1, event_id: 1 })) }));
    }
    if (String(url).includes("/weather/queue/")) return defer(pending.queue);
    if (String(url).includes("/weather/recheck/")) return defer(pending.recheck);
    if (String(url).includes("/weather/health/")) return Promise.resolve(response({ state: "not_started" }));
    if (String(url).includes("/courts/")) return Promise.resolve(response({ courts: withBooking ? [{ id: 1, name: "Synthetic", court_type: "outdoor", surface_status: "available" }] : [] }));
    if (String(url).includes("/customers/")) return Promise.resolve(response({ customers: withBooking ? [{ id: 1, name: "Synthetic", phone: "DEMO" }] : [] }));
    if (String(url).includes("/history/")) return Promise.resolve(response({ events: [] }));
    if (String(url).includes("/bookings/")) return Promise.resolve(response({ bookings: [item] }));
    if (String(url).includes("/weather/")) return Promise.resolve(response({ blocks: [], warnings: [] }));
    throw new Error("Unexpected URL: " + url);
  };
  const document = { hidden: false, getElementById: element, createElement: () => new Element(),
    querySelector: () => ({ value: "synthetic-csrf" }) };
  const window = { location: { origin: "http://localhost", pathname: "/", search: "" }, setInterval() {} };
  for (const name of ["starts_at", "ends_at", "reason"]) element("reschedule-booking").elements[name] = new Element();
  for (const name of ["reason", "confirmed"]) element("cancel-booking").elements[name] = new Element();
  let generated = 0;
  const storage = new Map();
  const context = { document, window, fetch, URL, Intl, Date, Map, Promise, Number, String, Object,
    Array, Error, crypto: { randomUUID: () => "00000000-0000-4000-8000-" + String(++generated).padStart(12, "0") },
    sessionStorage: { getItem(key) { return storage.get(key) ?? null; },
      setItem(key, value) { if (storageBroken) throw new Error("storage unavailable"); storage.set(key, value); },
      removeItem(key) { storage.delete(key); } } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../../static/bookings/employee.js"), "utf8"), context);
  return { element, pending };
}

function queue(name) {
  return { as_of: "2026-10-29T12:00:00Z", observation: [], action: [{ court_name: name,
    customer: { name: "Synthetic", phone: "DEMO" }, booking: { id: 1, court_id: 1, customer_id: 1,
      status: "active", starts_at: "2026-10-30T10:00:00Z", ends_at: "2026-10-30T11:00:00Z" } }] };
}
const tick = () => new Promise((resolve) => setImmediate(resolve));

test("out-of-order queue responses cannot overwrite the newer result", async () => {
  const { element, pending } = setup();
  assert.equal(pending.queue.length, 1);
  element("refresh-weather-queue").dispatch("click");
  assert.equal(pending.queue.length, 2);
  pending.queue[1](queue("NEW")); await tick();
  pending.queue[0](queue("OLD")); await tick();
  assert.match(element("queue-action").children[0].children[0].textContent, /NEW/);
});

test("a queue response started before a write cannot overwrite data after it", async () => {
  const { element, pending } = setup();
  element("recheck-weather").dispatch("click");
  assert.equal(pending.recheck.length, 1);
  pending.queue[0](queue("OLD")); await tick();
  assert.equal(element("queue-action").children.length, 0);
  pending.recheck[0]({ affected_bookings: [], checked_count: 0 });
  await tick(); await tick();
  assert.equal(pending.queue.length, 2);
  pending.queue[1](queue("NEW")); await tick(); await tick();
  assert.match(element("queue-action").children[0].children[0].textContent, /NEW/);
});

test("uncertain contact retries the same request, then a new action gets a new id", async () => {
  const { element, pending } = setup(true);
  pending.queue[0](queue("Synthetic")); await tick(); await tick();
  const opening = element("queue-action").children[0].lastElementChild.children[0].dispatch("click");
  await opening; await tick();
  const attempt1 = element("contact-no-answer").dispatch("click");
  assert.equal(pending.contact.length, 1);
  pending.contact[0].fail(); await tick(); await tick();
  pending.queue.at(-1)(queue("Synthetic")); await attempt1;
  const attempt2 = element("contact-no-answer").dispatch("click");
  assert.equal(pending.contact.length, 2);
  pending.contact[1].success(); await tick(); await tick();
  pending.queue.at(-1)(queue("Synthetic")); await attempt2;
  const attempt3 = element("contact-no-answer").dispatch("click");
  assert.equal(pending.contact.length, 3);
  pending.contact[2].success(); await tick(); await tick();
  pending.queue.at(-1)(queue("Synthetic")); await attempt3;
  assert.equal(pending.contactIds[0], pending.contactIds[1]);
  assert.notEqual(pending.contactIds[1], pending.contactIds[2]);
});

test("contact is not sent if its request id cannot be persisted", async () => {
  const { element, pending } = setup(true, true);
  pending.queue[0](queue("Synthetic")); await tick(); await tick();
  await element("queue-action").children[0].lastElementChild.children[0].dispatch("click");
  await element("contact-no-answer").dispatch("click");
  assert.equal(pending.contact.length, 0);
  assert.match(element("contact-status").textContent, /Не удалось подготовить/);
});
