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
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ (БЕЗ ХАРДКОДА!)
# ============================================================
WEBHOOK_URL = os.environ.get('BITRIX_WEBHOOK_URL')
BITRIX_DOMAIN = os.environ.get('BITRIX_DOMAIN', 'mariomicci.bitrix24.ru')

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')

AI_API_URL = os.environ.get('AI_API_URL', 'https://openrouter.ai/api/v1/chat/completions')
AI_API_KEY = os.environ.get('AI_API_KEY')
AI_MODEL = os.environ.get('AI_MODEL', 'qwen/qwen-2.5-7b-instruct:free')

TIMEZONE = os.environ.get('TIMEZONE', 'Europe/Moscow')

# Проверка обязательных переменных
if not WEBHOOK_URL:
    raise RuntimeError("BITRIX_WEBHOOK_URL is not set!")
if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is not set!")
if not TELEGRAM_CHAT_ID:
    raise RuntimeError("TELEGRAM_CHAT_ID is not set!")
if not AI_API_KEY:
    raise RuntimeError("AI_API_KEY is not set!")

# Поля для складского учёта
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
    with recent_lock:
        recent_deals.discard(did)


def bitrix_api(method, data=None, timeout=10):
    """Вызов REST API Битрикс24"""
    url = f"{WEBHOOK_URL}{method}"
    if data is None:
        data = {}
    resp = requests.post(url, json=data, timeout=timeout)
    return resp.json()


def send_telegram(text):
    """Отправка сообщения в Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": int(TELEGRAM_CHAT_ID),
            "text": text,
            "parse_mode": "HTML"
        }, timeout=10)
    except Exception as e:
        print(f"Telegram send error: {e}")


def anonymize_text(text):
    """Обезличивание персональных данных"""
    text = re.sub(r'\+?\d{1,3}[\s\-]?\(?\d{2,4}\)?[\s\-]?\d{2,3}[\s\-]?\d{2}[\s\-]?\d{2}', '[ТЕЛЕФОН]', text)
    text = re.sub(r'\b\d{10,12}\b', '[ТЕЛЕФОН]', text)
    text = re.sub(r'(Клиент|Заказчик|ФИО|Покупатель|Заказчик)\s*:?\s*[^\n,;]+', r'\1: [КЛИЕНТ]', text, flags=re.IGNORECASE)
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]', text)
    return text


# ============================================================
# МАРШРУТ 1: Складские документы (приход / перемещение)
# ============================================================
@app.route('/', methods=['POST'])
def warehouse_handler():
    data = request.form.to_dict()

    fields = {}
    for key, value in data.items():
        if key.startswith('data[FIELDS]['):
            field_name = key.split('[')[-1].replace(']', '')
            fields[field_name] = value

    doc_entity_id = fields.get('ENTITY_ID', '')
    if not doc_entity_id:
        return jsonify({"result": True, "msg": "no entity_id"}), 200

    resp = bitrix_api("catalog.document.list.json", {
        "filter": {"id": int(doc_entity_id)},
        "select": ["id", "docNumber", "docType", "dateDocument"]
    })

    documents = resp.get('result', {}).get('documents', [])
    if not documents:
        return jsonify({"result": True, "msg": "not found"}), 200

    doc = documents[0]
    parent_deal_id = doc.get('docNumber', '')
    doc_type = doc.get('docType', '')
    doc_date = doc.get('dateDocument', '')

    if not parent_deal_id:
        return jsonify({"result": True, "msg": "no deal_id"}), 200

    doc_link = f"https://{BITRIX_DOMAIN}/shop/documents/details/{doc_entity_id}/"

    if doc_type == "A":
        bitrix_api("crm.deal.update.json", {
            "id": int(parent_deal_id),
            "fields": {DEAL_LINK_FIELD: doc_link}
        })

        if doc_date:
            smart_search = bitrix_api("crm.item.list.json", {
                "entityTypeId": SMART_ENTITY_TYPE_ID,
                "filter": {"xmlId": parent_deal_id},
                "select": ["id"]
            })
            smart_items = smart_search.get('result', {}).get('items', [])
            if smart_items:
                smart_id = smart_items[0]['id']
                bitrix_api("crm.item.update.json", {
                    "entityTypeId": SMART_ENTITY_TYPE_ID,
                    "id": smart_id,
                    "fields": {SMART_DATE_FIELD: doc_date}
                })

    else:
        search_resp = bitrix_api("crm.item.list.json", {
            "entityTypeId": SMART_ENTITY_TYPE_ID,
            "filter": {"xmlId": parent_deal_id},
            "select": ["id"]
        })
        items = search_resp.get('result', {}).get('items', [])
        if not items:
            return jsonify({"result": True, "msg": "smart item not found"}), 200

        smart_item_id = items[0]['id']
        bitrix_api("crm.item.update.json", {
            "entityTypeId": SMART_ENTITY_TYPE_ID,
            "id": smart_item_id,
            "fields": {SMART_LINK_FIELD: doc_link}
        })

    return jsonify({"result": True, "doc_type": doc_type, "doc_link": doc_link, "doc_date": doc_date}), 200


# ============================================================
# МАРШРУТ 2: Договор → ждём завершения БП → создание счёта
# ============================================================
def process_contract_background(deal_id):
    """Фоновый процесс ожидания номера договора и запуска БП"""
    contract_number = None
    for attempt in range(30):
        time.sleep(2)

        deal_resp = bitrix_api("crm.deal.get.json", {"id": int(deal_id)})
        contract_number = deal_resp.get('result', {}).get(CONTRACT_NUMBER_FIELD, '')

        bp_resp = bitrix_api("bizproc.workflow.instances.json", {
            "SELECT": ["ID", "STATE"],
            "FILTER": {
                "DOCUMENT_ID": f"crm_CCrmDocumentDeal_{deal_id}",
                "STATE": "running"
            }
        })

        running_count = len(bp_resp.get('result', []))

        if contract_number and running_count == 0:
            break

    if contract_number:
        bitrix_api("bizproc.workflow.start.json", {
            "TEMPLATE_ID": BP_TEMPLATE_ID,
            "DOCUMENT_ID": ["crm", "CCrmDocumentDeal", int(deal_id)],
            "PARAMETERS": {"contractNumber": contract_number}
        })


@app.route('/contract', methods=['POST'])
def contract_handler():
    global recent_deals

    data = request.form.to_dict()

    fields = {}
    for key, value in data.items():
        if key.startswith('data[FIELDS]['):
            field_name = key.split('[')[-1].replace(']', '')
            fields[field_name] = value

    deal_id = fields.get('ENTITY_ID', '')

    if not deal_id:
        return jsonify({"result": True, "msg": "no deal_id"}), 200

    with recent_lock:
        if deal_id in recent_deals:
            return jsonify({"result": True, "msg": "already processed"}), 200
        recent_deals.add(deal_id)

    # Запускаем в фоновом потоке — мгновенно отвечаем Битриксу
    threading.Thread(target=process_contract_background, args=(deal_id,), daemon=True).start()
    threading.Thread(target=clear_deal, args=(deal_id,), daemon=True).start()

    return jsonify({"result": True, "msg": "processing started"}), 200


@app.route('/contract-debug', methods=['GET'])
def contract_debug():
    return jsonify(last_contract_debug)


# ============================================================
# МАРШРУТ 3: НЕВИДИМЫЙ КОНТРОЛЬ (Invisible Audit)
# ============================================================
@app.route('/audit', methods=['GET'])
def daily_audit_route():
    try:
        # Запускаем аудит в фоновом потоке, чтобы не ждать
        threading.Thread(target=run_daily_audit, daemon=True).start()
        return jsonify({"result": True, "status": "Audit started"}), 200
    except Exception as e:
        return jsonify({"result": False, "error": str(e)}), 500


def run_daily_audit():
    import traceback
    try:
        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        today = now.strftime('%Y-%m-%d')
        
        TASKS_URL = os.environ.get('BITRIX_TASKS_WEBHOOK_URL', '')
        
        def fmt_rub(amount):
            return f"{amount:,.0f}".replace(",", " ")
        
        users = {}
        def get_user_name(uid):
            if not uid or uid == '0': return 'Не назначен'
            uid = str(uid)
            if uid not in users:
                try:
                    resp = requests.post(f"{WEBHOOK_URL}user.get.json", json={"ID": int(uid)}, timeout=5).json()
                    user = resp.get('result', [{}])[0]
                    users[uid] = f"{user.get('NAME','')} {user.get('LAST_NAME','')}".strip() or f"User {uid}"
                except:
                    users[uid] = f"User {uid}"
            return users[uid]
        
        SMART_STAGES = {
            "DT1046_16:NEW": "Проверка ТЗ",
            "DT1046_16:UC_L5A63P": "Ожидание камня",
            "DT1046_16:UC_7X8UF5": "Материал на складе",
            "DT1046_16:PREPARATION": "Изготовление",
            "DT1046_16:UC_PYF2PE": "Готово к отгрузке",
            "DT1046_16:SUCCESS": "Успех",
            "DT1046_16:FAIL": "Провал"
        }
        
        msgs = []
        
        # --- ЛИДЫ ---
        try:
            leads_resp = bitrix_api("crm.lead.list.json", {
                "filter": {"!STATUS_ID": ["CONVERTED", "JUNK"]},
                "select": ["ID", "TITLE", "STATUS_ID", "OPPORTUNITY"]
            })
            leads = leads_resp.get('result', [])
            sum_leads = sum(float(l.get('OPPORTUNITY') or 0) for l in leads)
            msgs.append(f"🔹 <b>Лиды в работе:</b> {len(leads)} шт. на {fmt_rub(sum_leads)} руб.")
        except Exception as e:
            msgs.append(f"🔹 Лиды: ошибка — {str(e)[:80]}")
        
        # --- СДЕЛКИ ---
        try:
            deals_resp = bitrix_api("crm.deal.list.json", {
                "filter": {"!STAGE_ID": "WON"},
                "select": ["ID", "TITLE", "STAGE_ID", "OPPORTUNITY", "CLOSEDATE"]
            })
            deals = deals_resp.get('result', [])
            
            in_work = []
            lost_today = []
            for d in deals:
                stage = d.get('STAGE_ID', '')
                if stage == "LOSE":
                    closed = (d.get('CLOSEDATE', '') or '')[:10]
                    if closed == today:
                        lost_today.append(d)
                elif stage != "WON":
                    in_work.append(d)
            
            sum_work = sum(float(d.get('OPPORTUNITY') or 0) for d in in_work)
            sum_lost = sum(float(d.get('OPPORTUNITY') or 0) for d in lost_today)
            
            msgs.append(f"📊 <b>Сделки в работе:</b> {len(in_work)} шт. на {fmt_rub(sum_work)} руб.")
            if lost_today:
                msgs.append(f"   ❌ <b>Провалено сегодня:</b> {len(lost_today)} шт. на {fmt_rub(sum_lost)} руб.")
        except Exception as e:
            msgs.append(f"📊 Сделки: ошибка — {str(e)[:80]}")
        
        # --- СМАРТ-ПРОЦЕСС ---
        try:
            smart_resp = bitrix_api("crm.item.list.json", {
                "entityTypeId": SMART_ENTITY_TYPE_ID,
                "select": ["id", "title", "stageId"]
            })
            items = smart_resp.get('result', {}).get('items', [])
            
            active = [i for i in items if i.get('stageId') not in ('DT1046_16:SUCCESS', 'DT1046_16:FAIL')]
            by_stage = {}
            for i in active:
                sid = i.get('stageId', '?')
                name = SMART_STAGES.get(sid, sid)
                by_stage[name] = by_stage.get(name, 0) + 1
            
            msgs.append(f"\n📦 <b>Производство:</b> {len(active)} заказов")
            for stage in ("Изготовление", "Готово к отгрузке", "Ожидание камня", "Материал на складе"):
                cnt = by_stage.get(stage, 0)
                if cnt:
                    msgs.append(f"   • {stage}: {cnt}")
        except Exception as e:
            msgs.append(f"\n📦 Производство: ошибка — {str(e)[:80]}")
        
        # --- ЗАДАЧИ ---
        try:
            if TASKS_URL:
                WORK_STATUSES = {'-1', '-2', '-3', '2', '3'}
                
                employees = {}
                start = 0
                while True:
                    tasks_resp = requests.post(
                        f"{TASKS_URL}task.item.list.json",
                        json={"order": {"ID": "desc"}, "start": start},
                        timeout=15
                    ).json()
                    
                    batch = tasks_resp.get('result', [])
                    if not batch:
                        break
                    
                    for t in batch:
                        if not isinstance(t, dict):
                            continue
                        status = str(t.get('STATUS', ''))
                        if status not in WORK_STATUSES:
                            continue
                        
                        uid = str(t.get('RESPONSIBLE_ID', '0'))
                        if uid == '0':
                            continue
                        
                        deadline = t.get('DEADLINE', '')[:10]
                        if uid not in employees:
                            employees[uid] = {"total": 0, "overdue": 0}
                        employees[uid]["total"] += 1
                        if deadline and deadline < today:
                            employees[uid]["overdue"] += 1
                    
                    start += 50
                    if start >= 1000:
                        break
                
                total_t = sum(e['total'] for e in employees.values())
                total_o = sum(e['overdue'] for e in employees.values())
                
                msgs.append(f"\n📋 <b>Задачи в работе:</b> {total_t}, <b>просрочено: {total_o}</b>")
                
                sorted_emp = sorted(employees.items(), key=lambda x: x[1]['total'], reverse=True)
                for uid, data in sorted_emp:
                    name = get_user_name(uid)
                    msgs.append(f"   • {name}: {data['total']} задач, просрочено: {data['overdue']}")
                
                overdue_emp = {uid: d for uid, d in employees.items() if d['overdue'] > 0}
                if overdue_emp:
                    msgs.append(f"\n⚠️ <b>Просроченные задачи:</b>")
                    for uid, data in sorted(overdue_emp.items(), key=lambda x: x[1]['overdue'], reverse=True):
                        name = get_user_name(uid)
                        msgs.append(f"   • {name}: {data['overdue']} просрочено")
        except Exception as e:
            msgs.append(f"\n📋 Задачи: ошибка — {str(e)[:80]}")
        
        # --- ОСТАТКИ ПО СКЛАДАМ ---
        try:
            store_resp = bitrix_api("catalog.storeproduct.list.json")
            products = store_resp.get('result', {}).get('storeProducts', [])
            
            if products:
                product_ids = set(p['productId'] for p in products if p.get('amount'))
                store_ids = set(p['storeId'] for p in products if p.get('amount'))
                
                product_names = {}
                for pid in product_ids:
                    try:
                        p_resp = bitrix_api("catalog.product.get.json", {"id": int(pid)})
                        product_names[str(pid)] = p_resp.get('result', {}).get('product', {}).get('name', f'Товар {pid}')
                    except:
                        product_names[str(pid)] = f'Товар {pid}'
                
                store_names = {}
                try:
                    s_resp = bitrix_api("catalog.store.list.json")
                    for s in s_resp.get('result', {}).get('stores', []):
                        store_names[str(s['id'])] = s.get('title', f'Склад {s["id"]}')
                except:
                    pass
                
                by_product = {}
                for p in products:
                    amt = p.get('amount')
                    if not amt or float(amt) <= 0:
                        continue
                    pid = str(p['productId'])
                    sid = str(p['storeId'])
                    if pid not in by_product:
                        by_product[pid] = {}
                    by_product[pid][sid] = float(amt)
                
                if by_product:
                    msgs.append(f"\n📦 <b>Остатки по складам:</b>")
                    for pid, stores in sorted(by_product.items()):
                        name = product_names.get(pid, f'Товар {pid}')
                        total = sum(stores.values())
                        details = ", ".join([f"{store_names.get(sid, sid)}: {fmt_rub(amt)}" for sid, amt in stores.items()])
                        msgs.append(f"   • {name}: {fmt_rub(total)} ({details})")
        except Exception as e:
            msgs.append(f"\n📦 Остатки: ошибка — {str(e)[:80]}")
        
        # --- ИИ-анализ ---
        try:
            raw_text = "\n".join(msgs)
            
            models = [
                "google/gemma-4-31b-it:free",
                "meta-llama/llama-3.3-70b-instruct:free",
                "qwen/qwen3-next-80b-a3b-instruct:free",
                "meta-llama/llama-3.2-3b-instruct:free"
            ]
            
            ai_report = None
            for model in models:
                time.sleep(10)
                try:
                    ai_resp = requests.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {AI_API_KEY}", "Content-Type": "application/json"},
                        json={
                            "model": model,
                            "messages": [
                                {"role": "user", "content": f"Напиши краткий вывод на русском (2-3 предложения): что хуже всего в этих данных, на что обратить внимание.\n\n{raw_text[:800]}"}
                            ],
                            "max_tokens": 300,
                            "temperature": 0.5
                        },
                        timeout=30
                    ).json()
                    
                    ai_report = ai_resp.get('choices', [{}])[0].get('message', {}).get('content', '')
                    if ai_report:
                        msgs.append(f"\n\n🤖 <b>ИИ ({model}):</b>\n{ai_report}")
                        break
                except:
                    pass
            
            if not ai_report:
                msgs.append(f"\n\n⚠️ Все модели ИИ недоступны. Попробуйте позже.")
        except Exception as e:
            msgs.append(f"\n\n⚠️ Ошибка ИИ: {str(e)[:200]}")
        
        send_telegram("\n".join(msgs))
        
    except Exception as e:
        send_telegram(f"❌ Крах: {traceback.format_exc()[:1000]}")
        # --- 6. Отправка в Telegram ---
        header = f"📊 <b>ОТЧЁТ ЗА {now.strftime('%d.%m.%Y')}</b>\n\n"
        footer = "\n\n<i>Invisible Audit</i>"

        # Логируем для отладки
        debug_info = f"DEBUG:\nAI_API_URL: {AI_API_URL}\nModel: {AI_MODEL}\n"
        debug_info += f"Report length: {len(report)}\n"
        debug_info += f"Report preview: {report[:300]}\n"
        debug_info += f"Total logs: {len(raw_text)} chars, {len(clean_text)} chars cleaned\n"
        
        full_report = header + debug_info + "\n---\n" + report[:3000] + footer
        
        if len(full_report) > 4000:
            chunks = [full_report[i:i+4000] for i in range(0, len(full_report), 4000)]
            for chunk in chunks:
                send_telegram(chunk)
                time.sleep(0.5)
        else:
            send_telegram(full_report)


# ============================================================
# ТЕСТОВЫЙ ЭНДПОИНТ
# ============================================================
@app.route('/audit-test', methods=['GET'])
def audit_test():
    try:
        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        tasks_resp = bitrix_api("tasks.task.list.json", {
            "filter": {">=CHANGED_DATE": today_start},
            "select": ["ID", "TITLE", "STATUS", "RESPONSIBLE_ID"]
        })
        tasks = tasks_resp.get('result', {}).get('tasks', [])

        msg = f"🧪 <b>Тестовый отчёт ({now.strftime('%d.%m.%Y')})</b>\n\nНайдено задач: {len(tasks)}\n\n"
        for t in tasks[:10]:
            msg += f"• #{t['id']} {t['title']} (статус: {t.get('status')})\n"

        send_telegram(msg)
        return jsonify({"result": True, "tasks_count": len(tasks)}), 200
    except Exception as e:
        return jsonify({"result": False, "error": str(e)}), 500
@app.route('/ai-test', methods=['GET'])
def ai_test():
    try:
        resp = requests.post(
            AI_API_URL,
            headers={
                "Authorization": f"Bearer {AI_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": AI_MODEL,
                "messages": [
                    {"role": "user", "content": "Скажи: тест пройден"}
                ],
                "max_tokens": 50
            },
            timeout=30
        )
        return jsonify({
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "body": resp.text[:1000]
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/task-statuses', methods=['GET'])
def task_statuses():
    TASKS_URL = os.environ.get('BITRIX_TASKS_WEBHOOK_URL', '')
    if not TASKS_URL:
        return jsonify({"error": "no tasks url"}), 500
    
    statuses = {}
    start = 0
    while True:
        resp = requests.post(f"{TASKS_URL}task.item.list.json", json={"order": {"ID": "desc"}, "start": start}, timeout=15).json()
        batch = resp.get('result', [])
        if not batch: break
        
        for t in batch:
            if not isinstance(t, dict): continue
            uid = str(t.get('RESPONSIBLE_ID','0'))
            if uid == '0': continue
            st = str(t.get('STATUS','?'))
            real = str(t.get('REAL_STATUS','?'))
            key = f"STATUS={st}, REAL_STATUS={real}"
            if key not in statuses: statuses[key] = []
            statuses[key].append(t.get('TITLE','')[:50])
        
        start += 50
        if start >= 1000: break
    
    result = {}
    for k, v in statuses.items():
        result[k] = len(v)
    
    return jsonify({"total_tasks": sum(result.values()), "statuses": result, "examples": {k: v[:2] for k, v in statuses.items()}})
# ============================================================
# Telegram Webhook — команда отчет
# ============================================================
@app.route('/telegram', methods=['POST'])
def telegram_webhook():
    try:
        data = request.get_json(silent=True) or {}
        message = data.get('message', {})
        text = message.get('text', '')
        chat_id = message.get('chat', {}).get('id', '')
        
        if text and 'отчет' in text.lower() and chat_id:
            threading.Thread(target=run_daily_audit, daemon=True).start()
            send_telegram("🟢 Запускаю аудит... Отчёт придёт через минуту.")
        
        return jsonify({"ok": True}), 200
    except:
        return jsonify({"ok": True}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
