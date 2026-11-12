import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
const w=JSON.parse(fs.readFileSync(new URL('./calendar-gateway.n8n.json',import.meta.url),'utf8'));
const nodes=Object.fromEntries(w.nodes.map(n=>[n.id,n]));
const prepare=new Function('$json',nodes.request.parameters.jsCode);
const run=body=>prepare({body})[0].json;
const period={timeMin:'2026-11-18T17:00:00Z',timeMax:'2026-11-18T17:45:00Z'};
const id='a'.repeat(40);
const meeting={id,start:{dateTime:period.timeMin},end:{dateTime:period.timeMax},attendees:[{email:'synthetic@example.com'}],extendedProperties:{private:{owner:'synthetic'}},conferenceData:{createRequest:{conferenceSolutionKey:{type:'hangoutsMeet'}}}};
test('freebusy fixes the real calendar and does not accept caller URLs or calendar ids',()=>{
 const r=run({op:'freebusy',...period,calendar:'attacker',url:'https://example.invalid'});
 assert.equal(r.url,'https://www.googleapis.com/calendar/v3/freeBusy');
 assert.deepEqual(r.body.items,[{id:'ricardohenrique@agenciavetorial.com'}]);
 assert.equal(r.body.timeMin,period.timeMin);
});
test('all operations route through literal sendBody flags (n8n dynamic switch dropped JSON)',()=>{
 assert.equal(nodes.google.parameters.sendBody,true);
 assert.equal(nodes['google-no-body'].parameters.sendBody,false);
 for(const op of ['get','list','freebusy','insert','patch','delete']){
  const r=run({op,id,...period,body:meeting});
  assert.equal(r.sendBody,['insert','patch','freebusy'].includes(op));
  assert.equal(r.sendBody,['POST','PATCH'].includes(r.method));
 }
});
test('rejects long intervals, arbitrary event ids and appointment duration changes',()=>{
 assert.throws(()=>run({op:'freebusy',...period,timeMax:'2026-11-18T18:00:00Z'}));
 assert.throws(()=>run({op:'get',id:'https://example.invalid'}));
 assert.throws(()=>run({op:'insert',body:{...meeting,end:{dateTime:'2026-11-18T18:00:00Z'}}}));
 assert.throws(()=>run({op:'batchDelete'}));
});
