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
    """Основная функция аудита за текущий день"""
    try:
        tz = pytz.timezone(TIMEZONE)
        now = datetime.now(tz)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        logs = []

        # --- 1. Сбор лидов ---
        try:
            leads_resp = bitrix_api("crm.lead.list.json", {
                "filter": {">=DATE_MODIFY": today_start},
                "select": ["ID", "TITLE", "STATUS_ID", "ASSIGNED_BY_ID", "CONTACT_ID", "COMPANY_ID"]
            })
            leads = leads_resp.get('result', [])
            logs.append(f"=== ЛИДЫ (изменено: {len(leads)}) ===")
            for lead in leads:
                logs.append(
                    f"[Лид #{lead.get('id')}] {lead.get('title')} | "
                    f"Статус: {lead.get('statusId')} | "
                    f"Контакт: {lead.get('contactId', 'нет')} | Компания: {lead.get('companyId', 'нет')}"
                )
        except Exception as e:
            logs.append(f"⚠️ Ошибка сбора лидов: {str(e)[:200]}")

        # --- 2. Сбор сделок ---
        try:
            deals_resp = bitrix_api("crm.deal.list.json", {
                "filter": {">=DATE_MODIFY": today_start},
                "select": ["ID", "TITLE", "STAGE_ID", "ASSIGNED_BY_ID", "OPPORTUNITY", "CONTACT_ID", "COMPANY_ID"]
            })
            deals = deals_resp.get('result', [])
            logs.append(f"\n=== СДЕЛКИ (изменено: {len(deals)}) ===")
            for deal in deals:
                logs.append(
                    f"[Сделка #{deal.get('id')}] {deal.get('title')} | "
                    f"Стадия: {deal.get('stageId')} | Сумма: {deal.get('opportunity')} | "
                    f"Контакт: {deal.get('contactId', 'нет')} | Компания: {deal.get('companyId', 'нет')}"
                )
        except Exception as e:
            logs.append(f"⚠️ Ошибка сбора сделок: {str(e)[:200]}")

        # --- 3. Сбор задач и комментариев ---
        try:
            tasks_resp = bitrix_api("tasks.task.list.json", {
                "filter": {">=CHANGED_DATE": today_start},
                "select": ["ID", "TITLE", "STATUS", "RESPONSIBLE_ID", "CREATED_BY", "DESCRIPTION"]
            })
            tasks = tasks_resp.get('result', {}).get('tasks', [])
            logs.append(f"\n=== ЗАДАЧИ (изменено: {len(tasks)}) ===")

            for task in tasks:
                task_id = task.get('id')
                logs.append(
                    f"[Задача #{task_id}] {task.get('title')} | "
                    f"Статус: {task.get('status')} | Ответственный: {task.get('responsibleId')}"
                )

                # Комментарии к задаче
                try:
                    comments_resp = bitrix_api("task.commentitem.getlist.json", {
                        "TASKID": task_id,
                        "select": ["ID", "AUTHOR_ID", "POST_MESSAGE", "CREATED_DATE"]
                    })
                    comments = comments_resp.get('result', [])
                    for comment in comments:
                        if isinstance(comment, dict):
                            msg = comment.get('POST_MESSAGE', '')
                            logs.append(f"  ↳ Комментарий (user {comment.get('AUTHOR_ID')}): {msg[:300]}")
                except:
                    pass
        except Exception as e:
            logs.append(f"⚠️ Ошибка сбора задач: {str(e)[:200]}")

        # --- 4. Обезличивание ---
        raw_text = "\n".join(logs)
        clean_text = anonymize_text(raw_text)

        # --- 5. Отправка в ИИ ---
        system_prompt = """Ты — беспристрастный корпоративный аудитор. Твоя задача — изучить сырые логи из Битрикс24 за сегодня и составить для директора краткий, жесткий отчет без "воды".
Проанализируй данные по разделам:
1. СБОИ В РАБОТЕ С CRM: Кто завел Лид/Сделку без контактов? Какие обязательные поля (ИНН, сумма, файлы) пропущены, несмотря на закрытые авто-задачи по ним? Что "зависло"?
2. МАНЕРА ПЕРЕПИСКИ И КОНФЛИКТЫ: Найди признаки агрессии, токсичности, капслока или споров в задачах. Кто перекладывал ответственность вместо решения проблемы?
3. ПРОБЛЕМЫ И СПОТЫКАНИЯ: На каких этапах сотрудники буксуют и затягивают сроки (пишут "не знаю как", часами ждут другие отделы)?
4. ГЛАВНЫЕ ДОСТИЖЕНИЯ: Кто проявил инициативу или закрыл крупный чек без косяков?
Формат отчета: только факты, имена сотрудников, номера задач/сделок и суть. Если проблем по пункту нет — пропускай раздел."""

        try:
            ai_resp = requests.post(
                AI_API_URL,
                headers={
                    "Authorization": f"Bearer {AI_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": AI_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Логи за сегодня ({now.strftime('%d.%m.%Y')}):\n\n{clean_text[:50000]}"}
                    ],
                    "temperature": 0.7,
                    "max_tokens": 3000
                },
                timeout=120
            ).json()

            report = ai_resp.get('choices', [{}])[0].get('message', {}).get('content', 'Не удалось получить отчёт от ИИ')
        except Exception as e:
            report = f"⚠️ Ошибка при обращении к ИИ: {str(e)[:300]}"

        # --- 6. Отправка в Telegram ---
        header = f"📊 <b>ОТЧЁТ ПО КОНТРОЛЮ ЗА {now.strftime('%d.%m.%Y')}</b>\n\n"
        footer = "\n\n<i>Автоматический отчёт. Система Invisible Audit.</i>"

        full_report = header + report + footer
        if len(full_report) > 4000:
            chunks = [full_report[i:i+4000] for i in range(0, len(full_report), 4000)]
            for chunk in chunks:
                send_telegram(chunk)
                time.sleep(0.5)
        else:
            send_telegram(full_report)

    except Exception as e:
        send_telegram(f"⚠️ Критическая ошибка аудита: {str(e)[:500]}")


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


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
