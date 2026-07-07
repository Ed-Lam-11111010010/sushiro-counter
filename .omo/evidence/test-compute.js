// Extract and test the computeBill logic from index.html
// Replicates the exact logic to verify math correctness

const PLATES = [
  { id:'red',    label:'紅碟', price:12 },
  { id:'silver', label:'銀碟', price:17 },
  { id:'gold',   label:'金碟', price:22 },
  { id:'black',  label:'黑碟', price:27 },
];

const OTHER_ITEMS = {
  '麵類・湯類': [
    { id:'chawanmushi', name:'茶碗蒸', price:19 },
    { id:'tonkotsu_ramen', name:'豚骨拉麵', price:32 },
  ],
};

function computeBill(state) {
  let plateTotal = 0;
  PLATES.forEach(p => { plateTotal += (state.plates[p.id] || 0) * p.price; });
  let otherTotal = 0;
  Object.entries(state.other).forEach(([id, qty]) => {
    for (const cat of Object.values(OTHER_ITEMS)) {
      const item = cat.find(i => i.id === id);
      if (item) { otherTotal += qty * item.price; break; }
    }
  });
  const subtotal = plateTotal + otherTotal;
  let adjustment = 0;
  state.modifications.forEach(m => {
    if (m.unit === '$') {
      adjustment += (m.sign === '-' ? -1 : 1) * m.value;
    } else {
      adjustment += (m.sign === '-' ? -1 : 1) * (subtotal * m.value / 100);
    }
  });
  let adjusted = subtotal + adjustment;
  let clamped = false;
  if (adjusted < 0) { adjusted = 0; clamped = true; }
  adjusted = Math.round(adjusted * 100) / 100;
  const service = Math.round(adjusted * 0.10 * 100) / 100;
  const total = Math.round((adjusted + service) * 100) / 100;
  return { subtotal, adjustment, adjusted, service, total, clamped };
}

// Self-test cases
const tests = [
  {
    name: 'Standard bill (red×2 + black×1 + chawanmushi + tonkotsu_ramen − $20)',
    state: {
      plates: { red:2, silver:0, gold:0, black:1 },
      other: { chawanmushi:1, tonkotsu_ramen:1 },
      modifications: [{ id:'t1', label:'優惠券', sign:'-', unit:'$', value:20 }],
    },
    expect: { subtotal:102, adjustment:-20, adjusted:82, service:8.20, total:90.20, clamped:false },
  },
  {
    name: 'Negative clamp (−$999)',
    state: {
      plates: { red:2, silver:0, gold:0, black:1 },
      other: { chawanmushi:1, tonkotsu_ramen:1 },
      modifications: [{ id:'t2', label:'大折扣', sign:'-', unit:'$', value:999 }],
    },
    expect: { subtotal:102, adjustment:-999, adjusted:0, service:0, total:0, clamped:true },
  },
  {
    name: 'Percent discount (−10%)',
    state: {
      plates: { red:2, silver:0, gold:0, black:1 },
      other: { chawanmushi:1, tonkotsu_ramen:1 },
      modifications: [{ id:'t3', label:'9折', sign:'-', unit:'%', value:10 }],
    },
    expect: { subtotal:102, adjustment:-10.2, adjusted:91.8, service:9.18, total:100.98, clamped:false },
  },
  {
    name: 'Markup (+$30)',
    state: {
      plates: { red:2, silver:0, gold:0, black:1 },
      other: { chawanmushi:1, tonkotsu_ramen:1 },
      modifications: [{ id:'t4', label:'加購', sign:'+', unit:'$', value:30 }],
    },
    expect: { subtotal:102, adjustment:30, adjusted:132, service:13.2, total:145.2, clamped:false },
  },
  {
    name: 'All zeros',
    state: {
      plates: { red:0, silver:0, gold:0, black:0 },
      other: {},
      modifications: [],
    },
    expect: { subtotal:0, adjustment:0, adjusted:0, service:0, total:0, clamped:false },
  },
];

let allPass = true;
tests.forEach(t => {
  const got = computeBill(t.state);
  const exp = t.expect;
  const pass = ['subtotal','adjustment','adjusted','service','total','clamped'].every(k => got[k] === exp[k]);
  console.log((pass ? '✅' : '❌') + ' ' + t.name);
  if (!pass) {
    allPass = false;
    console.log('  expected:', JSON.stringify(exp));
    console.log('  got:     ', JSON.stringify(got));
  }
});

console.log(allPass ? '\n✅ ALL PASSED' : '\n❌ SOME FAILED');
process.exit(allPass ? 0 : 1);
