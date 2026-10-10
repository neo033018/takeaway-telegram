import os, json, time, hmac, hashlib, sqlite3, threading, logging
from urllib.parse import parse_qsl
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests
from flask import Flask, request, jsonify, render_template

app=Flask(__name__)
logging.basicConfig(level=logging.INFO)
TOKEN=os.environ.get('BOT_TOKEN','')
URL=os.environ.get('APP_URL','').rstrip('/')
ADMIN=os.environ.get('ADMIN_CHAT_ID','')
DB=os.environ.get('DATABASE_PATH','orders.db')
TZ=ZoneInfo('Europe/Belgrade')
MENU={
MENU={
 'espresso':('Espresso',200,'coffee',False),
 'americano':('Americano',250,'coffee',False),
 'cappuccino':('Cappuccino',320,'coffee',False),
 'latte':('Latte',350,'coffee',False),
 'flatwhite':('Flat White',400,'coffee',False),
 'icedlatte':('Iced Latte',380,'coffee',False),

 'thai':('Thai Classic Milk Tea',450,'tea',True),
 'green':('Thai Green Milk Tea',450,'tea',True),
 'lime':('Iced Lime Tea',480,'tea',False),

 'tonic':('Espresso Tonic',420,'special',False),
 'bumble':('Bumble Coffee',450,'special',False)
}

}

def db():
 c=sqlite3.connect(DB,timeout=15)
 c.execute('CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL, customer TEXT NOT NULL, pickup TEXT NOT NULL, details TEXT NOT NULL, total INTEGER NOT NULL, status TEXT NOT NULL DEFAULT "new", created TEXT NOT NULL)')
 c.commit(); return c

def tg(method,payload):
 if not TOKEN: return None
 try:
  r=requests.post(f'https://api.telegram.org/bot{TOKEN}/{method}',json=payload,timeout=12)
  r.raise_for_status(); return r.json()
 except requests.RequestException: logging.exception('Telegram API failure'); return None

def verify(init_data):
 if not TOKEN or not init_data or len(init_data)>8192:return None
 try:
  d=dict(parse_qsl(init_data,keep_blank_values=True,strict_parsing=True))
  their=d.pop('hash','')
  secret=hmac.new(b'WebAppData',TOKEN.encode(),hashlib.sha256).digest()
  check='\n'.join(f'{k}={v}' for k,v in sorted(d.items()))
  expected=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
  if not hmac.compare_digest(expected,their):return None
  if abs(time.time()-int(d.get('auth_date','0')))>3600:return None
  user=json.loads(d['user'])
  if not isinstance(user.get('id'),int):return None
  return user
 except (ValueError,KeyError,TypeError):return None

@app.get('/')
def index():return render_template('index.html')

@app.get('/api/menu')
def menu():return jsonify([{'id':k,'name':v[0],'price':v[1],'category':v[2],'boba':v[3]} for k,v in MENU.items()])

@app.post('/api/order')
def order():
 data=request.get_json(silent=True) or {}
 user=verify(data.get('initData',''))
 if not user:return jsonify(error='Open the app from Telegram to order.'),401
 items=data.get('items',[])
 if not isinstance(items,list) or not 1<=len(items)<=20:return jsonify(error='Select 1–20 drinks.'),400
 now=datetime.now(TZ)
 try: pickup=datetime.fromisoformat(data.get('pickup',''))
 except (ValueError,TypeError):return jsonify(error='Choose a pickup time.'),400
 if pickup.tzinfo is None:return jsonify(error='Invalid pickup time.'),400
 pickup=pickup.astimezone(TZ)
 if not now+timedelta(minutes=8)<=pickup<=now+timedelta(hours=6):return jsonify(error='Choose a pickup time 10 minutes to 6 hours ahead.'),400
 total=0; lines=[]
 for x in items:
  if not isinstance(x,dict) or x.get('id') not in MENU:return jsonify(error='Invalid drink.'),400
  name,price,cat,allow_boba=MENU[x['id']]
  sugar=x.get('sugar','Normal')
  if sugar not in ('Normal','Less','No sugar'):return jsonify(error='Invalid sugar choice.'),400
  boba=x.get('boba',False)
  if type(boba) is not bool or (boba and not allow_boba):return jsonify(error='Invalid topping.'),400
  qty=x.get('qty',1)
  if type(qty) is not int or not 1<=qty<=10:return jsonify(error='Invalid quantity.'),400
  unit=price+(100 if boba else 0);total+=qty*unit
  lines.append(f'{qty}× {name} — {sugar}'+(' + boba' if boba else '')+f' ({qty*unit} RSD)')
 if total>100000:return jsonify(error='Order too large.'),400
 customer=(user.get('first_name','')+' '+user.get('last_name','')).strip()[:80] or 'Telegram customer'
 c=db()
 try:
  cur=c.execute('INSERT INTO orders (telegram_id,customer,pickup,details,total,created) VALUES (?,?,?,?,?,?)',(user['id'],customer,pickup.isoformat(),json.dumps(lines,ensure_ascii=False),total,now.isoformat()))
  c.commit();oid=cur.lastrowid
 finally:c.close()
 message=f'☕ NEW ORDER #{oid}\n👤 {customer}'+(f' (@{user["username"]})' if user.get('username') else '')+f'\n🕒 {pickup:%d %b %H:%M}\n\n'+'\n'.join(lines)+f'\n\n💵 {total} RSD — PAY AT PICKUP'
 target=ADMIN or str(user['id'])
 sent=tg('sendMessage',{'chat_id':target,'text':message})
 if not sent or not sent.get('ok'):
  logging.error('Order %s saved but admin notification failed; configure ADMIN_CHAT_ID',oid)
 tg('sendMessage',{'chat_id':user['id'],'text':f'✅ Take Away order #{oid} received!\nPickup: {pickup:%H:%M}\nTotal: {total} RSD\nPay at pickup.'})
 return jsonify(order_id=oid,total=total,pickup=pickup.strftime('%H:%M'),notification_sent=bool(sent and sent.get('ok')))

@app.get('/health')
def health():return jsonify(status='ok')

@app.post('/api/bot-webhook')
def webhook():
 secret=os.environ.get('WEBHOOK_SECRET','')
 if not secret or not hmac.compare_digest(request.headers.get('X-Telegram-Bot-Api-Secret-Token',''),secret):return '',403
 data=request.get_json(silent=True) or {}
 msg=data.get('message') or {}
 chat=msg.get('chat',{})
 if chat.get('type')=='private' and msg.get('text','').startswith('/start'):
  if URL.startswith('https://'):
   tg('sendMessage',{'chat_id':chat['id'],'text':'Welcome to Take Away ☕\nPre-order your drinks and pay when you collect them.', 'reply_markup':{'keyboard':[[{'text':'☕ Order drinks','web_app':{'url':URL}}]],'resize_keyboard':True}})
  else:tg('sendMessage',{'chat_id':chat['id'],'text':'Our ordering app is being set up. See you soon! ☕'})
 return '',200

if __name__=='__main__':
 db().close();app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)))
