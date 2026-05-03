from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

WEBHOOK_URL = "https://mariomicci.bitrix24.ru/rest/14/8emi78wk1nant8ni/"
BITRIX_DOMAIN = "mariomicci.bitrix24.ru"

# Поля для ссылок
DEAL_LINK_FIELD = "UF_CRM_1777825383755"          # Ссылка в обычной сделке
SMART_LINK_FIELD = "UF_CRM_10_1777827987160"       # Ссылка в смарт-процессе

# entityTypeId смарт-процесса
SMART_ENTITY_TYPE_ID = 1046

@app.route('/', methods=['POST'])
def handler():
    data = request.form.to_dict()
    
    fields = {}
    for key, value in data.items():
        if key.startswith('data[FIELDS]['):
            field_name = key.split('[')[-1].replace(']', '')
            fields[field_name] = value
    
    doc_entity_id = fields.get('ENTITY_ID', '')
    if not doc_entity_id:
        return jsonify({"result": True, "msg": "no entity_id"}), 200
    
    # Получаем данные документа
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
        target_type = "deal"
        
    else:  # Перемещение → смарт-процесс (ищем по XML_ID)
        # Ищем элемент смарт-процесса с xmlId = parent_deal_id
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
            return jsonify({"result": True, "msg": "smart item not found by xmlId", "xmlId": parent_deal_id}), 200
        
        smart_item_id = items[0]['id']
        update_url = f"{WEBHOOK_URL}crm.item.update.json"
        payload = {
            "entityTypeId": SMART_ENTITY_TYPE_ID,
            "id": smart_item_id,
            "fields": {SMART_LINK_FIELD: doc_link}
        }
        target_type = "smart"
    
    update_resp = requests.post(update_url, json=payload, timeout=10)
    
    return jsonify({
        "result": True,
        "doc_type": doc_type,
        "target_type": target_type,
        "parent_deal_id": parent_deal_id,
        "doc_link": doc_link,
        "update_status": update_resp.status_code
    }), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
