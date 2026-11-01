import asyncio, sys, json
from playwright.async_api import async_playwright
import os
B=os.environ.get("CL_URL","http://127.0.0.1:8777"); O=os.environ.get("CL_SHOTS","ui_shots/")
CHECK="""()=>{const r=x=>x.getBoundingClientRect(),hit=(a,b)=>a.left<b.right&&b.left<a.right&&a.top<b.bottom&&b.top<a.bottom;
 const g=document.querySelector('#croot g.cpart.mcu')||document.querySelector('g.cpart.mcu');if(!g)return['no mcu'];
 const T=[...g.querySelectorAll('[data-title]')].map(r),P=[...g.querySelectorAll('.cpl.mp,.cref')].map(r),body=r(g.querySelector('rect'));
 const bad=[];T.forEach((t,i)=>{if(t.left<body.left||t.right>body.right||t.top<body.top||t.bottom>body.bottom)bad.push('title'+i+' outside body');
  P.forEach((p,j)=>{if(hit(t,p))bad.push('title'+i+' x '+g.querySelectorAll('.cpl.mp,.cref')[j].textContent)})});return bad}"""
async def main(shots):
  async with async_playwright() as p:
    b=await p.chromium.launch(executable_path="/usr/bin/google-chrome")
    fails={}
    for theme in ["light","dark"]:
      pg=await b.new_page(viewport={"width":1600,"height":1000}); pg.on("dialog",lambda d:asyncio.ensure_future(d.accept()))
      await pg.goto(B); await pg.evaluate(f"localStorage.setItem('cl_theme','{theme}')"); await pg.reload(); await pg.wait_for_timeout(1500)
      await pg.evaluate("tab('cir')"); await pg.wait_for_timeout(1000)
      devs=await pg.evaluate("[...document.querySelectorAll('#cdev option')].map(o=>o.value)")
      for d in devs:
        await pg.evaluate(f"Circuit.setDevice({json.dumps(d)})"); await pg.wait_for_timeout(700)
        for pk in (False,True):
          if pk: await pg.evaluate("Circuit.pkg()"); await pg.wait_for_timeout(300)
          await pg.evaluate("Circuit.fit()"); await pg.wait_for_timeout(150)
          bad=await pg.evaluate(CHECK)
          if bad: fails[f"{d}{' pkg' if pk else ''}"]=bad[:3]
          isb=await pg.evaluate("!!(window.Circuit&&document.querySelector('#cdev').selectedOptions[0].parentElement.label||'').match(/board|Arduino|STM|ESP|AVR/i)")
          if shots and (isb or d.startswith("PIC24FJ256GB110") or d.startswith("PIC18F45K22")):
            await pg.locator('#cwrap').screenshot(path=f"{O}m91_{theme}_{d.replace('/','_')}{'_pkg' if pk else ''}.png")
          if pk: await pg.evaluate("Circuit.pkg()")
      print(theme,len(devs),"devices")
      await pg.close()
    print("FAILS",json.dumps(fails,indent=0)[:3000]); await b.close(); sys.exit(1 if fails else 0)
asyncio.run(main(len(sys.argv)>1))
