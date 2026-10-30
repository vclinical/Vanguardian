import {chromium} from 'playwright';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';
import assert from 'node:assert/strict';
// Read-only regression for the subject31 RBC audit and QTc issue301 fixtures.
const phase='after';
const out=path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../../../../outputs/visual_audit_31');
await fs.mkdir(out,{recursive:true});
assert.ok(process.env.PLAYWRIGHT_SESSION_FILE,'Set PLAYWRIGHT_SESSION_FILE to a private JSON file with session_key.');
const auth=JSON.parse(await fs.readFile(process.env.PLAYWRIGHT_SESSION_FILE,'utf8'));
const browser=await chromium.launch({headless:true});
const results=[];
for(const viewport of [{width:1440,height:1000},{width:1280,height:720}]) {
 const context=await browser.newContext({viewport});
 await context.addCookies([{name:'sessionid',value:auth.session_key,domain:'127.0.0.1',path:'/',httpOnly:true,sameSite:'Lax'}]);
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 for(const type of ['rbc','qtc']) {
  const modal=type==='rbc'?'[data-field-audit-history-modal]':'[data-validation-issue-modal]';
  const trigger=type==='rbc'?'[data-field-audit-history-modal-trigger][data-field-key="LAB_CBC_RBC"]':'[data-validation-issue-modal-trigger]';
  const close=type==='rbc'?'[data-field-audit-history-modal-close]':'[data-validation-issue-modal-close]';
  await page.goto(`http://127.0.0.1:8000/studies/3/subjects/31/?event=473&form=${type==='rbc'?638:639}`,{waitUntil:'networkidle'});
  await page.locator(trigger).click();
  await page.locator(modal).waitFor({state:'visible'});
  if(type==='rbc') await page.waitForFunction(()=>document.querySelector('[data-field-audit-history-table-body]')?.innerText.includes('5.50'));
  const text=await page.locator(modal).innerText();
  if(type==='rbc') {
   assert.equal(await page.locator(`${modal} tbody tr`).count(),2);
   assert.ok(text.includes('thudt')&&text.includes('nhuquynh1395@gmail.com'));
   assert.ok(text.includes('28-10-2026 15:27'));
  }else{
   assert.equal(await page.locator(`${modal} [data-validation-issue-history-entry]`).count(),1);
   assert.ok(text.includes('Acknowledged'));
   assert.equal((text.match(/Please verify\./g)||[]).length,3); // active issue plus two FAIL snapshots
   if(phase==='after') assert.ok(/15:36:23|3:36:23/.test(text)&&/15:36:25|3:36:25/.test(text),text);
  }
  const layout=await page.locator(modal).evaluate(e=>{
   const dialog=e.querySelector('[role="dialog"]');const r=dialog.getBoundingClientRect();
   return {dialog:{x:r.x,y:r.y,width:r.width,height:r.height,right:r.right,bottom:r.bottom},viewport:{w:innerWidth,h:innerHeight},
    modalFits:r.x>=0&&r.y>=0&&r.right<=innerWidth+1&&r.bottom<=innerHeight+1,
    horizontalOverflow:dialog.scrollWidth>dialog.clientWidth+1,
    verticalScrollers:[dialog,...dialog.querySelectorAll('*')].filter(x=>x.scrollHeight>x.clientHeight+2&&['auto','scroll'].includes(getComputedStyle(x).overflowY)).map(x=>({class:x.className,height:x.clientHeight,content:x.scrollHeight})),
    initialFocusInside:dialog.contains(document.activeElement)};
  });
  await page.screenshot({path:`${out}/${type}-${viewport.width}x${viewport.height}-${phase}.png`});
  const focusPath=[];
  for(let i=0;i<6;i++) {await page.keyboard.press('Tab');focusPath.push(await page.locator(modal).evaluate(e=>({inside:e.contains(document.activeElement),text:document.activeElement?.textContent?.trim().slice(0,50),tag:document.activeElement?.tagName})));}
  const backwardFocusPath=[];
  for(let i=0;i<6;i++){await page.keyboard.press('Shift+Tab');backwardFocusPath.push(await page.locator(modal).evaluate(e=>({inside:e.contains(document.activeElement),tag:document.activeElement?.tagName})));}
  await page.keyboard.press('Escape');assert.ok(await page.locator(modal).isHidden());
  const escapeRestoresFocus=await page.locator(trigger).evaluate(e=>e===document.activeElement);
  await page.locator(trigger).click();await page.locator(close).click();assert.ok(await page.locator(modal).isHidden());
  const closeRestoresFocus=await page.locator(trigger).evaluate(e=>e===document.activeElement);
  if(phase==='after'){assert.ok(layout.modalFits,JSON.stringify(layout));assert.equal(layout.horizontalOverflow,false);assert.ok(layout.verticalScrollers.length<=1);assert.ok(layout.initialFocusInside);assert.ok(focusPath.every(x=>x.inside));assert.ok(backwardFocusPath.every(x=>x.inside));assert.ok(escapeRestoresFocus);assert.ok(closeRestoresFocus);}
  let syntheticLongHistory=null;
  if(type==='qtc') {
   await page.locator(trigger).click();
   syntheticLongHistory=await page.locator(modal).evaluate(e=>{
    const history=e.querySelector('[data-validation-issue-history-list]');
    const group=history.querySelector('[data-validation-issue-history-entry]');
    for(let i=0;i<12;i++)history.appendChild(group.cloneNode(true));
    const dialog=e.querySelector('[role="dialog"]');const rect=dialog.getBoundingClientRect();
    const close=e.querySelector('[data-validation-issue-modal-close]').getBoundingClientRect();
    return {domOnly:true,clonedGroups:12,modalFits:rect.x>=0&&rect.y>=0&&rect.right<=innerWidth+1&&rect.bottom<=innerHeight+1,
     horizontalOverflow:dialog.scrollWidth>dialog.clientWidth+1,
     verticalScrollers:[dialog,...dialog.querySelectorAll('*')].filter(x=>x.scrollHeight>x.clientHeight+2&&['auto','scroll'].includes(getComputedStyle(x).overflowY)).map(x=>({class:x.className,height:x.clientHeight,content:x.scrollHeight})),
     closeVisible:close.top>=0&&close.bottom<=innerHeight};
   });
   assert.ok(syntheticLongHistory.modalFits);assert.equal(syntheticLongHistory.horizontalOverflow,false);
   assert.equal(syntheticLongHistory.verticalScrollers.length,1);assert.ok(syntheticLongHistory.closeVisible);
   await page.screenshot({path:`${out}/qtc-long-history-${viewport.width}x${viewport.height}-${phase}.png`});
   await page.keyboard.press('Escape');assert.ok(await page.locator(modal).isHidden());
  }
  assert.deepEqual(errors,[]);
  results.push({type,viewport,layout,focusPath,backwardFocusPath,escapeRestoresFocus,closeRestoresFocus,text,errors:[...errors],escapeCloses:true,closeButtonWorks:true,syntheticLongHistory});
 }
 await context.close();
}
await browser.close();
await fs.writeFile(`${out}/verification-${phase}.json`,JSON.stringify(results,null,2));
console.log(JSON.stringify(results.map(({type,viewport,layout,focusPath,errors})=>({type,viewport,layout,focusPath,errors})),null,2));
