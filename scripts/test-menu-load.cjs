const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const script = html.slice(html.indexOf('<script>') + 8, html.lastIndexOf('</script>'));
const serviceWorkerSource = fs.readFileSync(path.join(__dirname, '..', 'service-worker.js'), 'utf8');

// Keep the reported order independent of future menu price/availability updates.
const menu = {
  schemaVersion: 1,
  updatedAt: '2026-10-14',
  categories: {
    '副餐類': [
      { id: 'item_7bfb164de8', name: '炸三寶', price: 18 },
      { id: 'unagi_chawanmushi', name: '店內特製鰻魚茶碗蒸', price: 22 },
    ],
    '飲料': [{ id: 'calpico', name: '可爾必思', price: 8 }],
  },
};

function savedOrder(other = { item_7bfb164de8: 1, calpico: 1, unagi_chawanmushi: 1 }) {
  return {
    plates: { red: 3, silver: 1, gold: 0, black: 0 },
    other,
    modifications: [{ id: 'm0', label: '海老', sign: '+', unit: '$', value: 42 }],
  };
}

function createElement() {
  let html = '';
  const classes = new Set();
  return {
    children: [], textContent: '', style: {}, dataset: {}, value: '',
    get innerHTML() { return html; },
    set innerHTML(value) { html = value; this.children = []; },
    appendChild(child) { this.children.push(child); },
    setAttribute() {}, removeAttribute() {},
    classList: {
      contains(value) { return classes.has(value); },
      add(value) { classes.add(value); },
      remove(value) { classes.delete(value); },
    },
  };
}

function createSharedStorage(order) {
  return { value: JSON.stringify(order) };
}

function startApp(order = savedOrder(), sharedStorage = null) {
  const nodes = new Map();
  const timers = new Map();
  let nextTimerId = 0;
  let writes = 0;
  let storageWriteFails = false;
  let storageListener;
  let resolveMenu;
  let rejectMenu;
  let stored = JSON.stringify(order);
  const response = new Promise((resolve, reject) => { resolveMenu = resolve; rejectMenu = reject; });
  const context = vm.createContext({
    document: {
      getElementById(id) {
        if (!nodes.has(id)) nodes.set(id, createElement());
        return nodes.get(id);
      },
      createElement,
      body: createElement(),
      addEventListener() {},
      querySelectorAll() { return []; },
    },
    window: {
      addEventListener(type, listener) {
        if (type === 'storage') storageListener = listener;
      },
    },
    navigator: {}, console: { log() {}, warn() {}, error() {} },
    localStorage: {
      getItem(key) {
        assert.equal(key, 'sushiro-counter-state-v1');
        return sharedStorage ? sharedStorage.value : stored;
      },
      setItem(key, value) {
        assert.equal(key, 'sushiro-counter-state-v1');
        if (storageWriteFails) throw new Error('QuotaExceededError');
        if (sharedStorage) sharedStorage.value = value;
        else stored = value;
        writes += 1;
      },
      removeItem(key) {
        assert.equal(key, 'sushiro-counter-state-v1');
        if (sharedStorage) sharedStorage.value = null;
        else stored = null;
        writes += 1;
      },
    },
    confirm: () => true,
    fetch: () => response,
    setTimeout(callback) {
      const id = ++nextTimerId;
      timers.set(id, callback);
      return id;
    },
    clearTimeout(id) { timers.delete(id); },
  });
  vm.runInContext(script, context);
  return {
    nodes,
    setStorageWriteFailure(value) { storageWriteFails = value; },
    storedOrder() { return JSON.parse(sharedStorage ? sharedStorage.value : stored); },
    rawStorage() { return sharedStorage ? sharedStorage.value : stored; },
    writeCount() { return writes; },
    pendingTimerCount() { return timers.size; },
    flushTimers() {
      for (const [id, callback] of [...timers]) {
        timers.delete(id);
        callback();
      }
    },
    refresh() {
      const raw = sharedStorage ? sharedStorage.value : stored;
      return startApp(raw === null ? undefined : JSON.parse(raw), sharedStorage);
    },
    fireStorageChange(key = 'sushiro-counter-state-v1', newValue) {
      if (!storageListener) return;
      const event = { key };
      if (arguments.length > 1) event.newValue = newValue;
      storageListener(event);
    },
    run(code) { return vm.runInContext(code, context); },
    async loadMenu(data = menu) {
      resolveMenu({ ok: true, json: async () => data });
      await new Promise(resolve => setImmediate(resolve));
    },
    async failMenu() {
      rejectMenu(new Error('offline'));
      await new Promise(resolve => setImmediate(resolve));
    },
  };
}

function generatedId(name) {
  return 'item_' + crypto.createHash('sha1').update(name, 'utf8').digest('hex').slice(0, 10);
}

function syntheticMenu(items, updatedAt = '2099-01-01') {
  return {
    schemaVersion: 1,
    updatedAt,
    categories: Object.fromEntries(items.map(item => [item.category, [
      { id: item.id, name: item.name, price: item.price },
    ]])),
  };
}

function createServiceWorkerHarness() {
  const handlers = new Map();
  const cached = new Map();
  let networkOnline = true;
  let networkResponse;
  let appShellEntries = [];
  const cache = {
    async addAll(entries) { appShellEntries = entries; },
    async put(key, response) { cached.set(String(key), response); },
  };
  const context = vm.createContext({
    URL,
    self: {
      location: { origin: 'https://counter.test' },
      addEventListener(type, handler) { handlers.set(type, handler); },
      async skipWaiting() {},
      clients: { async claim() {} },
    },
    caches: {
      async open() { return cache; },
      async match(key) { return cached.get(String(key)); },
      async keys() { return ['sushiro-counter-v2']; },
      async delete() { return true; },
    },
    async fetch() {
      if (!networkOnline) throw new Error('offline');
      return networkResponse;
    },
  });
  vm.runInContext(serviceWorkerSource, context);
  return {
    setOnlineMenu(menu) {
      networkResponse = { ok: true, clone() { return this; }, async json() { return menu; } };
      networkOnline = true;
    },
    setOnlineResponse(response) { networkResponse = response; networkOnline = true; },
    goOffline() { networkOnline = false; },
    async requestMenu(url) {
      let response;
      handlers.get('fetch')({
        request: { method: 'GET', url, mode: 'cors' },
        respondWith(promise) { response = promise; },
      });
      return response;
    },
    async install() {
      let completion;
      handlers.get('install')({ waitUntil(promise) { completion = promise; } });
      await completion;
    },
    appShellEntries() { return appShellEntries; },
    async flushCacheWrites() { await new Promise(resolve => setImmediate(resolve)); },
  };
}

function assertReportedBill(app) {
  for (const [id, amount] of Object.entries({
    'bill-subtotal': '$101.00',
    'bill-adjustment': '$42.00',
    'bill-adjusted': '$143.00',
    'bill-service': '$14.30',
    'bill-total': '$157.30',
  })) {
    assert.equal(app.nodes.get(id).textContent, amount, id);
  }
}

test('restored order recalculates the displayed bill after the menu arrives', async () => {
  const app = startApp();
  // Until the menu supplies 炸三寶's price, do not show an understated total.
  assert.equal(app.nodes.get('bill-total').textContent, '待確認價格');
  await app.loadMenu();
  assert(app.nodes.get('other-selected-list').children.some(row => row.innerHTML.includes('炸三寶')));
  assertReportedBill(app);
  assert.equal(app.nodes.get('unpriced-warning').style.display, 'none');
});

test('fresh selections still calculate correctly after the menu arrives', async () => {
  const app = startApp(savedOrder({}));
  await app.loadMenu();
  app.run("changeOtherQty('item_7bfb164de8', 1); changeOtherQty('calpico', 1); changeOtherQty('unagi_chawanmushi', 1);");
  assertReportedBill(app);
});

test('menu prices replace fallback prices in the displayed bill', async () => {
  const app = startApp(savedOrder({ calpico: 1 }));
  assert.equal(app.nodes.get('bill-subtotal').textContent, '$61.00');
  const repricedMenu = JSON.parse(JSON.stringify(menu));
  repricedMenu.categories['飲料'][0].price = 10;
  await app.loadMenu(repricedMenu);
  assert.equal(app.nodes.get('bill-subtotal').textContent, '$63.00');
  assert.equal(app.nodes.get('bill-total').textContent, '$115.50');
});

test('saved menu names and prices survive an offline reload', async () => {
  const online = startApp();
  await online.loadMenu();
  const offline = startApp(online.storedOrder());
  await offline.failMenu();
  assertReportedBill(offline);
  assert(offline.nodes.get('other-selected-list').children.some(row => row.innerHTML.includes('炸三寶')));
  offline.run("changeOtherQty('item_7bfb164de8', 1)");
  assert.equal(offline.nodes.get('bill-subtotal').textContent, '$119.00');
});

test('a removed menu item retains its saved name and price', async () => {
  const online = startApp();
  await online.loadMenu();
  const restored = startApp(online.storedOrder());
  const changedMenu = JSON.parse(JSON.stringify(menu));
  changedMenu.categories['副餐類'] = changedMenu.categories['副餐類'].filter(item => item.name !== '炸三寶');
  await restored.loadMenu(changedMenu);
  assertReportedBill(restored);
  assert(restored.nodes.get('other-selected-list').children.some(row => row.innerHTML.includes('炸三寶')));
});

test('legacy selections retain a fallback price when a new menu removes the item', async () => {
  const app = startApp(savedOrder({ opanchu_drink_set: 1 }));
  assert.equal(app.nodes.get('bill-subtotal').textContent, '$152.00');
  await app.loadMenu();
  assert.equal(app.nodes.get('bill-subtotal').textContent, '$152.00');
  assert(app.nodes.get('other-selected-list').children.some(row => row.innerHTML.includes('聯乘飲品套裝')));
});

test('downloaded prices take precedence over fallback prices on an offline reload', async () => {
  const online = startApp(savedOrder({ calpico: 1 }));
  const changedMenu = JSON.parse(JSON.stringify(menu));
  changedMenu.categories['飲料'][0].price = 10;
  await online.loadMenu(changedMenu);
  const offline = startApp(online.storedOrder());
  await offline.failMenu();
  assert.equal(offline.nodes.get('bill-subtotal').textContent, '$63.00');
  assert.equal(offline.nodes.get('bill-total').textContent, '$115.50');
});

test('new selections save item details for the next offline reload', async () => {
  const online = startApp(savedOrder({}));
  await online.loadMenu();
  online.run("changeOtherQty('item_7bfb164de8', 1)");
  assert.deepEqual(online.storedOrder().otherItemDetails.item_7bfb164de8, {
    name: '炸三寶', price: 18, menuUpdatedAt: '2026-10-14',
  });
  const offline = startApp(online.storedOrder());
  await offline.failMenu();
  assert.equal(offline.nodes.get('bill-subtotal').textContent, '$71.00');
  assert.equal(offline.nodes.get('bill-total').textContent, '$124.30');
});

test('an unknown legacy item warns and withholds a misleading complete total', async () => {
  const app = startApp();
  await app.failMenu();
  assert.equal(app.nodes.get('unpriced-warning').style.display, 'block');
  assert.equal(app.nodes.get('bill-total').textContent, '待確認價格');
  app.run("changePlate('red', 1)");
  assert.equal(app.nodes.get('bill-total').textContent, '待確認價格');
  app.run("changePlate('red', -1); removeOtherItem('item_7bfb164de8')");
  assert.equal(app.nodes.get('unpriced-warning').style.display, 'none');
  assert.equal(app.nodes.get('bill-total').textContent, '$137.50');
});

test('deleting a new adjustment after reload preserves the saved adjustment', async () => {
  const app = startApp();
  await app.loadMenu();
  app.run("document.getElementById('mod-label').value='新折扣'; document.getElementById('mod-value').value='10'; addModification()");
  const ids = Array.from(app.run('state.modifications.map(mod => mod.id)'));
  assert.equal(new Set(ids).size, 2);
  app.run(`removeModification(${JSON.stringify(ids[1])})`);
  assert.equal(app.run('state.modifications.length'), 1);
  assertReportedBill(app);
});

test('previously saved duplicate adjustment IDs are repaired on reload', async () => {
  const order = savedOrder();
  order.modifications.push({ id: 'm0', label: '折扣', sign: '-', unit: '$', value: 10 });
  const app = startApp(order);
  await app.loadMenu();
  const ids = Array.from(app.run('state.modifications.map(mod => mod.id)'));
  assert.equal(new Set(ids).size, 2);
  app.run(`removeModification(${JSON.stringify(ids[1])})`);
  assertReportedBill(app);
});

test('removing or resetting selections clears their saved item details', async () => {
  const app = startApp();
  await app.loadMenu();
  app.run("changeOtherQty('item_7bfb164de8', -1); saveState()");
  assert.equal(app.storedOrder().otherItemDetails.item_7bfb164de8, undefined);
  app.run('resetAll(); saveState()');
  assert.deepEqual(app.storedOrder().otherItemDetails, {});
  assert.equal(app.nodes.get('bill-total').textContent, '$0.00');
});

test('new menu selections persist before an immediate refresh', async () => {
  const item = {
    id: generatedId('未來柚子胡椒烏冬'),
    name: '未來柚子胡椒烏冬',
    price: 31,
    category: '新菜單・麵類',
  };
  const menuData = syntheticMenu([item]);
  const app = startApp(savedOrder({}));
  await app.loadMenu(menuData);

  const writesBeforeSelection = app.writeCount();
  app.run(`changeOtherQty(${JSON.stringify(item.id)}, 1)`);
  assert.equal(app.writeCount(), writesBeforeSelection + 1);
  assert.equal(app.storedOrder().other[item.id], 1);
  assert.equal(app.pendingTimerCount(), 0);

  const refreshed = app.refresh();
  await refreshed.loadMenu(menuData);
  assert.equal(refreshed.storedOrder().other[item.id], 1);
  assert.deepEqual(refreshed.storedOrder().otherItemDetails[item.id], {
    name: item.name, price: item.price, menuUpdatedAt: '2099-01-01',
  });
  assert.equal(refreshed.nodes.get('bill-subtotal').textContent, '$84.00');
  assert.equal(refreshed.nodes.get('bill-total').textContent, '$138.60');
});

test('future IDs and new items from different categories are each counted once after refresh', async () => {
  const items = [
    { name: '未來柚子胡椒烏冬', price: 31, category: '新菜單・麵類', quantity: 2 },
    { name: '雲呢拿紅豆芭菲', price: 14, category: '新菜單・甜品', quantity: 1 },
    { name: '冬季限定松葉蟹軍艦', price: 27, category: '期間限定・冬季祭（2099年1月）', quantity: 1 },
    { name: '未來青檸梳打', price: 9, category: '新菜單・飲料', quantity: 3 },
  ].map(item => ({ ...item, id: generatedId(item.name) }));
  const menuData = syntheticMenu(items);
  const app = startApp(savedOrder({}));
  await app.loadMenu(menuData);

  for (const item of items) {
    for (let quantity = 0; quantity < item.quantity; quantity += 1) {
      app.run(`changeOtherQty(${JSON.stringify(item.id)}, 1)`);
    }
  }

  const refreshed = app.refresh();
  await refreshed.loadMenu(menuData);
  for (const item of items) {
    assert.equal(refreshed.storedOrder().other[item.id], item.quantity, item.name);
  }
  assert.equal(new Set(Object.keys(refreshed.storedOrder().other)).size, items.length);
  assert.equal(refreshed.nodes.get('bill-subtotal').textContent, '$183.00');
  assert.equal(refreshed.nodes.get('bill-adjusted').textContent, '$225.00');
  assert.equal(refreshed.nodes.get('bill-service').textContent, '$22.50');
  assert.equal(refreshed.nodes.get('bill-total').textContent, '$247.50');
});

test('an unresolved future item shows a warning until its delayed menu request completes', async () => {
  const item = {
    id: generatedId('限時海鹽焦糖布甸'),
    name: '限時海鹽焦糖布甸',
    price: 19,
    category: '期間限定・甜品',
  };
  const app = startApp({
    plates: { red: 3, silver: 1, gold: 0, black: 0 },
    other: { [item.id]: 1 },
    modifications: [],
  });
  assert.equal(app.nodes.get('unpriced-warning').style.display, 'block');
  assert.equal(app.nodes.get('bill-total').textContent, '待確認價格');

  await app.loadMenu(syntheticMenu([item]));
  assert.equal(app.nodes.get('unpriced-warning').style.display, 'none');
  assert.equal(app.nodes.get('bill-subtotal').textContent, '$72.00');
  assert.equal(app.nodes.get('bill-total').textContent, '$79.20');
});

test('every committed calculator action is synchronously persisted without timers', async () => {
  const item = { id: generatedId('未來甜品'), name: '未來甜品', price: 16, category: '新甜品' };
  const app = startApp(savedOrder({}));
  await app.loadMenu(syntheticMenu([item]));
  const failures = [];

  const assertSyncWrite = (action, check) => {
    const writesBefore = app.writeCount();
    app.run(action);
    if (app.writeCount() !== writesBefore + 1) failures.push(`${action}: no synchronous write`);
    if (app.pendingTimerCount() !== 0) failures.push(`${action}: pending timer remains`);
    try { check(); } catch (error) { failures.push(`${action}: ${error.message}`); }
  };

  assertSyncWrite("changePlate('gold', 1)", () => assert.equal(app.storedOrder().plates.gold, 1));
  assertSyncWrite("changePlate('gold', -1)", () => assert.equal(app.storedOrder().plates.gold, 0));
  assertSyncWrite(`changeOtherQty(${JSON.stringify(item.id)}, 1)`, () => {
    assert.equal(app.storedOrder().other[item.id], 1);
    assert.deepEqual(app.storedOrder().otherItemDetails[item.id], {
      name: item.name, price: item.price, menuUpdatedAt: '2099-01-01',
    });
  });
  assertSyncWrite(`changeOtherQty(${JSON.stringify(item.id)}, -1)`, () => assert.equal(app.storedOrder().other[item.id], undefined));
  assertSyncWrite(`changeOtherQty(${JSON.stringify(item.id)}, 1)`, () => assert.equal(app.storedOrder().other[item.id], 1));
  assertSyncWrite(`removeOtherItem(${JSON.stringify(item.id)})`, () => assert.equal(app.storedOrder().other[item.id], undefined));
  assertSyncWrite("document.getElementById('mod-label').value='加收'; document.getElementById('mod-value').value='10'; addModification()", () => assert.equal(app.storedOrder().modifications.length, 2));
  const newModId = app.run('state.modifications[1].id');
  assertSyncWrite(`removeModification(${JSON.stringify(newModId)})`, () => assert.equal(app.storedOrder().modifications.length, 1));
  assertSyncWrite('resetAll()', () => {
    if (app.rawStorage() === null) failures.push('resetAll(): storage key was removed instead of persisting reset state');
    assert.deepEqual(app.storedOrder().other, {});
    assert.deepEqual(app.storedOrder().otherItemDetails, {});
    assert.deepEqual(app.storedOrder().modifications, []);
  });
  app.flushTimers();
  assert.deepEqual(failures, []);

  const refreshed = app.refresh();
  await refreshed.loadMenu(syntheticMenu([item]));
  assert.deepEqual(refreshed.storedOrder().other, {});
  assert.equal(refreshed.nodes.get('bill-total').textContent, '$0.00');
});

test('a reset during an unresolved menu request does not restore the previous order', async () => {
  const app = startApp();
  app.run('resetAll()');
  assert.notEqual(app.rawStorage(), null);
  assert.deepEqual(app.storedOrder().other, {});
  assert.deepEqual(app.storedOrder().modifications, []);
  assert.equal(app.pendingTimerCount(), 0);
  await app.loadMenu();
  assert.deepEqual(app.storedOrder().other, {});
  assert.deepEqual(app.storedOrder().plates, { red: 0, silver: 0, gold: 0, black: 0 });

  const refreshed = app.refresh();
  await refreshed.loadMenu();
  assert.deepEqual(refreshed.storedOrder().other, {});
  assert.equal(refreshed.nodes.get('bill-total').textContent, '$0.00');
});

test('a delayed menu response in one tab cannot restore an order reset in another tab', async () => {
  const sharedStorage = createSharedStorage(savedOrder());
  const loadingTab = startApp(savedOrder(), sharedStorage);
  const resetTab = startApp(savedOrder(), sharedStorage);

  resetTab.run('resetAll()');
  assert.deepEqual(JSON.parse(sharedStorage.value).other, {});
  await loadingTab.loadMenu();

  assert.deepEqual(loadingTab.storedOrder().other, {});
  assert.deepEqual(loadingTab.storedOrder().plates, { red: 0, silver: 0, gold: 0, black: 0 });
  assert.deepEqual(loadingTab.storedOrder().modifications, []);
  assert.equal(loadingTab.nodes.get('bill-subtotal').textContent, '$0.00');
  assert.equal(loadingTab.nodes.get('bill-total').textContent, '$0.00');
});

test('a failed delayed menu request cannot overwrite another tab latest selection', async () => {
  const sharedStorage = createSharedStorage(savedOrder({ calpico: 1 }));
  const loadingTab = startApp(savedOrder({ calpico: 1 }), sharedStorage);
  const editingTab = startApp(savedOrder({ calpico: 1 }), sharedStorage);

  editingTab.run("changePlate('gold', 1)");
  await loadingTab.failMenu();

  assert.equal(loadingTab.storedOrder().plates.gold, 1);
  assert.equal(loadingTab.nodes.get('bill-subtotal').textContent, '$83.00');
  assert.equal(loadingTab.nodes.get('bill-total').textContent, '$137.50');
});

test('a newer cross-tab reset wins even if the loading tab edited first', async () => {
  for (const outcome of ['success', 'failure']) {
    const sharedStorage = createSharedStorage(savedOrder());
    const loadingTab = startApp(savedOrder(), sharedStorage);
    loadingTab.run("changePlate('red', 1)");
    const resetTab = startApp(JSON.parse(sharedStorage.value), sharedStorage);
    resetTab.run('resetAll()');

    if (outcome === 'success') await loadingTab.loadMenu();
    else await loadingTab.failMenu();

    assert.deepEqual(loadingTab.storedOrder().plates, { red: 0, silver: 0, gold: 0, black: 0 }, outcome);
    assert.deepEqual(loadingTab.storedOrder().other, {}, outcome);
    assert.equal(loadingTab.nodes.get('bill-total').textContent, '$0.00', outcome);
  }
});

test('an older tab cannot replace a newer synced item price when it edits and refreshes', async () => {
  const item = { id: generatedId('跨分頁價格測試菜式'), name: '跨分頁價格測試菜式', price: 18, category: '期間限定・測試' };
  const oldMenu = syntheticMenu([item], '2026-10-14');
  const newMenu = syntheticMenu([{ ...item, price: 20 }], '2026-10-25');
  const order = {
    plates: { red: 0, silver: 0, gold: 0, black: 0 },
    other: { [item.id]: 1 },
    modifications: [],
  };
  const sharedStorage = createSharedStorage(order);
  const oldTab = startApp(order, sharedStorage);
  await oldTab.loadMenu(oldMenu);
  assert.equal(oldTab.nodes.get('bill-subtotal').textContent, '$18.00');

  const newTab = startApp(JSON.parse(sharedStorage.value), sharedStorage);
  await newTab.loadMenu(newMenu);
  assert.equal(newTab.nodes.get('bill-subtotal').textContent, '$20.00');
  assert.equal(newTab.storedOrder().otherItemDetails[item.id].menuUpdatedAt, '2026-10-25');

  oldTab.fireStorageChange('sushiro-counter-state-v1', sharedStorage.value);
  assert.equal(oldTab.nodes.get('bill-subtotal').textContent, '$20.00');
  oldTab.run(`changeOtherQty(${JSON.stringify(item.id)}, 1)`);
  assert.equal(oldTab.storedOrder().other[item.id], 2);
  assert.deepEqual(oldTab.storedOrder().otherItemDetails[item.id], {
    name: item.name,
    price: 20,
    menuUpdatedAt: '2026-10-25',
  });

  const refreshedWithOldMenu = oldTab.refresh();
  await refreshedWithOldMenu.loadMenu(oldMenu);
  assert.equal(refreshedWithOldMenu.storedOrder().other[item.id], 2);
  assert.equal(refreshedWithOldMenu.nodes.get('bill-subtotal').textContent, '$40.00');
  assert.equal(refreshedWithOldMenu.storedOrder().otherItemDetails[item.id].price, 20);

  const refreshedWithNewMenu = refreshedWithOldMenu.refresh();
  await refreshedWithNewMenu.loadMenu(syntheticMenu([{ ...item, price: 22 }], '2026-10-26'));
  assert.equal(refreshedWithNewMenu.nodes.get('bill-subtotal').textContent, '$44.00');
  assert.equal(refreshedWithNewMenu.storedOrder().otherItemDetails[item.id].price, 22);
  assert.equal(refreshedWithNewMenu.storedOrder().otherItemDetails[item.id].menuUpdatedAt, '2026-10-26');
});

test('a storage event refreshes another open tab calculation and selection list', () => {
  const sharedStorage = createSharedStorage(savedOrder({ calpico: 1 }));
  const firstTab = startApp(savedOrder({ calpico: 1 }), sharedStorage);
  const secondTab = startApp(savedOrder({ calpico: 1 }), sharedStorage);

  secondTab.run('resetAll()');
  firstTab.fireStorageChange();

  assert.deepEqual(firstTab.storedOrder().other, {});
  assert.deepEqual(firstTab.storedOrder().plates, { red: 0, silver: 0, gold: 0, black: 0 });
  assert.equal(firstTab.nodes.get('bill-subtotal').textContent, '$0.00');
  assert.equal(firstTab.nodes.get('bill-total').textContent, '$0.00');
});

test('a queued older storage event cannot rewind a newer local save', () => {
  const order = savedOrder({});
  const sharedStorage = createSharedStorage(order);
  const firstTab = startApp(order, sharedStorage);
  const secondTab = startApp(order, sharedStorage);

  secondTab.run("changePlate('gold', 1)");
  const queuedEventValue = sharedStorage.value;
  firstTab.run("changePlate('red', 1)");
  const currentValue = sharedStorage.value;
  assert.notEqual(currentValue, queuedEventValue);

  firstTab.fireStorageChange('sushiro-counter-state-v1', queuedEventValue);

  assert.equal(firstTab.run('state.plates.red'), 4);
  assert.equal(firstTab.run('state.plates.gold'), 1);
  assert.equal(firstTab.storedOrder().plates.red, 4);
  assert.equal(firstTab.storedOrder().plates.gold, 1);
  assert.equal(firstTab.nodes.get('bill-subtotal').textContent, '$87.00');
});

test('a delayed menu request preserves in-memory edits when localStorage writes fail', async () => {
  const order = {
    plates: { red: 3, silver: 1, gold: 0, black: 0 },
    other: {},
    modifications: [],
  };

  for (const outcome of ['success', 'failure']) {
    const app = startApp(order);
    app.setStorageWriteFailure(true);
    app.run("changePlate('red', 1)");
    assert.equal(app.nodes.get('bill-subtotal').textContent, '$65.00');

    if (outcome === 'success') await app.loadMenu();
    else await app.failMenu();

    assert.equal(app.run('state.plates.red'), 4, outcome);
    assert.equal(app.nodes.get('bill-subtotal').textContent, '$65.00', outcome);
  }
});

test('future items remain stable across repricing, category changes and repeated refreshes', async () => {
  const item = { id: generatedId('未來蟹肉烏冬'), name: '未來蟹肉烏冬', price: 26, category: '新菜單・麵類' };
  const originalMenu = syntheticMenu([item]);
  const app = startApp(savedOrder({}));
  await app.loadMenu(originalMenu);
  app.run(`changeOtherQty(${JSON.stringify(item.id)}, 1)`);

  const movedAndRepriced = { ...item, price: 29, category: '期間限定・冬季烏冬' };
  let refreshed = app.refresh();
  await refreshed.loadMenu(syntheticMenu([movedAndRepriced]));
  assert.equal(refreshed.nodes.get('bill-subtotal').textContent, '$82.00');
  assert.equal(refreshed.storedOrder().otherItemDetails[item.id].price, 29);

  for (let count = 0; count < 3; count += 1) {
    refreshed = refreshed.refresh();
    await refreshed.loadMenu(syntheticMenu([movedAndRepriced]));
    assert.equal(refreshed.storedOrder().other[item.id], 1);
    assert.equal(refreshed.nodes.get('bill-subtotal').textContent, '$82.00');
    assert.equal(refreshed.nodes.get('bill-total').textContent, '$136.40');
  }
});

test('removing a generated item stays removed after an immediate refresh', async () => {
  const item = { id: generatedId('未來炸魚餅'), name: '未來炸魚餅', price: 15, category: '新副餐類' };
  const menuData = syntheticMenu([item]);
  const app = startApp(savedOrder({}));
  await app.loadMenu(menuData);
  app.run(`changeOtherQty(${JSON.stringify(item.id)}, 1)`);
  app.run(`removeOtherItem(${JSON.stringify(item.id)})`);
  assert.equal(app.storedOrder().other[item.id], undefined);

  const refreshed = app.refresh();
  await refreshed.loadMenu(menuData);
  assert.equal(refreshed.storedOrder().other[item.id], undefined);
  assert.equal(refreshed.storedOrder().otherItemDetails[item.id], undefined);
  assert.equal(refreshed.nodes.get('bill-subtotal').textContent, '$53.00');
  assert.equal(refreshed.nodes.get('bill-total').textContent, '$104.50');
});

test('removed generated items retain their saved price and legacy unknown prices stay visible', async () => {
  const item = { id: generatedId('未來香芋雪糕'), name: '未來香芋雪糕', price: 21, category: '期間限定・甜品' };
  const online = startApp(savedOrder({}));
  await online.loadMenu(syntheticMenu([item]));
  online.run(`changeOtherQty(${JSON.stringify(item.id)}, 1)`);

  const removed = online.refresh();
  await removed.loadMenu(syntheticMenu([
    { id: 'unrelated_menu_item', name: '其他菜色', price: 5, category: '其他' },
  ]));
  assert(removed.nodes.get('other-selected-list').children.some(row => row.innerHTML.includes(item.name)));
  assert.equal(removed.nodes.get('bill-subtotal').textContent, '$74.00');

  const unknownId = generatedId('沒有價格的舊項目');
  const unknown = startApp({ plates: { red: 0, silver: 0, gold: 0, black: 0 }, other: { [unknownId]: 2 }, modifications: [] });
  await unknown.failMenu();
  assert(unknown.nodes.get('other-selected-list').children.some(row => row.innerHTML.includes('未能識別項目')));
  assert.equal(unknown.nodes.get('unpriced-warning').style.display, 'block');
  assert.equal(unknown.nodes.get('bill-total').textContent, '待確認價格');
});

test('a renamed generated item keeps its saved identity without merging into the new item', async () => {
  const oldItem = { id: generatedId('季節限定胡麻烏冬'), name: '季節限定胡麻烏冬', price: 17, category: '期間限定・麵類' };
  const app = startApp(savedOrder({}));
  await app.loadMenu(syntheticMenu([oldItem]));
  app.run(`changeOtherQty(${JSON.stringify(oldItem.id)}, 1)`);

  const newItem = { id: generatedId('改名季節胡麻烏冬'), name: '改名季節胡麻烏冬', price: 19, category: oldItem.category };
  const refreshed = app.refresh();
  await refreshed.loadMenu(syntheticMenu([newItem]));
  assert.equal(refreshed.storedOrder().other[oldItem.id], 1);
  assert.equal(refreshed.storedOrder().other[newItem.id], undefined);
  assert.equal(refreshed.storedOrder().otherItemDetails[oldItem.id].name, oldItem.name);
  assert.equal(refreshed.nodes.get('bill-subtotal').textContent, '$70.00');
  assert(refreshed.nodes.get('other-selected-list').children.some(row => row.innerHTML.includes(oldItem.name)));
});

test('menu validation rejects duplicate IDs across categories and invalid prices', () => {
  const app = startApp(savedOrder({}));
  const duplicateIds = {
    schemaVersion: 1,
    categories: {
      '新分類 A': [{ id: 'same-id', name: '菜色 A', price: 10 }],
      '新分類 B': [{ id: 'same-id', name: '菜色 B', price: 12 }],
    },
  };
  const negativePrice = syntheticMenu([
    { id: 'negative-price', name: '錯誤價格', price: -1, category: '新分類' },
  ]);
  const stringPrice = syntheticMenu([
    { id: 'string-price', name: '非數字價格', price: '18', category: '新分類' },
  ]);
  const blankName = syntheticMenu([
    { id: 'blank-name', name: '  ', price: 18, category: '新分類' },
  ]);
  const blankId = syntheticMenu([
    { id: '  ', name: '無效識別碼', price: 18, category: '新分類' },
  ]);
  const invalidDate = syntheticMenu([
    { id: 'invalid-date', name: '無效更新日期', price: 18, category: '新分類' },
  ], 'not-a-date');
  assert.equal(app.run(`validateMenuData(${JSON.stringify(duplicateIds)})`), false);
  assert.equal(app.run(`validateMenuData(${JSON.stringify(negativePrice)})`), false);
  assert.equal(app.run(`validateMenuData(${JSON.stringify(stringPrice)})`), false);
  assert.equal(app.run(`validateMenuData(${JSON.stringify(blankName)})`), false);
  assert.equal(app.run(`validateMenuData(${JSON.stringify(blankId)})`), false);
  assert.equal(app.run(`validateMenuData(${JSON.stringify(invalidDate)})`), false);
});

test('multiple discounts and adjustments survive immediate refresh', async () => {
  const app = startApp();
  await app.loadMenu();
  app.run("setModSign('+'); setModUnit('$'); document.getElementById('mod-label').value='附加'; document.getElementById('mod-value').value='8'; addModification()");
  app.run("setModSign('-'); setModUnit('%'); document.getElementById('mod-label').value='優惠'; document.getElementById('mod-value').value='10'; addModification()");
  assert.equal(app.storedOrder().modifications.length, 3);

  const refreshed = app.refresh();
  await refreshed.loadMenu();
  assert.equal(refreshed.storedOrder().modifications.length, 3);
  assert.deepEqual(refreshed.storedOrder().modifications.map(mod => [mod.label, mod.sign, mod.unit, mod.value]), [
    ['海老', '+', '$', 42],
    ['附加', '+', '$', 8],
    ['優惠', '-', '%', 10],
  ]);
  assert.equal(refreshed.nodes.get('bill-subtotal').textContent, '$101.00');
  assert.equal(refreshed.nodes.get('bill-adjustment').textContent, '$39.90');
  assert.equal(refreshed.nodes.get('bill-adjusted').textContent, '$140.90');
  assert.equal(refreshed.nodes.get('bill-service').textContent, '$14.09');
  assert.equal(refreshed.nodes.get('bill-total').textContent, '$154.99');
});

test('service-worker menu cache serves the latest valid menu after an offline refresh', async () => {
  const item = { id: generatedId('快取限定菜式'), name: '快取限定菜式', price: 24, category: '期間限定・快取測試' };
  const cachedMenu = syntheticMenu([item]);
  const serviceWorker = createServiceWorkerHarness();
  serviceWorker.setOnlineMenu(cachedMenu);
  await serviceWorker.install();
  assert.equal(serviceWorker.appShellEntries().includes('./menu.json'), false);

  const onlineResponse = await serviceWorker.requestMenu('https://counter.test/menu.json?ts=1');
  assert.deepEqual(JSON.parse(JSON.stringify(await onlineResponse.json())), cachedMenu);
  await serviceWorker.flushCacheWrites();

  serviceWorker.setOnlineResponse({
    ok: true,
    clone() { return this; },
    async json() { return { error: 'temporary upstream error' }; },
  });
  const errorPayloadResponse = await serviceWorker.requestMenu('https://counter.test/menu.json?ts=error');
  assert.deepEqual(JSON.parse(JSON.stringify(await errorPayloadResponse.json())), cachedMenu);

  serviceWorker.setOnlineMenu({
    schemaVersion: 1,
    categories: { '錯誤菜單': [{ id: item.id, name: '錯誤菜單', price: -1 }] },
  });
  const invalidPriceResponse = await serviceWorker.requestMenu('https://counter.test/menu.json?ts=bad-price');
  assert.deepEqual(JSON.parse(JSON.stringify(await invalidPriceResponse.json())), cachedMenu);

  serviceWorker.setOnlineMenu({
    schemaVersion: 1,
    categories: { '錯誤菜單': [{ id: '  ', name: '空白識別碼', price: 10 }] },
  });
  const blankIdResponse = await serviceWorker.requestMenu('https://counter.test/menu.json?ts=blank-id');
  assert.deepEqual(JSON.parse(JSON.stringify(await blankIdResponse.json())), cachedMenu);

  serviceWorker.setOnlineMenu({
    schemaVersion: 1,
    updatedAt: 'not-a-date',
    categories: { '錯誤菜單': [{ id: item.id, name: '無效更新日期', price: 10 }] },
  });
  const invalidDateResponse = await serviceWorker.requestMenu('https://counter.test/menu.json?ts=invalid-date');
  assert.deepEqual(JSON.parse(JSON.stringify(await invalidDateResponse.json())), cachedMenu);

  serviceWorker.goOffline();
  const offlineResponse = await serviceWorker.requestMenu('https://counter.test/menu.json?ts=2');
  const offlineMenu = await offlineResponse.json();
  assert.deepEqual(JSON.parse(JSON.stringify(offlineMenu)), cachedMenu);

  const app = startApp({
    plates: { red: 3, silver: 1, gold: 0, black: 0 },
    other: { [item.id]: 1 },
    modifications: [],
  });
  await app.loadMenu(offlineMenu);
  assert.equal(app.nodes.get('bill-subtotal').textContent, '$77.00');
  assert.equal(app.nodes.get('bill-total').textContent, '$84.70');
});

test('the calculator self-test still passes with restored price resolution', () => {
  const app = startApp();
  const result = app.run('window.__selftest()');
  assert.equal(result.pass, true);
  assert.equal(Array.from(result.details).every(detail => detail.startsWith('✅')), true);
});
