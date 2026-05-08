from flask import Flask, request, jsonify
import requests
import time
import threading

app = Flask(__name__)

WEBHOOK_URL = "https://mariomicci.bitrix24.ru/rest/14/8emi78wk1nant8ni/"
BITRIX_DOMAIN = "mariomicci.bitrix24.ru"

DEAL_LINK_FIELD = "UF_CRM_1777825383755"
SMART_LINK_FIELD = "ufCrm10_1777827987160"
SMART_DATE_FIELD = "ufCrm10_1775722563672"          # Дата документа в смарт-процессе (только для прихода)
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


# ============================================================
# Маршрут 1: Складские документы (приход / перемещение)
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

    resp = requests.post(
        f"{WEBHOOK_URL}catalog.document.list.json",
        json={"filter": {"id": int(doc_entity_id)}, "select": ["id", "docNumber", "docType", "dateDocument"]},
        timeout=10
    ).json()

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

    if doc_type == "A":  # Приход → обычная сделка + дата в смарт-процесс
        # Ссылка в обычную сделку
        update_url = f"{WEBHOOK_URL}crm.deal.update.json"
        payload = {"id": int(parent_deal_id), "fields": {DEAL_LINK_FIELD: doc_link}}
        requests.post(update_url, json=payload, timeout=10)

        # Дата в смарт-процесс
        if doc_date:
            smart_search = requests.post(
                f"{WEBHOOK_URL}crm.item.list.json",
                json={
                    "entityTypeId": SMART_ENTITY_TYPE_ID,
                    "filter": {"xmlId": parent_deal_id},
                    "select": ["id"]
                },
                timeout=10
            ).json()

            smart_items = smart_search.get('result', {}).get('items', [])
            if smart_items:
                smart_id = smart_items[0]['id']
                requests.post(
                    f"{WEBHOOK_URL}crm.item.update.json",
                    json={
                        "entityTypeId": SMART_ENTITY_TYPE_ID,
                        "id": smart_id,
                        "fields": {SMART_DATE_FIELD: doc_date}
                    },
                    timeout=10
                )

    else:  # Перемещение → смарт-процесс (ссылка, без даты)
        search_resp = requests.post(
            f"{WEBHOOK_URL}crm.item.list.json",
            json={
                "entityTypeId": SMART_ENTITY_TYPE_ID,
                "filter": {"xmlId": parent_deal_id},
                "select": ["id"]
            },
            timeout=10
        ).json()

        items = search_resp.get('result', {}).get('items', [])
        if not items:
            return jsonify({"result": True, "msg": "smart item not found"}), 200

        smart_item_id = items[0]['id']
        update_url = f"{WEBHOOK_URL}crm.item.update.json"
        payload = {
            "entityTypeId": SMART_ENTITY_TYPE_ID,
            "id": smart_item_id,
            "fields": {SMART_LINK_FIELD: doc_link}
        }
        requests.post(update_url, json=payload, timeout=10)

    return jsonify({"result": True, "doc_type": doc_type, "doc_link": doc_link, "doc_date": doc_date}), 200


# ============================================================
# Маршрут 2: Договор → ждём завершения БП → создание счёта
# ============================================================
@app.route('/contract', methods=['POST'])
def contract_handler():
    global last_contract_debug, recent_deals
    last_contract_debug = {}

    data = request.form.to_dict()
    last_contract_debug['raw_keys'] = list(data.keys())

    fields = {}
    for key, value in data.items():
        if key.startswith('data[FIELDS]['):
            field_name = key.split('[')[-1].replace(']', '')
            fields[field_name] = value

    last_contract_debug['fields'] = fields

    deal_id = fields.get('ENTITY_ID', '')
    last_contract_debug['deal_id'] = deal_id

    if not deal_id:
        return jsonify({"result": True, "msg": "no deal_id"}), 200

    with recent_lock:
        if deal_id in recent_deals:
            return jsonify({"result": True, "msg": "already processed"}), 200
        recent_deals.add(deal_id)
    threading.Thread(target=clear_deal, args=(deal_id,), daemon=True).start()

    contract_number = None
    attempts_log = []
    for attempt in range(30):
        time.sleep(2)

        deal_resp = requests.post(
            f"{WEBHOOK_URL}crm.deal.get.json",
            json={"id": int(deal_id)},
            timeout=10
        ).json()
        contract_number = deal_resp.get('result', {}).get(CONTRACT_NUMBER_FIELD, '')

        bp_resp = requests.post(
            f"{WEBHOOK_URL}bizproc.workflow.instances.json",
            json={
                "SELECT": ["ID", "STATE"],
                "FILTER": {
                    "DOCUMENT_ID": f"crm_CCrmDocumentDeal_{deal_id}",
                    "STATE": "running"
                }
            },
            timeout=10
        ).json()

        running_count = len(bp_resp.get('result', []))

        attempts_log.append({
            "attempt": attempt + 1,
            "contract_number": contract_number,
            "running_bp": running_count
        })

        if contract_number and running_count == 0:
            break

    last_contract_debug['attempts'] = attempts_log
    last_contract_debug['final_contract_number'] = contract_number

    if not contract_number:
        return jsonify({"result": True, "msg": "contract number still empty"}), 200

    bp_resp = requests.post(
        f"{WEBHOOK_URL}bizproc.workflow.start.json",
        json={
            "TEMPLATE_ID": BP_TEMPLATE_ID,
            "DOCUMENT_ID": ["crm", "CCrmDocumentDeal", int(deal_id)],
            "PARAMETERS": {
                "contractNumber": contract_number
            }
        },
        timeout=10
    ).json()

    last_contract_debug['bp_response'] = bp_resp

    return jsonify({"result": True, "bp_started": bp_resp.get('result') is not None}), 200


@app.route('/contract-debug', methods=['GET'])
def contract_debug():
    return jsonify(last_contract_debug)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
