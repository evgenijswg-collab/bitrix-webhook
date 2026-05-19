from flask import Flask, request, jsonify
import requests
import time
import threading
import re
import os
from datetime import datetime
import pytz

app = Flask(__name__)

# ============================================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# ============================================================
WEBHOOK_URL = os.environ.get('BITRIX_WEBHOOK_URL')
BITRIX_DOMAIN = os.environ.get('BITRIX_DOMAIN', 'mariomicci.bitrix24.ru')
TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
AI_API_URL = os.environ.get('AI_API_URL', 'https://openrouter.ai/api/v1/chat/completions')
AI_API_KEY = os.environ.get('AI_API_KEY')
AI_MODEL = os.environ.get('AI_MODEL', 'qwen/qwen-2.5-7b-instruct:free')
TIMEZONE = os.environ.get('TIMEZONE', 'Europe/Moscow')

if not WEBHOOK_URL: raise RuntimeError("BITRIX_WEBHOOK_URL is not set!")
if not TELEGRAM_TOKEN: raise RuntimeError("TELEGRAM_BOT_TOKEN is not set!")
if not TELEGRAM_CHAT_ID: raise RuntimeError("TELEGRAM_CHAT_ID is not set!")
if not AI_API_KEY: raise RuntimeError("AI_API_KEY is not set!")

DEAL_LINK_FIELD = "UF_CRM_1777825383755"
SMART_LINK_FIELD = "ufCrm10_1777827987160"
SMART_DATE_FIELD = "ufCrm10_1775722563672"
SMART_ENTITY_TYPE_ID = 1046
CONTRACT_NUMBER_FIELD = "UF_CRM_1777452882147"
BP_TEMPLATE_ID = 282

last_contract_debug = {}
recent_deals = set()
recent_lock = threading.Lock()

def clear_deal(did):
    time.sleep(60)
    with recent_lock: recent_deals.discard(did)

def bitrix_api(method, data=None, timeout=10):
    url = f"{WEBHOOK_URL}{method}"
    if data is None: data = {}
    return requests.post(url, json=data, timeout=timeout).json()

def send_telegram(text):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": int(TELEGRAM_CHAT_ID), "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        print(f"Telegram send error: {e}")

def send_telegram_to_chat(chat_id, text):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": int(chat_id), "text": text}, timeout=10)
    except: pass

def fmt_rub(amount):
    return f"{amount:,.0f}".replace(",", " ")

def get_ai_report(raw_text):
    models = [
        "google/gemma-4-31b-it:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "qwen/qwen3-next-80b-a3b-instruct:free",
        "meta-llama/llama-3.2-3b-instruct:free"
    ]
    for model in models:
        time.sleep(10)
        try:
            resp = requests.post("https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": raw_text}], "max_tokens": 300, "temperature": 0.5},
                timeout=30).json()
            for choice in resp.get('choices', []):
                content = choice.get('message', {}).get('content', '')
                if content and len(content) > 10:
                    return content
        except: pass
    return None

# ============================================================
# МАРШРУТ 1: Складские документы
# ============================================================
@app.route('/', methods=['POST'])
def warehouse_handler():
    data = request.form.to_dict()
    fields = {}
    for key, value in data.items():
        if key.startswith('data[FIELDS]['):
            fields[key.split('[')[-1].replace(']', '')] = value
    doc_entity_id = fields.get('ENTITY_ID', '')
    if not doc_entity_id:
        return jsonify({"result": True, "msg": "no entity_id"}), 200
    resp = bitrix_api("catalog.document.list.json", {
        "filter": {"id": int(doc_entity_id)}, "select": ["id", "docNumber", "docType", "dateDocument"]})
    documents = resp.get('result', {}).get('documents', [])
    if not documents: return jsonify({"result": True, "msg": "not found"}), 200
    doc = documents[0]
    parent_deal_id = doc.get('docNumber', '')
    doc_type = doc.get('docType', '')
    doc_date = doc.get('dateDocument', '')
    if not parent_deal_id: return jsonify({"result": True, "msg": "no deal_id"}), 200
    doc_link = f"https://{BITRIX_DOMAIN}/shop/documents/details/{doc_entity_id}/"
    if doc_type == "A":
        bitrix_api("crm.deal.update.json", {"id": int(parent_deal_id), "fields": {DEAL_LINK_FIELD: doc_link}})
        if doc_date:
            smart_search = bitrix_api("crm.item.list.json", {
                "entityTypeId": SMART_ENTITY_TYPE_ID, "filter": {"xmlId": parent_deal_id}, "select": ["id"]})
            smart_items = smart_search.get('result', {}).get('items', [])
            if smart_items:
                bitrix_api("crm.item.update.json", {
                    "entityTypeId": SMART_ENTITY_TYPE_ID, "id": smart_items[0]['id'],
                    "fields": {SMART_DATE_FIELD: doc_date}})
    else:
        search_resp = bitrix_api("crm.item.list.json", {
            "entityTypeId": SMART_ENTITY_TYPE_ID, "filter": {"xmlId": parent_deal_id}, "select": ["id"]})
        items = search_resp.get('result', {}).get('items', [])
        if not items: return jsonify({"result": True, "msg": "smart item not found"}), 200
        bitrix_api("crm.item.update.json", {
            "entityTypeId": SMART_ENTITY_TYPE_ID, "id": items[0]['id'],
            "fields": {SMART_LINK_FIELD: doc_link}})
    return jsonify({"result": True, "doc_type": doc_type, "doc_link": doc_link, "doc_date": doc_date}), 200

# ============================================================
# МАРШРУТ 2: Договор → БП → счёт
# ============================================================
def process_contract_background(deal_id):
    for _ in range(30):
        time.sleep(2)
        deal_resp = bitrix_api("crm.deal.get.json", {"id": int(deal_id)})
        cn = deal_resp.get('result', {}).get(CONTRACT_NUMBER_FIELD, '')
        bp_resp = bitrix_api("bizproc.workflow.instances.json", {
            "SELECT": ["ID", "STATE"],
            "FILTER": {"DOCUMENT_ID": f"crm_CCrmDocumentDeal_{deal_id}", "STATE": "running"}})
        if cn and len(bp_resp.get('result', [])) == 0:
            bitrix_api("bizproc.workflow.start.json", {
                "TEMPLATE_ID": BP_TEMPLATE_ID,
                "DOCUMENT_ID": ["crm", "CCrmDocumentDeal", int(deal_id)],
                "PARAMETERS": {"contractNumber": cn}})
            break

@app.route('/contract', methods=['POST'])
def contract_handler():
    global recent_deals
    fields = {}
    for key, value in request.form.to_dict().items():
        if key.startswith('data[FIELDS]['):
            fields[key.split('[')[-1].replace(']', '')] = value
    deal_id = fields.get('ENTITY_ID', '')
    if not deal_id: return jsonify({"result": True, "msg": "no deal_id"}), 200
    with recent_lock:
        if deal_id in recent_deals: return jsonify({"result": True, "msg": "already processed"}), 200
        recent_deals.add(deal_id)
    threading.Thread(target=process_contract_background, args=(deal_id,), daemon=True).start()
    threading.Thread(target=clear_deal, args=(deal_id,), daemon=True).start()
    return jsonify({"result": True, "msg": "processing started"}), 200

@app.route('/contract-debug', methods=['GET'])
def contract_debug():
    return jsonify(last_contract_debug)

# ============================================================
# AUDIT
# ============================================================
@app.route('/audit', methods=['GET'])
def daily_audit_route():
    threading.Thread(target=run_daily_audit, daemon=True).start()
    return jsonify({"result": True, "status": "Audit started"}), 200

def get_user_name(uid):
    if not uid or uid == '0': return 'Не назначен'
    uid = str(uid)
    if not hasattr(get_user_name, 'cache'): get_user_name.cache = {}
    if uid not in get_user_name.cache:
        try:
            resp = requests.post(f"{WEBHOOK_URL}user.get.json", json={"ID": int(uid)}, timeout=5).json()
            user = resp.get('result', [{}])[0]
            get_user_name.cache[uid] = f"{user.get('NAME','')} {user.get('LAST_NAME','')}".strip() or f"User {uid}"
        except:
            get_user_name.cache[uid] = f"User {uid}"
    return get_user_name.cache[uid]

SMART_STAGES = {
    "DT1046_16:NEW": "Проверка ТЗ", "DT1046_16:UC_L5A63P": "Ожидание камня",
    "DT1046_16:UC_7X8UF5": "Материал на складе", "DT1046_16:PREPARATION": "Изготовление",
    "DT1046_16:UC_PYF2PE": "Готово к отгрузке", "DT1046_16:SUCCESS": "Успех", "DT1046_16:FAIL": "Провал"
}

def run_daily_audit():
    import traceback
    try:
        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        today = now.strftime('%Y-%m-%d')
        TASKS_URL = os.environ.get('BITRIX_TASKS_WEBHOOK_URL', '')
        msgs = []

        # --- ЛИДЫ ---
        try:
            leads = bitrix_api("crm.lead.list.json", {
                "filter": {"!STATUS_ID": ["CONVERTED", "JUNK"]}, "select": ["ID", "TITLE", "STATUS_ID", "OPPORTUNITY"]}).get('result', [])
            sum_leads = sum(float(l.get('OPPORTUNITY') or 0) for l in leads)
            msgs.append(f"🔹 <b>Лиды в работе:</b> {len(leads)} шт. на {fmt_rub(sum_leads)} руб.")
        except Exception as e: msgs.append(f"🔹 Лиды: ошибка — {str(e)[:80]}")

        # --- СДЕЛКИ ---
        try:
            deals = bitrix_api("crm.deal.list.json", {
                "filter": {"!STAGE_ID": "WON"}, "select": ["ID", "TITLE", "STAGE_ID", "OPPORTUNITY", "CLOSEDATE"]}).get('result', [])
            in_work, lost_today = [], []
            for d in deals:
                stage = d.get('STAGE_ID', '')
                if stage == "LOSE":
                    if (d.get('CLOSEDATE', '') or '')[:10] == today: lost_today.append(d)
                elif stage != "WON": in_work.append(d)
            sum_work = sum(float(d.get('OPPORTUNITY') or 0) for d in in_work)
            sum_lost = sum(float(d.get('OPPORTUNITY') or 0) for d in lost_today)
            msgs.append(f"📊 <b>Сделки в работе:</b> {len(in_work)} шт. на {fmt_rub(sum_work)} руб.")
            if lost_today: msgs.append(f"   ❌ <b>Провалено сегодня:</b> {len(lost_today)} шт. на {fmt_rub(sum_lost)} руб.")
        except Exception as e: msgs.append(f"📊 Сделки: ошибка — {str(e)[:80]}")

        # --- СМАРТ-ПРОЦЕСС ---
        try:
            items = bitrix_api("crm.item.list.json", {
                "entityTypeId": SMART_ENTITY_TYPE_ID, "select": ["id", "title", "stageId"]}).get('result', {}).get('items', [])
            active = [i for i in items if i.get('stageId') not in ('DT1046_16:SUCCESS', 'DT1046_16:FAIL')]
            by_stage = {}
            for i in active:
                name = SMART_STAGES.get(i.get('stageId', '?'), i.get('stageId', '?'))
                by_stage[name] = by_stage.get(name, 0) + 1
            msgs.append(f"\n📦 <b>Производство:</b> {len(active)} заказов")
            for stage in ("Изготовление", "Готово к отгрузке", "Ожидание камня", "Материал на складе"):
                cnt = by_stage.get(stage, 0)
                if cnt: msgs.append(f"   • {stage}: {cnt}")
        except Exception as e: msgs.append(f"\n📦 Производство: ошибка — {str(e)[:80]}")

        # --- ЗАДАЧИ ---
        try:
            if TASKS_URL:
                WORK_STATUSES = {'-1', '-2', '-3', '2', '3'}
                employees = {}
                start = 0
                while True:
                    batch = requests.post(f"{TASKS_URL}task.item.list.json",
                        json={"order": {"ID": "desc"}, "start": start}, timeout=15).json().get('result', [])
                    if not batch: break
                    for t in batch:
                        if not isinstance(t, dict): continue
                        if str(t.get('STATUS', '')) not in WORK_STATUSES: continue
                        uid = str(t.get('RESPONSIBLE_ID', '0'))
                        if uid == '0': continue
                        if uid not in employees: employees[uid] = {"total": 0, "overdue": 0}
                        employees[uid]["total"] += 1
                        deadline = t.get('DEADLINE', '')[:10]
                        if deadline and deadline < today: employees[uid]["overdue"] += 1
                    start += 50
                    if start >= 1000: break
                total_t = sum(e['total'] for e in employees.values())
                total_o = sum(e['overdue'] for e in employees.values())
                msgs.append(f"\n📋 <b>Задачи в работе:</b> {total_t}, <b>просрочено: {total_o}</b>")
                for uid, data in sorted(employees.items(), key=lambda x: x[1]['total'], reverse=True):
                    msgs.append(f"   • {get_user_name(uid)}: {data['total']} задач, просрочено: {data['overdue']}")
                overdue_emp = {uid: d for uid, d in employees.items() if d['overdue'] > 0}
                if overdue_emp:
                    msgs.append(f"\n⚠️ <b>Просроченные задачи:</b>")
                    for uid, data in sorted(overdue_emp.items(), key=lambda x: x[1]['overdue'], reverse=True):
                        msgs.append(f"   • {get_user_name(uid)}: {data['overdue']} просрочено")
        except Exception as e: msgs.append(f"\n📋 Задачи: ошибка — {str(e)[:80]}")

                # --- ОСТАТКИ ПО СКЛАДАМ ---
        try:
            store_resp = bitrix_api("catalog.storeproduct.list.json")
            products = store_resp.get('result', {}).get('storeProducts', [])
            if products:
                # Названия товаров
                product_names_local = {}
                all_pids = set(str(p['productId']) for p in products if p.get('amount'))
                for pid in all_pids:
                    try:
                        p_resp = bitrix_api("catalog.product.get.json", {"id": int(pid)})
                        product_names_local[pid] = p_resp.get('result', {}).get('product', {}).get('name', f'Товар {pid}')
                    except:
                        product_names_local[pid] = f'Товар {pid}'
                
                # Названия складов
                store_names = {}
                try:
                    s_resp = bitrix_api("catalog.store.list.json")
                    for s in s_resp.get('result', {}).get('stores', []):
                        store_names[str(s['id'])] = s.get('title', f'Склад {s["id"]}')
                except:
                    pass
                
                # Группируем: склад → товар → количество
                by_store = {}
                for p in products:
                    amt = float(p.get('amount') or 0)
                    if amt <= 0:
                        continue
                    sid = str(p['storeId'])
                    pid = str(p['productId'])
                    if sid not in by_store:
                        by_store[sid] = {}
                    by_store[sid][pid] = by_store[sid].get(pid, 0) + amt
                
                if by_store:
                    msgs.append(f"\n📦 <b>Остатки по складам:</b>")
                    
                    for sid in sorted(by_store.keys()):
                        store_name = store_names.get(sid, f'Склад {sid}')
                        items = by_store[sid]
                        
                        # Разделяем на слэбы и штуки
                        slabs = {}  # Товары-слэбы (камень)
                        pieces = {} # Товары-штуки (клей и т.д.)
                        
                        for pid, qty in items.items():
                            name = product_names_local.get(pid, f'Товар {pid}')
                            if 'камень' in name.lower() or 'слэб' in name.lower() or 'акрил' in name.lower() or 'кварц' in name.lower():
                                slabs[name] = qty
                            else:
                                pieces[name] = qty
                        
                        # Формируем строку для склада
                        parts = []
                        
                        # Слэбы
                        if slabs:
                            total_slabs = sum(slabs.values())
                            slab_detail = ", ".join([f"{name} {qty:.1f} слэб" if qty != 1 else f"{name} {qty:.1f} слэба" for name, qty in slabs.items()])
                            parts.append(f"{total_slabs:.1f} слэбов ({slab_detail})")
                        
                        # Штуки
                        if pieces:
                            total_pieces = sum(pieces.values())
                            piece_detail = ", ".join([f"{name} {qty:.1f} шт." for name, qty in pieces.items()])
                            parts.append(f"{total_pieces:.1f} шт. ({piece_detail})")
                        
                        if parts:
                            msgs.append(f"   • <b>{store_name}:</b> {'; '.join(parts)}")
        except Exception as e:
            msgs.append(f"\n📦 Остатки: ошибка — {str(e)[:80]}")

        # --- ИИ ---
        try:
            ai_report = get_ai_report(f"Напиши краткий вывод на русском (2-3 предложения): что хуже всего в этих данных, на что обратить внимание.\n\n{chr(10).join(msgs)[:500]}")
            if ai_report: msgs.append(f"\n\n🤖 <b>ИИ:</b>\n{ai_report}")
            else: msgs.append(f"\n\n⚠️ Все модели ИИ недоступны.")
        except Exception as e: msgs.append(f"\n\n⚠️ Ошибка ИИ: {str(e)[:200]}")

        send_telegram("\n".join(msgs))
    except Exception as e:
        send_telegram(f"❌ Крах: {traceback.format_exc()[:1000]}")

def run_monthly_audit():
    import traceback
    try:
        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime('%Y-%m-%d')
        today = now.strftime('%Y-%m-%d')
        TASKS_URL = os.environ.get('BITRIX_TASKS_WEBHOOK_URL', '')
        product_names = {}
        msgs = [f"📅 <b>ОТЧЁТ ЗА {now.strftime('%B %Y').upper()}</b>\n"]

        # --- ЛИДЫ ---
        try:
            leads = bitrix_api("crm.lead.list.json", {
                "filter": {">=DATE_CREATE": month_start, "<=DATE_CREATE": today},
                "select": ["ID", "TITLE", "STATUS_ID", "OPPORTUNITY"]}).get('result', [])
            converted = [l for l in leads if l.get('STATUS_ID') == 'CONVERTED']
            junk = [l for l in leads if l.get('STATUS_ID') == 'JUNK']
            active = [l for l in leads if l.get('STATUS_ID') not in ('CONVERTED', 'JUNK')]
            msgs.append(f"🔹 <b>Лиды за месяц:</b>")
            msgs.append(f"   • Создано: {len(leads)} шт.")
            msgs.append(f"   ✅ Перешло в сделку: {len(converted)} шт. на {fmt_rub(sum(float(l.get('OPPORTUNITY') or 0) for l in converted))} руб.")
            msgs.append(f"   ❌ Провалено: {len(junk)} шт. на {fmt_rub(sum(float(l.get('OPPORTUNITY') or 0) for l in junk))} руб.")
            if active: msgs.append(f"   🔄 В работе: {len(active)} шт.")
        except Exception as e: msgs.append(f"🔹 Лиды: ошибка — {str(e)[:80]}")

        # --- СДЕЛКИ ---
        try:
            deals = bitrix_api("crm.deal.list.json", {
                "filter": {">=DATE_CREATE": month_start, "<=DATE_CREATE": today},
                "select": ["ID", "TITLE", "STAGE_ID", "OPPORTUNITY"]}).get('result', [])
            won = [d for d in deals if d.get('STAGE_ID') == 'WON']
            lost = [d for d in deals if d.get('STAGE_ID') == 'LOSE']
            in_work = [d for d in deals if d.get('STAGE_ID') not in ('WON', 'LOSE')]
            msgs.append(f"\n📊 <b>Сделки за месяц:</b>")
            msgs.append(f"   • Создано: {len(deals)} шт.")
            msgs.append(f"   ✅ Успешно: {len(won)} шт. на {fmt_rub(sum(float(d.get('OPPORTUNITY') or 0) for d in won))} руб.")
            msgs.append(f"   ❌ Провалено: {len(lost)} шт. на {fmt_rub(sum(float(d.get('OPPORTUNITY') or 0) for d in lost))} руб.")
            msgs.append(f"   🔄 В работе: {len(in_work)} шт. на {fmt_rub(sum(float(d.get('OPPORTUNITY') or 0) for d in in_work))} руб.")
        except Exception as e: msgs.append(f"📊 Сделки: ошибка — {str(e)[:80]}")

        # --- ЗАДАЧИ ---
        try:
            if TASKS_URL:
                total_created = total_closed = total_overdue_closed = 0
                employees = {}
                start = 0
                while True:
                    batch = requests.post(f"{TASKS_URL}task.item.list.json",
                        json={"order": {"ID": "desc"}, "start": start}, timeout=15).json().get('result', [])
                    if not batch: break
                    for t in batch:
                        if not isinstance(t, dict): continue
                        created_date = (t.get('CREATED_DATE', '') or '')[:10]
                        status = str(t.get('STATUS', ''))
                        closed_date = (t.get('CLOSED_DATE', '') or '')[:10]
                        uid = str(t.get('RESPONSIBLE_ID', '0'))
                        deadline = (t.get('DEADLINE', '') or '')[:10]
                        if created_date >= month_start: total_created += 1
                        if status == '5' and closed_date >= month_start:
                            total_closed += 1
                            if deadline and deadline < closed_date: total_overdue_closed += 1
                        if uid != '0' and (created_date >= month_start or (status == '5' and closed_date >= month_start)):
                            if uid not in employees: employees[uid] = {"total": 0, "closed": 0, "overdue_closed": 0}
                            if created_date >= month_start: employees[uid]["total"] += 1
                            if status == '5' and closed_date >= month_start:
                                employees[uid]["closed"] += 1
                                if deadline and deadline < closed_date: employees[uid]["overdue_closed"] += 1
                    start += 50
                    if start >= 1000: break
                msgs.append(f"\n📋 <b>Задачи за месяц:</b>")
                msgs.append(f"   • Создано: {total_created} шт.")
                msgs.append(f"   • Закрыто: {total_closed} шт.")
                msgs.append(f"   • Закрыто с просрочкой: {total_overdue_closed} шт.")
                if employees:
                    msgs.append(f"\n📋 <b>Задачи по сотрудникам:</b>")
                    for uid, data in sorted(employees.items(), key=lambda x: x[1]['total'], reverse=True):
                        msgs.append(f"   • {get_user_name(uid)}: создано {data['total']}, закрыто {data['closed']}, просрочено {data['overdue_closed']}")
        except Exception as e: msgs.append(f"\n📋 Задачи: ошибка — {str(e)[:80]}")

        # --- ТОВАРОДВИЖЕНИЕ ---
        try:
            documents = bitrix_api("catalog.document.list.json", {
                "filter": {">=dateCreate": month_start, "<=dateCreate": today},
                "select": ["id", "docType"]}).get('result', {}).get('documents', [])
            incoming = {}
            to_production = {}
            shipped = {}
            written_off = {}
            for doc in documents:
                doc_id = doc.get('id')
                doc_type = doc.get('docType', '')
                try:
                    all_items = bitrix_api("catalog.document.element.list.json", {"DOC_ID": int(doc_id)}).get('result', {}).get('documentElements', [])
                    items = [i for i in all_items if i.get('docId') == doc_id]
                except: continue
                for item in items:
                    pid = str(item.get('elementId', ''))
                    qty = float(item.get('amount') or 0)
                    sf = item.get('storeFrom')
                    st = item.get('storeTo')
                    if not pid or qty <= 0: continue
                    if pid not in product_names:
                        try: product_names[pid] = bitrix_api("catalog.product.get.json", {"id": int(pid)}).get('result', {}).get('product', {}).get('name', f'Товар {pid}')
                        except: product_names[pid] = f'Товар {pid}'
                    name = product_names[pid]
                    if st and not sf: incoming[name] = incoming.get(name, 0) + qty
                    elif sf and not st: shipped[name] = shipped.get(name, 0) + qty
                    elif sf and st: to_production[name] = to_production.get(name, 0) + qty
                    elif doc_type == 'D': written_off[name] = written_off.get(name, 0) + qty
            if incoming:
                msgs.append(f"\n📥 <b>Принято на склад:</b>")
                for name, qty in sorted(incoming.items(), key=lambda x: x[1], reverse=True): msgs.append(f"   • {name}: {qty:.1f} шт.")
            if to_production:
                msgs.append(f"\n🏭 <b>Перемещено между складами:</b>")
                for name, qty in sorted(to_production.items(), key=lambda x: x[1], reverse=True): msgs.append(f"   • {name}: {qty:.1f} шт.")
            if shipped:
                msgs.append(f"\n🚚 <b>Отгружено со склада:</b>")
                for name, qty in sorted(shipped.items(), key=lambda x: x[1], reverse=True): msgs.append(f"   • {name}: {qty:.1f} шт.")
            if written_off:
                msgs.append(f"\n🗑 <b>Списано:</b>")
                for name, qty in sorted(written_off.items(), key=lambda x: x[1], reverse=True): msgs.append(f"   • {name}: {qty:.1f} шт.")
            if not any([incoming, to_production, shipped, written_off]): msgs.append(f"\n📦 Товародвижение: нет данных за месяц.")
        except Exception as e: msgs.append(f"\n📦 Товародвижение: ошибка — {str(e)[:80]}")

        # --- ИИ ---
        try:
            ai_report = get_ai_report(f"Напиши краткий вывод на русском (2-3 предложения) по итогам месяца: что хорошо, что плохо, какие тренды.\n\n{chr(10).join(msgs)[:500]}")
            if ai_report: msgs.append(f"\n\n🤖 <b>ИИ:</b>\n{ai_report}")
            else: msgs.append(f"\n\n⚠️ ИИ недоступен.")
        except Exception as e: msgs.append(f"\n\n⚠️ Ошибка ИИ: {str(e)[:200]}")

        send_telegram("\n".join(msgs))
    except Exception as e:
        send_telegram(f"❌ Ошибка месячного отчёта: {traceback.format_exc()[:500]}")

# ============================================================
# ТЕСТОВЫЕ ЭНДПОИНТЫ
# ============================================================
@app.route('/audit-test', methods=['GET'])
def audit_test():
    try:
        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        tasks = bitrix_api("tasks.task.list.json", {
            "filter": {">=CHANGED_DATE": now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()},
            "select": ["ID", "TITLE", "STATUS", "RESPONSIBLE_ID"]}).get('result', {}).get('tasks', [])
        msg = f"🧪 <b>Тестовый отчёт ({now.strftime('%d.%m.%Y')})</b>\n\nНайдено задач: {len(tasks)}\n\n"
        for t in tasks[:10]: msg += f"• #{t['id']} {t['title']} (статус: {t.get('status')})\n"
        send_telegram(msg)
        return jsonify({"result": True, "tasks_count": len(tasks)}), 200
    except Exception as e: return jsonify({"result": False, "error": str(e)}), 500

@app.route('/ai-test', methods=['GET'])
def ai_test():
    try:
        resp = requests.post(AI_API_URL,
            headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
            json={"model": AI_MODEL, "messages": [{"role": "user", "content": "Скажи: тест пройден"}], "max_tokens": 50}, timeout=30)
        return jsonify({"status_code": resp.status_code, "body": resp.text[:1000]}), 200
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/task-statuses', methods=['GET'])
def task_statuses():
    TASKS_URL = os.environ.get('BITRIX_TASKS_WEBHOOK_URL', '')
    if not TASKS_URL: return jsonify({"error": "no tasks url"}), 500
    statuses = {}
    start = 0
    while True:
        batch = requests.post(f"{TASKS_URL}task.item.list.json",
            json={"order": {"ID": "desc"}, "start": start}, timeout=15).json().get('result', [])
        if not batch: break
        for t in batch:
            if not isinstance(t, dict): continue
            key = f"STATUS={t.get('STATUS','?')}, REAL_STATUS={t.get('REAL_STATUS','?')}"
            if key not in statuses: statuses[key] = []
            statuses[key].append(t.get('TITLE', '')[:50])
        start += 50
        if start >= 1000: break
    return jsonify({"total_tasks": sum(len(v) for v in statuses.values()),
        "statuses": {k: len(v) for k, v in statuses.items()},
        "examples": {k: v[:2] for k, v in statuses.items()}})

# ============================================================
# Telegram Webhook
# ============================================================
ALLOWED_CHAT_IDS = {'181382021'}

@app.route('/telegram', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json(silent=True) or {}
        msg = data.get('message', {})
        text = msg.get('text', '')
        chat_id = str(msg.get('chat', {}).get('id', ''))
        if chat_id not in ALLOWED_CHAT_IDS:
            send_telegram_to_chat(chat_id, "⛔ У вас нет доступа.")
            return jsonify({"ok": True}), 200
        if text:
            tl = text.lower()
            if 'отчет за месяц' in tl or 'отчёт за месяц' in tl:
                threading.Thread(target=run_monthly_audit, daemon=True).start()
                send_telegram("🟢 Запускаю отчёт за месяц...")
            elif 'отчет' in tl or 'отчёт' in tl:
                threading.Thread(target=run_daily_audit, daemon=True).start()
                send_telegram("🟢 Запускаю аудит за сегодня...")
        return jsonify({"ok": True}), 200
    except: return jsonify({"ok": True}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
