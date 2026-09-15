
import os, sqlite3, logging, asyncio, uuid
from datetime import datetime
import requests

TOKEN=os.getenv("BALE_BOT_TOKEN","").strip()
ADMIN_ID=os.getenv("ADMIN_CHAT_ID","").strip()
PAYMENT_TOKEN=os.getenv("BALE_PAYMENT_PROVIDER_TOKEN","").strip()
API=f"https://tapi.bale.ai/bot{TOKEN}"
DB="orders.db"
logging.basicConfig(level=logging.INFO)

PRICES={"10":5000,"20":10000,"30":15000,"40":20000}

def db():
    c=sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS orders(
      id INTEGER PRIMARY KEY AUTOINCREMENT,user_id TEXT,username TEXT,kind TEXT,
      quantity INTEGER,price_toman INTEGER,channel TEXT,text TEXT,photo_file_id TEXT,
      status TEXT DEFAULT 'awaiting_payment',payload TEXT UNIQUE,created_at TEXT,
      payment_id TEXT)""")
    c.commit(); return c

def api(method, **data):
    r=requests.post(API+"/"+method,json=data,timeout=30)
    try:return r.json()
    except:return {}

def send(chat,text,markup=None):
    d={"chat_id":chat,"text":text}
    if markup:d["reply_markup"]=markup
    return api("sendMessage",**d)

def photo(chat,file_id,caption=""):
    return api("sendPhoto",chat_id=chat,photo=file_id,caption=caption)

def kb(rows): return {"inline_keyboard":rows}

def menu():
    return kb([[{"text":"📢 ثبت سفارش","callback_data":"new"}],
               [{"text":"💰 تعرفه‌ها","callback_data":"prices"},{"text":"📋 پیگیری","callback_data":"track"}]])

def invoice(chat,order_id,title,desc,amount_toman,payload):
    # Bale payment examples use IRR; our UI prices are in toman.
    return api("sendInvoice",chat_id=chat,title=title,description=desc,
               payload=payload,provider_token=PAYMENT_TOKEN,
               currency="IRR",prices=[{"label":title,"amount":amount_toman*10}])

def create_order(chat,state):
    payload="order_"+uuid.uuid4().hex
    c=db()
    c.execute("""INSERT INTO orders(user_id,username,kind,quantity,price_toman,channel,text,photo_file_id,payload,created_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?)""",
              (str(chat),state.get("username",""),state["kind"],state["quantity"],
               state["price"],state.get("channel",""),state.get("text",""),
               state.get("photo",""),payload,datetime.now().isoformat(timespec="seconds")))
    oid=c.execute("SELECT last_insert_rowid()").fetchone()[0]
    c.commit();c.close()
    return oid,payload

def order_row(oid):
    c=db(); r=c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone();c.close();return r

def admin_send(oid):
    if not ADMIN_ID:return
    r=order_row(oid)
    _,user,username,kind,qty,price,channel,text,pic,status,payload,created,payment=r
    send(ADMIN_ID,f"""🚨 سفارش پرداخت‌شده #{oid}

👤 مشتری: {username or '-'}
🆔 شناسه: {user}
📦 نوع: {kind}
🔢 تعداد/مدت: {qty}
💰 مبلغ: {price:,} تومان
🔗 کانال: {channel}
📝 متن: {text}
💳 پرداخت: ✅ موفق
🧾 شناسه پرداخت: {payment or '-'}
📌 وضعیت: در انتظار انجام""",
         kb([[{"text":"✅ انجام شد","callback_data":f"done:{oid}"},
              {"text":"❌ لغو","callback_data":f"cancel:{oid}"}]]))
    if pic: photo(ADMIN_ID,pic,f"🖼 بنر سفارش #{oid}")

def prices_text():
    return """💰 تعرفه‌ها

👥 ۱۰ عضو — ۵,۰۰۰ تومان
👥 ۲۰ عضو — ۱۰,۰۰۰ تومان
👥 ۳۰ عضو — ۱۵,۰۰۰ تومان
👥 ۴۰ عضو — ۲۰,۰۰۰ تومان
📢 تبلیغ ساعتی — هر ساعت ۱۰,۰۰۰ تومان"""

async def run():
    if not TOKEN: raise SystemExit("BALE_BOT_TOKEN is not set")
    if not PAYMENT_TOKEN: raise SystemExit("BALE_PAYMENT_PROVIDER_TOKEN is not set")
    db(); offset=0; states={}
    while True:
      try:
        res=api("getUpdates",offset=offset,timeout=25)
        for u in res.get("result",[]):
          offset=u["update_id"]+1
          # successful payment
          msg=u.get("message",{})
          sp=msg.get("successful_payment")
          if sp:
            chat=str(msg["chat"]["id"]); payload=sp.get("invoice_payload","")
            c=db(); row=c.execute("SELECT id FROM orders WHERE payload=?",(payload,)).fetchone()
            if row:
              oid=row[0]
              payid=sp.get("telegram_payment_charge_id") or sp.get("payment_charge_id") or sp.get("provider_payment_charge_id","")
              c.execute("UPDATE orders SET status='paid',payment_id=? WHERE id=?",(payid,oid));c.commit();c.close()
              send(chat,f"✅ پرداخت با موفقیت انجام شد!\n\n🧾 شماره سفارش: #{oid}\n⏳ سفارش برای مدیر ارسال شد.",menu())
              admin_send(oid)
            continue

          # pre-checkout
          pc=u.get("pre_checkout_query")
          if pc:
            payload=pc.get("invoice_payload","")
            c=db(); row=c.execute("SELECT id,status FROM orders WHERE payload=?",(payload,)).fetchone();c.close()
            if row and row[1]=="awaiting_payment":
              api("answerPreCheckoutQuery",pre_checkout_query_id=pc["id"],ok=True)
            else:
              api("answerPreCheckoutQuery",pre_checkout_query_id=pc["id"],ok=False,
                  error_message="این سفارش معتبر یا فعال نیست.")
            continue

          cb=u.get("callback_query")
          if cb:
            data=cb.get("data",""); chat=str(cb["message"]["chat"]["id"])
            api("answerCallbackQuery",callback_query_id=cb["id"])
            if data=="new":
              states[chat]={"step":"type","username":cb.get("from",{}).get("username","")}
              send(chat,"📢 نوع سفارش را انتخاب کن:",kb([[{"text":"👥 افزایش عضو","callback_data":"members"}],
                                                        [{"text":"📢 تبلیغ ساعتی","callback_data":"hourly"}]]))
            elif data=="prices": send(chat,prices_text(),menu())
            elif data=="track":
              c=db(); rows=c.execute("SELECT id,kind,quantity,price_toman,status FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 10",(chat,)).fetchall();c.close()
              send(chat,"📋 سفارش‌ها:\n\n"+("\n".join(f"#{x[0]} | {x[1]} | {x[2]} | {x[3]:,} تومان | {x[4]}" for x in rows) if rows else "سفارشی نداری."),menu())
            elif data=="members":
              states[chat]["step"]="quantity"
              send(chat,"👥 تعداد عضو:",kb([[{"text":"۱۰ عضو — ۵,۰۰۰","callback_data":"qty:10"},{"text":"۲۰ عضو — ۱۰,۰۰۰","callback_data":"qty:20"}],
                                            [{"text":"۳۰ عضو — ۱۵,۰۰۰","callback_data":"qty:30"},{"text":"۴۰ عضو — ۲۰,۰۰۰","callback_data":"qty:40"}]]))
            elif data=="hourly":
              states[chat]={"step":"hours","kind":"تبلیغ ساعتی","username":cb.get("from",{}).get("username","")}
              send(chat,"⏱ چند ساعت تبلیغ می‌خواهی؟ (مثلاً ۲)")
            elif data.startswith("qty:"):
              q=data.split(":")[1]; states[chat].update(step="channel",kind="افزایش عضو",quantity=int(q),price=PRICES[q])
              send(chat,"🔗 لینک کانالت را ارسال کن.")
            elif data=="confirm":
              st=states.get(chat)
              if not st or st.get("step")!="confirm": send(chat,"❌ سفارش فعالی نیست.",menu());continue
              oid,payload=create_order(chat,st)
              inv=invoice(chat,oid,f"سفارش تبلیغ #{oid}",f"{st['kind']} - {st['quantity']} - سفارش #{oid}",st["price"],payload)
              if not inv.get("ok",True):
                send(chat,"❌ ایجاد فاکتور پرداخت ناموفق بود. لطفاً دوباره تلاش کن.")
              else:
                send(chat,f"💳 فاکتور پرداخت سفارش #{oid} ایجاد شد.\nمبلغ: {st['price']:,} تومان\n\nروی گزینه پرداخت فاکتور بله بزن.",menu())
              states.pop(chat,None)
            elif data=="abort":
              states.pop(chat,None);send(chat,"❌ سفارش لغو شد.",menu())
            elif data.startswith("done:") or data.startswith("cancel:"):
              if chat!=ADMIN_ID: continue
              action,oid=data.split(":"); status="done" if action=="done" else "cancelled"
              c=db(); row=c.execute("SELECT user_id FROM orders WHERE id=?",(oid,)).fetchone()
              c.execute("UPDATE orders SET status=? WHERE id=?",(status,oid));c.commit();c.close()
              if row: send(row[0],("✅ سفارش انجام شد." if status=="done" else "❌ سفارش لغو شد.")+f"\n\nشماره سفارش: #{oid}",menu())
              send(chat,f"✅ وضعیت سفارش #{oid} تغییر کرد.")
            continue

          if not msg: continue
          chat=str(msg["chat"]["id"]); text=msg.get("text",""); user=msg.get("from",{})
          if text=="/start": states.pop(chat,None);send(chat,"👋 سلام!\nبه سامانه ثبت تبلیغات خوش آمدی.",menu());continue
          st=states.get(chat)
          if not st: continue
          if st.get("step")=="hours":
            try:
              h=int(text)
              if h<1 or h>168: raise ValueError
              st.update(step="channel",quantity=h,price=h*10000)
              send(chat,f"💰 مبلغ: {h*10000:,} تومان\n\n🔗 لینک کانالت را بفرست.")
            except: send(chat,"❌ عددی بین ۱ تا ۱۶۸ وارد کن.")
          elif st.get("step")=="channel":
            st.update(step="photo",channel=text);send(chat,"🖼 بنر را به صورت عکس ارسال کن.")
          elif st.get("step")=="photo" and msg.get("photo"):
            st.update(step="text",photo=msg["photo"][-1]["file_id"]);send(chat,"📝 متن تبلیغ را ارسال کن.")
          elif st.get("step")=="text":
            st["text"]=text;st["step"]="confirm"
            send(chat,f"📋 خلاصه سفارش\n\n📦 {st['kind']}\n🔢 {st['quantity']}\n💰 {st['price']:,} تومان\n🔗 {st['channel']}\n📝 {st['text']}\n\nتأیید و رفتن به پرداخت؟",
                 kb([[{"text":"💳 تأیید و پرداخت","callback_data":"confirm"},{"text":"❌ لغو","callback_data":"abort"}]]))
      except Exception:
        logging.exception("polling error");await asyncio.sleep(3)

if __name__=="__main__": asyncio.run(run())
