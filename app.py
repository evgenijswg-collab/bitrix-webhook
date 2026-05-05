from flask import Flask, request, jsonify
import requests
import time

app = Flask(__name__)

WEBHOOK_URL = "https://mariomicci.bitrix24.ru/rest/14/8emi78wk1nant8ni/"
BITRIX_DOMAIN = "mariomicci.bitrix24.ru"

# Поля для ссылок (склад)
DEAL_LINK_FIELD = "UF_CRM_1777825383755"
SMART_LINK_FIELD = "ufCrm10_1777827987160"
SMART_ENTITY_TYPE_ID = 1046

# Поля для договора
CONTRACT_NUMBER_FIELD = "UF_CRM_1777452882147"  # Номер договора в сделке
BP_TEMPLATE_ID = 282                            # ID шаблона БП «Создание счёта»

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
        json={"filter": {"id": int(doc_entity_id)}, "select": ["id", "docNumber", "docType"]},
        timeout=10
    ).json()

    documents = resp.get('result', {}).get('documents', [])
    if not documents:
        return jsonify({"result": True, "msg": "not found"}), 200

    doc = documents[0]
    parent_deal_id = doc.get('docNumber', '')
    doc_type = doc.get('docType', '')

    if not parent_deal_id:
        return jsonify({"result": True, "msg": "no deal_id"}), 200

    doc_link = f"https://{BITRIX_DOMAIN}/shop/documents/details/{doc_entity_id}/"

    if doc_type == "A":  # Приход → обычная сделка
        update_url = f"{WEBHOOK_URL}crm.deal.update.json"
        payload = {"id": int(parent_deal_id), "fields": {DEAL_LINK_FIELD: doc_link}}
    else:  # Перемещение → смарт-процесс
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
    return jsonify({"result": True, "doc_type": doc_type, "doc_link": doc_link}), 200


# ============================================================
# Маршрут 2: Договор → ожидание номера → создание счёта
# ============================================================
@app.route('/contract', methods=['POST'])
def contract_handler():
    data = request.form.to_dict()

    # Извлекаем ID сделки из данных вебхука
    fields = {}
    for key, value in data.items():
        if key.startswith('data[FIELDS]['):
            field_name = key.split('[')[-1].replace(']', '')
            fields[field_name] = value

    # Для документа ENTITY_ID — это ID сделки
    deal_id = fields.get('ENTITY_ID', '')
    if not deal_id:
        return jsonify({"result": True, "msg": "no deal_id"}), 200

    # Ждём появления номера договора (до 15 попыток по 2 секунды)
    contract_number = None
    for attempt in range(15):
        time.sleep(2)

        resp = requests.post(
            f"{WEBHOOK_URL}crm.deal.get.json",
            json={"id": int(deal_id)},
            timeout=10
        ).json()

        contract_number = resp.get('result', {}).get(CONTRACT_NUMBER_FIELD, '')
        if contract_number:
            break

    if not contract_number:
        return jsonify({"result": True, "msg": "contract number still empty after 30 sec"}), 200

    # Запускаем БП «Создание счёта»
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

    return jsonify({
        "result": True,
        "deal_id": deal_id,
        "contract_number": contract_number,
        "bp_started": bp_resp.get('result') is not None
    }), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
