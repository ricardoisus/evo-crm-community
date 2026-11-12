// n8n Code node, after a Webhook with headerAuth. Never accept calendar IDs/URLs.
const input = $json.body;
// Agenda "Trabalho" (primária de ricardohenrique@agenciavetorial.com); credencial n8n 8plZrE3YozmTzatm é owner (12/11/2026).
const calendar = 'ricardohenrique@agenciavetorial.com';
if (calendar.startsWith('CONFIGURE_')) throw new Error('Calendar not configured');
const ops = ['get','list','freebusy','insert','patch','delete'];
if (!input || !ops.includes(input.op)) throw new Error('Invalid operation');
const base = 'https://www.googleapis.com/calendar/v3';
const events = `${base}/calendars/${encodeURIComponent(calendar)}/events`;
const id = input.id;
if (['get','patch','delete'].includes(input.op) && !/^[0-9a-f]{40}$/.test(id || '')) throw new Error('Invalid event id');
const methods = { get:'GET',list:'GET',freebusy:'POST',insert:'POST',patch:'PATCH',delete:'DELETE' };
let url = input.op==='freebusy' ? `${base}/freeBusy` : events;
let query = {};
let body = {};
if (['get','patch','delete'].includes(input.op)) url += '/' + id;
if (['list','freebusy'].includes(input.op)) {
  const lo = Date.parse(input.timeMin), hi = Date.parse(input.timeMax);
  if (!Number.isFinite(lo) || !Number.isFinite(hi) || hi <= lo || hi-lo > 2700000) throw new Error('Invalid interval');
  if (input.op === 'list') query={timeMin:input.timeMin,timeMax:input.timeMax,singleEvents:true,showDeleted:false,maxResults:2500};
  else body={timeMin:input.timeMin,timeMax:input.timeMax,timeZone:'America/Sao_Paulo',items:[{id:calendar}]};
}
if (['insert','patch'].includes(input.op)) {
  const b = input.body;
  if (!b || Date.parse(b.end?.dateTime)-Date.parse(b.start?.dateTime)!==2700000 ||
      !Array.isArray(b.attendees) || b.attendees.length!==1 || !b.extendedProperties?.private?.owner ||
      b.conferenceData?.createRequest?.conferenceSolutionKey?.type!=='hangoutsMeet') throw new Error('Invalid appointment');
  if (input.op==='insert' && !/^[0-9a-f]{40}$/.test(b.id || '')) throw new Error('Invalid event id');
  body={summary:'Diagnóstico Vetorial',start:b.start,end:b.end,attendees:b.attendees,
        extendedProperties:{private:{owner:b.extendedProperties.private.owner}},conferenceData:b.conferenceData,status:'confirmed'};
  if (input.op==='insert') body.id=b.id;
  query={sendUpdates:'all',conferenceDataVersion:1};
}
if (input.op==='delete') query={sendUpdates:'all'};
return [{json:{method:methods[input.op],url,query,body,sendBody:['insert','patch','freebusy'].includes(input.op)}}];
