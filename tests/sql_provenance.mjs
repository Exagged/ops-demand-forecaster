import { PGlite } from '@electric-sql/pglite';
import fs from 'node:fs';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';
const root=fileURLToPath(new URL('..', import.meta.url));
const db=new PGlite();
for(let repeat=0;repeat<2;repeat++) for(const name of fs.readdirSync(root+'/sql').filter(x=>x.endsWith('.sql')).sort()) await db.exec(fs.readFileSync(root+'/sql/'+name,'utf8'));
await db.exec(`INSERT INTO category(source_label,display_name,in_scope) VALUES ('Noise - Residential','Noise',true),('Illegal Parking','Parking',true),('Blocked Driveway','Driveway',true);
INSERT INTO area(level,source_label,display_name,in_scope) VALUES ('borough','BROOKLYN','Brooklyn',true);`);
async function load(id,mode,status,cat,started,observe=true){
 const payload={unique_key:'1',created_date:'2026-08-01T10:00:00',agency:'NYPD',borough:'BROOKLYN',complaint_type:cat};
 await db.query(`INSERT INTO ingestion_run(run_id,mode,query,window_start,window_end,started_at,fetched_at,rows_fetched,source_count,status) VALUES ($1,$2,'test','2026-08-01','2026-08-01',$3,$3,1,1,$4)`,[id,mode,started,status]);
 await db.query(`INSERT INTO service_request(request_id,created_at,created_date,agency,category_id,area_id,first_seen_run,last_seen_run) VALUES (1,'2026-08-01T10:00:00','2026-08-01','NYPD',(SELECT category_id FROM category WHERE source_label=$1),1,$2,$2) ON CONFLICT(request_id) DO UPDATE SET category_id=EXCLUDED.category_id,last_seen_run=$2`,[cat,id]);
 await db.query(`INSERT INTO request_version(request_id,run_id,payload_hash,payload) VALUES (1,$1,$2,$3) ON CONFLICT DO NOTHING`,[id,cat,payload]);
 if(observe) await db.query(`INSERT INTO request_observation VALUES (1,$1,$2)`,[id,payload]);
}
const category=async()=> (await db.query('SELECT category FROM forecast_live_request')).rows.map(x=>x.category);
await load(1,'fixtures','ok','Noise - Residential','2026-09-01T12:00:00Z',false);
assert.deepEqual(await category(),[]);
await db.query(`SELECT rebuild_daily_demand('2026-08-01','2026-08-01')`);
assert.equal((await db.query('SELECT bool_or(is_complete) AS b FROM daily_demand')).rows[0].b,false);
await load(2,'backfill','ok','Noise - Residential','2026-09-02T12:00:00Z',false);
assert.deepEqual(await category(),['Noise - Residential']); // identical legacy hash first in fixture
await load(3,'backfill','ok','Illegal Parking','2026-09-03T12:00:00Z');
assert.deepEqual(await category(),['Illegal Parking']);
await load(4,'backfill','ok','Noise - Residential','2026-09-04T12:00:00Z'); // reversion
await load(5,'incremental','failed','Illegal Parking','2026-09-05T12:00:00Z');
assert.deepEqual(await category(),['Noise - Residential']);
await load(6,'fixtures','ok','Illegal Parking','2026-09-06T12:00:00Z');
assert.deepEqual(await category(),['Noise - Residential']);
await db.query(`SELECT rebuild_daily_demand('2026-08-01','2026-08-02')`);
let rows=(await db.query('SELECT demand_date::text,request_count,is_complete FROM daily_demand ORDER BY demand_date,category_id')).rows;
assert.deepEqual(rows.map(x=>x.request_count),[1,0,0,0,0,0]);
assert.deepEqual(rows.map(x=>x.is_complete),[true,true,true,false,false,false]);
console.log('PASS: migrations applied twice; fixtures excluded; identical legacy live hash recovered; live reversion preserved across failed/fixture edits; grid counts/completeness correct.');
await db.close();
