const fs = require('fs');

const current = JSON.parse(fs.readFileSync('epidemiology.json', 'utf8'));

const ISO3_MAP = {
  ARG:'AR', CHL:'CL', BRA:'BR', USA:'US', PRY:'PY', BOL:'BO',
  DEU:'DE', FRA:'FR', FIN:'FI', SWE:'SE', CHN:'CN', KOR:'KR',
  RUS:'RU', CAN:'CA', MEX:'MX', PER:'PE', COL:'CO', MNG:'MN',
};

async function fetchWHOGHO() {
  try {
    const res = await fetch('https://ghoapi.azureedge.net/api/Indicator?$filter=contains(IndicatorName,\'anta\')&$select=IndicatorCode,IndicatorName');
    const data = await res.json();
    const codes = (data.value || []).map(i => i.IndicatorCode);
    console.log(`WHO GHO: ${codes.length} indicateurs trouvés`);
    const updates = {};
    for (const code of codes.slice(0, 5)) {
      try {
        const r = await fetch(`https://ghoapi.azureedge.net/api/${code}?$filter=TimeDim ge 2020&$select=SpatialDim,TimeDim,Value`);
        const d = await r.json();
        for (const row of (d.value || [])) {
          const iso2 = ISO3_MAP[row.SpatialDim];
          if (!iso2) continue;
          const val = Math.round(parseFloat(row.Value));
          if (!isNaN(val) && val > 0) {
            if (!updates[iso2] || row.TimeDim > updates[iso2].year) {
              updates[iso2] = { year: row.TimeDim, cases: val, source: 'WHO GHO' };
            }
          }
        }
      } catch (e) { console.warn(`WHO GHO ${code}:`, e.message); }
    }
    return updates;
  } catch (e) {
    console.warn('WHO GHO erreur:', e.message);
    return {};
  }
}

async function fetchWHODON() {
  try {
    const res = await fetch('https://api.rss2json.com/v1/api.json?rss_url=https%3A%2F%2Fwww.who.int%2Ffeeds%2Fentity%2Fcsr%2Fdon%2Flang--en%2Frss.xml&count=50');
    const data = await res.json();
    const items = (data.items || []).filter(item => {
      const text = ((item.title || '') + ' ' + (item.description || '')).toLowerCase();
      return text.includes('hanta') || text.includes('puumala') || text.includes('hantaan');
    });
    console.log(`WHO DON: ${items.length} alertes Hantavirus`);
    return items;
  } catch (e) {
    console.warn('WHO DON erreur:', e.message);
    return [];
  }
}

async function main() {
  console.log('Démarrage mise à jour...');
  const [whoData, donItems] = await Promise.all([fetchWHOGHO(), fetchWHODON()]);

  const updated = {
    ...current,
    generatedAt: new Date().toISOString(),
    countries: current.countries.map(country => {
      let c = { ...country };
      const who = whoData[country.code];
      if (who && who.cases > 0) {
        const existingHist = c.historicalData || [];
        const alreadyHas = existingHist.some(h => h.year === who.year);
        if (!alreadyHas) {
          c.historicalData = [...existingHist, who].sort((a, b) => a.year - b.year);
          console.log(`${country.code}: ${who.year} → ${who.cases} cas (WHO GHO)`);
        }
      }
      return c;
    }),
  };

  fs.writeFileSync('epidemiology.json', JSON.stringify(updated, null, 2));
  console.log('epidemiology.json mis à jour');
  console.log(`WHO GHO: ${Object.keys(whoData).length} pays | WHO DON: ${donItems.length} alertes`);
}

main().catch(err => { console.error('Erreur:', err); process.exit(1); });
